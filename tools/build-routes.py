#!/usr/bin/env python3
"""Convert a Strava activities export into the site's browser route layer.

Usage:
  PYTHONPATH=/path/to/fitdecode python3 tools/build-routes.py activities/ data/runs.js \\
    --activities-csv activities.csv

The export contains a mix of GPX, GPX.GZ, and FIT.GZ files. When supplied,
Strava's activities.csv is the source of truth for which files are Runs. This
matters because the embedded labels in old third-party GPX files are unreliable.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

try:
    import fitdecode
except ImportError as error:
    raise SystemExit(
        "fitdecode is required for FIT files. Install it with: "
        "python3 -m pip install fitdecode"
    ) from error


RUNNING_SPORTS = {"running", "trail_running"}
SEMICIRCLES_PER_DEGREE = 11930464.711111112
EARTH_RADIUS_METERS = 6_371_008.8


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def read_gpx(path: Path) -> tuple[str, list[list[list[float]]]]:
    with gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb") as handle:
        root = ET.parse(handle).getroot()

    activity_type = ""
    tracks = [child for child in root if local_name(child) == "trk"]
    for track in tracks:
        for child in track:
            if local_name(child) == "type" and child.text:
                activity_type = child.text.strip().lower().replace(" ", "_")
                break
        if activity_type:
            break

    lines: list[list[list[float]]] = []
    for track in tracks:
        for segment in track.iter():
            if local_name(segment) != "trkseg":
                continue
            line = []
            for point in segment:
                if local_name(point) != "trkpt":
                    continue
                try:
                    line.append([float(point.attrib["lon"]), float(point.attrib["lat"])])
                except (KeyError, ValueError):
                    continue
            if len(line) > 1:
                lines.append(line)
    return activity_type, lines


def read_fit(path: Path) -> tuple[str, list[list[list[float]]]]:
    sport = ""
    lines: list[list[list[float]]] = []
    current_line: list[list[float]] = []
    opener = gzip.open if path.suffix == ".gz" else open

    with opener(path, "rb") as handle, fitdecode.FitReader(handle) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "session":
                value = frame.get_value("sport")
                if value:
                    sport = str(value).lower()
            elif frame.name == "record":
                if not (frame.has_field("position_lat") and frame.has_field("position_long")):
                    if len(current_line) > 1:
                        lines.append(current_line)
                    current_line = []
                    continue
                latitude = frame.get_value("position_lat")
                longitude = frame.get_value("position_long")
                if latitude is None or longitude is None:
                    if len(current_line) > 1:
                        lines.append(current_line)
                    current_line = []
                    continue
                current_line.append(
                    [float(longitude) / SEMICIRCLES_PER_DEGREE, float(latitude) / SEMICIRCLES_PER_DEGREE]
                )
    if len(current_line) > 1:
        lines.append(current_line)
    return sport, lines


def load_run_filenames(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return {
            Path(row["Filename"]).name
            for row in csv.DictReader(handle)
            if row.get("Activity Type", "").strip().lower() == "run" and row.get("Filename")
        }


def distance_meters(first: list[float], second: list[float]) -> float:
    latitude = math.radians(second[1] - first[1])
    longitude = math.radians(second[0] - first[0]) * math.cos(math.radians((first[1] + second[1]) / 2))
    return EARTH_RADIUS_METERS * math.hypot(latitude, longitude)


def simplify_line(line: list[list[float]], minimum_spacing_meters: float) -> list[list[float]]:
    """Thin dense GPS samples for a responsive browser map, retaining endpoints."""
    if len(line) < 3 or minimum_spacing_meters <= 0:
        return [[round(longitude, 6), round(latitude, 6)] for longitude, latitude in line]

    simplified = [line[0]]
    for point in line[1:-1]:
        if distance_meters(simplified[-1], point) >= minimum_spacing_meters:
            simplified.append(point)
    simplified.append(line[-1])
    return [[round(longitude, 6), round(latitude, 6)] for longitude, latitude in simplified]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("activities_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--activities-csv",
        type=Path,
        help="Strava activities.csv; its Run rows determine which activity files are included.",
    )
    parser.add_argument(
        "--min-point-spacing-meters",
        type=float,
        default=7,
        help="Discard GPS points closer than this in the browser layer (default: 7).",
    )
    args = parser.parse_args()

    run_filenames = load_run_filenames(args.activities_csv) if args.activities_csv else None
    features = []
    sports = Counter()
    skipped = Counter()
    files = sorted(path for path in args.activities_dir.rglob("*") if path.is_file() and not path.name.startswith("._"))

    for path in files:
        suffixes = "".join(path.suffixes).lower()
        try:
            if suffixes in {".gpx", ".gpx.gz"}:
                sport, lines = read_gpx(path)
            elif suffixes in {".fit", ".fit.gz"}:
                sport, lines = read_fit(path)
            else:
                skipped["unsupported"] += 1
                continue
        except Exception as error:  # one damaged export file should not halt an update
            print(f"Warning: skipped {path.name}: {error}", file=sys.stderr)
            skipped["unreadable"] += 1
            continue

        sports[sport or "unknown"] += 1
        is_run = path.name in run_filenames if run_filenames is not None else sport in RUNNING_SPORTS
        if not is_run:
            skipped[sport or "unknown"] += 1
            continue
        if not lines:
            skipped["no_gps"] += 1
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"sourceFile": path.name, "sport": sport},
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [simplify_line(line, args.min_point_spacing_meters) for line in lines],
                },
            }
        )

    geojson = {"type": "FeatureCollection", "features": features}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("window.EVERY_STREET_ROUTES=" + json.dumps(geojson, separators=(",", ":")) + ";\n")
    print(f"Wrote {len(features)} running activities to {args.output}")
    print("Activity types:", ", ".join(f"{sport}={count}" for sport, count in sorted(sports.items())))
    print("Skipped:", ", ".join(f"{reason}={count}" for reason, count in sorted(skipped.items())))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Add one GPX activity to the existing Philly map without rebuilding its base data."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

try:
    from shapely.geometry import LineString, shape
    from shapely.ops import transform, unary_union
except ImportError as error:
    raise SystemExit("Install tools/requirements-map-update.txt in your map-update Python environment first.") from error

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "map" / "index.html"
UPDATE_MAP = ROOT / "tools" / "update-map.py"


def load_updater():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("map_updater", UPDATE_MAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_gpx(path: Path):
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rb") as stream:
        root = ET.parse(stream).getroot()
    segments, timestamps = [], []
    name = None
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "name" and element.text and not name:
            name = element.text.strip()
    for segment in root.iter():
        if segment.tag.rsplit("}", 1)[-1] != "trkseg":
            continue
        coords = []
        for element in segment:
            if element.tag.rsplit("}", 1)[-1] != "trkpt":
                continue
            try:
                coords.append([float(element.attrib["lon"]), float(element.attrib["lat"])])
            except (KeyError, ValueError):
                continue
            for child in element:
                if child.tag.rsplit("}", 1)[-1] == "time" and child.text:
                    try:
                        timestamps.append(datetime.fromisoformat(child.text.replace("Z", "+00:00")))
                    except ValueError:
                        pass
                    break
        if len(coords) > 1:
            segments.append(coords)
    if sum(map(len, segments)) < 2 or not timestamps:
        raise SystemExit("This GPX needs at least two GPS points and a recorded start time.")
    return name or path.stem.replace("_", " "), segments, min(timestamps)


def meters(a, b):
    radius = 6_371_008.8
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1, h)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gpx", type=Path)
    parser.add_argument("--strava-url", help="Optional matching activity URL")
    parser.add_argument("--map-html", type=Path, default=MAP)
    args = parser.parse_args()
    path = args.gpx.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"GPX not found: {path}")

    mod = load_updater()
    name, segments, started = parse_gpx(path)
    strava_id = None
    if args.strava_url:
        match = re.search(r"/activities/(\d+)", args.strava_url)
        if not match:
            raise SystemExit("Strava URL should look like https://www.strava.com/activities/123456")
        strava_id = match.group(1)

    html = args.map_html.read_text(encoding="utf-8")
    routes, route_span = mod.read_embedded(html, "route-data")
    red, red_span = mod.read_embedded(html, "unrun-data")
    boundary_data, _ = mod.read_embedded(html, "boundary-data")
    boundary = unary_union([shape(feature["geometry"]) for feature in boundary_data["features"]])
    lat0 = boundary.centroid.y
    project, unproject = mod.to_local_xy(lat0), mod.from_local_xy(lat0)
    boundary_xy = transform(project, boundary)
    parts, track_lines = [], []
    for segment in segments:
        gps_xy = transform(project, LineString(segment))
        clipped = gps_xy.intersection(boundary_xy)
        for part in mod.line_parts(clipped):
            if part.length < 2:
                continue
            track_lines.append(part)
            coords = transform(unproject, part)
            simplified = mod.simplify([[x, y] for x, y in coords.coords])
            if len(simplified) > 1:
                parts.append(simplified)
    if not parts:
        raise SystemExit("This track does not cross the saved Philadelphia boundary.")

    started = started.astimezone(timezone.utc)
    existing = routes["features"]
    duplicate = next((f for f in existing if f.get("properties", {}).get("gpx_import") and
                      f["properties"].get("start_time_utc") == started.isoformat(timespec="seconds").replace("+00:00", "Z") and
                      f["properties"].get("activity_name") == name), None)
    if duplicate:
        raise SystemExit(f"Already on the map: {name} ({started.date()}). No changes made.")
    if strava_id and any(str(f.get("properties", {}).get("strava_activity_id", "")) == strava_id for f in existing):
        raise SystemExit(f"Strava activity {strava_id} is already on the map. No changes made.")

    distance = sum(meters(a, b) for segment in segments for a, b in zip(segment, segment[1:]))
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "run"
    activity_id = f"strava-{strava_id}" if strava_id else f"gpx-{started:%Y%m%d}-{slug}"
    props = {
        "activity_id": activity_id,
        "start_time": int(started.timestamp() * 1000),
        "start_time_utc": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "activity_name": name,
        "activity_type": "Run",
        "activity_year": str(started.astimezone(mod.PHILLY_TZ).year),
        "gpx_import": True,
        "gpx_distance_m": round(distance, 1),
    }
    if strava_id:
        props["strava_activity_id"] = strava_id
    feature = {"type": "Feature", "id": activity_id, "properties": props,
               "geometry": {"type": "MultiLineString", "coordinates": parts}}

    coverage = []
    for saved in existing:
        try:
            coverage.extend(mod.line_parts(transform(project, shape(saved["geometry"]))))
        except Exception:
            continue
    coverage.extend(track_lines)
    tree = mod.STRtree(coverage) if coverage else None
    radius = mod.COVERAGE_TOLERANCE_M + mod.COVERAGE_PRECISION_MARGIN_M
    remaining = []
    for street in red["features"]:
        original = transform(project, shape(street["geometry"]))
        candidates = tree.query(original.buffer(radius)) if tree is not None else []
        if len(candidates):
            covered = unary_union([coverage[int(index)].buffer(radius) for index in candidates])
            original = original.difference(covered)
        for part in mod.line_parts(original):
            remaining.append({"type": "Feature", "properties": street.get("properties", {}),
                              "geometry": mod.rounded_geometry(transform(unproject, part))})

    routes["features"].append(feature)
    red["features"] = remaining
    replacements = [(route_span, json.dumps(routes, separators=(",", ":"), ensure_ascii=False)),
                    (red_span, json.dumps(red, separators=(",", ":"), ensure_ascii=False))]
    for (start, end), content in sorted(replacements, reverse=True):
        html = html[:start] + content + html[end:]
    html = mod.update_map_timestamp(html)
    args.map_html.write_text(html, encoding="utf-8")
    streets = {str(f.get("properties", {}).get("street_id", "")) for f in remaining} - {""}
    print(f"Added {name} ({started.date()}, {distance / 1609.344:.2f} mi).")
    print(f"Map now has {len(routes['features'])} runs and {len(streets)} unfinished streets.")
    print("Map timestamp updated. Pace is left blank unless moving-time data is available.")


if __name__ == "__main__":
    main()

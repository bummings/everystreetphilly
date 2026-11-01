#!/usr/bin/env python3
"""Merge new Philadelphia runs into the map and clear the newly covered red lines.

The existing map GeoJSON stays the baseline. This imports only Strava Run
activities newer than the latest route already in map/index.html (unless
--after is supplied), clips their GPS tracks to the saved Philadelphia boundary,
and removes red street geometry within the existing 15 m coverage tolerance.

Preview first; pass --apply to write map/index.html.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import fitdecode
    from shapely.geometry import GeometryCollection, LineString, MultiLineString, shape, mapping
    from shapely.ops import transform, unary_union
except ImportError as error:
    raise SystemExit(
        "Map updating needs the packages in requirements-map-update.txt. "
        "Install them with: python3 -m pip install -r requirements-map-update.txt"
    ) from error

EARTH_RADIUS_M = 6_371_008.8
MIN_POINT_SPACING_M = 7
COVERAGE_TOLERANCE_M = 15
PHILLY_TZ = ZoneInfo("America/New_York")


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def read_gpx(path: Path) -> list[list[list[float]]]:
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rb") as stream:
        root = ET.parse(stream).getroot()
    result = []
    for segment in root.iter():
        if local_name(segment) != "trkseg":
            continue
        coords = []
        for point in segment:
            if local_name(point) != "trkpt":
                continue
            try:
                coords.append([float(point.attrib["lon"]), float(point.attrib["lat"])])
            except (KeyError, ValueError):
                continue
        if len(coords) > 1:
            result.append(coords)
    return result


def read_fit(path: Path) -> list[list[list[float]]]:
    lines: list[list[list[float]]] = []
    current = []
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rb") as stream, fitdecode.FitReader(stream) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            if not (frame.has_field("position_lat") and frame.has_field("position_long")):
                if len(current) > 1:
                    lines.append(current)
                current = []
                continue
            lat, lon = frame.get_value("position_lat"), frame.get_value("position_long")
            if lat is None or lon is None:
                if len(current) > 1:
                    lines.append(current)
                current = []
                continue
            current.append([float(lon) / 11930464.711111112, float(lat) / 11930464.711111112])
    if len(current) > 1:
        lines.append(current)
    return lines


def distance_m(a: list[float], b: list[float]) -> float:
    lat = math.radians(b[1] - a[1])
    lon = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    return EARTH_RADIUS_M * math.hypot(lat, lon)


def simplify(line: list[list[float]]) -> list[list[float]]:
    if len(line) < 3:
        return [[round(x, 6), round(y, 6)] for x, y in line]
    kept = [line[0]]
    for point in line[1:-1]:
        if distance_m(kept[-1], point) >= MIN_POINT_SPACING_M:
            kept.append(point)
    kept.append(line[-1])
    return [[round(x, 6), round(y, 6)] for x, y in kept]


def parse_date(value: str) -> datetime:
    value = value.strip()
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    for candidate in (normalized, value.replace(" UTC", "")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=PHILLY_TZ)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt).replace(tzinfo=PHILLY_TZ)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"Unrecognized Strava activity date: {value!r}")


def parse_distance_km(value: str) -> float | None:
    try:
        return float(value.strip().replace(",", ""))
    except (AttributeError, ValueError):
        return None


def parse_pace_per_mile(value: str) -> int | None:
    match = re.search(r"(\d+):(\d{2})", value or "")
    if not match:
        return None
    seconds = int(match.group(1)) * 60 + int(match.group(2))
    if "/km" in (value or "").lower() or "/km" in (value or "").lower().replace(" ", ""):
        seconds = round(seconds * 1.609344)
    return seconds


def tag_value(row: dict[str, str], *names: str) -> str:
    normalized = {key.strip().lower(): (value or "").strip() for key, value in row.items()}
    for name in names:
        if normalized.get(name.lower()):
            return normalized[name.lower()]
    return ""


def read_embedded(html: str, element_id: str) -> tuple[dict, tuple[int, int]]:
    pattern = re.compile(
        rf'(<script[^>]*\bid=["\']{re.escape(element_id)}["\'][^>]*>)(.*?)(</script>)',
        re.S,
    )
    match = pattern.search(html)
    if not match:
        raise ValueError(f"Could not find embedded {element_id} in map HTML")
    return json.loads(match.group(2)), match.span(2)


def to_local_xy(lat0: float):
    x_scale = EARTH_RADIUS_M * math.cos(math.radians(lat0)) * math.pi / 180
    y_scale = EARTH_RADIUS_M * math.pi / 180
    return lambda x, y, z=None: (x * x_scale, y * y_scale) if z is None else (x * x_scale, y * y_scale, z)


def from_local_xy(lat0: float):
    x_scale = 180 / (EARTH_RADIUS_M * math.cos(math.radians(lat0)) * math.pi)
    y_scale = 180 / (EARTH_RADIUS_M * math.pi)
    return lambda x, y, z=None: (x * x_scale, y * y_scale) if z is None else (x * x_scale, y * y_scale, z)


def line_parts(geometry):
    if geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        if len(geometry.coords) > 1:
            yield geometry
    elif isinstance(geometry, (MultiLineString, GeometryCollection)):
        for part in geometry.geoms:
            yield from line_parts(part)


def rounded_geometry(geometry) -> dict:
    result = mapping(geometry)

    def round_coords(coords):
        if not coords:
            return coords
        if isinstance(coords[0], (float, int)):
            return [round(float(value), 6) for value in coords]
        return [round_coords(part) for part in coords]

    result["coordinates"] = round_coords(result["coordinates"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("activities_dir", type=Path, help="Strava export's activities folder")
    parser.add_argument("activities_csv", type=Path, help="Strava export's activities.csv")
    parser.add_argument("--map-html", type=Path, default=Path("map/index.html"))
    parser.add_argument("--after", help="Import activities after this date/time; defaults to the latest route already on the map")
    parser.add_argument("--apply", action="store_true", help="Write the merged routes and revised red lines to the map")
    args = parser.parse_args()

    html = args.map_html.read_text(encoding="utf-8")
    routes, route_span = read_embedded(html, "route-data")
    red, red_span = read_embedded(html, "unrun-data")
    boundary_data, _ = read_embedded(html, "boundary-data")
    current = routes.get("features", [])
    existing_ids = {str(f.get("properties", {}).get("strava_activity_id", "")) for f in current}
    existing_ids.discard("")
    if args.after:
        cutoff = parse_date(args.after)
    else:
        latest = max((f.get("properties", {}).get("start_time_utc", "") for f in current), default="")
        cutoff = parse_date(latest) if latest else datetime.min.replace(tzinfo=timezone.utc)

    boundary = unary_union([shape(f["geometry"]) for f in boundary_data.get("features", [])])
    lat0 = boundary.centroid.y
    project, unproject = to_local_xy(lat0), from_local_xy(lat0)
    boundary_xy = transform(project, boundary)
    file_index = {p.name: p for p in args.activities_dir.rglob("*") if p.is_file()}
    imported, skipped = [], {"not_new_run": 0, "already_present": 0, "missing_file": 0, "no_gps": 0, "outside_philadelphia": 0}
    csv_path = args.activities_csv
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            kind = tag_value(row, "Activity Type").lower()
            if "run" not in kind:
                skipped["not_new_run"] += 1
                continue
            activity_id = tag_value(row, "Activity ID", "Activity Id", "Id")
            filename = Path(tag_value(row, "Filename")).name
            if not activity_id or not filename:
                skipped["missing_file"] += 1
                continue
            if activity_id in existing_ids:
                skipped["already_present"] += 1
                continue
            try:
                started = parse_date(tag_value(row, "Activity Date", "Start Date", "Date"))
            except ValueError as error:
                print(f"Warning: {error}; skipped {filename}", file=sys.stderr)
                skipped["missing_file"] += 1
                continue
            if started <= cutoff:
                skipped["not_new_run"] += 1
                continue
            path = file_index.get(filename)
            if path is None:
                skipped["missing_file"] += 1
                continue
            suffixes = "".join(path.suffixes).lower()
            try:
                raw_lines = read_gpx(path) if suffixes in (".gpx", ".gpx.gz") else read_fit(path) if suffixes in (".fit", ".fit.gz") else []
            except Exception as error:
                print(f"Warning: skipped {filename}: {error}", file=sys.stderr)
                skipped["no_gps"] += 1
                continue
            if not raw_lines:
                skipped["no_gps"] += 1
                continue

            clipped = []
            local_tracks = []
            for coords in raw_lines:
                try:
                    track_xy = transform(project, LineString(coords))
                    inside_xy = track_xy.intersection(boundary_xy)
                except Exception:
                    continue
                for part in line_parts(inside_xy):
                    if part.length < 2:
                        continue
                    local_tracks.append(part)
                    lonlat = transform(unproject, part)
                    simplified = simplify([[x, y] for x, y in lonlat.coords])
                    if len(simplified) > 1:
                        clipped.append(simplified)
            if not clipped:
                skipped["outside_philadelphia"] += 1
                continue

            distance_km = parse_distance_km(tag_value(row, "Distance"))
            props = {
                "activity_id": f"strava-{activity_id}",
                "strava_activity_id": activity_id,
                "start_time": int(started.timestamp() * 1000),
                "start_time_utc": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "activity_name": tag_value(row, "Activity Name", "Name") or "Run",
                "activity_type": "Run",
                "activity_year": str(started.astimezone(PHILLY_TZ).year),
            }
            if distance_km is not None:
                props["distance_km"] = distance_km
                props["strava_distance_m"] = distance_km * 1000
            pace = parse_pace_per_mile(tag_value(row, "Average Pace", "Avg Pace"))
            if pace is not None:
                props["strava_pace_seconds_per_mile"] = pace
            imported.append({
                "feature": {"type": "Feature", "id": props["activity_id"], "properties": props,
                            "geometry": {"type": "MultiLineString", "coordinates": clipped}},
                "track_xy": local_tracks,
            })
            existing_ids.add(activity_id)

    print(f"New Philly runs found: {len(imported)}")
    print(f"Skipped: {', '.join(f'{key}={value}' for key, value in skipped.items())}")
    if not imported:
        print("Map unchanged.")
        return

    coverage = unary_union([part for item in imported for part in item["track_xy"]]).buffer(COVERAGE_TOLERANCE_M)
    revised_red = []
    before_lines = len(red.get("features", []))
    for feature in red.get("features", []):
        original = transform(project, shape(feature["geometry"]))
        remainder = original.difference(coverage)
        for part in line_parts(remainder):
            revised_red.append({"type": "Feature", "properties": feature.get("properties", {}), "geometry": rounded_geometry(transform(unproject, part))})
    before_streets = {str(f.get("properties", {}).get("street_id", "")) for f in red.get("features", [])}
    after_streets = {str(f.get("properties", {}).get("street_id", "")) for f in revised_red}
    print(f"Red street pieces: {before_lines} → {len(revised_red)}; streets with remaining red: {len(after_streets - {''})}")
    print(f"New runs entirely outside Philadelphia: {skipped['outside_philadelphia']}")

    if not args.apply:
        print("Preview only; map/index.html was not changed. Re-run with --apply to save.")
        return

    routes["features"] = current + [item["feature"] for item in imported]
    red["features"] = revised_red
    replacements = [(route_span, json.dumps(routes, separators=(",", ":"), ensure_ascii=False)),
                    (red_span, json.dumps(red, separators=(",", ":"), ensure_ascii=False))]
    for (start, end), content in sorted(replacements, reverse=True):
        html = html[:start] + content + html[end:]
    args.map_html.write_text(html, encoding="utf-8")
    print(f"Updated {args.map_html}: {len(routes['features'])} total runs.")


if __name__ == "__main__":
    main()

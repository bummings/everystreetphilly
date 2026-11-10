#!/usr/bin/env python3
"""Build a date-aware red street layer from the saved routes and OSM streets.

The current red layer remains the authoritative present-day unfinished layer.
This adds geometry for street sections that are complete today, tagged with the
first saved-run date that passed within the map's 16 m matching radius. The
browser can then show those sections as red when the selected end date predates
their first completion.

Requires tools/requirements-map-update.txt. The source street network is the
named, walkable Philadelphia OSM way set saved at data/philadelphia-streets.json.gz.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from shapely.geometry import GeometryCollection, LineString, MultiLineString, shape, mapping
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree
import shapely

EARTH_RADIUS_M = 6_371_008.8
COVERAGE_TOLERANCE_M = 16


def read_embedded(html: str, element_id: str) -> tuple[dict, tuple[int, int]]:
    pattern = re.compile(
        rf'(<script[^>]*\bid=["\']{re.escape(element_id)}["\'][^>]*>)(.*?)(</script>)',
        re.S,
    )
    match = pattern.search(html)
    if not match:
        raise ValueError(f"Could not find embedded {element_id} in map HTML")
    return json.loads(match.group(2)), match.span(2)


def projectors(latitude: float):
    x_scale = EARTH_RADIUS_M * math.cos(math.radians(latitude)) * math.pi / 180
    y_scale = EARTH_RADIUS_M * math.pi / 180

    def project(x, y, z=None):
        return (x * x_scale, y * y_scale) if z is None else (x * x_scale, y * y_scale, z)

    def unproject(x, y, z=None):
        return (x / x_scale, y / y_scale) if z is None else (x / x_scale, y / y_scale, z)

    return project, unproject


def line_parts(geometry):
    if geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        if len(geometry.coords) > 1 and geometry.length > 1:
            yield geometry
    elif isinstance(geometry, (MultiLineString, GeometryCollection)):
        for part in geometry.geoms:
            yield from line_parts(part)


def all_line_parts(geometry):
    if geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        if len(geometry.coords) > 1:
            yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from all_line_parts(part)


def rounded_geometry(geometry) -> dict:
    result = mapping(geometry)

    def rounded(coords):
        if not coords:
            return coords
        if isinstance(coords[0], (float, int)):
            return [round(float(value), 6) for value in coords]
        return [rounded(part) for part in coords]

    result["coordinates"] = rounded(result["coordinates"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-html", type=Path, default=Path("map/index.html"))
    parser.add_argument(
        "--streets",
        type=Path,
        default=Path("data/philadelphia-streets.json.gz"),
        help="Compressed Overpass JSON containing the named Philadelphia street ways",
    )
    parser.add_argument("--apply", action="store_true", help="Write history-data into the map HTML")
    args = parser.parse_args()

    html = args.map_html.read_text(encoding="utf-8")
    routes, _ = read_embedded(html, "route-data")
    boundary_data, _ = read_embedded(html, "boundary-data")
    current_unrun, _ = read_embedded(html, "unrun-data")
    boundary = unary_union([shape(f["geometry"]) for f in boundary_data["features"]])
    project, unproject = projectors(boundary.centroid.y)
    boundary_xy = transform(project, boundary)

    routes_by_date = defaultdict(list)
    for feature in routes.get("features", []):
        props = feature.get("properties", {})
        date = str(props.get("start_time_utc", ""))[:10]
        geometry = feature.get("geometry")
        if not date or not geometry:
            continue
        # Simplify dense GPS polylines before buffering. The tolerance is
        # intentionally modest relative to the coverage radius and keeps the
        # one-time full-city rebuild from spending minutes buffering every
        # recorded GPS sample.
        line = transform(project, shape(geometry)).simplify(8.0, preserve_topology=False).intersection(boundary_xy)
        if not line.is_empty:
            routes_by_date[date].append(line)

    route_dates = sorted(routes_by_date)
    if not route_dates:
        raise SystemExit("No dated route geometry found in route-data")
    buffers = []
    all_route_parts = []
    for date in route_dates:
        route_parts = [part for route in routes_by_date[date] for part in all_line_parts(route)]
        all_route_parts.extend(route_parts)
        buffers.append(unary_union(shapely.buffer(route_parts, COVERAGE_TOLERANCE_M, quad_segs=4)))
    tree = STRtree(buffers)

    # Match historical geometries to the same street IDs used by the current
    # red layer wherever names overlap, so the unfinished street count remains
    # stable when a street has both old and current red fragments.
    current_ids_by_name = defaultdict(set)
    for feature in current_unrun.get("features", []):
        props = feature.get("properties", {})
        name = str(props.get("street_name", "")).strip().casefold()
        if name:
            current_ids_by_name[name].add(str(props.get("street_id", "")))

    with gzip.open(args.streets, "rt", encoding="utf-8") as stream:
        street_data = json.load(stream)

    # Treat one-way, multi-lane carriageways sharing a street name as one
    # corridor. Explicitly non-pedestrian trunk/motorway pieces are excluded
    # from unfinished streets (notably Roosevelt Boulevard's express lanes).
    wide_lines_by_name = defaultdict(list)
    excluded_by_name = defaultdict(list)
    for element in street_data.get("elements", []):
        tags = element.get("tags", {})
        name = str(tags.get("name", "")).strip()
        coords = [(float(point["lon"]), float(point["lat"])) for point in element.get("geometry", [])]
        if not name or len(coords) < 2:
            continue
        highway = str(tags.get("highway", ""))
        sidewalk = str(tags.get("sidewalk", "")).casefold()
        foot = str(tags.get("foot", "")).casefold()
        if (highway in {"motorway", "motorway_link", "trunk_link"}
                or foot in {"no", "private", "use_sidepath"}
                or str(tags.get("motorroad", "")).casefold() == "yes"
                or (highway == "trunk" and sidewalk == "no")):
            excluded_by_name[name.casefold()].append(transform(project, LineString(coords)).buffer(12))
        if highway in {"primary", "secondary", "tertiary", "trunk"} and tags.get("oneway") == "yes":
            try:
                lanes = int(str(tags.get("lanes", "1")).split(";")[0])
            except ValueError:
                lanes = 1
            if lanes >= 2:
                wide_lines_by_name[name.casefold()].append(
                    transform(project, LineString(coords)).intersection(boundary_xy)
                )
    excluded_by_name = {name: unary_union(geometries) for name, geometries in excluded_by_name.items()}
    wide_lines_by_name = {
        name: unary_union(lines).difference(excluded_by_name.get(name, GeometryCollection()))
        for name, lines in wide_lines_by_name.items()
    }
    wide_corridors_by_name = {}
    wide_dates_by_name = {}
    for name, road_lines in wide_lines_by_name.items():
        corridors, dates = [], []
        for index, route_buffer in enumerate(buffers):
            run_on_named_road = road_lines.intersection(route_buffer)
            if not run_on_named_road.is_empty and run_on_named_road.length > 0:
                corridors.append(run_on_named_road.buffer(60))
                dates.append(index)
        if corridors:
            wide_corridors_by_name[name] = corridors
            wide_dates_by_name[name] = dates
    wide_trees_by_name = {
        name: STRtree(corridors) for name, corridors in wide_corridors_by_name.items()
    }

    all_route_buffer = unary_union(shapely.buffer(all_route_parts, COVERAGE_TOLERANCE_M, quad_segs=4))
    current_wide_coverage = {
        name: road_lines.intersection(all_route_buffer).buffer(60)
        for name, road_lines in wide_lines_by_name.items()
        if road_lines.intersects(all_route_buffer)
    }

    dated_parts = defaultdict(list)
    street_ids_by_date = defaultdict(set)
    way_count = 0
    matched_way_count = 0
    for element_index, element in enumerate(street_data.get("elements", []), 1):
        tags = element.get("tags", {})
        name = str(tags.get("name", "")).strip()
        geometry = element.get("geometry", [])
        coords = [(float(point["lon"]), float(point["lat"])) for point in geometry]
        if not name or len(coords) < 2:
            continue
        line = transform(project, LineString(coords)).intersection(boundary_xy)
        if name.casefold() in excluded_by_name:
            line = line.difference(excluded_by_name[name.casefold()])
        if line.is_empty or line.length <= 1:
            continue
        way_count += 1
        if way_count % 5000 == 0:
            print(f"Processed {way_count:,} street ways; {len(dated_parts):,} date/name groups so far.", flush=True)
        normalized = name.casefold()
        is_wide_road = normalized in wide_lines_by_name
        if is_wide_road:
            corridors = wide_corridors_by_name.get(normalized, [])
            if not corridors:
                continue
            local_tree = wide_trees_by_name[normalized]
            candidates = local_tree.query(line, predicate="intersects")
        else:
            corridors = buffers
            candidates = tree.query(line, predicate="intersects")
        if len(candidates) == 0:
            continue
        remaining = line
        for candidate in sorted(int(index) for index in candidates):
            if is_wide_road:
                route_date_index = wide_dates_by_name[normalized][candidate]
                corridor = corridors[candidate]
            else:
                route_date_index = candidate
                corridor = buffers[candidate]
            covered = remaining.intersection(corridor)
            parts = list(line_parts(covered))
            if parts:
                matched_way_count += 1
                existing_ids = sorted(current_ids_by_name.get(normalized, ()))
                street_id = existing_ids[0] if existing_ids else f"osm-name:{normalized}"
                dated_parts[route_dates[route_date_index]].extend(parts)
                street_ids_by_date[route_dates[route_date_index]].add(street_id)
                remaining = remaining.difference(corridor)
                if remaining.is_empty:
                    break


    output_features = []
    for date, geometries in sorted(dated_parts.items()):
        merged = unary_union(geometries)
        wgs84 = transform(unproject, merged)
        line_geometries = list(all_line_parts(wgs84))
        if not line_geometries:
            continue
        geometry = line_geometries[0] if len(line_geometries) == 1 else MultiLineString(line_geometries)
        output_features.append(
            {
                "type": "Feature",
                "properties": {
                    "completed_on": date,
                    "street_ids": sorted(street_ids_by_date[date]),
                },
                "geometry": rounded_geometry(geometry),
            }
        )

    collection = {"type": "FeatureCollection", "features": output_features}

    # Recalculate today's red layer with the same corridor rules, keeping the
    # embedded GeoJSON authoritative while fixing divided-road false reds.
    revised_unrun = []
    for feature in current_unrun.get("features", []):
        props = feature.get("properties", {})
        name = str(props.get("street_name", "")).casefold()
        line = transform(project, shape(feature["geometry"]))
        if name in excluded_by_name:
            line = line.difference(excluded_by_name[name])
        route_buffer = current_wide_coverage.get(name, all_route_buffer)
        line = line.difference(route_buffer)
        for part in line_parts(line):
            revised_unrun.append({"type": "Feature", "properties": props,
                                  "geometry": rounded_geometry(transform(unproject, part))})
    current_unrun["features"] = revised_unrun
    print(
        f"Built {len(output_features):,} dated street pieces from {way_count:,} OSM ways; "
        f"{matched_way_count:,} way/date sections matched run coverage."
    )
    if not args.apply:
        print("Preview only; rerun with --apply to update map/index.html.")
        return

    encoded = json.dumps(collection, separators=(",", ":"), ensure_ascii=False)
    history_pattern = re.compile(
        r'(<script[^>]*\bid=["\']history-data["\'][^>]*>)(.*?)(</script>)', re.S
    )
    old_history = history_pattern.search(html)
    if old_history:
        html = html[: old_history.start(2)] + encoded + html[old_history.end(2) :]
    else:
        unrun_match = re.search(
            r'<script[^>]*\bid=["\']unrun-data["\'][^>]*>.*?</script>', html, re.S
        )
        if not unrun_match:
            raise ValueError("Could not find unrun-data while inserting historical data")
        insert_at = unrun_match.end()
        html = html[:insert_at] + f'<script type="application/json" id="history-data">{encoded}</script>' + html[insert_at:]
    unrun_pattern = re.compile(
        r'(<script[^>]*\bid=["\']unrun-data["\'][^>]*>)(.*?)(</script>)', re.S
    )
    unrun_match = unrun_pattern.search(html)
    html = html[: unrun_match.start(2)] + json.dumps(current_unrun, separators=(",", ":"), ensure_ascii=False) + html[unrun_match.end(2) :]
    args.map_html.write_text(html, encoding="utf-8")
    print(f"Wrote {len(encoded):,} bytes of history data into {args.map_html}.")


if __name__ == "__main__":
    main()

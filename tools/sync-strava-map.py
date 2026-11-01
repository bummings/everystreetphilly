#!/usr/bin/env python3
"""Incrementally sync new Strava runs into the existing Philadelphia map.

This uses Strava's API for activities newer than the latest map activity and
requests GPS streams only for those new runs. It then hands the temporary GPX
files to update-map.py, which preserves the GeoJSON baseline and clips to Philly.

Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET first. Preview is the default;
use --apply only after reviewing the preview output.
"""

from __future__ import annotations

import argparse
import csv
import http.server
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

API = "https://www.strava.com/api/v3"
OAUTH = "https://www.strava.com/oauth"
REDIRECT_URI = "http://localhost:8765/callback"
DEFAULT_MAP = Path("map/index.html")
TOKEN_FILE = Path(".strava-token.json")


def load_local_env() -> None:
    """Load missing STRAVA_* values from the ignored repo-root .env file."""
    env_file = Path(".env")
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key.startswith("STRAVA_") and key not in os.environ:
            os.environ[key] = value


def request_json(url: str, *, token: str | None = None, form: dict | None = None) -> dict | list:
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Strava returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach Strava: {error.reason}") from error


def save_token(token: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(token, indent=2) + "\n", encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def authorize(client_id: str, client_secret: str) -> dict:
    state = secrets.token_urlsafe(24)
    result: dict[str, str] = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if self.path.split("?", 1)[0] != "/callback" or query.get("state", [""])[0] != state:
                self.send_error(400, "Authorization state did not match.")
                return
            if query.get("error"):
                result["error"] = query["error"][0]
            else:
                result["code"] = query.get("code", [""])[0]
                result["scope"] = query.get("scope", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Strava connected.</h2><p>You can close this tab and return to the terminal.</p>")

        def log_message(self, *_args):
            pass

    try:
        server = http.server.HTTPServer(("localhost", 8765), Callback)
    except OSError as error:
        raise RuntimeError("Could not open the local Strava sign-in callback on port 8765. Close another sync window and retry.") from error
    server.timeout = 1
    auth_url = OAUTH + "/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "activity:read_all",
        "state": state,
    })
    print("Opening Strava so you can authorize read access to your activities…")
    print("If the browser does not open, visit this sign-in URL:\n" + auth_url)
    webbrowser.open(auth_url)
    deadline = time.monotonic() + 300
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if result.get("error"):
        raise RuntimeError("Strava authorization was not granted.")
    if not result.get("code"):
        raise RuntimeError("Timed out waiting for Strava sign-in.")
    token = request_json(OAUTH + "/token", form={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": result["code"],
        "grant_type": "authorization_code",
    })
    token["scope"] = result.get("scope", "")
    save_token(token)
    return token


def get_token(client_id: str, client_secret: str) -> dict:
    token = None
    if TOKEN_FILE.exists():
        try:
            token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"Ignoring unreadable {TOKEN_FILE}; sign in again.", file=sys.stderr)
    if not token:
        return authorize(client_id, client_secret)
    if int(token.get("expires_at", 0)) <= int(time.time()) + 300:
        refreshed = request_json(OAUTH + "/token", form={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        })
        refreshed["scope"] = token.get("scope", "")
        token = refreshed
        save_token(token)  # Strava may rotate the refresh token on each refresh.
    if "activity:read_all" not in token.get("scope", ""):
        raise RuntimeError("This saved Strava authorization lacks activity:read_all. Remove .strava-token.json and sign in again to grant it.")
    return token


def api_get(path: str, token: dict, client_id: str, client_secret: str, params: dict | None = None):
    query = urllib.parse.urlencode(params or {})
    try:
        return request_json(API + path + ("?" + query if query else ""), token=token["access_token"])
    except RuntimeError as error:
        if "HTTP 401" not in str(error):
            raise
        refreshed = request_json(OAUTH + "/token", form={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        })
        refreshed["scope"] = token.get("scope", "")
        token.clear()
        token.update(refreshed)
        save_token(token)
        return request_json(API + path + ("?" + query if query else ""), token=token["access_token"])


def latest_map_time(map_html: Path) -> tuple[datetime, set[str]]:
    html = map_html.read_text(encoding="utf-8")
    import re
    match = re.search(r'<script[^>]*id=["\']route-data["\'][^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError(f"No embedded route data found in {map_html}")
    features = json.loads(match.group(1)).get("features", [])
    timestamps = [datetime.fromisoformat(f["properties"]["start_time_utc"].replace("Z", "+00:00"))
                  for f in features if f.get("properties", {}).get("start_time_utc")]
    ids = {str(f["properties"]["strava_activity_id"]) for f in features
           if f.get("properties", {}).get("strava_activity_id")}
    latest = max(timestamps, default=datetime(2000, 1, 1, tzinfo=timezone.utc))
    return latest, ids


def activity_stream(token, activity_id, client_id, client_secret):
    result = api_get(f"/activities/{activity_id}/streams", token, client_id, client_secret,
                     {"keys": "latlng,time,distance", "key_by_type": "true"})
    if isinstance(result, list):
        return {item.get("type"): item.get("data", []) for item in result if isinstance(item, dict)}
    return {key: value.get("data", []) if isinstance(value, dict) else value
            for key, value in result.items()} if isinstance(result, dict) else {}


def write_gpx(path: Path, coordinates: list[list[float]]) -> None:
    root = ET.Element("gpx", {"version": "1.1", "creator": "Every Street Philly"})
    track = ET.SubElement(root, "trk")
    segment = ET.SubElement(track, "trkseg")
    for lon, lat in coordinates:
        ET.SubElement(segment, "trkpt", {"lat": str(lat), "lon": str(lon)})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-html", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--apply", action="store_true", help="Write changes after previewing the imports")
    parser.add_argument("--after", type=int, help="Override the activity cutoff with an epoch timestamp")
    args = parser.parse_args()
    load_local_env()
    client_id, client_secret = os.environ.get("STRAVA_CLIENT_ID"), os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in your terminal first. Keep the secret out of the repository.")
    token = get_token(client_id, client_secret)
    latest, existing_ids = latest_map_time(args.map_html)
    # Overlap by two days in case an activity was uploaded with a delayed timestamp;
    # stable Strava activity IDs prevent duplicate imports.
    after = args.after if args.after is not None else max(0, int(latest.timestamp()) - 2 * 24 * 60 * 60)
    print(f"Checking Strava activities after {datetime.fromtimestamp(after, timezone.utc).isoformat()}…")
    new_runs = []
    page = 1
    while True:
        activities = api_get("/athlete/activities", token, client_id, client_secret,
                             {"after": after, "page": page, "per_page": 200})
        if not isinstance(activities, list):
            raise RuntimeError("Strava returned an unexpected activity list.")
        for activity in activities:
            activity_id = str(activity.get("id", ""))
            sport = str(activity.get("sport_type") or activity.get("type") or "")
            if activity_id and activity_id not in existing_ids and "run" in sport.lower():
                new_runs.append(activity)
        if len(activities) < 200:
            break
        page += 1
    print(f"New running activities to inspect: {len(new_runs)}")
    if not new_runs:
        print("Map unchanged.")
        return

    with tempfile.TemporaryDirectory(prefix="every-street-strava-") as temp:
        tmp = Path(temp)
        activities_dir = tmp / "activities"
        activities_dir.mkdir()
        csv_path = tmp / "activities.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            fields = ["Activity ID", "Activity Date", "Activity Name", "Activity Type", "Distance", "Average Pace", "Filename"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for activity in new_runs:
                activity_id = str(activity["id"])
                try:
                    streams = activity_stream(token, activity_id, client_id, client_secret)
                except RuntimeError as error:
                    print(f"Skipping {activity.get('name', activity_id)} ({activity_id}): {error}", file=sys.stderr)
                    continue
                coordinates = streams.get("latlng") or []
                if len(coordinates) < 2:
                    print(f"Skipping {activity.get('name', activity_id)} ({activity_id}): no GPS stream.")
                    continue
                filename = f"activity-{activity_id}.gpx"
                write_gpx(activities_dir / filename, [[float(point[1]), float(point[0])] for point in coordinates])
                distance_km = float(activity.get("distance", 0)) / 1000
                speed = float(activity.get("average_speed", 0) or 0)
                pace = ""
                if speed > 0:
                    seconds = round(1609.344 / speed)
                    pace = f"{seconds // 60}:{seconds % 60:02d} /mi"
                writer.writerow({
                    "Activity ID": activity_id,
                    "Activity Date": activity.get("start_date", ""),
                    "Activity Name": activity.get("name", "Run"),
                    "Activity Type": activity.get("sport_type") or "Run",
                    "Distance": f"{distance_km:.4f}",
                    "Average Pace": pace,
                    "Filename": filename,
                })

        command = [sys.executable, str(Path(__file__).with_name("update-map.py")),
                   str(activities_dir), str(csv_path), "--map-html", str(args.map_html)]
        if args.apply:
            command.append("--apply")
        subprocess.run(command, check=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        raise SystemExit(str(error)) from error

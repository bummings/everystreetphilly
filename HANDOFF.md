# Every Street Philly — future-session handoff

Read this first when reopening the project in VS Code/Codex.

## Current state

This is a static GitHub Pages site whose principal feature is an interactive
Philadelphia running map. The current `index.html` is the preferred
field-journal layout: desktop text is on the left and the map is intentionally
tall on the right. The stylesheet query string
`variation.css?v=20260728-layout` is intentional—it busts a Chrome cache issue
that made an earlier layout update look as though it had not applied.

The visible map is **scorecard mode**:

- Black = completed street coverage **and** original recorded GPS routes.
- Coral = a named, runnable street-network way not yet verified complete.
- The city exterior is masked dark and the Philadelphia boundary is outlined.

The raw GPS overlay is deliberate. An earlier street-only scorecard made
Wissahickon / Forbidden Drive appear incomplete because public trails are not
always present in the runnable-road network. CityStrides' purple LifeMap shows
GPS traces, so the `recorded-routes-on-scorecard` layer was added in `script.js`
to retain those traces in black. Do not remove it without a trail-aware
replacement.

## Files that matter

```text
index.html                    deployed entry page; scorecard enabled here
styles.css                    shared site styling
variation.css                 field-journal / tall-map desktop layout
script.js                     MapLibre map, layers, boundary mask, guestbook
data/runs.js                  local GPS route data used in browser
data/scorecard.js             local completed + remaining street data
data/philly-boundary.js       local city boundary
data/scorecard-summary.json   scorecard totals/settings snapshot
tools/build-routes.py         Strava activity export → browser GPS route layer
phila.png / og.png            decorative outline / social preview
.nojekyll                     keep for GitHub Pages
```

Keep this folder structure unchanged when publishing. In particular, `data/`
must be beside `index.html`.

## Design / behavior constraints

- Palette: paper `#fff8ef`, near-black `#241a17`, coral `#f05b43`.
- Map uses MapLibre plus OpenStreetMap raster tiles. Map tiles, the MapLibre CDN,
  and Google Fonts need internet; the GPS, boundary, and scorecard layers are
  bundled locally.
- Guestbook and visit count are browser-local (`localStorage`), not shared or
  backed by a server.
- The visible number `794 GPS-backed runs` is currently HTML copy, not computed
  automatically on page load.

## Accuracy caveat

The street scorecard is an independent conservative approximation, not an
official CityStrides export. It uses an OpenStreetMap-derived CityStrides-style
named, runnable street network and local GPS-to-road matching. CityStrides has
its own graph, access rules, and matching behavior. Do not promise pixel-perfect
parity. When coral looks wrong, inspect the individual named street, its OSM
geometry, and nearby GPS trace before changing thresholds just to hide it.

## Updating runs

There is no automated Strava sync yet. To refresh the **GPS route layer** from
a Strava export:

1. Download a refreshed full Strava archive and unzip it.
2. Install the one dependency: `python3 -m pip install fitdecode`.
3. Run:

   ```sh
   python3 tools/build-routes.py /path/to/export/activities data/runs.js \
     --activities-csv /path/to/export/activities.csv
   ```

   The CSV is important: it reliably includes only activities Strava calls
   `Run`, even when older GPX files have missing or inconsistent embedded
   activity types. The route layer keeps GPS samples about 7 m apart so the
   map remains responsive.
4. Validate the result, then publish `data/runs.js`.

This only refreshes the raw black GPS-trace overlay. The completed/remaining
street scorecard remains an independent snapshot until its separate OSM
matching pipeline is restored and rerun. A true automatic Strava pull requires
a hosted backend and Strava OAuth; GitHub Pages alone cannot safely do that.

## Current matching settings (source pipeline)

The most recent scorecard build used:

- GPS snap distance: **24 meters**
- Completed-way threshold: **65%**

It classifies full OSM ways to avoid tiny dashed black/coral fragments. It is a
heuristic; tune only after checking a representative mismatch.

## Best next work

1. Build a friendly weekly import/update script.
2. Add an intentional trail network if road-only scorecard parity needs to
   improve; preserve the raw-GPS overlay in the meantime.
3. Replace the local-only guestbook with a backend/form service if public notes
   should persist for everyone.
4. Use downloaded tiles/PMTiles only after checking licensing if a fully offline
   basemap becomes important.

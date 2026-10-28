# Every Street Philly — Netlify site

This is a static site with no build step. The homepage contains the project story and guestbook; the detailed interactive run map is served from `map/index.html` at `/map/` (Netlify’s Pretty URLs also resolves `/map`). The homepage opens the map in a new tab.

## Deploy with Netlify

Connect the GitHub repository `bummings/everystreetphilly` to Netlify and use:

- Build command: leave blank
- Publish directory: `.` (the repository root)
- Production branch: `main`

A push to the production branch deploys the site. Review the deploy preview before promoting changes if the site uses Netlify deploy previews.

## Custom domain

Add `everystreetphilly.com` to the Netlify site’s domain settings first. DNS is configured at Namecheap only if the domain is not already pointed to that Netlify site. Use the records Netlify shows under the domain’s pending DNS verification; don’t change the records until confirming the intended site and existing email/DNS services. DNS changes apply to the domain as a whole; `/map` is a page path served by the site and needs no separate DNS record.

## Map behavior

The run map is a standalone HTML page with its route, Philadelphia boundary, and incomplete-street data embedded. MapLibre and the OpenFreeMap basemap load from the internet, so map tiles and the map library require a connection. Profile icons link to Strava and CityStrides. Runs without a Strava activity ID remain clickable through their CityStrides activity page; the local link-entry tool is omitted from the public page.

The home page retains the project information and browser-local guestbook. Guestbook notes and visit counts are stored separately in each visitor’s browser, not shared between visitors.

## Updating run data

There is no automated Strava sync yet. To refresh the homepage’s GPS route archive, use `tools/build-routes.py` as documented in `HANDOFF.md`. The standalone `/map/` page has its own embedded export and must also be refreshed from the latest Citystrides/Strava data when it changes.

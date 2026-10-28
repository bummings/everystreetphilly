# Every Street Philly — Netlify site

This is a static Netlify site with a small serverless guestbook backend. The homepage contains the project story and guestbook; the detailed interactive run map is served from `map/index.html` at `/map/` (Netlify’s Pretty URLs also resolves `/map`). The homepage opens the map in a new tab.

## Deploy with Netlify

Connect the GitHub repository `bummings/everystreetphilly` to Netlify and use:

- Build command: leave blank
- Publish directory: `.` (the repository root)
- Production branch: `main`

The functions in `netlify/functions/` are deployed automatically. Before
accepting guestbook submissions, set `GUESTBOOK_ADMIN_TOKEN` in Netlify
(Project configuration → Environment variables) to a password-manager-generated
random secret of at least 32 characters. Keep it private and do not commit it.
The private moderation page is at `/guestbook-admin/`; enter the token there to
review pending notes, approve them for publication, or delete them. New notes
are private until approved. The public API returns approved notes only.

Spam controls include an invisible honeypot field, a minimum time before
submitting, and a short per-IP cooldown. These measures reduce simple automated
spam but do not replace moderation. Netlify Blobs stores entries across visitors
and deploys. The local Netlify Dev environment has a separate store, so local
entries do not appear on the live site. Notes saved by the old browser-local
guestbook are not migrated.

For local function development, install dependencies, then run this from the
repository root (the pinned CLI version works with Node 20):

```sh
npm install
npx netlify-cli@21.0.0 dev --offline
```

Netlify Dev uses a separate local Blobs store; local entries do not appear on
the live site. Add `GUESTBOOK_ADMIN_TOKEN` to a local `.env` file to use the
moderator locally. The `.env` file is ignored by Git.

A push to the production branch deploys the site. Review the deploy preview before promoting changes if the site uses Netlify deploy previews.

## Custom domain

Add `everystreetphilly.com` to the Netlify site’s domain settings first. DNS is configured at Namecheap only if the domain is not already pointed to that Netlify site. Use the records Netlify shows under the domain’s pending DNS verification; don’t change the records until confirming the intended site and existing email/DNS services. DNS changes apply to the domain as a whole; `/map` is a page path served by the site and needs no separate DNS record.

## Map behavior

The run map is a standalone HTML page with its route, Philadelphia boundary, and incomplete-street data embedded. MapLibre and the OpenFreeMap basemap load from the internet, so map tiles and the map library require a connection. Profile icons link to Strava and CityStrides. Runs without a Strava activity ID remain clickable through their CityStrides activity page; the local link-entry tool is omitted from the public page.

The home page retains the project information and a Netlify-backed guestbook.
The visit counter remains browser-local.

## Contact email

The public contact link uses `hello@everystreetphilly.com`, not the owner's
private inbox address. Configure a Namecheap email forwarder for the `hello`
alias and set its destination to the private inbox. In Namecheap, open **Domain
List → Manage → Domain → Redirect Email → Add Forwarder**. If **Email
Forwarding** is not already selected under **Advanced DNS → Mail Settings**,
select it and save the change first. Test delivery from a different mailbox;
messages sent from the destination inbox to itself may not be forwarded as a
valid test. Namecheap's free forwarding is receive-only: it does not let you
send replies from the alias. Do not put the private inbox address in the site
source or documentation. If another mail provider currently handles the
domain's MX records, check that setup before changing Mail Settings.

## Updating run data

There is no automated Strava sync yet. To refresh the homepage’s GPS route archive, use `tools/build-routes.py` as documented in `HANDOFF.md`. The standalone `/map/` page has its own embedded export and must also be refreshed from the latest Citystrides/Strava data when it changes.

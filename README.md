# Kith

A private family history website for your relatives: people, families, photos, documents and stories, with
living people kept private. Free and open source (AGPL), written in Python/Flask on MariaDB.

Kith is a "here's the code" project. It runs a real family's site every day. Pull requests are welcome, but there
are no support promises.

Kith is built by a veteran, retired IT professional working with AI (Anthropic's Claude). Much of the code is
AI-written; a person decides what gets built, and reviews and tests every change, and the test suite covers every
line.

![A person's page](docs/screenshots/person.png)

| | |
|---|---|
| ![Descendants chart](docs/screenshots/chart.png) | ![Home page](docs/screenshots/home.png) |
| ![Dark theme](docs/screenshots/person-dark.png) | ![Welcome page before logging in](docs/screenshots/welcome.png) |
| ![Hourglass chart: parents above, children below](docs/screenshots/hourglass.png) | ![Census by year: households by place](docs/screenshots/census.png) |

<img src="docs/screenshots/person-phone.png" alt="A person's page on a phone" width="260">

The screenshots show the made-up sample family that comes with Kith.

## What it does

- Person and family pages with life events, sources and citations (with scans of the record), notes, a life
  story, a map of where a life happened (a line through the places, in the order of the events), a woman's
  married names, and links that search FamilySearch, Find a
  Grave, Ancestry and newspapers (Chronicling America, Newspapers.com) for the person.
- Charts: ancestors, descendants, hourglass and a fan chart with photos that prints as a poster. The Tree menu
  opens an hourglass of you, or of a starting person an admin picks. The relationship page shows the path between
  two people as a row of faces.
- A branch book (the life stories of a person's descendants, to print), a family map (where a surname's people
  or a person's descendants were born and died), and "Where the lines begin" (the earliest person of each
  surname).
- Census by year: each census year's households, by place. Census matches suggests which person in the tree
  each name typed in a census transcription is, and can fill in a birth year from the age; an editor says yes.
- Places drill down from state to county to town, with everyone in an area on one list.
- Photos and documents, with faces tagged to people, comments, and PDF thumbnails.
- Search by name (including married names and spelling variants), by family, and by place.
- Privacy: living people are hidden from those who may not see them. Accounts have roles, and relatives can ask
  for an account and say "this is me".
- Editing on the site: add relatives, merge duplicates, tidy place names (spellings, misspellings, record titles
  where a place should be), link surname spellings, and data checks for impossible dates and death dates that
  differ from the obituary.
- GEDCOM import (with undo) and export.
- What's new, watching a person, and an optional weekly email.
- Admin page with what's waiting, recent visits, and what's changed on the site lately (from its git history).
- Five themes, which each person can choose for themselves, including large print.

## Install

You need a server that your family can reach. Kith listens on port 8000. Put it behind a reverse proxy with
HTTPS before it's on the internet (see "Settings").

### Proxmox VE

On the Proxmox host's shell:

    bash -c "$(curl -fsSL https://raw.githubusercontent.com/dreed47/kith/main/ct/kith.sh)"

This makes a Debian 13 container with Kith, MariaDB and the first admin account. It uses the
[community-scripts](https://community-scripts.github.io/ProxmoxVE/) engine. The admin login is in the container:
`cat ~/kith.creds`. To update, run the same command inside the container. It saves a copy of the database in
`/var/backups/kith` first.

### Docker

    git clone https://github.com/dreed47/kith.git
    cd kith/docker
    cp example.env .env          # set DB_PASSWORD
    docker compose up -d
    docker compose exec app flask --app app setup db       # the first admin
    docker compose exec app flask --app app setup sample   # optional: a made-up family to try it with

Then open http://localhost:8000. To update: `git pull`, then `docker compose up -d --build`. Database upgrades
run as the site starts.

### Debian by hand

`install/kith-install.sh` is the reference: MariaDB, a Python venv in `/opt/kith`, settings in `/etc/kith.env`,
photos in `/srv/kith/media`, and the `kith` systemd service. Do the same steps by hand on any Debian-like server.

## Your tree

Import a GEDCOM file from your genealogy program under Admin > Import GEDCOM. Photos and documents are uploaded
on the site. A GEDCOM file names them, but doesn't carry them.

## Settings

Settings are environment variables (`/etc/kith.env`, or `docker/.env`):

| Variable | What it does |
|---|---|
| `KITH_SECURE_COOKIES=1` | Send the login cookie over HTTPS only. Set it once you are behind HTTPS. |
| `KITH_TRUSTED_PROXIES=1` | Behind a reverse proxy, so login throttling sees real visitors' addresses. |
| `KITH_SITE_URL` | The site's address (`https://tree.example.org`), for links in email. |
| `KITH_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `KITH_MAIL_FROM` | Outgoing email: account confirmations, password resets, invitations. Off until `KITH_SMTP_USER` is set. |
| `KITH_THEME` | The site's theme (default `default`). |
| `KITH_MAX_UPLOAD_MB` | Largest upload (default 50). |

The site title, tagline, welcome page text, banner picture and where the Tree page starts for people not linked
to their own record are set under Admin > Site settings.

Two scheduled jobs. The Proxmox installer sets them up as systemd timers (`deploy/jobs.sh`); elsewhere run them
from cron:

- `flask --app app geocode run`: looks up map coordinates for places that have none (OpenStreetMap's Nominatim,
  one a second). Only place names leave the server.
- `flask --app app digest send`: the weekly email, once an admin turns it on.

Back up the database (`mariadb-dump`) and the photos folder. On Proxmox, the container's own backups cover both.

## Themes

A theme is `themes/<name>/`:

- `theme.css` (required): the CSS tokens (`--bg`, `--text`, `--font-body`, ...), light and dark.
- `theme.toml`: the default site title and welcome text. `name`, `description` and `swatches` label it on the
  account page.
- `templates/` (optional): overrides any template in `app/templates/`. Extend the default with
  `{% extends "app/base.html" %}`.
- `static/` (optional): served at `/theme/static/...`.

Each person can pick a theme for themselves under Your account. Only its `theme.css` applies then. Fonts are
bundled in `app/static/fonts/` (SIL Open Font License). `app/static/app.css` and `app/templates/` use only
`var(--token)`, never literal colors or fonts. `tests/test_themes.py` checks this.

## Development

    docker compose up -d db                  # MariaDB for development and tests, on port 3307
    uv run pytest                            # every line and branch of app/ must be covered
    uv run flask --app app setup db          # once: tables and an admin in the dev database
    uv run flask --app app setup sample      # optional: the made-up family
    uv run flask --app app run               # http://127.0.0.1:5000

The tests build `kith_test` from an empty database (`app/schema.sql` plus `app/migrations`) and the made-up
sample family (`app/sample.ged`, made by `scripts/make_sample_gedcom.py`). A database change is a new numbered
file in `app/migrations/`. It runs once on every site at its next start, so it must be safe to run twice.

See `CONTRIBUTING.md`.

## License

AGPL-3.0. If you run a changed version for others, offer them its source. See `LICENSE`.

# Changelog

Kith follows semantic versioning. Releases are on GitHub.

## Unreleased

- Logins last 90 days from the last visit. Before, phones logged people out whenever they closed the browser.
  Existing logins need one fresh login to pick this up.
- Docker: the site no longer fails to start (the image was missing `pyproject.toml`; broken since 0.2.0).
- README: which install to choose and what each needs; step-by-step Debian install and update; Docker installs the
  newest release, not `main`, and gets the scheduled jobs from the host's crontab.

## 0.3.0 (2026-11-04)

- Version check: once a day the site asks kithgenealogy.org for the newest version, and Admin says when there's
  one. It sends a random site ID and the version number, nothing else; the Kith project counts running sites from
  it. On by default, off under Admin > Site settings, or for good with `KITH_UPDATE_CHECK=0`. Proxmox installs get
  a `kith-updates` timer (re-run `deploy/jobs.sh` on an existing install). The other end is `worker/`, a
  Cloudflare Worker. README: new "What leaves your server" section listing every outside connection.

## 0.2.0 (2026-11-03)

- Census by year (`/census`): each census year's households, by place, linked from Places.
- Census matches (Admin > Tidy the tree): suggests which person in the tree each name typed in a census
  transcription is, for an editor to confirm; a confirmed person gets that census on their page, and a birth year
  from the census age can be filled in. Database upgrade 0004.
- Places: "Everyone in" an area (the events in a place and every place inside it). A place with no coordinates
  shows on maps at the place it sits in, marked approximate.
- Tree opens an hourglass chart of you; people not linked to their record start at a person chosen under
  Admin > Site settings.
- Person page: a woman's married names ("Later Mary REED"), and newspaper searches (Chronicling America,
  Newspapers.com) next to the other outside links.
- Where the lines begin (`/lines`): the earliest person of each surname with no parents in the tree. Person pages
  say how many generations down from the start of their surname line they are.
- Relationship page: the path as a row of faces, from one person up to the common ancestor and down to the other.
- Person map: a line through the places of a life, in the order of the events, with the order written under it.
- Fan chart: photos in the wedges, and Print poster (just the fan, landscape).
- Admin: What's changed, the newest changes to the site by date.
- Data checks: death date differs from the obituary.
- Person page: half-siblings, grouped by the parent's other marriage.
- Hourglass: children grouped by marriage when someone married more than once.
- Dates: "5/1890" saves as MAY 1890; "May 2" is no longer read as the year 2; day-and-month-only dates get a
  hint on Dates to fix.
- Branch book (`/book/<id>`) and family map (`/map`).
- Hourglass: spouses shown, and an Hourglass button on person pages. Place page map.
- Tidy places: possible misspellings, and moving the real place out of census notes where a record title sits in
  the place field. Surname pair suggestions.
- Banner picture choice, and light, dark or device mode for every theme. More contrast in the light themes.
- Your account: unlink yourself from the tree. Version and commit in the footer. ID tooltip on person and family
  links.
- The person picker now lists name matches on Android Chrome (it used to show nothing until you typed an ID).
- Tagging a face offers quick buttons for the family of the people on the photo: parents, siblings, spouses and
  children.

## 0.1.0 (2026-10-29)

First public release.

"""Coordinates for places, from OpenStreetMap's Nominatim. genealogy-geocode.timer runs `flask --app app geocode run`
each night: places with no coordinates are looked up, most used first, one a second (Nominatim's usage policy).
Only place names leave the server. A cemetery's own coordinates are used when a place is that cemetery. If the
whole name isn't found, its leading parts are dropped ("Grandview Cemetery, Sligo, ..." -> "Sligo, ..."): that
match is approximate, and so is a county found for a name that doesn't say county. app_geocode remembers each
outcome, so names that weren't found aren't asked again, and approximate ones are listed for an editor to check
(Tidy the tree > Place coordinates)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import click
from flask import Blueprint, current_app, render_template

from .db import execute, query, update
from .edit import bp as edit_bp
from .edit import require
from .placenames import CODES
from .views import tree

bp = Blueprint("geocode", __name__)
SEARCH = "https://nominatim.openstreetmap.org/search"
PAUSE = 1.1  # seconds between requests: Nominatim allows one a second


class Stop(Exception):
    """Nominatim is refusing or unreachable: end the run, leave the rest for next time."""


def search(q, state):
    """Nominatim's best match for q as (lat, lon, what it found), or None. A US state (two-letter code) must match."""
    params = {"q": q.replace(" Cnty", " County").replace(" Twp", " Township"), "format": "jsonv2", "limit": 1,
              "addressdetails": 1, **({"countrycodes": "us"} if state else {})}
    site = current_app.config["SITE_URL"] or "a private family-history site"
    req = urllib.request.Request(f"{SEARCH}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": f"kith-geocoder ({site})"})
    time.sleep(PAUSE)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            found = json.load(r)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise Stop(str(e)) from e
    if not found or (state and found[0].get("address", {}).get("ISO3166-2-lvl4") != f"US-{state}"):
        return None
    return found[0]["lat"], found[0]["lon"], found[0]["display_name"]


def locate(name):
    """(status, lat, lon, what matched) for a place name; status is found, approximate or not found."""
    parts = [p.strip() for p in name.split(",") if p.strip()]
    # The cemetery itself: by its place, or by its name when only one cemetery has it ("Rimersburg Cemetery, ...")
    cem = query("SELECT latitude, longitude, cemname FROM cemeteries WHERE place = %s"
                " AND latitude <> '' AND longitude <> '' LIMIT 1", (name,))
    named = query("SELECT latitude, longitude, cemname FROM cemeteries WHERE cemname = %s",
                  (parts[0] if parts else "",))
    cem = cem or (named if len(named) == 1 and named[0]["latitude"] and named[0]["longitude"] else [])
    if cem:
        return "found", cem[0]["latitude"], cem[0]["longitude"], f"cemetery: {cem[0]['cemname']}"
    state = parts[-1] if parts and parts[-1] in CODES else ""
    for i in range(max(len(parts) - 1, 0)):  # a name alone ("Grandview Cemetery", "PA") is too vague to look up
        hit = search(", ".join(parts[i:]), state)
        if hit:
            # "Franklin, PA" can come back as Franklin County: a county answer to a name that isn't one needs checking
            county = hit[2].split(",")[0].endswith(" County") and not any(w in parts[i] for w in (" Cnty", " County"))
            return ("found" if i == 0 and not county else "approximate"), *hit
    return "not found", "", "", ""


def pending(limit):
    """Places with no coordinates that haven't already failed, most used first."""
    from .edit_places import place_counts  # here, not at the top: edit_places imports edit, which this module does
    failed = {r["place"].lower() for r in query(
        "SELECT place FROM app_geocode WHERE gedcom = %s AND status = 'not found'", (tree(),))}
    counts = {k.rstrip().lower(): n for k, n in place_counts().items()}
    rows = [r for r in query("SELECT ID, place FROM places WHERE gedcom = %s AND place <> ''"
                             " AND (latitude IS NULL OR latitude = '')"
                             " AND COALESCE(placelevel, 0) <> -1", (tree(),))  # -1: TNG's "do not geocode"
            if r["place"].lower() not in failed]
    rows.sort(key=lambda r: -counts.get(r["place"].rstrip().lower(), 0))
    return rows[:limit] if limit else rows


@bp.cli.command("run")
@click.option("--limit", type=int, default=0, help="Look up at most this many places (default: all).")
def run_command(limit):
    """Look up coordinates for places that have none (the timer runs this nightly)."""
    tally = {"found": 0, "approximate": 0, "not found": 0}
    try:
        for r in pending(limit):
            status, lat, lon, matched = locate(r["place"])
            tally[status] += 1
            if lat:
                update("places", {"latitude": lat, "longitude": lon}, {"ID": r["ID"]})
            execute("REPLACE INTO app_geocode (gedcom, place, status, matched, at) VALUES (%s, %s, %s, %s, NOW())",
                    (tree(), r["place"], status, matched[:255]))
    except Stop as e:
        click.echo(f"Stopped early, the rest wait for next time: {e}")
    click.echo(", ".join(f"{k} {n}" for k, n in tally.items()) + ".")


@edit_bp.get("/places/coordinates")
def place_coordinates():
    """Places the lookup could only place roughly, or not at all, for an editor to check or fill in."""
    require("allow_edit")
    rows = query("SELECT a.*, p.latitude, p.longitude FROM app_geocode a JOIN places p ON p.gedcom = a.gedcom"
                 " AND p.place = a.place WHERE a.gedcom = %s AND a.status <> 'found' ORDER BY a.status, a.place",
                 (tree(),))
    waiting = query("SELECT COUNT(*) AS n FROM places WHERE gedcom = %s AND place <> ''"
                    " AND (latitude IS NULL OR latitude = '')", (tree(),))[0]["n"]
    # shortcut: "last run" is every lookup on the day of the newest one, so a manual run that day is counted in too
    last = query("SELECT status, COUNT(*) AS n, MIN(at) AS at FROM app_geocode WHERE gedcom = %s AND DATE(at) ="
                 " (SELECT DATE(MAX(at)) FROM app_geocode WHERE gedcom = %s) GROUP BY status", (tree(), tree()))
    latest = query("SELECT a.place, a.matched, a.at, p.latitude, p.longitude FROM app_geocode a JOIN places p"
                   " ON p.gedcom = a.gedcom AND p.place = a.place WHERE a.gedcom = %s AND a.status = 'found'"
                   " ORDER BY a.at DESC, a.place LIMIT 25", (tree(),))
    return render_template("place_coordinates.html", rows=rows, waiting=waiting, latest=latest,
                           last={r["status"]: r["n"] for r in last}, last_at=min((r["at"] for r in last), default=None))

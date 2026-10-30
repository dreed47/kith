"""Home page. Editors get what changed lately and what needs tidying first; everyone else gets the family
side first (your branch, on this day, photos). Logged-out visitors get a public welcome page with no family data.
Also the "Dates to fix" list."""
import datetime
import threading
import time

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for

from . import localtime
from .dates import convert_date, describe, readings
from .db import query, update
from .files import is_image, is_url
from .media import folder
from .privacy import families_filter, media_filter, people_filter
from .relations import relationship
from .reports import dated_events
from .views import bp, one, people_by_id, sex_of, tree

RECENT = 8
ON_THIS_DAY = 10
PHOTOS = 8
FIX_PER_PAGE = 50
PERSON_DATES = {"birth": "Birth", "altbirth": "Christening / baptism", "death": "Death", "burial": "Burial"}
FAMILY_DATES = {"marr": "Marriage", "div": "Divorce"}


def ago(when, now=None):
    """'today', 'yesterday', '5 days ago', '3 weeks ago', '2 months ago', or the date for older changes."""
    now = now or localtime.now()
    days = (now.date() - localtime.local(when).date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{days // 30} months ago"
    return when.strftime("%-d %b %Y")


def recent_changes():
    """Latest edited people and families (TNG's change dates, which our edits set too)."""
    pcond, pparams = people_filter("p")
    fcond, fparams = families_filter("f")
    rows = query(
        f"(SELECT 'I' AS kind, p.personID AS id, p.changedate, p.changedby FROM people p"
        f" WHERE p.gedcom = %s AND {pcond} ORDER BY p.changedate DESC LIMIT %s)"
        f" UNION ALL (SELECT 'F', f.familyID, f.changedate, f.changedby FROM families f"
        f" WHERE f.gedcom = %s AND {fcond} ORDER BY f.changedate DESC LIMIT %s)"
        " ORDER BY changedate DESC LIMIT %s", [tree(), *pparams, RECENT, tree(), *fparams, RECENT, RECENT])
    people = people_by_id([r["id"] for r in rows if r["kind"] == "I"])
    fids = [r["id"] for r in rows if r["kind"] == "F"] or [""]
    fams = {f["familyID"]: f for f in query(
        f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(fids))})", [tree(), *fids])}
    spouses = people_by_id([x for f in fams.values() for x in (f["husband"], f["wife"])])
    for r in rows:
        r["ago"] = ago(r["changedate"])
        r["person"] = people.get(r["id"])
        if f := fams.get(r["id"]):
            r["spouses"] = [spouses.get(f["husband"]), spouses.get(f["wife"])]
    return rows


def fix_count():
    return len(to_fix())


TIDY_TTL = 600  # seconds a background count is shown before it's redone
_tidy = {"at": 0.0, "counts": None, "thread": None}


def tidy_counts():
    """The slow counts for the Tidy up card (seconds each on a big tree): the last background count, or None
    before the first one has finished (just after a restart). A stale count starts a new one, at most one at a time."""
    if time.time() - _tidy["at"] > TIDY_TTL and not (_tidy["thread"] and _tidy["thread"].is_alive()):
        _tidy["thread"] = threading.Thread(target=count_tidy, args=(current_app._get_current_object(), g.user),
                                           daemon=True)
        _tidy["thread"].start()
    return _tidy["counts"]


def count_tidy(app, user):
    """Counted as the editor who asked (who may see living people) so the numbers match the pages they open."""
    from .checks import CHECKS, run  # here, not at the top: these import this module's blueprint
    from .edit_places import misspellings, place_counts, tidy_rows
    from .living import report
    with app.app_context():
        g.user = user
        places = place_counts()
        _tidy.update(at=time.time(), counts={
            "places": len(tidy_rows(places)), "typos": len(misspellings(places)),
            "living": sum(len(people) for _, _, _, people, _ in report()),
            "checks": sum(len(run(selects)) for _, _, selects in CHECKS)})


def my_branch():
    """The logged-in user's own record with parents and grandparents."""
    me = g.user["personID"]
    if not me:
        return None
    people = people_by_id([me])
    if me not in people:
        return None
    branch = {"me": people[me], "parents": [], "grandparents": []}
    for gen in ("parents", "grandparents"):
        kids = [branch["me"]] if gen == "parents" else branch["parents"]
        famcs = [k["famc"] for k in kids if k and k["famc"]]
        if not famcs:
            break
        fams = query(f"SELECT husband, wife FROM families WHERE gedcom = %s AND familyID IN"
                     f" ({','.join(['%s'] * len(famcs))})", [tree(), *famcs])
        folks = people_by_id([x for f in fams for x in (f["husband"], f["wife"])])
        branch[gen] = [folks[x] for f in fams for x in (f["husband"], f["wife"]) if x in folks]
    return branch


def on_this_day(today=None):
    """Births, deaths and marriages on today's month and day, relatives of the user first."""
    today = today or localtime.today()
    items = dated_events(today.month, today.day)
    me = g.user["personID"]
    for x in items:
        x["id"] = x["person"]["personID"] if "person" in x else x["family"]["husband"] or x["family"]["wife"]
        x["years_ago"] = today.year - x["year"]
        # Label only: describe_relationship also loads everyone along the way, which the card never shows.
        x["rel"] = relationship(me, x["id"], sex_of) if me and x["id"] and x["id"] != me else None
    items.sort(key=lambda x: (x["rel"] is None, x["year"]))
    return items[:ON_THIS_DAY], len(items)


def recent_photos():
    cond, params = media_filter("m")
    rows = query(f"SELECT * FROM media m WHERE m.gedcom = %s AND m.path <> '' AND {cond}"
                 " ORDER BY m.mediaID DESC LIMIT 60", [tree(), *params])
    # Only photos whose file is really there (some TNG records point at files that never uploaded).
    return [m for m in rows if not is_url(m) and is_image(m["path"]) and (folder(m) / m["path"]).is_file()][:PHOTOS]


@bp.get("/")
def home():
    if g.user is None:
        return render_template("public.html")  # welcome page: no family data at all
    editor = g.user["allow_edit"] == 1
    days, day_total = on_this_day()
    from .news import HOME_COUNT, whats_new  # here, not at the top: news imports this module
    return render_template("home.html", news=whats_new(HOME_COUNT)[0], recent=recent_changes(), fix=fix_count() if editor else None,
                           tidy=tidy_counts() if editor else None,
                           branch=my_branch(), days=days, day_total=day_total, photos=recent_photos(),
                           today=localtime.today())


# GEDCOM's "Y": the death, burial or divorce happened, date unknown. Not a date to fix.
HAPPENED = {"death", "burial", "div"}
# Typed as numbers ("1/3/1964", "1964-01-03"): TNG may have sorted these, but often by the wrong reading.
NUMERIC_SQL = "^([A-Za-z.]+ +)?([0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{3,4}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2})$"


def to_fix():
    """Every stored date that doesn't sort (TNG left no sortable value) or that has a one-click fix
    (readings), as {label, date, field, edit, fixes, record}."""
    t = tree()
    rows = []
    for table, key, dates, kind in (("people", "personID", PERSON_DATES, "person"),
                                    ("families", "familyID", FAMILY_DATES, "family")):
        wanted = " OR ".join(f"({k}date <> '' AND ({k}datetr = '0000-00-00' OR {k}date REGEXP %s))" for k in dates)
        for rec in query(f"SELECT * FROM {table} WHERE gedcom = %s AND ({wanted})", (t, *[NUMERIC_SQL] * len(dates))):
            for k, label in dates.items():
                rows.append({"label": label, "date": rec[f"{k}date"], "sortless": str(rec[f"{k}datetr"]) == "0000-00-00",
                             "field": k, kind: rec, "edit": (kind, rec[key])})
    for e in query("SELECT e.*, et.display FROM events e JOIN eventtypes et ON et.eventtypeID = e.eventtypeID"
                   " WHERE e.gedcom = %s AND e.eventdate <> '' AND (e.eventdatetr = '0000-00-00' OR e.eventdate REGEXP %s)",
                   (t, NUMERIC_SQL)):
        rows.append({"label": e["display"], "date": e["eventdate"], "sortless": str(e["eventdatetr"]) == "0000-00-00",
                     "field": "event", "owner": e["persfamID"], "edit": ("event", e["eventID"])})
    for r in rows:
        r["fixes"] = readings(r["date"])
    return [r for r in rows if r["date"] and (r["sortless"] or r["fixes"])
            and not (r["field"] in HAPPENED and r["date"].strip().upper() in ("Y", "YES"))]


@bp.get("/dates-to-fix")
def dates_to_fix():
    """The to_fix list, a page at a time, with a link to fix each and one-click buttons where the date can be read."""
    if g.user["allow_edit"] != 1:
        abort(403)
    t = tree()
    rows = to_fix()
    rows.sort(key=lambda r: (r["date"].upper(), r["label"]))
    page = max(request.args.get("page", 1, type=int), 1)
    shown = rows[(page - 1) * FIX_PER_PAGE: page * FIX_PER_PAGE]
    for r in shown:
        if "owner" in r:  # an event: belongs to a person or a family
            r["person"] = people_by_id([r["owner"]]).get(r["owner"])
            if not r["person"]:
                r["family"] = one("SELECT * FROM families WHERE gedcom = %s AND familyID = %s", (t, r["owner"]))
        if r.get("family"):
            folks = people_by_id([r["family"]["husband"], r["family"]["wife"]])
            r["spouses"] = [folks.get(r["family"]["husband"]), folks.get(r["family"]["wife"])]
        r["hint"] = "" if r["fixes"] else describe(r["date"])[0]
    return render_template("dates_to_fix.html", rows=shown, total=len(rows), page=page,
                           pages=max((len(rows) + FIX_PER_PAGE - 1) // FIX_PER_PAGE, 1))


@bp.post("/dates-to-fix")
def fix_date():
    """One-click fix: store one of the date's readings. Only a reading of the date as it is now, so a stale page
    (someone fixed it meanwhile) changes nothing."""
    from .edit import audit, now  # edit imports views, which this module extends
    if g.user["allow_edit"] != 1:
        abort(403)
    f = request.form
    kind, rid, field, old, new = f.get("kind"), f.get("id", ""), f.get("field"), f.get("old", ""), f.get("new", "")
    tables = {"person": ("people", "personID", PERSON_DATES), "family": ("families", "familyID", FAMILY_DATES),
              "event": ("events", "eventID", {"event": ""})}
    if kind not in tables or field not in tables[kind][2]:
        abort(400)
    table, key, _ = tables[kind]
    col = f"{field}date"
    before = one(f"SELECT * FROM {table} WHERE gedcom = %s AND {key} = %s", (tree(), rid))
    if not before or before[col] != old or new not in (v for v, _ in readings(old)):
        flash("That date has changed since the list was shown; nothing was saved.")
        return redirect(url_for("main.dates_to_fix", page=f.get("page", 1, type=int)))
    values = {col: new, f"{field}datetr": convert_date(new)}
    if kind != "event":  # TNG's change date, which Recently updated reads; events have none
        values.update(changedate=now(), changedby=g.user["username"])
    update(table, values, {"gedcom": tree(), key: rid})
    audit(f"edit {kind}", table, str(rid), before, one(f"SELECT * FROM {table} WHERE gedcom = %s AND {key} = %s", (tree(), rid)))
    flash(f"Saved {old} as {new}.")
    return redirect(url_for("main.dates_to_fix", page=f.get("page", 1, type=int)))

"""Living flags (TNG's "living" checkbox decides what guests may see). A report of people whose flag looks wrong
for their dates, with one-click fixes, and the birth-year reasoning the GEDCOM import shares.

The rule, as in TNG: someone is living unless a death or burial is recorded or they were born more than
LIVING_YEARS ago. When the birth year is missing, the latest year they could have been born comes from their own
dates, their marriages (minus 12) and their children's latest possible births (minus 12), passed up the
generations only: a mistyped date can make ancestors look older, never a living descendant look dead."""
import datetime

from flask import flash, redirect, render_template, request, url_for

from .db import execute, query
from .edit import audit, bp, require, sync_family_flags
from .privacy import people_filter
from .views import people_by_id, tree

LIVING_YEARS = 110
POSSIBLY_LIVING_YEARS = 100  # not marked living, no death recorded, born this recently: worth a look
SHOWN = 500  # people listed per group


def plausible(y):
    """A year as evidence, or None. Years before 1000 or in the future are typos or misread dates
    (e.g. "3/5/1474/5" sorts as year 5)."""
    return y if 1000 <= y <= datetime.date.today().year else None


def year(sort_date):
    """The year of a stored sort date (TNG's ...datetr columns, "1850-00-00")."""
    return plausible(int(str(sort_date)[:4]))


def latest_births(own, families):
    """{person: latest possible birth year or None}. own: {person: earliest year in their own record or None};
    families: [(parents, children, earliest year in the family's record or None)]."""
    bound = dict(own)

    def cap(who, value):
        if who in bound and value is not None:
            bound[who] = value if bound[who] is None else min(bound[who], value)

    for _ in range(50):  # until nothing changes: a descendant's date reaches every generation above
        before = dict(bound)
        for parents, kids, married in families:
            for p in parents:
                cap(p, married - 12 if married else None)
                for k in kids:
                    cap(p, bound.get(k) - 12 if bound.get(k) else None)
        if bound == before:
            break
    return bound


def too_old(latest_birth):
    return latest_birth is not None and latest_birth <= datetime.date.today().year - LIVING_YEARS


def tree_bounds():
    """latest_births over the whole tree, from the database."""
    t = tree()
    own = {}
    for p in query("SELECT personID, birthdatetr, altbirthdatetr, deathdatetr, burialdatetr FROM people"
                   " WHERE gedcom = %s", (t,)):
        years = [year(p[k]) for k in ("birthdatetr", "altbirthdatetr", "deathdatetr", "burialdatetr")]
        own[p["personID"]] = min((y for y in years if y), default=None)
    for e in query("SELECT persfamID, MIN(eventdatetr) AS d FROM events WHERE gedcom = %s"
                   " AND YEAR(eventdatetr) > 0 GROUP BY persfamID", (t,)):
        if e["persfamID"] in own and (y := year(e["d"])):
            own[e["persfamID"]] = min(y, own[e["persfamID"]] or y)
    kids = {}
    for c in query("SELECT familyID, personID FROM children WHERE gedcom = %s", (t,)):
        kids.setdefault(c["familyID"], []).append(c["personID"])
    families = [([x for x in (f["husband"], f["wife"]) if x], kids.get(f["familyID"], []), year(f["marrdatetr"]))
                for f in query("SELECT familyID, husband, wife, marrdatetr FROM families WHERE gedcom = %s", (t,))]
    return latest_births(own, families)


def died(p):
    return any(p[k] for k in ("deathdate", "deathplace", "burialdate", "burialplace"))


def report():
    """The three groups of people whose flag looks wrong: (key, title, why, people, suggested fix)."""
    cond, params = people_filter("p")
    rows = query(f"SELECT p.* FROM people p WHERE p.gedcom = %s AND {cond} ORDER BY lastname, firstname",
                 [tree(), *params])
    bounds = tree_bounds()
    recent = datetime.date.today().year - POSSIBLY_LIVING_YEARS
    dead_but_living = [p for p in rows if p["living"] and died(p)]
    old_but_living = [p for p in rows if p["living"] and not died(p) and too_old(bounds.get(p["personID"]))]
    for p in old_but_living:
        p["latest"] = bounds[p["personID"]]
    maybe_living = [p for p in rows if not p["living"] and not died(p) and (year(p["birthdatetr"]) or 0) > recent]
    return [("died", "Marked living, but a death or burial is recorded",
             "These can't be living. Guests can't see them while they're marked living.", dead_but_living, 0),
            ("old", f"Marked living, but born more than {LIVING_YEARS} years ago",
             "From their birth date, or when it's missing, from their own dates, marriages and children.",
             old_but_living, 0),
            ("recent", f"Not marked living, born in the last {POSSIBLY_LIVING_YEARS} years, no death recorded",
             "Check these first: if any of them is alive, guests can see their details.", maybe_living, 1)]


@bp.route("/living", methods=["GET", "POST"])
def living_flags():
    require("allow_edit")
    if request.method == "POST":
        value = int(request.form.get("living") == "1")
        ids = request.form.getlist("pid")
        before = people_by_id(ids)
        changed = [pid for pid, p in before.items() if p["living"] != value]
        for pid in changed:
            execute("UPDATE people SET living = %s WHERE gedcom = %s AND personID = %s", (value, tree(), pid))
            sync_family_flags(pid)
        if changed:
            audit("mark living" if value else "mark not living", "people", f"{len(changed)} people",
                  {pid: before[pid]["living"] for pid in changed}, {pid: value for pid in changed})
        flash(f"Marked {len(changed)} {'person' if len(changed) == 1 else 'people'} "
              f"{'living' if value else 'not living'}.")
        return redirect(url_for("edit.living_flags"))
    return render_template("living_flags.html", groups=report(), shown=SHOWN)

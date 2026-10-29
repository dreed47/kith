"""Timeline (TNG's timeline.php): the lifetimes of a few people as bars on one time scale, their marriages marked,
next to the historical events admins have entered (TNG's timelineevents, empty until someone adds them)."""
import datetime

from flask import abort, flash, redirect, render_template, request, url_for

from .admin import bp as admin_bp
from .db import execute, insert, query
from .edit import audit
from .living import year
from .privacy import rights
from .views import bp, one, people_by_id, tree

MAX_PEOPLE = 12
TITLE_MAX = 128


def relatives(pid):
    """A person, their parents, spouses and children: who a timeline starts with."""
    ids = [pid]
    p = one("SELECT famc FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if p and p["famc"]:
        f = one("SELECT husband, wife FROM families WHERE gedcom = %s AND familyID = %s", (tree(), p["famc"]))
        ids += [x for x in (f or {}).values() if x]
    for f in query("SELECT familyID, husband, wife FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)",
                   (tree(), pid, pid)):
        ids.append(f["wife"] if f["husband"] == pid else f["husband"])
        ids += [c["personID"] for c in query("SELECT personID FROM children WHERE gedcom = %s AND familyID = %s"
                                             " ORDER BY ordernum", (tree(), f["familyID"]))]
    return list(dict.fromkeys(x for x in ids if x))


@bp.get("/timeline")
def timeline():
    ids = [x.strip().upper() for x in request.args.getlist("p") if x.strip()]
    if request.args.get("around"):
        ids = relatives(request.args["around"].upper()) + ids
    ids = list(dict.fromkeys(ids))[:MAX_PEOPLE]
    folks = people_by_id(ids)
    this_year = datetime.date.today().year
    bars, left_out = [], []
    for pid in ids:
        p = folks.get(pid)
        if not p:
            continue
        born = year(p["birthdatetr"]) or year(p["altbirthdatetr"])
        if not rights(p)["both"] or not born:
            left_out.append(p)
            continue
        died = year(p["deathdatetr"]) or year(p["burialdatetr"])
        marriages = [year(f["marrdatetr"]) for f in query(
            "SELECT marrdatetr FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)", (tree(), pid, pid))]
        bars.append({"p": p, "start": born, "end": died or (this_year if p["living"] else None),
                     "open": not died, "marriages": [m for m in marriages if m]})
    for b in bars:  # a death nobody recorded: draw to their last known year, at least one year long, faded
        b["end"] = b["end"] or max([b["start"] + 1, *b["marriages"]])
    lo = min([b["start"] for b in bars], default=this_year - 100)
    hi = max([b["end"] for b in bars], default=this_year)
    lo, hi = lo - lo % 10, hi + -hi % 10  # out to whole decades
    span = max(hi - lo, 10)

    def pct(y):  # a year's position across the chart
        return round(100 * (y - lo) / span, 2)

    for b in bars:
        b["left"], b["width"] = pct(b["start"]), max(pct(b["end"]) - pct(b["start"]), 0.5)
        b["marks"] = [pct(m) for m in b["marriages"]]
    history = [dict(h, left=pct(int(h["evyear"]))) for h in query(
        "SELECT * FROM timelineevents WHERE evyear REGEXP '^[0-9]{3,4}$' ORDER BY CAST(evyear AS UNSIGNED)")
        if lo <= int(h["evyear"]) <= hi]
    ticks = [(y, pct(y)) for y in range(lo, hi + 1, 10 if span <= 150 else 25)]
    return render_template("timeline.html", bars=bars, left_out=left_out, history=history, ticks=ticks, ids=ids,
                           max_people=MAX_PEOPLE)


@admin_bp.route("/history", methods=["GET", "POST"])
def history_events():
    """The historical events timelines show (TNG's timeline events)."""
    if request.method == "POST":
        start, end = request.form.get("evyear", "").strip(), request.form.get("endyear", "").strip()
        title, detail = request.form.get("evtitle", "").strip(), request.form.get("evdetail", "").strip()
        errors = [m for bad, m in ((not start.isdigit() or not 100 <= int(start or 0) <= 3000, "Enter the year."),
                                   (bool(end) and (not end.isdigit() or int(end) < int(start or 0)),
                                    "The end year should be a year after the start, or empty."),
                                   (not title, "Enter what happened."),
                                   (len(title) > TITLE_MAX, f"The title is longer than {TITLE_MAX} characters."))
                  if bad]
        if errors:
            for e in errors:
                flash(e)
        else:
            _, tid = insert("timelineevents", {"evyear": start, "endyear": end, "evtitle": title,
                                                   "evdetail": detail})
            audit("add history event", "timelineevents", str(tid), None, {"year": start, "title": title})
            flash(f"Added {title}.")
        return redirect(url_for("admin.history_events"))
    rows = query("SELECT * FROM timelineevents ORDER BY CAST(evyear AS UNSIGNED), evtitle")
    return render_template("admin_history.html", rows=rows)


@admin_bp.post("/history/<int:tid>/delete")
def delete_history_event(tid):
    row = one("SELECT * FROM timelineevents WHERE tleventID = %s", (tid,))
    if not row:
        abort(404)
    execute("DELETE FROM timelineevents WHERE tleventID = %s", (tid,))
    audit("delete history event", "timelineevents", str(tid), row, None)
    flash(f"Deleted {row['evtitle']}.")
    return redirect(url_for("admin.history_events"))

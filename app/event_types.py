"""Kinds of event (TNG's event types): add a new one, rename one, hide one from the "add event" list, or delete
one nobody uses. New kinds are GEDCOM "EVEN" events with their name as the type, which is how other genealogy
programs receive custom events. LDS types aren't listed (the site never shows LDS data)."""
from flask import abort, flash, redirect, render_template, request, url_for

from .admin import bp
from .db import execute, insert, query, update
from .edit import audit
from .views import EVENT_LABELS, one

KINDS = {"I": "Person", "F": "Family"}
NAME_MAX = 100


def get_type(tid):
    t = one("SELECT * FROM eventtypes WHERE eventtypeID = %s AND type IN ('I', 'F') AND ldsevent = 0", (tid,))
    if not t:
        abort(404)
    return t


def name_errors(name, kind, tid=0):
    if not name:
        return ["Enter a name."]
    if len(name) > NAME_MAX:
        return [f"The name is longer than {NAME_MAX} characters."]
    if one("SELECT 1 AS x FROM eventtypes WHERE type = %s AND eventtypeID <> %s AND (LOWER(display) = LOWER(%s)"
           " OR LOWER(description) = LOWER(%s))", (kind, tid, name, name)):
        return [f"There's already a {KINDS[kind].lower()} event called {name}."]
    return []


@bp.route("/event-types", methods=["GET", "POST"])
def event_types():
    if request.method == "POST":  # add one
        kind, name = request.form.get("type"), request.form.get("name", "").strip()
        if kind not in KINDS:
            abort(400)
        errors = name_errors(name, kind)
        if not errors:
            _, tid = insert("eventtypes", {"tag": "EVEN", "description": name, "display": name, "type": kind,
                                               "keep": 1})
            audit("add event type", "eventtypes", str(tid), None, get_type(tid))
            flash(f"Added {name}.")
            return redirect(url_for("admin.event_types"))
        for e in errors:
            flash(e)
    rows = query("SELECT et.*, (SELECT COUNT(*) FROM events e WHERE e.eventtypeID = et.eventtypeID) AS n"
                 " FROM eventtypes et WHERE et.type IN ('I', 'F') AND et.ldsevent = 0")
    for r in rows:
        r["name"] = EVENT_LABELS.get(r["display"], r["display"])
    rows.sort(key=lambda r: (r["type"], r["name"].lower()))
    return render_template("admin_event_types.html", rows=rows, kinds=KINDS)


@bp.route("/event-types/<int:tid>", methods=["GET", "POST"])
def event_type(tid):
    t = get_type(tid)
    errors = []
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        errors = name_errors(name, t["type"], tid)
        if not errors:
            v = {"display": name, "keep": int(request.form.get("keep") == "1")}
            if t["tag"] == "EVEN":
                v["description"] = name  # what GEDCOM exports as the event's TYPE
            update("eventtypes", v, {"eventtypeID": tid})
            audit("edit event type", "eventtypes", str(tid), t, get_type(tid))
            flash("Saved.")
            return redirect(url_for("admin.event_types"))
    n = one("SELECT COUNT(*) AS n FROM events WHERE eventtypeID = %s", (tid,))["n"]
    return render_template("admin_event_type.html", t=t, errors=errors, used=n, kinds=KINDS,
                           name=request.form.get("name") or EVENT_LABELS.get(t["display"], t["display"]))


@bp.post("/event-types/<int:tid>/delete")
def delete_event_type(tid):
    t = get_type(tid)
    if one("SELECT COUNT(*) AS n FROM events WHERE eventtypeID = %s", (tid,))["n"]:
        abort(400)  # the page only offers delete for unused kinds
    execute("DELETE FROM eventtypes WHERE eventtypeID = %s", (tid,))
    audit("delete event type", "eventtypes", str(tid), t, None)
    flash(f"Deleted {t['display']}.")
    return redirect(url_for("admin.event_types"))

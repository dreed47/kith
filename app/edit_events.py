"""Events and notes on a person or family, ported from TNG's admin_addevent/updateevent/deleteevent and
admin_addnote/updatenote/deletenote."""
from flask import abort, flash, g, redirect, render_template, request, url_for

from .dates import convert_date, standard as standard_date
from .db import execute, insert, query, update
from .edit import (DATE_MAX, PLACE_MAX, add_places, audit, bp, editable, get_family, get_person, person_page,
                   require, warn_dates)
from .placenames import standard
from .privacy import families_filter, people_filter, rights
from .views import EVENT_LABELS, FACT_LABELS, one, tree

EVENT_TEXT = {"age": 12, "agency": 120, "cause": 90}
ADDRESS = {"address1": 64, "address2": 64, "city": 64, "state": 64, "zip": 10, "country": 64,
           "phone": 30, "email": 100, "www": 100}
TEXT_MAX = 65000  # TEXT columns hold 65,535 bytes


def owner(persfam_id, anchor=None):
    """The person or family an event/note belongs to: (kind, row, page URL). 404 if neither exists, or the user
    may not see it in full."""
    if p := get_person(persfam_id):
        return "I", editable(p), person_page(persfam_id, anchor)
    if f := get_family(persfam_id):
        return "F", editable(f), url_for("main.family", fid=persfam_id)
    abort(404)


def event_types(kind, current=None):
    """Event types offered for a person (I) or family (F): the active ones, plus the one an event being edited
    already has, even if it has since been hidden (else saving would quietly change its type)."""
    rows = query("SELECT eventtypeID, display FROM eventtypes WHERE type = %s AND ldsevent = 0"
                 " AND (keep = 1 OR eventtypeID = %s)", (kind, current or 0))
    return sorted(({**t, "display": EVENT_LABELS.get(t["display"], t["display"])} for t in rows),
                  key=lambda t: t["display"].lower())


def event_form(form, kind, current=None):
    v, errors = {}, []
    allowed = {t["eventtypeID"] for t in event_types(kind, current)}
    v["eventtypeID"] = form.get("eventtypeID", type=int)
    if v["eventtypeID"] not in allowed:
        errors.append("Choose an event type.")
    v["eventdate"], v["eventplace"] = standard_date(form.get("eventdate", "")), standard(form.get("eventplace", ""))
    v["eventdatetr"] = convert_date(v["eventdate"])
    limits = {"eventdate": DATE_MAX, "eventplace": PLACE_MAX, **EVENT_TEXT, "info": TEXT_MAX}
    for k in ("age", "agency", "cause", "info"):
        v[k] = form.get(k, "").strip()
    for k, n in limits.items():
        if len(v[k]) > n:
            errors.append(f"{k} is longer than {n} characters.")
    addr = {k: form.get(k, "").strip() for k in ADDRESS}
    errors += [f"{k} is longer than {n} characters." for k, n in ADDRESS.items() if len(addr[k]) > n]
    return v, addr, errors


def save_address(address_id, addr):
    """TNG keeps an event's address in addresses: update it, create it, or delete it when emptied."""
    if address_id:
        if any(addr.values()):
            update("addresses", addr, {"addressID": address_id})
            return address_id
        execute("DELETE FROM addresses WHERE addressID = %s", (address_id,))
        return ""
    if any(addr.values()):
        return str(insert("addresses", {**addr, "gedcom": tree()})[1])
    return ""


def get_event(eid):
    e = one("SELECT * FROM events WHERE gedcom = %s AND eventID = %s", (tree(), eid))
    if not e:
        abort(404)
    return e


@bp.route("/event/new", methods=["GET", "POST"])
def new_event():
    require("allow_add")
    kind, row, back = owner(request.values.get("for", ""), "events")
    persfam_id = request.values["for"]
    v, addr, errors = {}, {}, []
    if request.method == "POST":
        v, addr, errors = event_form(request.form, kind)
        if not errors:
            v.update(gedcom=tree(), persfamID=persfam_id, addressID=save_address("", addr))
            _, eid = insert("events", v)
            add_places(v["eventplace"])
            audit("add event", "events", str(eid), None, get_event(eid))
            flash("Event added.")
            warn_dates(v["eventdate"])
            return redirect(back)
    return render_template("edit_event.html", e=None, v=v, addr=addr, errors=errors, types=event_types(kind),
                           owner_id=persfam_id, back=back)


@bp.route("/event/<int:eid>", methods=["GET", "POST"])
def event(eid):
    require("allow_edit")
    e = get_event(eid)
    kind, _, back = owner(e["persfamID"], "events")
    addr = one("SELECT * FROM addresses WHERE addressID = %s", (e["addressID"],)) if e["addressID"] else None
    v, errors = e, []
    addr = {k: (addr or {}).get(k, "") for k in ADDRESS}
    if request.method == "POST":
        v, addr, errors = event_form(request.form, kind, e["eventtypeID"])
        if not errors:
            v["addressID"] = save_address(e["addressID"], addr)
            update("events", v, {"gedcom": tree(), "eventID": eid})
            add_places(v["eventplace"])
            audit("edit event", "events", str(eid), e, get_event(eid))
            flash("Event saved.")
            warn_dates(v["eventdate"])
            return redirect(back)
    return render_template("edit_event.html", e=e, v=v, addr=addr, errors=errors,
                           types=event_types(kind, e["eventtypeID"]), owner_id=e["persfamID"], back=back)


def delete_note_link(link):
    """TNG's deleteNote: drop the link, and the note text too when nothing else links to it."""
    shared = one("SELECT COUNT(*) AS n FROM notelinks WHERE xnoteID = %s", (link["xnoteID"],))["n"] > 1
    note = one("SELECT * FROM xnotes WHERE ID = %s", (link["xnoteID"],))
    if not shared:
        execute("DELETE FROM xnotes WHERE ID = %s", (link["xnoteID"],))
    execute("DELETE FROM notelinks WHERE ID = %s", (link["ID"],))
    return note


@bp.route("/event/<int:eid>/delete", methods=["GET", "POST"])
def delete_event(eid):
    require("allow_delete")
    e = get_event(eid)
    _, _, back = owner(e["persfamID"], "events")
    label = one("SELECT display FROM eventtypes WHERE eventtypeID = %s", (e["eventtypeID"],))
    what = f"{label['display'] if label else 'event'} {e['eventdate']} {e['eventplace']}".strip()
    if request.method == "GET":
        return render_template("confirm_delete.html", title=f"Delete {what}?", back=back,
                               details=["Its notes, source citations and media links go with it."])
    note_links = query("SELECT * FROM notelinks WHERE gedcom = %s AND eventID = %s", (tree(), str(eid)))
    removed = {"events": e, "notelinks": note_links,
               "citations": query("SELECT * FROM citations WHERE gedcom = %s AND eventID = %s", (tree(), str(eid))),
               "medialinks": query("SELECT * FROM medialinks WHERE gedcom = %s AND eventID = %s", (tree(), str(eid))),
               "addresses": query("SELECT * FROM addresses WHERE addressID = %s", (e["addressID"] or 0,)),
               "xnotes": [delete_note_link(n) for n in note_links]}
    if e["addressID"]:
        execute("DELETE FROM addresses WHERE addressID = %s", (e["addressID"],))
    execute("DELETE FROM events WHERE eventID = %s", (eid,))
    execute("DELETE FROM citations WHERE gedcom = %s AND eventID = %s", (tree(), str(eid)))
    execute("DELETE FROM medialinks WHERE gedcom = %s AND eventID = %s", (tree(), str(eid)))
    audit("delete event", "events", str(eid), removed, None)
    flash(f"Deleted {what}.")
    return redirect(back)


def next_note_order(persfam_id):
    return one("SELECT COALESCE(MAX(ordernum), 0) AS n FROM notelinks WHERE gedcom = %s AND persfamID = %s",
               (tree(), persfam_id))["n"] + 1


def set_biography(link, on):
    """GEDCOM has no biography tag; ours is the person's own note given a negative ordernum (no TNG note has one),
    so TNG and GEDCOM exports, which list notes by ordernum, put it first. One per person: a new one demotes the old."""
    if on == (link["ordernum"] < 0):
        return
    if on:
        execute("UPDATE notelinks SET ordernum = %s WHERE gedcom = %s AND persfamID = %s AND ordernum < 0",
                (next_note_order(link["persfamID"]), tree(), link["persfamID"]))
    execute("UPDATE notelinks SET ordernum = %s WHERE ID = %s",
            (-1 if on else next_note_order(link["persfamID"]), link["ID"]))


def note_form(form):
    note = form.get("note", "").strip()
    errors = [] if note else ["The note is empty."]
    if len(note) > TEXT_MAX:
        errors.append(f"The note is longer than {TEXT_MAX} characters.")
    return note, int(form.get("secret") == "1"), errors


def get_note_link(link_id):
    link = one("SELECT nl.*, x.note, (SELECT COUNT(*) FROM notelinks o WHERE o.xnoteID = nl.xnoteID) AS links"
               " FROM notelinks nl JOIN xnotes x ON x.ID = nl.xnoteID WHERE nl.gedcom = %s AND nl.ID = %s",
               (tree(), link_id))
    if not link:
        abort(404)
    return link


PERSON_FACTS = ("BIRT", "CHR", "BAPM", "DEAT", "BURI", "NAME")
FAMILY_FACTS = ("MARR", "DIV")


def about_label(persfam_id, key):
    """What a note is about, in words: a fact's name, a custom event's type, or "" for the whole record."""
    if not key or key in FACT_LABELS:
        return FACT_LABELS.get(key, "")
    e = one("SELECT et.display FROM events e JOIN eventtypes et ON et.eventtypeID = e.eventtypeID"
            " WHERE e.gedcom = %s AND e.persfamID = %s AND e.eventID = %s", (tree(), persfam_id, key))
    return EVENT_LABELS.get(e["display"], e["display"]) if e else None


@bp.route("/note/new", methods=["GET", "POST"])
def new_note():
    require("allow_add")
    kind, _, back = owner(request.values.get("for", ""), "notes")
    persfam_id = request.values["for"]
    about = request.values.get("about", "")
    if about and (about not in (PERSON_FACTS if kind == "I" else FAMILY_FACTS) and not about.isdigit()
                  or about_label(persfam_id, about) is None):
        abort(400)  # a fact this record doesn't have, or someone else's event
    note, secret, errors = "", 0, []
    can_bio = kind == "I" and not about
    bio = can_bio and request.values.get("bio") == "1"
    if request.method == "POST":
        note, secret, errors = note_form(request.form)
        if not errors:
            _, xid = insert("xnotes", {"gedcom": tree(), "note": note})
            _, link_id = insert("notelinks", {"gedcom": tree(), "persfamID": persfam_id, "xnoteID": xid,
                                                  "eventID": about, "secret": secret,
                                                  "ordernum": next_note_order(persfam_id)})
            if can_bio:
                set_biography(get_note_link(link_id), request.form.get("bio") == "1")
            audit("add note", "notelinks", str(link_id), None, get_note_link(link_id))
            flash("Note added.")
            return redirect(back)
    return render_template("edit_note.html", link=None, note=note, secret=secret, errors=errors, can_bio=can_bio,
                           bio=bio, owner_id=persfam_id, back=back, about=about,
                           about_label=about_label(persfam_id, about))


@bp.route("/note/<int:link_id>", methods=["GET", "POST"])
def note(link_id):
    require("allow_edit")
    link = get_note_link(link_id)
    kind, row, back = owner(link["persfamID"], "notes")
    if link["secret"] and not rights(row)["private_notes"]:
        abort(404)
    note, secret, errors = link["note"], link["secret"], []
    can_bio, bio = kind == "I" and not link["eventID"], link["ordernum"] < 0
    if request.method == "POST":
        note, secret, errors = note_form(request.form)
        bio = can_bio and request.form.get("bio") == "1"
        if not errors:
            execute("UPDATE xnotes SET note = %s WHERE ID = %s", (note, link["xnoteID"]))
            execute("UPDATE notelinks SET secret = %s WHERE ID = %s", (secret, link_id))
            if can_bio:
                set_biography(link, bio)
            audit("edit note", "notelinks", str(link_id), link, get_note_link(link_id))
            flash("Note saved.")
            return redirect(back)
    return render_template("edit_note.html", link=link, note=note, secret=secret, errors=errors, can_bio=can_bio,
                           bio=bio, owner_id=link["persfamID"], back=back,
                           about_label=about_label(link["persfamID"], link["eventID"]))


@bp.route("/note/<int:link_id>/delete", methods=["GET", "POST"])
def delete_note(link_id):
    require("allow_delete")
    link = get_note_link(link_id)
    _, row, back = owner(link["persfamID"], "notes")
    if link["secret"] and not rights(row)["private_notes"]:
        abort(404)
    if request.method == "GET":
        details = [link["note"][:200] + ("…" if len(link["note"]) > 200 else "")]
        if link["links"] > 1:
            details.append(f"This note is shared with {link['links'] - 1} other record(s); it stays there.")
        return render_template("confirm_delete.html", title="Delete this note?", back=back, details=details)
    delete_note_link(link)
    audit("delete note", "notelinks", str(link_id), link, None)
    flash("Note deleted.")
    return redirect(back)


SEARCH_PER_PAGE = 50
SNIPPET = 150  # characters shown either side of the first match


def snippet(text, q):
    """(before, match, after) around the first match, trimmed with "…" at either end. The database matches
    ignoring accents (é = e), which find() doesn't; then the note's start is shown, unmarked."""
    i = text.lower().find(q.lower())
    if i < 0:
        return text[:2 * SNIPPET] + ("…" if len(text) > 2 * SNIPPET else ""), "", ""
    start, end = max(i - SNIPPET, 0), i + len(q) + SNIPPET
    return (("…" if start else "") + text[start:i], text[i:i + len(q)], text[i + len(q):end] + ("…" if end < len(text) else ""))


@bp.get("/notes")
def search_notes():
    """Find notes by their text (TNG's admin Notes page). Notes on people or families the user can't see, and private
    notes without that right, are left out."""
    require("allow_edit")
    q, page = request.args.get("q", "").strip(), max(request.args.get("page", 1, type=int), 1)
    rows, total = [], 0
    if q:
        pcond, pargs = people_filter("p")
        fcond, fargs = families_filter("f")
        where = (f"nl.gedcom = %s AND x.note LIKE %s AND (p.personID IS NULL OR {pcond})"
                 f" AND (f.familyID IS NULL OR {fcond}) AND (p.personID IS NOT NULL OR f.familyID IS NOT NULL)"
                 f" AND nl.eventID NOT IN ('BAPL', 'CONL', 'INIT', 'ENDL', 'SLGC', 'SLGS')"
                 + ("" if g.user["allow_private_notes"] == 1 else " AND nl.secret = 0"))
        like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # typed % and _ are literal
        args = [tree(), f"%{like}%", *pargs, *fargs]
        joins = ("FROM notelinks nl JOIN xnotes x ON x.ID = nl.xnoteID"
                 " LEFT JOIN people p ON p.gedcom = nl.gedcom AND p.personID = nl.persfamID"
                 " LEFT JOIN families f ON f.gedcom = nl.gedcom AND f.familyID = nl.persfamID")
        total = one(f"SELECT COUNT(*) AS n {joins} WHERE {where}", args)["n"]
        rows = query(f"SELECT nl.ID, nl.persfamID, nl.eventID, nl.secret, x.note {joins} WHERE {where}"
                     " ORDER BY nl.persfamID, nl.ordernum LIMIT %s OFFSET %s",
                     [*args, SEARCH_PER_PAGE, (page - 1) * SEARCH_PER_PAGE])
        for r in rows:
            r["parts"], r["about"] = snippet(r["note"], q), about_label(r["persfamID"], r["eventID"])
            r["url"] = owner(r["persfamID"])[2]
    return render_template("search_notes.html", q=q, rows=rows, total=total, page=page,
                           pages=max((total + SEARCH_PER_PAGE - 1) // SEARCH_PER_PAGE, 1))

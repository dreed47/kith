"""Editing, ported from TNG's admin_updateperson / admin_addperson so records stay TNG-compatible.
Every change is recorded with before/after copies in app_audit.

TNG's tables are MyISAM (no transactions), so multi-row saves write the main record first and
the links after; a failure part-way leaves a valid record that's just missing a link.
"""
import datetime
import json

import pymysql
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import relations, search
from .dates import convert_date, describe, standard as standard_date
from .db import execute, insert, query, update
from .placenames import standard
from .watch import notify as notify_watchers
from .privacy import display_name as pname
from .privacy import rights
from .views import one, person_detail, relatives, tree

bp = Blueprint("edit", __name__, url_prefix="/edit")

PERSON_TEXT = {"firstname": 127, "lnprefix": 25, "lastname": 127, "nickname": 200,
               "title": 100, "prefix": 100, "suffix": 100}
PERSON_EVENTS = {"birth": "Birth", "altbirth": "Christening / baptism", "death": "Death", "burial": "Burial"}
DATE_MAX, PLACE_MAX = 50, 248


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def require(perm):
    if not g.user or g.user[perm] != 1:
        abort(403)


def audit(action, tbl, record, before, after):
    relations.invalidate()  # every edit is audited, so this keeps relationship answers
    search.invalidate()     # and the surname list current
    dump = lambda r: None if r is None else json.dumps(r, default=str)
    execute("INSERT INTO app_audit (at, username, action, tbl, record, before_json, after_json)"
            " VALUES (NOW(), %s, %s, %s, %s, %s, %s)",
            # record is VARCHAR(60) and place names run to 248; the whole name is in the JSON
            (g.user["username"], action, tbl, record[:60], dump(before), dump(after)))
    notify_watchers(tbl, record, before, after)


def can_edit_anything():
    if g.user["allow_edit"] != 1 and g.user["allow_add"] != 1:
        abort(403)


@bp.get("/date")
def date_hint():
    """How a typed date is understood (shown live under date boxes)."""
    can_edit_anything()
    message, ok = describe(request.args.get("d", ""))
    return jsonify({"message": message, "ok": ok})


@bp.get("/places")
def place_hint():
    """Existing places matching what's typed, for the place boxes' suggestions (reusing a place keeps spellings consistent)."""
    can_edit_anything()
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    rows = query("SELECT place FROM places WHERE gedcom = %s AND place LIKE %s"
                 " ORDER BY place LIKE %s DESC, CHAR_LENGTH(place), place LIMIT 20",
                 (tree(), f"%{q}%", f"{q}%"))
    return jsonify([r["place"] for r in rows])


LIVING_AGE, PROBABLY_LIVING_AGE = 110, 90


def living_warnings(v):
    """The living flag decides who sees a person's details, so point out likely mistakes either way."""
    born = int(v["birthdatetr"][:4]) if v.get("birthdatetr", "0000")[:4] != "0000" else None
    this_year = datetime.date.today().year
    has_end = any(v.get(k) for k in ("deathdate", "deathplace", "burialdate", "burialplace"))
    if v["living"] and has_end:
        flash("Check: marked living but has a death or burial entry.")
    elif v["living"] and born and this_year - born > LIVING_AGE:
        flash(f"Check: marked living but born in {born}.")
    elif not v["living"] and not has_end and born and this_year - born < PROBABLY_LIVING_AGE:
        flash(f"Check: born in {born} with no death recorded, but not marked living. "
              "Their details are visible to every logged-in user.")


def warn_dates(*dates):
    """Saved, but tell the editor about dates that won't sort."""
    for d in dates:
        if d and convert_date(d) == "0000-00-00":
            flash(f"Saved, but \"{d}\" isn't recognised as a date, so it won't sort by date.")


def owner_name(persfam_id):
    """'Name (ID)' for a person, 'Husband & Wife (ID)' for a family, so forms say whose record this is."""
    if p := get_person(persfam_id):
        return f"{pname(p)} ({persfam_id})"
    f = get_family(persfam_id)
    spouses = " & ".join(pname(get_person(x)) for x in (f["husband"], f["wife"]) if x) if f else ""
    return f"{spouses or 'family'} ({persfam_id})"


def person_page(pid, anchor=None):
    """Where to go after saving something of a person's: their page, or their edit page (at anchor) when the form
    was opened from there (back=edit)."""
    if request.values.get("back") == "edit":
        return url_for("edit.person", pid=pid, _anchor=anchor)
    return url_for("main.person", pid=pid)


def get_person(pid):
    return one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))


def get_family(fid):
    return one("SELECT * FROM families WHERE gedcom = %s AND familyID = %s", (tree(), fid))


def editable(row):
    """A person or family an editor may open: one they can see in full. Anyone else looks missing, as on the site's
    own pages, so an edit page can't show (or change) a living or private person the editor may not see."""
    if not row or not rights(row)["both"]:
        abort(404)
    return row


def insert_with_new_id(table, key, prefix, values):
    """Next ID after the highest in use (TNG fills gaps, but reusing a deleted person's ID would
    repoint old links). The unique key catches a race with another save; retry with the next number."""
    for _ in range(5):
        n = one(f"SELECT COALESCE(MAX(CAST(SUBSTRING({key}, %s) AS UNSIGNED)), 0) AS n FROM {table}"
                f" WHERE gedcom = %s AND {key} LIKE %s", (len(prefix) + 1, tree(), prefix + "%"))["n"]
        new_id = f"{prefix}{n + 1}"
        try:
            insert(table, {**values, "gedcom": tree(), key: new_id})
            return new_id
        except pymysql.err.IntegrityError:
            continue
    raise RuntimeError(f"could not allocate a new {key}")


def add_places(*places):
    for place in {p for p in places if p}:
        insert("places", {"gedcom": tree(), "place": place, "changedate": now(),
                              "changedby": g.user["username"]}, ignore=True)


def person_form(form):
    v, errors = {}, []
    for k, n in PERSON_TEXT.items():
        v[k] = form.get(k, "").strip()
        if len(v[k]) > n:
            errors.append(f"{k} is longer than {n} characters.")
    if not (v["firstname"] or v["lastname"]):
        errors.append("Enter a first or last name.")
    v["sex"] = form.get("sex") if form.get("sex") in ("M", "F", "U") else "U"
    v["living"] = int(form.get("living") == "1")
    v["private"] = int(form.get("private") == "1")
    v["burialtype"] = int(form.get("cremated") == "1")
    for ev, label in PERSON_EVENTS.items():
        date, place = standard_date(form.get(f"{ev}date", "")), standard(form.get(f"{ev}place", ""))
        if form.get(f"remove_{ev}") == "1":  # the event's Remove box: same as emptying its date and place
            date = place = ""
        if len(date) > DATE_MAX:
            errors.append(f"{label} date is longer than {DATE_MAX} characters.")
        if len(place) > PLACE_MAX:
            errors.append(f"{label} place is longer than {PLACE_MAX} characters.")
        v[f"{ev}date"], v[f"{ev}datetr"], v[f"{ev}place"] = date, convert_date(date), place
    alt = form.get("altbirthtype")
    v["altbirthtype"] = (alt if alt in ("CHR", "BAPM") else "CHR") if (v["altbirthdate"] or v["altbirthplace"]) else ""
    return v, errors


def sync_family_flags(pid):
    """A family is living/private if either spouse is (TNG does this on every person save)."""
    for f in query("SELECT * FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)", (tree(), pid, pid)):
        spouses = [get_person(x) for x in (f["husband"], f["wife"]) if x]
        flags = {k: int(any(s[k] for s in spouses if s)) for k in ("living", "private")}
        if any(f[k] != flags[k] for k in flags):
            update("families", flags, {"gedcom": tree(), "familyID": f["familyID"]})
            audit("update family flags", "families", f["familyID"], f, {**f, **flags})


def set_famc(pid):
    """famc is the person's first parent family (TNG recomputes it the same way on every save)."""
    first = one("SELECT familyID FROM children WHERE gedcom = %s AND personID = %s ORDER BY parentorder, ID LIMIT 1",
                (tree(), pid))
    execute("UPDATE people SET famc = %s WHERE gedcom = %s AND personID = %s",
            (first["familyID"] if first else "", tree(), pid))


def link_child(fam, pid):
    n = one("SELECT COALESCE(MAX(ordernum), 0) AS n FROM children WHERE gedcom = %s AND familyID = %s",
            (tree(), fam["familyID"]))["n"]
    row = {"gedcom": tree(), "familyID": fam["familyID"], "personID": pid, "ordernum": n + 1}
    insert("children", row)
    refresh_haskids(pid)  # the new child row says whether this child has children of their own
    for parent in (fam["husband"], fam["wife"]):
        refresh_haskids(parent)
    set_famc(pid)
    audit("add child", "children", f"{fam['familyID']}/{pid}", None, row)


def unlink_child(fid, pid):
    row = one("SELECT * FROM children WHERE gedcom = %s AND familyID = %s AND personID = %s", (tree(), fid, pid))
    execute("DELETE FROM children WHERE gedcom = %s AND familyID = %s AND personID = %s", (tree(), fid, pid))
    fam = get_family(fid)
    for parent in (fam["husband"], fam["wife"]):
        refresh_haskids(parent)
    set_famc(pid)
    audit("remove child", "children", f"{fid}/{pid}", row, None)


def create_family(a, b):
    """New family for two existing people, placing them as husband/wife by sex."""
    husband, wife = (b, a) if b["sex"] == "M" or (b["sex"] != "F" and a["sex"] == "F") else (a, b)
    order = lambda col, p: one(f"SELECT COUNT(*) AS n FROM families WHERE gedcom = %s AND {col} = %s",
                               (tree(), p["personID"]))["n"] + 1
    values = {"husband": husband["personID"], "wife": wife["personID"],
              "husborder": order("husband", husband), "wifeorder": order("wife", wife),
              "living": int(bool(husband["living"] or wife["living"])),
              "private": int(bool(husband["private"] or wife["private"])),
              "changedate": now(), "changedby": g.user["username"]}
    fid = insert_with_new_id("families", "familyID", "F", values)
    audit("add family", "families", fid, None, get_family(fid))
    return fid


@bp.route("/person/<pid>", methods=["GET", "POST"])
def person(pid):
    require("allow_edit")
    p = editable(get_person(pid))
    values, errors = p, []
    if request.method == "POST":
        values, errors = person_form(request.form)
        if request.form.get("changedate") != str(p["changedate"]):
            errors.insert(0, "Someone else saved this person while you were editing. "
                             "Open the person again to see their changes, then redo yours.")
        if not errors:
            values.update(changedate=now(), changedby=g.user["username"], edituser="", edittime=0)
            if (values["lnprefix"], values["lastname"]) != (p["lnprefix"], p["lastname"]):
                # shortcut: TNG's "sounds like" key (PHP metaphone) isn't ported; only TNG's soundex search reads it.
                values["metaphone"] = ""
            update("people", values, {"gedcom": tree(), "personID": pid})
            add_places(*(values[f"{e}place"] for e in PERSON_EVENTS))
            audit("edit person", "people", pid, p, get_person(pid))
            sync_family_flags(pid)
            flash("Saved.")
            warn_dates(*(values[f"{e}date"] for e in PERSON_EVENTS))
            living_warnings(values)
            return redirect(url_for("edit.person", pid=pid))
        values = {**p, **values}
    from .edit_events import event_types  # these modules import this one
    from .edit_media import collections
    r = rights(p)
    fam = relatives(pid)
    return render_template("edit_person.html", p=p, v=values, errors=errors, events=PERSON_EVENTS, r=r, **fam,
                           **(person_detail(p, r, fam) if r["both"] else {}),
                           event_types=event_types("I"), collections=collections())


@bp.route("/person/new", methods=["GET", "POST"])
def new_person():
    """Add a person, optionally as a child of a family, as the spouse of someone (which creates the family), or
    as someone's father or mother (role=husband/wife: fills that place in their parents' family, creating it if
    they have none). return_to=<person ID> comes back to that page afterwards, for adding several relatives."""
    require("allow_add")
    args = request.values.to_dict()
    kind, _, target = args.pop("relation", "").partition(":")  # the edit page's quick add: husband:<pid>, child_of:<fid>...
    if kind in ("husband", "wife"):
        args.update(parent_of=target, role=kind)
    elif kind in ("child_of", "spouse_of"):
        args[kind] = target
    elif kind:
        abort(400)
    fam = editable(get_family(args["child_of"])) if args.get("child_of") else None
    spouse = editable(get_person(args["spouse_of"])) if args.get("spouse_of") else None
    child = editable(get_person(args["parent_of"])) if args.get("parent_of") else None
    role = args.get("role")
    parents = None
    if child:
        if role not in ("husband", "wife"):
            abort(400)
        families = query("SELECT f.* FROM children c JOIN families f ON f.gedcom = c.gedcom"
                         " AND f.familyID = c.familyID WHERE c.gedcom = %s AND c.personID = %s"
                         " ORDER BY c.parentorder, c.ID", (tree(), child["personID"]))
        if families and families[0][role]:
            abort(400)  # they already have one; the link to get here isn't shown then
        parents = families[0] if families else None
    back = get_person(args.get("return_to", ""))
    sex = "U"  # a guess from the relationship, to prefill the form and for the quick add, which doesn't ask
    if child:
        sex = "M" if role == "husband" else "F"
    elif spouse:
        sex = {"M": "F", "F": "M"}.get(spouse["sex"], "U")
    errors = []
    if request.method == "POST":
        values, errors = person_form(request.form)
        if not request.form.get("sex"):  # the quick add leaves it to the relationship
            values["sex"] = sex
        if not errors:
            values.update(changedate=now(), changedby=g.user["username"],
                          famc=fam["familyID"] if fam else "")
            pid = insert_with_new_id("people", "personID", "I", values)
            add_places(*(values[f"{e}place"] for e in PERSON_EVENTS))
            new = get_person(pid)
            audit("add person", "people", pid, None, new)
            if fam:
                link_child(fam, pid)
            if spouse:
                create_family(spouse, new)
            if child:
                add_parent(child, parents, role, new)
            flash(f"Added {pname(new)}.")
            warn_dates(*(values[f"{e}date"] for e in PERSON_EVENTS))
            living_warnings(values)
            return redirect(person_page(back["personID"], "family") if back else url_for("main.person", pid=pid))
    else:
        father = get_person(fam["husband"]) if fam and fam["husband"] else None
        values = {"living": 1, "sex": sex, "lastname": father["lastname"] if father else ""}
        if child:
            values["lastname"] = child["lastname"] if role == "husband" else ""
    return render_template("edit_person.html", p=None, v=values, errors=errors, events=PERSON_EVENTS,
                           fam=fam, spouse=spouse, child=child, role=role, back=back,
                           back_url=person_page(back["personID"], "family") if back else None)


def add_parent(child, fam, role, parent):
    """Put a new person in the father's or mother's place of child's parents' family, or a new one if none."""
    order = {"husborder" if role == "husband" else "wifeorder": 1}  # a new person: this is their first family
    if fam:
        update("families", {role: parent["personID"], **order, "changedate": now(), "changedby": g.user["username"]},
               {"gedcom": tree(), "familyID": fam["familyID"]})
        audit("edit family", "families", fam["familyID"], fam, get_family(fam["familyID"]))
    else:
        fid = insert_with_new_id("families", "familyID", "F", {
            role: parent["personID"], **order, "changedate": now(), "changedby": g.user["username"]})
        audit("add family", "families", fid, None, get_family(fid))
        fam = get_family(fid)
        link_child(fam, child["personID"])
    sync_family_flags(parent["personID"])
    refresh_haskids(parent["personID"])


def refresh_haskids(pid):
    """TNG's updateHasKids: clear a person's "has children" marker when none of their families has children."""
    if not pid:
        return
    n = one("SELECT COUNT(*) AS n FROM children c JOIN families f ON f.gedcom = c.gedcom"
            " AND f.familyID = c.familyID WHERE f.gedcom = %s AND (f.husband = %s OR f.wife = %s)",
            (tree(), pid, pid))["n"]
    execute("UPDATE children SET haskids = %s WHERE gedcom = %s AND personID = %s", (int(n > 0), tree(), pid))


OWNED = (("events", "persfamID"), ("citations", "persfamID"), ("notelinks", "persfamID"),
         ("medialinks", "personID"), ("branchlinks", "persfamID"))


def owned_links(pid):
    """Rows that belong to a person or family (events, sources, notes, media links), keyed by table."""
    t = tree()
    links = {table: query(f"SELECT * FROM {table} WHERE gedcom = %s AND {col} = %s", (t, pid)) for table, col in OWNED}
    ids = [n["xnoteID"] for n in links["notelinks"]]
    # A note is only deleted when this is its sole link, like TNG's deleteNote.
    links["xnotes"] = query(
        f"SELECT x.* FROM xnotes x WHERE x.ID IN ({','.join(['%s'] * len(ids))})"
        " AND (SELECT COUNT(*) FROM notelinks nl WHERE nl.xnoteID = x.ID) = 1", ids) if ids else []
    return links


def delete_owned(pid, links):
    for table, col in OWNED:
        execute(f"DELETE FROM {table} WHERE gedcom = %s AND {col} = %s", (tree(), pid))
    for x in links["xnotes"]:
        execute("DELETE FROM xnotes WHERE ID = %s", (x["ID"],))


def person_links(pid):
    """Every row that refers to this person, keyed by table (shown before deleting, kept in the audit for undo)."""
    t = tree()
    return {
        "children": query("SELECT * FROM children WHERE gedcom = %s AND personID = %s", (t, pid)),
        "families": query("SELECT * FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)", (t, pid, pid)),
        **owned_links(pid),
    }


def family_is_empty(fid):
    t = tree()
    return not any(one(f"SELECT 1 AS x FROM {table} WHERE gedcom = %s AND {col} = %s LIMIT 1", (t, fid))
                   for table, col in (("children", "familyID"), ("events", "persfamID"),
                                      ("citations", "persfamID"), ("notelinks", "persfamID"),
                                      ("medialinks", "personID")))


def unlink_users(pids):
    """Users linked to people who are being deleted ("This is me") lose the link, and their requests to be them."""
    marks = ",".join(["%s"] * len(pids))
    execute(f"UPDATE users SET personID = '', mygedcom = '' WHERE mygedcom = %s AND personID IN ({marks})",
            (tree(), *pids))
    execute(f"DELETE FROM app_link_requests WHERE status = 'pending' AND personID IN ({marks})", pids)


@bp.route("/person/<pid>/delete", methods=["GET", "POST"])
def delete_person(pid):
    """Port of TNG's deletePersonPlus. Unlike TNG, a family left with nothing in it (no children, marriage
    details, events, notes, sources or media) is removed too, instead of lingering as an empty record."""
    require("allow_delete")
    p = editable(get_person(pid))
    links = person_links(pid)
    if request.method == "GET":
        return render_template("delete_person.html", p=p, links=links)

    t = tree()
    faces = query("SELECT * FROM app_face_tags WHERE gedcom = %s AND personID = %s", (t, pid))
    execute("DELETE FROM app_face_tags WHERE gedcom = %s AND personID = %s", (t, pid))
    comments = query("SELECT * FROM app_comments WHERE gedcom = %s AND kind = 'person' AND target = %s", (t, pid))
    execute("DELETE FROM app_comments WHERE gedcom = %s AND kind = 'person' AND target = %s", (t, pid))
    execute("DELETE FROM app_watches WHERE gedcom = %s AND personID = %s", (t, pid))
    census = query("SELECT * FROM app_census_links WHERE gedcom = %s AND personID = %s", (t, pid))
    execute("DELETE FROM app_census_links WHERE gedcom = %s AND personID = %s", (t, pid))
    execute("DELETE FROM people WHERE gedcom = %s AND personID = %s", (t, pid))
    execute("DELETE FROM children WHERE gedcom = %s AND personID = %s", (t, pid))
    unlink_users([pid])
    delete_owned(pid, links)
    execute("UPDATE families SET husband = '', husborder = 0 WHERE gedcom = %s AND husband = %s", (t, pid))
    execute("UPDATE families SET wife = '', wifeorder = 0 WHERE gedcom = %s AND wife = %s", (t, pid))

    removed_families = []
    for f in links["families"]:
        other = f["wife"] if f["husband"] == pid else f["husband"]
        if not any(f[k] for k in ("marrdate", "marrplace", "divdate", "divplace")) and family_is_empty(f["familyID"]):
            execute("DELETE FROM families WHERE gedcom = %s AND familyID = %s", (t, f["familyID"]))
            removed_families.append(f["familyID"])
        else:
            o = get_person(other) if other else None
            update("families", {"living": int(bool(o and o["living"])), "private": int(bool(o and o["private"]))},
                   {"gedcom": t, "familyID": f["familyID"]})
        refresh_haskids(other)
    for c in links["children"]:  # parents of the deleted person may now have no children
        f = get_family(c["familyID"])
        if f:
            refresh_haskids(f["husband"])
            refresh_haskids(f["wife"])

    audit("delete person", "people", pid, {"people": p, **links, "app_face_tags": faces, "app_comments": comments,
                                                   "app_census_links": census,
                                                   "removed_families": removed_families}, None)
    flash(f"Deleted {pname(p)} ({pid}).")
    parents = [c["familyID"] for c in links["children"]]
    return redirect(url_for("main.family", fid=parents[0]) if parents else url_for("main.home"))

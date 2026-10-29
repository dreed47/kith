"""Family editing, ported from TNG's admin_updatefamily / admin_addfamily and its child link actions."""
from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from .dates import convert_date, standard as standard_date
from .db import execute, query, update
from .edit import (DATE_MAX, PLACE_MAX, add_places, audit, bp, can_edit_anything, delete_owned, editable, get_family,
                   get_person, insert_with_new_id, link_child, now, owned_links, refresh_haskids, require,
                   set_famc, unlink_child, warn_dates)
from .placenames import standard
from .privacy import display_name as pname
from .views import child_order, find_people, one, tree

FAMILY_EVENTS = {"marr": "Marriage", "div": "Divorce"}


def spouse_order(col, pid, fid=""):
    """This family's position in the spouse's list of marriages: after the ones they already have."""
    return one(f"SELECT COUNT(*) AS n FROM families WHERE gedcom = %s AND {col} = %s AND familyID != %s",
               (tree(), pid, fid))["n"] + 1


def family_form(form, fid=""):
    v, errors, people = {}, [], {}
    for col, label in (("husband", "Husband"), ("wife", "Wife")):
        v[col] = form.get(col, "").strip().upper()
        if v[col]:
            people[col] = get_person(v[col])
            if not people[col]:
                errors.append(f"{label}: no person with ID {v[col]}.")
    if not (v["husband"] or v["wife"]):
        errors.append("Choose at least one spouse.")
    elif v["husband"] == v["wife"]:
        errors.append("Husband and wife can't be the same person.")
    if fid and one("SELECT 1 AS x FROM children WHERE gedcom = %s AND familyID = %s AND personID IN (%s, %s)",
                   (tree(), fid, v["husband"], v["wife"])):
        errors.append("A child of this family can't also be one of its spouses.")
    for ev, label in FAMILY_EVENTS.items():
        date, place = standard_date(form.get(f"{ev}date", "")), standard(form.get(f"{ev}place", ""))
        if len(date) > DATE_MAX:
            errors.append(f"{label} date is longer than {DATE_MAX} characters.")
        if len(place) > PLACE_MAX:
            errors.append(f"{label} place is longer than {PLACE_MAX} characters.")
        v[f"{ev}date"], v[f"{ev}datetr"], v[f"{ev}place"] = date, convert_date(date), place
    v["marrtype"] = form.get("marrtype", "").strip()
    if len(v["marrtype"]) > 90:
        errors.append("Marriage type is longer than 90 characters.")
    v["living"] = int(form.get("living") == "1")
    v["private"] = int(form.get("private") == "1")
    return v, errors, people


def children_of(fid):
    return query("SELECT c.ordernum, p.* FROM children c JOIN people p ON p.gedcom = c.gedcom"
                 f" AND p.personID = c.personID WHERE c.gedcom = %s AND c.familyID = %s ORDER BY {child_order()}, c.ID",
                 (tree(), fid))


@bp.get("/find")
def find():
    """Name lookup for the editors' person pickers."""
    can_edit_anything()
    return jsonify(find_people(request.args.get("q", "")))


@bp.route("/family/<fid>", methods=["GET", "POST"])
def family(fid):
    require("allow_edit")
    f = editable(get_family(fid))
    values, errors, people = f, [], {}
    if request.method == "POST":
        values, errors, people = family_form(request.form, fid)
        if request.form.get("changedate") != str(f["changedate"]):
            errors.insert(0, "Someone else saved this family while you were editing. "
                             "Open it again to see their changes, then redo yours.")
        if not errors:
            for col, order in (("husband", "husborder"), ("wife", "wifeorder")):
                if values[col] != f[col]:
                    values[order] = spouse_order(col, values[col], fid) if values[col] else 0
            values.update(changedate=now(), changedby=g.user["username"], edituser="", edittime=0)
            update("families", values, {"gedcom": tree(), "familyID": fid})
            add_places(values["marrplace"], values["divplace"])
            for pid in {f["husband"], f["wife"], values["husband"], values["wife"]}:
                refresh_haskids(pid)  # spouses joining or leaving gain or lose these children
            audit("edit family", "families", fid, f, get_family(fid))
            flash("Saved.")
            warn_dates(values["marrdate"], values["divdate"])
            return redirect(url_for("main.family", fid=fid))
        values = {**f, **values}
    return render_template("edit_family.html", f=f, v=values, errors=errors, people=people or {
        "husband": get_person(f["husband"]) if f["husband"] else None,
        "wife": get_person(f["wife"]) if f["wife"] else None}, children=children_of(fid), events=FAMILY_EVENTS)


@bp.route("/family/new", methods=["GET", "POST"])
def new_family():
    """New family from existing people: ?husband= / ?wife= prefill a spouse, ?child= links a child (add parents)."""
    require("allow_add")
    child = editable(get_person(request.values["child"])) if request.values.get("child") else None
    errors, people = [], {}
    if request.method == "POST":
        values, errors, people = family_form(request.form)
        if child and child["personID"] in (values["husband"], values["wife"]):
            errors.append("The child can't also be a parent in the same family.")
        if not errors:
            values.update(husborder=spouse_order("husband", values["husband"]) if values["husband"] else 0,
                          wifeorder=spouse_order("wife", values["wife"]) if values["wife"] else 0,
                          changedate=now(), changedby=g.user["username"])
            fid = insert_with_new_id("families", "familyID", "F", values)
            add_places(values["marrplace"], values["divplace"])
            audit("add family", "families", fid, None, get_family(fid))
            if child:
                link_child(get_family(fid), child["personID"])
            flash("Family added.")
            warn_dates(values["marrdate"], values["divdate"])
            return redirect(url_for("main.family", fid=fid))
    else:
        values = {"husband": request.args.get("husband", ""), "wife": request.args.get("wife", "")}
        people = {k: get_person(v) for k, v in values.items() if v}
        values["living"] = int(any(p["living"] for p in people.values() if p))
    return render_template("edit_family.html", f=None, v=values, errors=errors, people=people, child=child,
                           children=[], events=FAMILY_EVENTS)


@bp.post("/family/<fid>/children")
def family_children(fid):
    """Add an existing person as a child, remove a child, or move a child up/down."""
    f = editable(get_family(fid))
    action, pid = request.form.get("action"), request.form.get("personID", "").strip().upper()
    kids = [c["personID"] for c in children_of(fid)]
    if action == "add":
        require("allow_add")
        if not get_person(pid):
            flash(f"No person with ID {pid}.")
        elif pid in kids:
            flash(f"{pid} is already a child of this family.")
        elif pid in (f["husband"], f["wife"]):
            flash("A spouse can't also be a child of the same family.")
        else:
            link_child(f, pid)
            flash(f"Added {pname(get_person(pid))} as a child.")
    elif action in ("remove", "up", "down") and pid in kids:
        require("allow_edit")
        if action == "remove":
            unlink_child(fid, pid)
            flash(f"Removed {pname(get_person(pid))} from this family (the person still exists).")
        else:
            i = kids.index(pid)
            j = i - 1 if action == "up" else i + 1
            if 0 <= j < len(kids):
                kids[i], kids[j] = kids[j], kids[i]
                for n, kid in enumerate(kids, 1):  # renumber 1..n, which also repairs gaps and duplicates
                    execute("UPDATE children SET ordernum = %s WHERE gedcom = %s AND familyID = %s"
                            " AND personID = %s", (n, tree(), fid, kid))
                audit("reorder children", "children", fid, None, {"order": kids})
    else:
        abort(400)
    return redirect(url_for("edit.family", fid=fid) + "#children")


@bp.route("/family/<fid>/delete", methods=["GET", "POST"])
def delete_family(fid):
    """Port of TNG's family delete: the family, its child links and its own events/notes/sources/media links go;
    the people stay."""
    require("allow_delete")
    f = editable(get_family(fid))
    links = {"children": query("SELECT * FROM children WHERE gedcom = %s AND familyID = %s", (tree(), fid)),
             **owned_links(fid)}
    if request.method == "GET":
        return render_template("delete_family.html", f=f, links=links, husband=get_person(f["husband"]),
                               wife=get_person(f["wife"]))
    execute("DELETE FROM families WHERE gedcom = %s AND familyID = %s", (tree(), fid))
    execute("DELETE FROM children WHERE gedcom = %s AND familyID = %s", (tree(), fid))
    delete_owned(fid, links)
    for c in links["children"]:
        set_famc(c["personID"])
    for pid in (f["husband"], f["wife"]):
        refresh_haskids(pid)
    audit("delete family", "families", fid, {"families": f, **links}, None)
    flash(f"Deleted family {fid}. The people in it were kept.")
    survivor = f["husband"] or f["wife"]
    return redirect(url_for("main.person", pid=survivor) if survivor else url_for("main.home"))

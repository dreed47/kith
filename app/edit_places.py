"""Editing places and cemeteries. Renaming a place changes it on every record that uses it, the way TNG's place
edit does when told to; renaming it to a place that already exists merges the two. The tidy page finds names that
are the same place written differently and puts them in the house style ("Clarion Cnty, PA")."""
from collections import defaultdict

from flask import abort, flash, g, redirect, render_template, request, url_for

from .db import execute, insert, query, update
from .edit import PLACE_MAX, audit, bp, now, require
from .placenames import standard
from .views import one, tree

# Every column that holds a place name (TNG stores the name itself on each record, not a link).
PLACE_COLUMNS = (*(("people", c) for c in ("birthplace", "altbirthplace", "deathplace", "burialplace",
                                               "baptplace", "confplace", "initplace", "endlplace")),
                 ("families", "marrplace"), ("families", "divplace"), ("families", "sealplace"),
                 ("children", "sealplace"), ("events", "eventplace"), ("media", "placetaken"))
CEMETERY_TEXT = {"cemname": 64, "place": PLACE_MAX, "city": 64, "county": 64, "state": 64, "country": 64,
                 "maplink": 255}
LABELS = {"cemname": "Name", "place": "Place (as written on burials)", "city": "City or township", "county": "County",
          "state": "State", "country": "Country", "maplink": "Map link"}


def coordinates(form, errors):
    """Latitude and longitude from a form: blank, or numbers in range."""
    out = {}
    for key, limit in (("latitude", 90), ("longitude", 180)):
        value = form.get(key, "").strip()
        try:
            if value and abs(float(value)) > limit:
                raise ValueError
        except ValueError:
            errors.append(f"{key.capitalize()} should be a number between -{limit} and {limit}, or empty.")
        out[key] = value
    return out


def uses(name):
    """How many records use a place name: {(table, column): n}, only those above zero."""
    counts = {(t, c): one(f"SELECT COUNT(*) AS n FROM {t} WHERE gedcom = %s AND {c} = %s", (tree(), name))["n"]
              for t, c in PLACE_COLUMNS}
    counts["cemeteries", "place"] = one("SELECT COUNT(*) AS n FROM cemeteries WHERE place = %s", (name,))["n"]
    return {k: n for k, n in counts.items() if n}


def get_place(name):
    return one("SELECT * FROM places WHERE gedcom = %s AND place = %s", (tree(), name))


def move_place(old, new):
    return move_places({old: new})


def move_places(renames):
    """Rename places ({old: new}) on every record and in the places list, merging into a new name that's already
    there (blank coordinates and notes on it are filled from the old one). One UPDATE per column for the lot: one
    per name took 40 seconds for a full tidy. Returns how many records changed."""
    case = "CASE {c} " + " ".join(["WHEN %s THEN %s"] * len(renames)) + " END"
    pairs, olds, marks = [x for kv in renames.items() for x in kv], list(renames), ", ".join(["%s"] * len(renames))
    n = sum(execute(f"UPDATE {t} SET {c} = {case.format(c=c)} WHERE gedcom = %s AND {c} IN ({marks})",
                    [*pairs, tree(), *olds])[0] for t, c in PLACE_COLUMNS)
    n += execute(f"UPDATE cemeteries SET place = {case.format(c='place')} WHERE place IN ({marks})",
                 [*pairs, *olds])[0]
    for old, new in renames.items():
        row, target = get_place(old), get_place(new)
        if row and target and target["ID"] != row["ID"]:
            fill = {k: row[k] for k in ("latitude", "longitude", "notes") if row[k] and not target[k]}
            if fill:
                update("places", fill, {"ID": target["ID"]})
            execute("DELETE FROM places WHERE ID = %s", (row["ID"],))
        elif row:  # no other row, or the same one (the names differ only in case or trailing spaces)
            update("places", {"place": new}, {"ID": row["ID"]})
    return n


@bp.route("/place", methods=["GET", "POST"])
def place():
    require("allow_edit")
    old = request.values.get("name", "")
    row, used = get_place(old), uses(old)
    if not row and not used:
        abort(404)
    v, errors = row or {"place": old}, []
    if request.method == "POST":
        new = standard(request.form.get("place", ""))
        if not new:
            errors.append("Enter the place name.")
        if len(new) > PLACE_MAX:
            errors.append(f"The place name is longer than {PLACE_MAX} characters.")
        v = {"place": new, "notes": request.form.get("notes", "").strip(), **coordinates(request.form, errors)}
        if not errors:
            if new != old:
                move_place(old, new)
            fields, target = {**v, "changedate": now(), "changedby": g.user["username"]}, get_place(new)
            if target:
                update("places", fields, {"ID": target["ID"]})
            else:
                insert("places", {**fields, "gedcom": tree()})
            execute("DELETE FROM app_geocode WHERE gedcom = %s AND place IN (%s, %s)", (tree(), old, new))  # checked
            audit("rename place" if new != old else "edit place", "places", new,
                  {"place": row, "renamed from": old, "records changed": sum(used.values())} if new != old else row,
                  get_place(new))
            flash(f"Renamed on {sum(used.values())} record(s)." if new != old else "Saved.")
            return redirect(url_for("places.place", name=new))
    return render_template("edit_place.html", old=old, v=v, errors=errors, used=sum(used.values()))


def place_counts():
    """{exact place name: records using it} for every name on a record or in the places list."""
    union = " UNION ALL ".join(f"SELECT {c} AS place FROM {t} WHERE gedcom = %(t)s" for t, c in PLACE_COLUMNS)
    counts = {r["place"]: r["n"] for r in query(
        f"SELECT place, COUNT(*) AS n FROM ({union} UNION ALL SELECT place FROM cemeteries) x"
        " WHERE place <> '' GROUP BY BINARY place", {"t": tree()})}
    for r in query("SELECT place FROM places WHERE gedcom = %s AND place <> ''", (tree(),)):
        counts.setdefault(r["place"], 0)
    return counts


def tidy_rows(counts):
    """Names that look like the same place, and names alone that aren't in the house style:
    [{"names": [(name, records)], "to": suggested name}], groups first, most used first."""
    groups = defaultdict(list)
    for name, n in counts.items():
        groups[standard(name).lower()].append((name, n))
    rows = []
    for names in groups.values():
        names.sort(key=lambda x: -x[1])
        to = standard(names[0][0])
        if len(names) > 1 or names[0][0] != to:
            rows.append({"names": names, "to": to})
    rows.sort(key=lambda r: (len(r["names"]) == 1, -sum(n for _, n in r["names"]), r["to"]))
    return rows


@bp.route("/places/tidy", methods=["GET", "POST"])
def tidy_places():
    require("allow_edit")
    if request.method == "POST":
        renames = {}
        for i in request.form.getlist("apply"):
            to = request.form.get(f"to{i}", "").strip()
            if not to or len(to) > PLACE_MAX:
                flash(f"Skipped a name that was blank or longer than {PLACE_MAX} characters.")
                continue
            renames.update({old: to for old in request.form.getlist(f"from{i}") if old != to})
        if renames:
            counts, before = place_counts(), {old: get_place(old) for old in renames}
            move_places(renames)
            for old, to in renames.items():
                audit("rename place", "places", to, {"place": before[old], "renamed from": old,
                                                         "records changed": counts.get(old, 0)}, get_place(to))
        deleted = 0
        if g.user["allow_delete"] == 1:
            for name in request.form.getlist("delete"):
                row = get_place(name)
                # still unused (something may have started using it since the page loaded); a blank name is no place
                if row and (not name.strip() or not uses(name)):
                    execute("DELETE FROM places WHERE ID = %s", (row["ID"],))
                    audit("delete place", "places", name, row, None)
                    deleted += 1
        flash(f"Renamed {len(renames)} place name(s), deleted {deleted} unused one(s).")
        return redirect(url_for("edit.tidy_places"))
    counts = place_counts()
    unused = query("SELECT * FROM places WHERE gedcom = %s ORDER BY place", (tree(),))
    used = {name.rstrip().lower() for name, n in counts.items() if n}  # the database ignores case and trailing spaces
    unused = [r for r in unused if r["place"].rstrip().lower() not in used]
    return render_template("tidy_places.html", rows=tidy_rows(counts), unused=unused)


def get_cemetery(cid):
    c = one("SELECT * FROM cemeteries WHERE cemeteryID = %s", (cid,))
    if not c:
        abort(404)
    return c


def cemetery_form(form):
    v, errors = {}, []
    for k, n in CEMETERY_TEXT.items():
        v[k] = standard(form.get(k, "")) if k == "place" else form.get(k, "").strip()
        if len(v[k]) > n:
            errors.append(f"{LABELS[k]} is longer than {n} characters.")
    if not v["cemname"]:
        errors.append("Enter the cemetery's name.")
    v["notes"] = form.get("notes", "").strip()
    v.update(coordinates(form, errors))
    return v, errors


@bp.route("/cemetery/new", methods=["GET", "POST"])
@bp.route("/cemetery/<int:cid>", methods=["GET", "POST"])
def cemetery(cid=None):
    require("allow_edit" if cid else "allow_add")
    c = get_cemetery(cid) if cid else None
    v, errors = c or {}, []
    if request.method == "POST":
        v, errors = cemetery_form(request.form)
        if not errors:
            if c:
                update("cemeteries", v, {"cemeteryID": cid})
            else:
                _, cid = insert("cemeteries", v)
            audit("edit cemetery" if c else "add cemetery", "cemeteries", str(cid), c, get_cemetery(cid))
            flash("Saved." if c else "Cemetery added.")
            return redirect(url_for("places.cemetery", cid=cid))
    return render_template("edit_cemetery.html", c=c, v=v, errors=errors, labels=LABELS)


@bp.route("/cemetery/<int:cid>/delete", methods=["GET", "POST"])
def delete_cemetery(cid):
    require("allow_delete")
    c = get_cemetery(cid)
    photos = one("SELECT COUNT(*) AS n FROM media WHERE cemeteryID = %s", (cid,))["n"]
    if photos:
        flash(f"{c['cemname']} has {photos} headstone photo(s) linked. Move or delete those first.")
        return redirect(url_for("places.cemetery", cid=cid))
    if request.method == "GET":
        return render_template("confirm_delete.html", title=f"Delete {c['cemname']}?", back=url_for(
            "places.cemetery", cid=cid), details=["Burials that name this place keep it; only the cemetery page goes."])
    execute("DELETE FROM cemeteries WHERE cemeteryID = %s", (cid,))
    audit("delete cemetery", "cemeteries", str(cid), c, None)
    flash(f"Deleted {c['cemname']}.")
    return redirect(url_for("places.cemeteries"))

"""Sources and citations (TNG's showsource/browsesources and its source and citation editing). A source is the
document or book (sources); a citation says where in it a fact about a person or family comes from
(citations: the page, a quote, a date and a note). A citation can be about the whole record or one fact,
keyed like notes (BIRT, MARR, a custom event's ID)."""
from flask import abort, flash, g, redirect, render_template, request, url_for

from .db import execute, insert, query, update
from .edit import audit, bp as edit_bp, insert_with_new_id, now, require
from .edit_events import FAMILY_FACTS, PERSON_FACTS, about_label, owner
from .edit_media import attach_scan
from .privacy import rights
from .views import LDS_NOTE_KEYS, bp, citation_media, one, people_by_id, tree

SOURCE_TEXT = {"title": 500, "shorttitle": 200, "author": 200, "publisher": 300, "callnum": 120}
SOURCE_LABELS = {"title": "Title", "shorttitle": "Short title", "author": "Author", "publisher": "Publisher / where",
                 "callnum": "Call number"}
CITATION_TEXT = {"page": 500, "citedate": 50}


def get_source(sid):
    s = one("SELECT * FROM sources WHERE gedcom = %s AND sourceID = %s", (tree(), sid))
    if not s:
        abort(404)
    return s


@bp.get("/sources")
def sources():
    rows = query("SELECT s.*, (SELECT COUNT(*) FROM citations c WHERE c.gedcom = s.gedcom"
                 " AND c.sourceID = s.sourceID) AS n FROM sources s WHERE s.gedcom = %s ORDER BY s.title",
                 (tree(),))
    return render_template("sources.html", sources=rows)


@bp.get("/sources/<sid>")
def source(sid):
    """A source and everyone citing it (people and families the user may see)."""
    s = get_source(sid)
    cites = [c for c in query("SELECT * FROM citations WHERE gedcom = %s AND sourceID = %s"
                              " ORDER BY persfamID, ordernum", (tree(), sid)) if c["eventID"] not in LDS_NOTE_KEYS]
    folks = people_by_id([c["persfamID"] for c in cites])
    fids = [c["persfamID"] for c in cites if c["persfamID"] not in folks]
    fams = {f["familyID"]: f for f in query(
        f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(fids))})",
        [tree(), *fids])} if fids else {}
    spouses = people_by_id([x for f in fams.values() for x in (f["husband"], f["wife"])])
    shown = []
    for c in cites:
        c["person"], c["family"] = folks.get(c["persfamID"]), fams.get(c["persfamID"])
        record = c["person"] or c["family"]
        if record and rights(record)["both"]:
            c["about"] = about_label(c["persfamID"], c["eventID"])
            shown.append(c)
    others = query("SELECT sourceID, title FROM sources WHERE gedcom = %s AND sourceID <> %s ORDER BY title",
                   (tree(), sid)) if g.user["allow_edit"] == 1 and g.user["allow_delete"] == 1 else []
    return render_template("source.html", s=s, cites=shown, hidden=len(cites) - len(shown), spouses=spouses,
                           others=others)


def source_form(form):
    v, errors = {}, []
    for k, n in SOURCE_TEXT.items():
        v[k] = form.get(k, "").strip()
        if len(v[k]) > n:
            errors.append(f"{SOURCE_LABELS[k]} is longer than {n} characters.")
    if not v["title"]:
        errors.append("Enter a title.")
    v["actualtext"] = form.get("actualtext", "").strip()
    v["comments"] = form.get("comments", "").strip()
    return v, errors


@edit_bp.route("/source/new", methods=["GET", "POST"])
@edit_bp.route("/source/<sid>", methods=["GET", "POST"])
def edit_source(sid=None):
    require("allow_edit" if sid else "allow_add")
    s = get_source(sid) if sid else None
    v, errors = s or {}, []
    if request.method == "POST":
        v, errors = source_form(request.form)
        if not errors:
            v.update(changedate=now(), changedby=g.user["username"])
            if s:
                update("sources", v, {"gedcom": tree(), "sourceID": sid})
            else:
                sid = insert_with_new_id("sources", "sourceID", "S", v)
            audit("edit source" if s else "add source", "sources", sid, s, get_source(sid))
            flash("Saved." if s else "Source added.")
            return redirect(request.form.get("back") or url_for("main.source", sid=sid))
    return render_template("edit_source.html", s=s, v=v, errors=errors, labels=SOURCE_LABELS,
                           back=request.values.get("back", ""))


@edit_bp.route("/source/<sid>/delete", methods=["GET", "POST"])
def delete_source(sid):
    require("allow_delete")
    s = get_source(sid)
    n = one("SELECT COUNT(*) AS n FROM citations WHERE gedcom = %s AND sourceID = %s", (tree(), sid))["n"]
    if n:
        flash(f"{n} citation(s) still use this source. Merge it into another source, or remove those first.")
        return redirect(url_for("main.source", sid=sid))
    if request.method == "GET":
        return render_template("confirm_delete.html", title=f"Delete the source {s['title']}?", details=[],
                               back=url_for("main.source", sid=sid))
    execute("DELETE FROM sources WHERE gedcom = %s AND sourceID = %s", (tree(), sid))
    audit("delete source", "sources", sid, s, None)
    flash("Source deleted.")
    return redirect(url_for("main.sources"))


@edit_bp.post("/source/<sid>/merge")
def merge_source(sid):
    """Fold another source (a duplicate) into this one: its citations move here, then it's deleted."""
    require("allow_edit")
    require("allow_delete")
    keep, dup = get_source(sid), get_source(request.form.get("other", ""))
    if keep["sourceID"] == dup["sourceID"]:
        abort(400)
    moved = query("SELECT * FROM citations WHERE gedcom = %s AND sourceID = %s", (tree(), dup["sourceID"]))
    execute("UPDATE citations SET sourceID = %s WHERE gedcom = %s AND sourceID = %s",
            (sid, tree(), dup["sourceID"]))
    execute("DELETE FROM sources WHERE gedcom = %s AND sourceID = %s", (tree(), dup["sourceID"]))
    audit("merge source", "sources", sid, {"merged": dup, "citations moved": moved}, keep)
    flash(f"Merged {dup['title']} into this source ({len(moved)} citation(s) moved).")
    return redirect(url_for("main.source", sid=sid))


def citation_form(form):
    v, errors = {"sourceID": form.get("sourceID", "")}, []
    if not one("SELECT 1 AS x FROM sources WHERE gedcom = %s AND sourceID = %s", (tree(), v["sourceID"])):
        errors.append("Choose a source.")
    for k, n in CITATION_TEXT.items():
        v[k] = form.get(k, "").strip()
        if len(v[k]) > n:
            errors.append(f"The {'page' if k == 'page' else 'date'} is longer than {n} characters.")
    v["citetext"], v["note"] = form.get("citetext", "").strip(), form.get("note", "").strip()
    return v, errors


def get_citation(cid):
    c = one("SELECT * FROM citations WHERE gedcom = %s AND citationID = %s", (tree(), cid))
    if not c:
        abort(404)
    return c


def all_sources():
    return query("SELECT sourceID, title FROM sources WHERE gedcom = %s ORDER BY title", (tree(),))


@edit_bp.route("/citation/new", methods=["GET", "POST"])
def new_citation():
    require("allow_add")
    kind, _, back = owner(request.values.get("for", ""))
    persfam_id, about = request.values["for"], request.values.get("about", "")
    if about and (about not in (PERSON_FACTS if kind == "I" else FAMILY_FACTS) and not about.isdigit()
                  or about_label(persfam_id, about) is None):
        abort(400)
    v, errors = {"sourceID": request.values.get("source", "")}, []
    if request.method == "POST":
        v, errors = citation_form(request.form)
        if not errors:
            order = one("SELECT COALESCE(MAX(ordernum), 0) AS n FROM citations WHERE gedcom = %s"
                        " AND persfamID = %s", (tree(), persfam_id))["n"] + 1
            _, cid = insert("citations", {**v, "gedcom": tree(), "persfamID": persfam_id, "eventID": about,
                                              "ordernum": order})
            audit("add citation", "citations", str(cid), None, get_citation(cid))
            flash("Source added.")
            return redirect(back)
    return render_template("edit_citation.html", c=None, v=v, errors=errors, sources=all_sources(),
                           owner_id=persfam_id, about=about, about_label=about_label(persfam_id, about), back=back)


@edit_bp.route("/citation/<int:cid>", methods=["GET", "POST"])
def citation(cid):
    require("allow_edit")
    c = get_citation(cid)
    _, _, back = owner(c["persfamID"])
    v, errors = c, []
    if request.method == "POST":
        v, errors = citation_form(request.form)
        if not errors:
            update("citations", v, {"citationID": cid})
            audit("edit citation", "citations", str(cid), c, get_citation(cid))
            flash("Saved.")
            return redirect(back)
    return render_template("edit_citation.html", c=c, v=v, errors=errors, sources=all_sources(),
                           owner_id=c["persfamID"], about=c["eventID"], scans=citation_media([cid]).get(cid, []),
                           about_label=about_label(c["persfamID"], c["eventID"]), back=back)


@edit_bp.post("/citation/<int:cid>/scans")
def citation_scans(cid):
    """Attach an existing photo or document (by its media number) to a citation, or detach one."""
    require("allow_edit")
    c = get_citation(cid)
    mid = request.form.get("mediaID", type=int)
    attached = one("SELECT * FROM app_citation_media WHERE citationID = %s AND mediaID = %s", (cid, mid))
    if request.form.get("action") == "add":
        if not one("SELECT 1 AS x FROM media WHERE gedcom = %s AND mediaID = %s", (tree(), mid)):
            flash(f"No photo or document number {request.form.get('mediaID', '')}.")
        elif attached:
            flash("That one is already attached.")
        else:
            attach_scan(cid, c["persfamID"], mid)
            flash("Attached.")
    elif request.form.get("action") == "remove" and attached:
        execute("DELETE FROM app_citation_media WHERE citationID = %s AND mediaID = %s", (cid, mid))
        audit("detach scan", "app_citation_media", f"{cid}/{mid}", attached, None)
        flash("Detached. The photo or document itself is kept.")
    else:
        abort(400)
    return redirect(url_for("edit.citation", cid=cid) + "#scans")


@edit_bp.route("/citation/<int:cid>/delete", methods=["GET", "POST"])
def delete_citation(cid):
    require("allow_delete")
    c = get_citation(cid)
    _, _, back = owner(c["persfamID"])
    if request.method == "GET":
        return render_template("confirm_delete.html", title="Remove this source citation?", back=back,
                               details=[" · ".join(x for x in (c["page"], c["citetext"][:200]) if x)])
    execute("DELETE FROM citations WHERE citationID = %s", (cid,))
    audit("delete citation", "citations", str(cid), c, None)
    flash("Citation removed.")
    return redirect(back)

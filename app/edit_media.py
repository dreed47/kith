"""Media upload, editing, links and delete, modelled on TNG's admin_addmedia/updatemedia.

Files go in MEDIA_ROOT/<collection>/ like TNG's usecollfolder layout. Deleting moves files to
TRASH_ROOT (MEDIA_ROOT/.deleted by default) instead of erasing them, so a mistaken delete can be undone by hand.
"""
import datetime
import shutil
from pathlib import Path

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from PIL import UnidentifiedImageError
from werkzeug.utils import secure_filename

from .db import execute, insert, query, update
from .edit import add_places, audit, bp, editable, get_family, get_person, now, person_page, require, warn_dates
from .edit_events import TEXT_MAX
from .files import RASTER_EXTS, shrink
from .media import IMAGE_EXTS, folder, get_visible
from .placenames import standard
from .privacy import display_name as pname
from .views import one, tree

# No html/svg/js: files are served from our own site, so those could run scripts in a visitor's browser.
ALLOWED_EXTS = IMAGE_EXTS | {"tif", "tiff", "bmp", "pdf", "txt", "rtf", "doc", "docx", "odt", "xls", "xlsx",
                             "mp3", "m4a", "wav", "mp4", "mov", "m4v"}
THUMB_SIZE = (80, 100)  # TNG config: thumbmaxw, thumbmaxh
THUMB_PREFIX = "thumb_"
MEDIA_TEXT = {"description": 255, "datetaken": 50, "placetaken": 248, "plot": 255}


def collections():
    return [r["mediatypeID"] for r in query("SELECT mediatypeID FROM mediatypes ORDER BY ordernum, mediatypeID")]


def media_form(form):
    v, errors = {}, []
    for k, n in MEDIA_TEXT.items():
        v[k] = standard(form.get(k, "")) if k == "placetaken" else form.get(k, "").strip()
        if len(v[k]) > n:
            errors.append(f"{k} is longer than {n} characters.")
    v["notes"] = form.get("notes", "").strip()
    if len(v["notes"]) > TEXT_MAX:
        errors.append(f"notes is longer than {TEXT_MAX} characters.")
    v["alwayson"] = int(form.get("alwayson") == "1")
    v["cemeteryID"] = form.get("cemeteryID", type=int) or 0
    if v["cemeteryID"] and not one("SELECT 1 AS x FROM cemeteries WHERE cemeteryID = %s", (v["cemeteryID"],)):
        errors.append("Unknown cemetery.")
    v["linktocem"] = int(bool(v["cemeteryID"]))
    return v, errors


def unique_name(directory, filename):
    """filename, or filename-2, -3... if taken, so an upload never overwrites another file."""
    stem, ext = Path(filename).stem, Path(filename).suffix
    candidate, n = filename, 1
    while (directory / candidate).exists() or (directory / (THUMB_PREFIX + candidate)).exists():
        n += 1
        candidate = f"{stem}-{n}{ext}"
    return candidate


def make_thumbnail(source, target):
    """TNG-sized thumbnail, so TNG itself shows the upload too."""
    shrink(source, target, THUMB_SIZE, quality=80)


def save_upload(upload, collection):
    """Store an uploaded file in the collection's folder. Returns (path, thumbpath, error)."""
    name = secure_filename(upload.filename or "")
    ext = Path(name).suffix.lstrip(".").lower()
    name = f"{Path(name).stem[:150]}.{ext}"  # path and thumbpath columns hold 255 characters
    if not name or ext not in ALLOWED_EXTS:
        return None, None, f"That kind of file can't be uploaded. Allowed: {', '.join(sorted(ALLOWED_EXTS))}."
    directory = Path(current_app.config["MEDIA_ROOT"]) / collection
    directory.mkdir(parents=True, exist_ok=True)
    name = unique_name(directory, name)
    target = directory / name
    upload.save(target)
    thumb = ""
    if ext in RASTER_EXTS:
        try:
            thumb = f"{THUMB_PREFIX}{Path(name).stem}.jpg"
            make_thumbnail(target, directory / thumb)
        except (UnidentifiedImageError, OSError):
            target.unlink()
            return None, None, "That file isn't a readable image."
    return name, thumb, None


def link_media(mid, persfam_id):
    linktype = "F" if get_family(persfam_id) else "I"
    order = one("SELECT COUNT(*) AS n FROM medialinks WHERE gedcom = %s AND personID = %s",
                (tree(), persfam_id))["n"] + 1
    row = {"gedcom": tree(), "personID": persfam_id, "mediaID": mid, "linktype": linktype, "ordernum": order}
    insert("medialinks", row, ignore=True)
    audit("link media", "medialinks", f"{mid}/{persfam_id}", None, row)


def attach_scan(cid, persfam_id, mid):
    """Make a photo or document evidence for a citation. It's linked to the citation's person or family too, so it
    shows with their media and is hidden whenever they are."""
    order = one("SELECT COALESCE(MAX(ordernum), 0) AS n FROM app_citation_media WHERE citationID = %s", (cid,))["n"] + 1
    row = {"citationID": cid, "mediaID": mid, "ordernum": order}
    insert("app_citation_media", row, ignore=True)
    audit("attach scan", "app_citation_media", f"{cid}/{mid}", None, row)
    if not one("SELECT 1 AS x FROM medialinks WHERE gedcom = %s AND mediaID = %s AND personID = %s",
               (tree(), mid, persfam_id)):
        link_media(mid, persfam_id)


def owner_url(persfam_id):
    if p := get_person(persfam_id):
        editable(p)
        return person_page(persfam_id, "media")
    if f := get_family(persfam_id):
        editable(f)
        return url_for("main.family", fid=persfam_id)
    abort(404)


def get_media(mid):
    """The row as saved, for the edit history (pages check visibility with get_visible first)."""
    return one("SELECT * FROM media WHERE mediaID = %s", (mid,))


def cemeteries():
    return query("SELECT cemeteryID, cemname, city, state FROM cemeteries ORDER BY cemname")


@bp.route("/media/new", methods=["GET", "POST"])
def new_media():
    require("allow_add")
    persfam_id = request.values.get("for", "")
    back = owner_url(persfam_id)
    cid = request.values.get("citation", type=int)  # uploading a scan for this citation (of persfam_id's)
    if cid and not one("SELECT 1 AS x FROM citations WHERE gedcom = %s AND citationID = %s AND persfamID = %s",
                       (tree(), cid, persfam_id)):
        abort(400)
    if cid:
        back = url_for("edit.citation", cid=cid) + "#scans"
    v, errors = {"alwayson": 0}, []
    collection = request.values.get("collection", "documents" if cid else "photos")
    if request.method == "POST":
        v, errors = media_form(request.form)
        if collection not in collections():
            errors.append("Choose a collection.")
        upload = request.files.get("file")
        if not upload or not upload.filename:
            errors.append("Choose a file to upload.")
        if not errors:
            path, thumb, error = save_upload(upload, collection)
            if error:
                errors.append(error)
            else:
                v.update(gedcom=tree(), mediatypeID=collection, path=path, thumbpath=thumb, usecollfolder=1,
                         mediakey=f"{collection}/{path}", form=Path(path).suffix.lstrip(".").upper(),
                         changedate=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         changedby=g.user["username"])
                _, mid = insert("media", v)
                add_places(v["placetaken"])
                audit("add media", "media", str(mid), None, get_media(mid))
                link_media(mid, persfam_id)
                if cid:
                    attach_scan(cid, persfam_id, mid)
                flash(f"Uploaded {path}.")
                warn_dates(v["datetaken"])
                return redirect(back)
    return render_template("edit_media.html", m=None, v=v, errors=errors, collections=collections(),
                           collection=collection, cemeteries=cemeteries(), owner_id=persfam_id, back=back, links=[],
                           citation=cid)


def media_links(mid):
    links = query("SELECT * FROM medialinks WHERE gedcom = %s AND mediaID = %s ORDER BY linktype, personID",
                  (tree(), mid))
    for link in links:
        if link["linktype"] == "F":
            f = get_family(link["personID"])
            link["name"] = " & ".join(pname(get_person(x)) for x in (f["husband"], f["wife"]) if x) if f else ""
            link["url"] = url_for("main.family", fid=link["personID"])
        else:
            p = get_person(link["personID"])
            link["name"] = pname(p) if p else ""
            link["url"] = url_for("main.person", pid=link["personID"])
    return links


@bp.route("/media/<int:mid>", methods=["GET", "POST"])
def media(mid):
    require("allow_edit")
    m = get_visible(mid)  # media the editor may not see looks missing, as on the media pages
    v, errors = m, []
    if request.method == "POST":
        v, errors = media_form(request.form)
        if not errors:
            update("media", {**v, "changedate": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "changedby": g.user["username"]}, {"mediaID": mid})
            add_places(v["placetaken"])
            audit("edit media", "media", str(mid), m, get_media(mid))
            flash("Saved.")
            warn_dates(v["datetaken"])
            return redirect(url_for("media.show", media_id=mid))
        v = {**m, **v}
    return render_template("edit_media.html", m=m, v=v, errors=errors, collections=collections(),
                           collection=m["mediatypeID"], cemeteries=cemeteries(), links=media_links(mid),
                           back=url_for("media.show", media_id=mid))


@bp.post("/media/<int:mid>/links")
def media_link_action(mid):
    get_visible(mid)
    action, target = request.form.get("action"), request.form.get("personID", "").strip().upper()
    if action == "add":
        require("allow_add")
        if not (get_person(target) or get_family(target)):
            flash(f"No person or family with ID {target}.")
        elif one("SELECT 1 AS x FROM medialinks WHERE gedcom = %s AND mediaID = %s AND personID = %s",
                 (tree(), mid, target)):
            flash(f"Already linked to {target}.")
        else:
            link_media(mid, target)
            flash(f"Linked to {target}.")
    elif action == "remove":
        require("allow_edit")
        link = one("SELECT * FROM medialinks WHERE gedcom = %s AND mediaID = %s AND personID = %s",
                   (tree(), mid, target))
        if not link:
            abort(400)
        execute("DELETE FROM medialinks WHERE medialinkID = %s", (link["medialinkID"],))
        audit("unlink media", "medialinks", f"{mid}/{target}", link, None)
        flash(f"Unlinked from {target}. The media itself is kept.")
    else:
        abort(400)
    return redirect(url_for("edit.media", mid=mid) + "#links")


@bp.post("/media/<int:mid>/faces")
def add_face(mid):
    """Tag a person's face on a photo. The photo is linked to them too, so it shows on their page."""
    require("allow_edit")
    get_visible(mid)
    pid = (request.form.get("quick") or request.form.get("personID", "")).strip().upper()  # quick: a "Tag as" button
    try:
        box = {k: float(request.form[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, ValueError):
        abort(400)
    # Written so NaN fails too. A hair over 1 is rounding in the browser.
    if not (0 <= box["x"] and 0 <= box["y"] and box["w"] >= 0.01 and box["h"] >= 0.01
            and box["x"] + box["w"] <= 1.001 and box["y"] + box["h"] <= 1.001):
        abort(400)
    p = get_person(pid)
    if not p:
        flash(f"No person with ID {pid}.")
        return redirect(url_for("media.show", media_id=mid))
    row = {"mediaID": mid, "gedcom": tree(), "personID": pid, **box, "created_by": g.user["username"],
           "created_at": now()}
    _, tid = insert("app_face_tags", row)
    audit("tag face", "app_face_tags", str(tid), None, row)
    if not one("SELECT 1 AS x FROM medialinks WHERE gedcom = %s AND mediaID = %s AND personID = %s",
               (tree(), mid, pid)):
        link_media(mid, pid)
    flash(f"Tagged {pname(p)}.")
    return redirect(url_for("media.show", media_id=mid))


@bp.post("/media/<int:mid>/faces/<int:tid>/delete")
def remove_face(mid, tid):
    require("allow_edit")
    tag = one("SELECT * FROM app_face_tags WHERE id = %s AND mediaID = %s", (tid, mid))
    if not tag:
        abort(404)
    execute("DELETE FROM app_face_tags WHERE id = %s", (tid,))
    audit("untag face", "app_face_tags", str(tid), tag, None)
    flash("Tag removed. The photo is still linked to them; unlink it under Edit details and links if it shouldn't be.")
    return redirect(url_for("media.show", media_id=mid))


@bp.route("/media/<int:mid>/delete", methods=["GET", "POST"])
def delete_media(mid):
    require("allow_delete")
    m = get_visible(mid)
    links = query("SELECT * FROM medialinks WHERE mediaID = %s", (mid,))
    if request.method == "GET":
        details = [m["description"] or m["path"],
                   f"It is linked to {len(links)} record(s); those links go too." if links else "It isn't linked to anyone.",
                   "The file is moved to a .deleted folder rather than erased."]
        return render_template("confirm_delete.html", title="Delete this media?", details=details,
                               back=url_for("media.show", media_id=mid))
    faces = query("SELECT * FROM app_face_tags WHERE mediaID = %s", (mid,))
    execute("DELETE FROM media WHERE mediaID = %s", (mid,))
    execute("DELETE FROM medialinks WHERE mediaID = %s", (mid,))
    execute("DELETE FROM app_face_tags WHERE mediaID = %s", (mid,))
    comments = query("SELECT * FROM app_comments WHERE gedcom = %s AND kind = 'media' AND target = %s", (tree(), mid))
    execute("DELETE FROM app_comments WHERE gedcom = %s AND kind = 'media' AND target = %s", (tree(), mid))
    moved = []
    # Only move files no other media record still points at.
    if m["path"] and not m["abspath"] and not one("SELECT 1 AS x FROM media WHERE mediatypeID = %s AND path = %s",
                                                  (m["mediatypeID"], m["path"])):
        trash = Path(current_app.config["TRASH_ROOT"]) / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        for name in {m["path"], m["thumbpath"]} - {""}:
            source = folder(m) / name
            if source.is_file():
                (trash / Path(name).parent).mkdir(parents=True, exist_ok=True)
                shutil.move(source, trash / name)
                moved.append(str(trash / name))
    for cached in Path(current_app.config["THUMB_ROOT"]).glob(f"{mid}-*.jpg"):
        cached.unlink()  # our display thumbnails are derived, so just drop them
    audit("delete media", "media", str(mid), {"media": m, "medialinks": links, "app_face_tags": faces,
                                                     "app_comments": comments,
                                                     "moved_files": moved}, None)
    flash("Media deleted.")
    return redirect(url_for("media.browse"))


@bp.route("/media/arrange/<persfam_id>", methods=["GET", "POST"])
def arrange_media(persfam_id):
    """The order of a person's or family's photos and documents, and which picture heads their page (TNG's
    "default photo": one link per record marked defphoto = 1)."""
    require("allow_edit")
    back = owner_url(persfam_id)
    links = query("SELECT ml.*, m.description, m.path, m.mediatypeID, m.thumbpath, m.abspath, m.usecollfolder"
                  " FROM medialinks ml JOIN media m ON m.mediaID = ml.mediaID"
                  " WHERE ml.gedcom = %s AND ml.personID = %s ORDER BY ml.ordernum, ml.medialinkID",
                  (tree(), persfam_id))
    if not links:
        abort(404)
    if request.method == "POST":
        def position(link):
            v = request.form.get(f"pos{link['medialinkID']}", "")
            return (int(v) if v.isdigit() else 9999, link["ordernum"])  # blank or odd: keeps its place at the end
        main = request.form.get("main", "")
        for n, link in enumerate(sorted(links, key=position), 1):
            execute("UPDATE medialinks SET ordernum = %s, defphoto = %s WHERE medialinkID = %s",
                    (n, ("1" if str(link["medialinkID"]) == main else "") if main else link["defphoto"],
                     link["medialinkID"]))
        audit("arrange media", "medialinks", persfam_id,
              [{k: x[k] for k in ("medialinkID", "ordernum", "defphoto")} for x in links],
              [{k: x[k] for k in ("medialinkID", "ordernum", "defphoto")} for x in query(
                  "SELECT * FROM medialinks WHERE gedcom = %s AND personID = %s ORDER BY ordernum",
                  (tree(), persfam_id))])
        flash("Saved.")
        return redirect(back)
    return render_template("arrange_media.html", links=links, back=back, owner_id=persfam_id)

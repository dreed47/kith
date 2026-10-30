import os
import subprocess
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, g, render_template, request, send_file, send_from_directory
from PIL import Image, ImageOps, UnidentifiedImageError

from .db import query
from .files import IMAGE_EXTS, RASTER_EXTS, clean_html, is_image, is_url, shrink  # noqa: F401 (IMAGE_EXTS re-exported)
from .privacy import media_filter, rights
from .views import people_by_id, tree

bp = Blueprint("media", __name__, url_prefix="/media")
PER_PAGE = 48
# Faces tagged on a photo; the box is fractions (0-1) of the upright picture, so it fits any size it's shown at.


# Comments on people and photos (comments.py): target is a personID (kind "person") or a mediaID (kind "media"),
# so like face tags, media comments name media by ID.


def get_visible(media_id):
    cond, params = media_filter("m")
    rows = query(f"SELECT * FROM media m WHERE m.mediaID = %s AND {cond}", [media_id, *params])
    if not rows:
        abort(404)  # missing and hidden look the same, so hidden media can't be probed for
    return rows[0]


def face_tags(media_id):
    """Tagged faces with their person's row, left to right; people this user can't see aren't shown at all."""
    rows = query("SELECT t.id AS tag_id, t.x, t.y, t.w, t.h, p.* FROM app_face_tags t JOIN people p"
                 " ON p.gedcom = t.gedcom AND p.personID = t.personID WHERE t.mediaID = %s ORDER BY t.x", (media_id,))
    return [r for r in rows if rights(r)["living"]]


def folder(m):
    # TNG keeps each collection in a folder named after its type, unless usecollfolder is off.
    return Path(current_app.config["MEDIA_ROOT"]) / (m["mediatypeID"] if m["usecollfolder"] else "media")


@bp.get("/")
def browse():
    types = query("SELECT mediatypeID FROM mediatypes ORDER BY ordernum")
    mtype = request.args.get("type", "")
    page = max(request.args.get("page", 1, type=int), 1)
    cond, params = media_filter("m")
    where = f"{cond} AND m.abspath = 0 AND m.path <> '' AND m.path NOT LIKE 'http%%'"
    if mtype:
        where += " AND m.mediatypeID = %s"
        params.append(mtype)
    total = query(f"SELECT COUNT(*) AS n FROM media m WHERE {where}", params)[0]["n"]
    items = query(f"SELECT * FROM media m WHERE {where} ORDER BY m.description LIMIT %s OFFSET %s",
                  [*params, PER_PAGE, (page - 1) * PER_PAGE])
    return render_template("media_browse.html", items=items, types=types, mtype=mtype, page=page,
                           pages=max((total + PER_PAGE - 1) // PER_PAGE, 1))


def relatives(ids, fids):
    """Everyone in these families and in the families of these people (as parent or child): their parents, siblings,
    spouses and children, oldest first, for the "Tag as" buttons on a family photo. The people themselves are left out."""
    ids, fids = [i for i in ids if i], [f for f in fids if f]
    if ids:
        marks = ",".join(["%s"] * len(ids))
        fids += [r["familyID"] for r in query(
            f"SELECT familyID FROM families WHERE gedcom = %s AND (husband IN ({marks}) OR wife IN ({marks}))"
            f" UNION SELECT familyID FROM children WHERE gedcom = %s AND personID IN ({marks})",
            [tree(), *ids, *ids, tree(), *ids])]
    if not fids:
        return []
    marks = ",".join(["%s"] * len(fids))
    kin = query("SELECT * FROM people WHERE gedcom = %s AND personID IN ("
                f"SELECT husband FROM families WHERE gedcom = %s AND familyID IN ({marks})"
                f" UNION SELECT wife FROM families WHERE gedcom = %s AND familyID IN ({marks})"
                f" UNION SELECT personID FROM children WHERE gedcom = %s AND familyID IN ({marks}))"
                " ORDER BY IFNULL(birthdatetr, '0000-00-00') = '0000-00-00', birthdatetr",
                [tree(), tree(), *fids, tree(), *fids, tree(), *fids])
    return [k for k in kin if k["personID"] not in ids]


@bp.get("/<int:media_id>")
def show(media_id):
    m = get_visible(media_id)
    links = query(
        "SELECT ml.linktype, ml.personID AS linkid, p.*, f.familyID, f.husband, f.wife,"
        " f.living AS fliving, f.private AS fprivate FROM medialinks ml"
        " LEFT JOIN people p ON ml.linktype = 'I' AND p.personID = ml.personID AND p.gedcom = ml.gedcom"
        " LEFT JOIN families f ON ml.linktype = 'F' AND f.familyID = ml.personID AND f.gedcom = ml.gedcom"
        " WHERE ml.mediaID = %s ORDER BY ml.ordernum", (media_id,))
    people = [l for l in links if l["linktype"] == "I" and l["personID"]]
    families = [{**l, "living": l["fliving"], "private": l["fprivate"]} for l in links
                if l["linktype"] == "F" and l["familyID"]]
    spouses = {}
    if families:
        spouses = people_by_id([x for f in families for x in (f["husband"], f["wife"])])
    image = is_image(m["path"])
    from .comments import comments_for  # here, not at the top: comments imports this module
    from .edit_events import about_label  # edit imports this module
    cites = query("SELECT c.*, s.title FROM app_citation_media cm JOIN citations c ON c.citationID = cm.citationID"
                  " LEFT JOIN sources s ON s.gedcom = c.gedcom AND s.sourceID = c.sourceID WHERE cm.mediaID = %s"
                  " ORDER BY c.persfamID, c.ordernum", (media_id,))
    folks = people_by_id([c["persfamID"] for c in cites])
    fids = [c["persfamID"] for c in cites if c["persfamID"] not in folks]
    fams = {f["familyID"]: f for f in query(
        f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(fids))})",
        [tree(), *fids])} if fids else {}
    evidence = [{**c, "person": folks.get(c["persfamID"]), "about": about_label(c["persfamID"], c["eventID"])}
                for c in cites if (r := folks.get(c["persfamID"]) or fams.get(c["persfamID"])) and rights(r)["both"]]
    faces = face_tags(media_id) if image else []
    kin = []
    if image and g.user and g.user["allow_edit"] == 1:
        kin = relatives([p["personID"] for p in people] + [t["personID"] for t in faces],
                        [f["familyID"] for f in families])
    return render_template("media_show.html", m=m, people=people, families=families, spouses=spouses, kin=kin,
                           url=is_url(m), image=image, faces=faces,
                           comments=comments_for("media", media_id), evidence=evidence, body=clean_html(m["bodytext"]))


@bp.get("/<int:media_id>/file")
def file(media_id):
    m = get_visible(media_id)
    if is_url(m) or not m["path"]:
        abort(404)
    return send_from_directory(folder(m), m["path"])  # refuses paths escaping the folder


THUMB_SIZES = (300, 600, 1200)  # tile, person header, media page
ICON_KINDS = {"pdf": "PDF", "doc": "DOC", "docx": "DOC", "odt": "DOC", "rtf": "DOC", "txt": "TEXT",
              "xls": "SHEET", "xlsx": "SHEET", "mp3": "AUDIO", "m4a": "AUDIO", "wav": "AUDIO",
              "mp4": "VIDEO", "mov": "VIDEO", "m4v": "VIDEO"}
ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect x="28" y="14" width="64" height="92" rx="6"
fill="none" stroke="#8a8f98" stroke-width="4"/><text x="60" y="68" font-family="sans-serif" font-size="{size}"
font-weight="700" text-anchor="middle" fill="#8a8f98">{label}</text></svg>"""


def icon(label):
    """A neutral file icon (grey reads on light and dark themes) for things without a picture."""
    svg = ICON.format(label=label, size=18 if len(label) <= 5 else 13)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


# What can go wrong making a thumbnail: a file that isn't the picture or PDF it claims to be, or no pdftoppm.
PREVIEW_ERRORS = (UnidentifiedImageError, OSError, Image.DecompressionBombError, subprocess.SubprocessError)


def pdf_page(source, target, size):
    """First page of a PDF as a JPEG, like TNG's PDF thumbnails. Poppler's pdftoppm runs in its own process, so a
    broken or huge file can't hang or crash the site."""
    tmp = target.with_name(f"{target.name}.{os.getpid()}")  # pdftoppm adds .jpg
    subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-jpeg", "-scale-to", str(size), "--",
                    str(source), str(tmp)], check=True, capture_output=True, timeout=30)
    Path(f"{tmp}.jpg").replace(target)


def cached_thumb(m, size):
    """Path of a size-px JPEG made from the media's own file (a PDF's first page), regenerated when the file is
    newer."""
    source = folder(m) / m["path"]
    target = Path(current_app.config["THUMB_ROOT"]) / f"{m['mediaID']}-{size}.jpg"
    if not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == ".pdf":
            pdf_page(source, target, size)
        else:
            shrink(source, target, (size, size))
    return target


@bp.get("/<int:media_id>/thumb")
def thumb(media_id):
    """Picture for tiles and headers, made here at a sensible size (TNG's own thumbnails are 80x100 and
    often missing). Non-images, web links and missing files get an icon instead of a broken image."""
    m = get_visible(media_id)
    size = request.args.get("s", THUMB_SIZES[0], type=int)
    size = size if size in THUMB_SIZES else THUMB_SIZES[0]
    if is_url(m):
        return icon("LINK")
    ext = Path(m["path"] or "").suffix.lstrip(".").lower()
    if not m["path"] or not (folder(m) / m["path"]).is_file():
        return icon("MISSING")
    if ext in ICON_KINDS and ext != "pdf":
        return icon(ICON_KINDS[ext])
    try:  # images, PDFs, and files whose name lost its extension ("Bettyjpg") but are pictures anyway
        return send_file(cached_thumb(m, size), mimetype="image/jpeg", max_age=86400)
    except PREVIEW_ERRORS:
        return icon(ICON_KINDS.get(ext) or ("IMAGE" if ext in RASTER_EXTS else "FILE"))


FACE_SIZE = 160
PORTRAIT_SIZE = (400, 500)  # the person page's 4:5 picture box, at double size for sharp screens


def face_box(t, w, h, portrait):
    """(left, top, width, height) in pixels to crop around tag t on a w x h picture, kept inside it. Square and a
    bit bigger than the face for chart boxes; portrait is head and shoulders, the face in the upper part."""
    cx, cy = (t["x"] + t["w"] / 2) * w, (t["y"] + t["h"] / 2) * h
    if portrait:
        bh = min(t["h"] * h * 2.6, h, w * 5 / 4)
        bw = bh * 4 / 5
        top = cy - bh * 0.4
    else:
        bw = bh = min(max(t["w"] * w, t["h"] * h) * 1.3, w, h)
        top = cy - bh / 2
    return min(max(cx - bw / 2, 0), w - bw), min(max(top, 0), h - bh), bw, bh


@bp.get("/<int:media_id>/face/<int:tag_id>")
def face(media_id, tag_id):
    """A crop around a tagged face: square for chart boxes, or ?shape=portrait for the person page's picture.
    Cached like thumbnails; a new tag is a new ID."""
    portrait = request.args.get("shape") == "portrait"
    m = get_visible(media_id)
    tags = query("SELECT * FROM app_face_tags WHERE id = %s AND mediaID = %s", (tag_id, media_id))
    if not tags or is_url(m) or not m["path"] or not (folder(m) / m["path"]).is_file():
        abort(404)
    t, source = tags[0], folder(m) / m["path"]
    target = Path(current_app.config["THUMB_ROOT"]) / f"{media_id}-face{tag_id}{'-portrait' if portrait else ''}.jpg"
    if not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
        try:
            with Image.open(source) as im:
                im = ImageOps.exif_transpose(im)  # tags were drawn on the upright picture
                left, top, bw, bh = face_box(t, *im.size, portrait)
                crop = im.crop((round(left), round(top), round(left + bw), round(top + bh)))
                crop.thumbnail(PORTRAIT_SIZE if portrait else (FACE_SIZE, FACE_SIZE))
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")  # never serve a half-written file
                crop.convert("RGB").save(tmp, "JPEG", quality=85)
                tmp.replace(target)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            abort(404)
    return send_file(target, mimetype="image/jpeg", max_age=86400)


@bp.cli.command("thumbs")
def make_thumbs():
    """Pre-make display thumbnails for every image (otherwise each is made the first time it's viewed)."""
    made = failed = 0
    for m in query("SELECT * FROM media WHERE gedcom = %s AND path <> ''", (current_app.config["TREE"],)):
        ext = Path(m["path"]).suffix.lstrip(".").lower()
        if is_url(m) or ext in ICON_KINDS and ext != "pdf" or not (folder(m) / m["path"]).is_file():
            continue
        for size in THUMB_SIZES[:2]:
            try:
                cached_thumb(m, size)
                made += 1
            except PREVIEW_ERRORS:
                failed += 1
    print(f"{made} thumbnails ready, {failed} images couldn't be read")

import html as htmllib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.db import insert
from conftest import client_for, make, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def png(size=(400, 300), color="red"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    buf.seek(0)
    return buf


def upload(client, owner, filename="photo.png", data=None, **fields):
    return client.post("/edit/media/new", content_type="multipart/form-data", data={
        "csrf": "t", "for": owner, "collection": "photos", "description": "Upload test",
        "file": (data if data is not None else png(), filename), **fields})


def newest_media(app):
    return sql(app, "SELECT * FROM media ORDER BY mediaID DESC LIMIT 1")[0]


def test_upload_photo_creates_file_thumb_record_and_link(app, admin, media_root):
    ids = make(app, ("A", "M", 0))
    assert 'enctype="multipart/form-data"' in text(admin.get(f"/edit/media/new?for={ids['A']}"))
    r = upload(admin, ids["A"], "Grandpa's Farm.png", datetaken="abt 1920", placetaken="Farmtown, Testshire",
               notes="On the porch", alwayson="1")
    assert r.status_code == 302 and r.location.endswith(f"/person/{ids['A']}")
    m = newest_media(app)
    assert (m["mediatypeID"], m["path"], m["thumbpath"], m["form"]) == ("photos", "Grandpas_Farm.png", "thumb_Grandpas_Farm.jpg", "PNG")
    assert (m["mediakey"], m["usecollfolder"], m["alwayson"], m["changedby"]) == ("photos/Grandpas_Farm.png", 1, 1, "fx_admin")
    assert (media_root / "photos" / "Grandpas_Farm.png").is_file()
    with Image.open(media_root / "photos" / "thumb_Grandpas_Farm.jpg") as t:
        assert t.size[0] <= 80 and t.size[1] <= 100 and t.format == "JPEG"
    link = sql(app, "SELECT * FROM medialinks WHERE mediaID = %s", (m["mediaID"],))[0]
    assert (link["personID"], link["linktype"], link["ordernum"]) == (ids["A"], "I", 1)
    assert sql(app, "SELECT 1 FROM places WHERE place = 'Farmtown, Testshire'")
    assert "Upload test" in text(admin.get(f"/person/{ids['A']}"))  # shows in the person's media
    assert admin.get(f"/media/{m['mediaID']}/thumb").data[:2] == b"\xff\xd8"  # served JPEG thumbnail
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'add media' AND record = %s", (str(m["mediaID"]),))


def test_rotated_phone_photo_thumbnail_is_upright(app, admin, media_root):
    ids = make(app, ("A", "M", 0))
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # camera says: rotate 90° clockwise to view
    Image.new("RGB", (400, 200), "blue").save(buf, "JPEG", exif=exif)
    buf.seek(0)
    upload(admin, ids["A"], "phone.jpg", buf)
    with Image.open(media_root / "photos" / newest_media(app)["thumbpath"]) as t:
        assert t.size[1] > t.size[0]  # landscape pixels, portrait once turned


def test_same_name_never_overwrites(app, admin, media_root):
    ids = make(app, ("A", "M", 0))
    upload(admin, ids["A"], "dup.png", png(color="red"))
    upload(admin, ids["A"], "dup.png", png(color="green"))
    assert newest_media(app)["path"] == "dup-2.png"
    with Image.open(media_root / "photos" / "dup.png") as first:
        assert first.getpixel((0, 0)) == (255, 0, 0)


def test_document_upload_to_family_without_thumbnail(app, admin, media_root):
    ids = make(app, ("A", "M", 0), family=("A", None))
    r = upload(admin, ids["family"], "census.pdf", io.BytesIO(b"%PDF-1.4 test"), collection="documents")
    assert r.location.endswith(f"/family/{ids['family']}")
    m = newest_media(app)
    assert (m["mediatypeID"], m["path"], m["thumbpath"]) == ("documents", "census.pdf", "")
    assert sql(app, "SELECT linktype FROM medialinks WHERE mediaID = %s", (m["mediaID"],))[0]["linktype"] == "F"


def test_headstone_with_cemetery(app, admin):
    ids = make(app, ("A", "M", 0))
    cem = sql(app, "SELECT cemeteryID FROM cemeteries LIMIT 1")[0]["cemeteryID"]
    upload(admin, ids["A"], "stone.png", collection="headstones", cemeteryID=str(cem), plot="Row 3")
    m = newest_media(app)
    assert (m["cemeteryID"], m["linktocem"], m["plot"]) == (cem, 1, "Row 3")


@pytest.mark.parametrize("filename,data,fields,message", [
    ("evil.html", b"<script>alert(1)</script>", {}, "can't be uploaded"),
    ("vector.svg", b"<svg/>", {}, "can't be uploaded"),
    ("fake.jpg", b"not an image at all", {}, "isn't a readable image"),
    ("ok.png", None, {"collection": "nonsense"}, "Choose a collection"),
    ("ok.png", None, {"cemeteryID": "999999"}, "Unknown cemetery"),
    ("ok.png", None, {"description": "x" * 256}, "description is longer than 255"),
    ("ok.png", None, {"notes": "x" * 65001}, "notes is longer than 65000"),
])
def test_rejected_uploads(app, admin, media_root, filename, data, fields, message):
    ids = make(app, ("A", "M", 0))
    before = newest_media(app)["mediaID"]
    r = upload(admin, ids["A"], filename, io.BytesIO(data) if data is not None else None, **fields)
    assert r.status_code == 200 and message in text(r)
    assert newest_media(app)["mediaID"] == before
    assert not (media_root / "photos" / "fake.jpg").exists()  # a bad image isn't left on disk


def test_missing_file_and_owner(app, admin):
    ids = make(app, ("A", "M", 0))
    r = admin.post("/edit/media/new", data={"csrf": "t", "for": ids["A"], "collection": "photos"})
    assert "Choose a file to upload" in text(r)
    assert admin.get("/edit/media/new?for=XNOBODY").status_code == 404


def test_upload_size_limit(app, admin):
    ids = make(app, ("A", "M", 0))
    app.config["MAX_CONTENT_LENGTH"] = 1000
    try:
        assert upload(admin, ids["A"], "big.png", png((2000, 2000))).status_code == 413
    finally:
        app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


def test_long_filename_is_shortened(app, admin):
    ids = make(app, ("A", "M", 0))
    upload(admin, ids["A"], "x" * 300 + ".png")
    m = newest_media(app)
    assert len(m["path"]) <= 160 and len(m["thumbpath"]) <= 170


def own_pdf(app, name):
    """A media record of this test's own, so shared fixtures stay as other tests expect."""
    with app.app_context():
        return insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "letter.pdf",
                                    "usecollfolder": 1, "mediakey": f"X{name}", "description": name})[1]


def test_edit_media_details(app, admin):
    mid = own_pdf(app, "Edit me")
    html = text(admin.get(f"/edit/media/{mid}"))
    assert 'value="Edit me"' in html and "Delete media" in html and "Not linked to anyone" in html
    r = admin.post(f"/edit/media/{mid}", data={"csrf": "t", "description": "Renamed PDF", "datetaken": "someday"})
    assert r.location.endswith(f"/media/{mid}")
    m = sql(app, "SELECT * FROM media WHERE mediaID = %s", (mid,))[0]
    assert m["description"] == "Renamed PDF" and m["path"] == "letter.pdf"  # the file itself is untouched
    assert "\"someday\" isn't recognised" in text(admin.get(r.location))
    r = admin.post(f"/edit/media/{mid}", data={"csrf": "t", "description": "x" * 300})
    assert "description is longer" in text(r) and 'value="' + "x" * 300 in text(r)
    assert admin.get("/edit/media/99999999").status_code == 404


def test_link_and_unlink(app, admin):
    mid = own_pdf(app, "Link me")
    ids = make(app, ("A", "M", 0), family=("A", None))
    act = lambda action, pid: admin.post(f"/edit/media/{mid}/links", data={"csrf": "t", "action": action, "personID": pid})
    assert act("add", ids["A"].lower()).location.endswith(f"/edit/media/{mid}#links")
    act("add", ids["family"])
    html = text(admin.get(f"/edit/media/{mid}"))
    assert ids["A"] in html and ids["family"] in html and "A T" in html
    assert "Already linked" in text(admin.get(act("add", ids["A"]).location))
    assert "No person or family with ID XNOPE" in text(admin.get(act("add", "XNOPE").location))
    act("remove", ids["A"])
    assert [l["personID"] for l in sql(app, "SELECT personID FROM medialinks WHERE mediaID = %s", (mid,))] == [ids["family"]]
    assert act("remove", "XNOPE").status_code == 400
    assert act("explode", ids["A"]).status_code == 400
    assert admin.post("/edit/media/99999999/links", data={"csrf": "t", "action": "add"}).status_code == 404


def test_link_list_tolerates_missing_records(app, admin):
    mid = own_pdf(app, "Dangling links")
    with app.app_context():
        insert("medialinks", {"gedcom": "tree1", "personID": "XGONEP", "mediaID": mid, "linktype": "I"})
        insert("medialinks", {"gedcom": "tree1", "personID": "XGONEF", "mediaID": mid, "linktype": "F"})
    html = text(admin.get(f"/edit/media/{mid}"))
    assert "XGONEP" in html and "XGONEF" in html


def test_delete_moves_files_to_trash(app, admin, media_root):
    ids = make(app, ("A", "M", 0))
    upload(admin, ids["A"], "trashme.png")
    m = newest_media(app)
    admin.get(f"/media/{m['mediaID']}/thumb")  # makes a cached display thumbnail
    assert (media_root / ".thumbs" / f"{m['mediaID']}-300.jpg").is_file()
    html = text(admin.get(f"/edit/media/{m['mediaID']}/delete"))
    assert "linked to 1 record(s)" in html and "moved to a .deleted folder" in html
    r = admin.post(f"/edit/media/{m['mediaID']}/delete", data={"csrf": "t"})
    assert r.location.endswith("/media/")
    assert not sql(app, "SELECT 1 FROM media WHERE mediaID = %s", (m["mediaID"],))
    assert not sql(app, "SELECT 1 FROM medialinks WHERE mediaID = %s", (m["mediaID"],))
    assert not (media_root / "photos" / "trashme.png").exists()
    assert not list((media_root / ".thumbs").glob(f"{m['mediaID']}-*"))
    trashed = sorted(p.name for p in (media_root / ".deleted").rglob("*trashme*"))
    assert trashed == ["thumb_trashme.jpg", "trashme.png"]
    log = sql(app, "SELECT * FROM app_audit WHERE action = 'delete media' AND record = %s", (str(m["mediaID"]),))[0]
    assert len(json.loads(log["before_json"])["moved_files"]) == 2


def test_delete_keeps_files_still_in_use(app, admin, media_root):
    # Two records pointing at one file (TNG allows this): deleting one keeps the file.
    with app.app_context():
        _, a = insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "letter.pdf",
                                    "usecollfolder": 1, "mediakey": "Xshared1"})
        _, b = insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "letter.pdf",
                                    "usecollfolder": 1, "mediakey": "Xshared2"})
    assert "isn't linked to anyone" in text(admin.get(f"/edit/media/{a}/delete"))
    admin.post(f"/edit/media/{a}/delete", data={"csrf": "t"})
    assert (media_root / "documents" / "letter.pdf").is_file()
    # A web-link record has no file to move.
    with app.app_context():
        _, c = insert("media", {"gedcom": "tree1", "mediatypeID": "photos", "path": "http://example.com/y.jpg",
                                    "abspath": 1, "mediakey": "Xurl2"})
    admin.post(f"/edit/media/{c}/delete", data={"csrf": "t"})
    assert not sql(app, "SELECT 1 FROM media WHERE mediaID = %s", (c,))
    assert admin.get("/edit/media/99999999/delete").status_code == 404


def test_delete_when_file_already_missing(app, admin):
    with app.app_context():
        _, mid = insert("media", {"gedcom": "tree1", "mediatypeID": "photos", "path": "never_there.jpg",
                                      "thumbpath": "thumb_never_there.jpg", "usecollfolder": 1, "mediakey": "Xghost"})
    admin.post(f"/edit/media/{mid}/delete", data={"csrf": "t"})
    assert not sql(app, "SELECT 1 FROM media WHERE mediaID = %s", (mid,))


def test_permissions_and_links_on_pages(app, admin, guest, media_ids):
    mid = media_ids["plain"]
    assert "/edit/media/new?for=XHUSB" in text(admin.get("/person/XHUSB"))
    assert f"/edit/media/{mid}" in text(admin.get(f"/media/{mid}"))
    for r in (guest.get("/edit/media/new?for=XHUSB"), guest.get(f"/edit/media/{mid}"), guest.get(f"/edit/media/{mid}/delete"),
              guest.post(f"/edit/media/{mid}/links", data={"csrf": "t", "action": "add", "personID": "XHUSB"})):
        assert r.status_code == 403
    assert "/edit/media" not in text(guest.get(f"/media/{mid}"))
    editor = client_for(app, "fx_editor")
    assert editor.post(f"/edit/media/{mid}/links", data={"csrf": "t", "action": "add", "personID": "XWIFE"}).status_code == 403


def test_nosniff_header(admin, media_ids):
    assert admin.get(f"/media/{media_ids['plain']}/file").headers["X-Content-Type-Options"] == "nosniff"


def test_forms_name_whose_record_it_is(app, admin):
    ids = make(app, ("Ann", "F", 0), ("Bob", "M", 0), family=("Bob", "Ann"))
    surname = sql(app, "SELECT lastname FROM people WHERE personID = %s", (ids["Ann"],))[0]["lastname"]
    for url in (f"/edit/media/new?for={ids['Ann']}", f"/edit/event/new?for={ids['Ann']}", f"/edit/note/new?for={ids['Ann']}"):
        assert f"for Ann {surname} ({ids['Ann']})" in text(admin.get(url)), url
    assert f"for Bob {surname} & Ann {surname} ({ids['family']})" in text(admin.get(f"/edit/note/new?for={ids['family']}"))
    nobody = make(app, family=(None, None))
    assert f"for family ({nobody['family']})" in text(admin.get(f"/edit/note/new?for={nobody['family']}"))

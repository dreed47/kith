"""Tagging faces on photos: drawing boxes, who sees them, and tags following people through merges and deletes."""
import html as htmllib
import io

from PIL import Image

from conftest import CSRF, client_for, make, sql
from test_edit_media import newest_media, upload


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def tag(client, mid, pid, x=0.1, y=0.2, w=0.2, h=0.3):
    return client.post(f"/edit/media/{mid}/faces", data={"csrf": CSRF, "personID": pid, "x": x, "y": y, "w": w, "h": h})


def tags(app, mid):
    return sql(app, "SELECT * FROM app_face_tags WHERE mediaID = %s ORDER BY id", (mid,))


def test_tag_show_and_remove(app, admin):
    ids = make(app, ("Owner", "M", 0), ("Cousin", "F", 0))
    upload(admin, ids["Owner"])
    mid = newest_media(app)["mediaID"]
    page = text(admin.get(f"/media/{mid}"))
    assert 'id="tag-face"' in page and "In this photo" not in page
    r = tag(admin, mid, ids["Cousin"].lower())  # IDs are taken in any case
    assert r.status_code == 302 and r.location.endswith(f"/media/{mid}")
    t = tags(app, mid)[0]
    assert (t["personID"], round(t["x"], 3), round(t["h"], 3), t["created_by"]) == (ids["Cousin"], 0.1, 0.3, "fx_admin")
    # Tagging links the photo to them, so it shows on their page; a second tag doesn't link twice.
    assert sql(app, "SELECT 1 FROM medialinks WHERE mediaID = %s AND personID = %s", (mid, ids["Cousin"]))
    tag(admin, mid, ids["Owner"], x=0.6)
    assert len(sql(app, "SELECT 1 FROM medialinks WHERE mediaID = %s", (mid,))) == 2
    page = text(admin.get(f"/media/{mid}"))
    assert "In this photo" in page and "left: 10.0" in page and "height: 30.0" in page
    assert page.index(f"Cousin {ids['Cousin'][1:-6]}") < page.index("left: 60.0")  # left to right
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'tag face' AND record = %s", (str(t["id"]),))
    # Viewers without edit rights see the tags but no tagging tools.
    page = text(client_for(app, "fx_adder").get(f"/media/{mid}"))
    assert "In this photo" in page and 'id="tag-face"' not in page and "Remove tag" not in page
    assert admin.post(f"/edit/media/{mid}/faces/{t['id']}/delete", data={"csrf": CSRF}).status_code == 302
    assert [x["personID"] for x in tags(app, mid)] == [ids["Owner"]]
    assert sql(app, "SELECT 1 FROM medialinks WHERE mediaID = %s AND personID = %s", (mid, ids["Cousin"]))
    assert admin.post(f"/edit/media/{mid}/faces/{t['id']}/delete", data={"csrf": CSRF}).status_code == 404


def test_tiff_is_a_picture(app, admin):
    """Browsers can't show TIFF, but every picture is shown as a JPEG made here, so a scan can be tagged and chosen
    as someone's main picture like any photo."""
    ids = make(app, ("Scan", "F", 0))
    scan = io.BytesIO()
    Image.new("L", (400, 500), 128).save(scan, "TIFF")  # greyscale, like the family's old scans
    scan.seek(0)
    upload(admin, ids["Scan"], "old scan.tif", data=scan)
    mid = newest_media(app)["mediaID"]
    assert 'id="tag-face"' in text(admin.get(f"/media/{mid}"))
    tag(admin, mid, ids["Scan"])
    t = tags(app, mid)[0]
    r = admin.get(f"/media/{mid}/face/{t['id']}?shape=portrait")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert 'name="main"' in text(admin.get(f"/edit/media/arrange/{ids['Scan']}"))
    assert f"/media/{mid}/face/{t['id']}?shape=portrait" in text(admin.get(f"/person/{ids['Scan']}"))


def test_quick_tag_as_linked_person(app, admin):
    ids = make(app, ("Owner", "F", 0), ("Other", "M", 0))
    upload(admin, ids["Owner"])
    mid = newest_media(app)["mediaID"]
    page = text(admin.get(f"/media/{mid}"))
    assert f'name="quick" value="{ids["Owner"]}"' in page and "or someone else:" in page
    r = admin.post(f"/edit/media/{mid}/faces", data={"csrf": CSRF, "quick": ids["Owner"], "personID": "",
                                                     "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
    assert r.status_code == 302 and [t["personID"] for t in tags(app, mid)] == [ids["Owner"]]
    assert 'name="quick"' not in text(admin.get(f"/media/{mid}"))  # already tagged: no button
    tag(admin, mid, ids["Other"], x=0.5)  # the picker still works for anyone else
    assert [t["personID"] for t in tags(app, mid)] == [ids["Owner"], ids["Other"]]


def test_quick_tag_family(app, admin):
    """A family photo linked to one child offers buttons for the parents and siblings too."""
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), ("Kid", "F", 0), ("Sis", "F", 0), ("Stranger", "M", 0),
               family=("Dad", "Mom"), kids=("Kid", "Sis"))
    upload(admin, ids["Kid"])
    mid = newest_media(app)["mediaID"]
    page = text(admin.get(f"/media/{mid}"))
    for who in ("Dad", "Mom", "Sis"):
        assert f'name="quick" value="{ids[who]}"' in page, who
    assert page.count(f'value="{ids["Kid"]}"') == 1  # once, under "Tag as", not again under "Family"
    assert ids["Stranger"] not in page
    assert 'name="quick"' not in text(client_for(app, "fx_adder").get(f"/media/{mid}"))  # editors only


def test_tag_checks(app, admin):
    ids = make(app, ("Snap", "M", 0))
    upload(admin, ids["Snap"])
    mid = newest_media(app)["mediaID"]
    assert tag(client_for(app, "fx_adder"), mid, ids["Snap"]).status_code == 403
    assert tag(admin, 99999999, ids["Snap"]).status_code == 404
    for bad in ({"x": "nan"}, {"x": "oops"}, {"w": 0}, {"x": 0.9, "w": 0.2}, {"y": -0.1}):
        assert tag(admin, mid, ids["Snap"], **bad).status_code == 400, bad
    assert admin.post(f"/edit/media/{mid}/faces", data={"csrf": CSRF, "personID": ids["Snap"]}).status_code == 400
    tag(admin, mid, "NOSUCH")
    assert "No person with ID NOSUCH" in text(admin.get(f"/media/{mid}"))
    assert not tags(app, mid)
    assert client_for(app, "fx_adder").post(f"/edit/media/{mid}/faces/1/delete", data={"csrf": CSRF}).status_code == 403


def test_hidden_people_are_not_shown(app, admin):
    ids = make(app, ("Alive", "F", 1), ("Gone", "M", 0))
    upload(admin, ids["Gone"])
    mid = newest_media(app)["mediaID"]
    sql(app, "UPDATE media SET alwayson = 1 WHERE mediaID = %s", (mid,))
    tag(admin, mid, ids["Alive"])
    tag(admin, mid, ids["Gone"], x=0.5)
    page = text(client_for(app, "fx_guest").get(f"/media/{mid}"))
    # Only Gone's box; Alive is in "Linked to" as "Living" like everywhere else, but gets no box or tag list entry.
    assert page.count('class="face"') == 1 and f'href="/person/{ids["Gone"]}"\n' in page
    assert page.count(f'/person/{ids["Alive"]}') == 1
    assert text(admin.get(f"/media/{mid}")).count('class="face"') == 2


def test_tags_follow_merge_and_go_with_deletes(app, admin):
    ids = make(app, ("Keep", "M", 0), ("Dup", "M", 0), ("Other", "F", 0))
    upload(admin, ids["Keep"])
    mid = newest_media(app)["mediaID"]
    tag(admin, mid, ids["Dup"])
    tag(admin, mid, ids["Other"], x=0.5)
    assert admin.post("/edit/merge", data={"csrf": CSRF, "keep": ids["Keep"], "merge": ids["Dup"]}).status_code == 302
    assert [t["personID"] for t in tags(app, mid)] == [ids["Keep"], ids["Other"]]
    admin.post(f"/edit/person/{ids['Other']}/delete", data={"csrf": CSRF})
    assert [t["personID"] for t in tags(app, mid)] == [ids["Keep"]]
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'delete person' AND record = %s"
                    " AND before_json LIKE '%%app_face_tags%%'", (ids["Other"],))
    admin.post(f"/edit/media/{mid}/delete", data={"csrf": CSRF})
    assert not tags(app, mid)


def test_chart_pictures_are_faces(app, admin, media_root):
    import io

    from PIL import Image
    ids = make(app, ("Kid", "M", 0), ("Dad", "M", 0), ("Mom", "F", 0), family=("Dad", "Mom"), kids=("Kid",))
    upload(admin, ids["Dad"], collection="headstones")  # TNG's old default main picture: never in a chart
    sql(app, "UPDATE medialinks SET defphoto = '1' WHERE personID = %s", (ids["Dad"],))
    html = admin.get(f"/pedigree/{ids['Kid']}?gens=2&photos=1").get_data(as_text=True)
    assert "chart-face\" src" not in html  # no face pictures yet: initials only
    upload(admin, ids["Mom"], collection="documents")  # a document chosen as her main picture: an editor's call
    sql(app, "UPDATE medialinks SET defphoto = '1' WHERE personID = %s", (ids["Mom"],))
    doc = newest_media(app)["mediaID"]
    upload(admin, ids["Dad"])  # Photos collection: used
    photo = newest_media(app)["mediaID"]
    html = admin.get(f"/pedigree/{ids['Kid']}?gens=2").get_data(as_text=True)
    assert f'src="/media/{photo}/thumb"' in html and f'src="/media/{doc}/thumb"' in html
    # Tagged in the photo: the chart crops to the face.
    tag(admin, photo, ids["Dad"], x=0.5, y=0.25, w=0.25, h=0.25)
    tid = tags(app, photo)[0]["id"]
    html = admin.get(f"/pedigree/{ids['Kid']}?gens=2").get_data(as_text=True)
    assert f'src="/media/{photo}/face/{tid}"' in html
    r = admin.get(f"/media/{photo}/face/{tid}")
    with Image.open(io.BytesIO(r.data)) as im:
        assert r.mimetype == "image/jpeg" and im.size[0] == im.size[1] <= 160  # square
    assert admin.get(f"/media/{photo}/face/{tid}").status_code == 200  # cached
    assert admin.get(f"/media/{photo}/face/999999").status_code == 404
    assert admin.get(f"/media/{doc}/face/{tid}").status_code == 404  # the tag is on another picture
    # An unreadable file gives no face rather than an error page.
    path = media_root / "photos" / sql(app, "SELECT path FROM media WHERE mediaID = %s", (photo,))[0]["path"]
    path.write_bytes(b"not an image")
    for cached in (media_root / ".thumbs").glob(f"{photo}-face*.jpg"):
        cached.unlink()
    assert admin.get(f"/media/{photo}/face/{tid}").status_code == 404


def test_person_page_picture_crops_to_their_face(app, admin):
    import io

    from PIL import Image
    pid = make(app, ("Pic", "F", 0))["Pic"]
    upload(admin, pid)
    photo = newest_media(app)["mediaID"]
    assert f'src="/media/{photo}/thumb?s=600"' in admin.get(f"/person/{pid}").get_data(as_text=True)  # not tagged
    tag(admin, photo, pid, x=0.5, y=0.25, w=0.2, h=0.2)
    tid = tags(app, photo)[0]["id"]
    html = admin.get(f"/person/{pid}").get_data(as_text=True)
    assert f'src="/media/{photo}/face/{tid}?shape=portrait"' in html
    r = admin.get(f"/media/{photo}/face/{tid}?shape=portrait")
    with Image.open(io.BytesIO(r.data)) as im:
        w, h = im.size
        assert h <= 500 and abs(w / h - 0.8) < 0.02  # 4:5, like the box it fills
    assert admin.get(f"/media/{photo}/face/{tid}").status_code == 200  # the chart's square is cached separately


def test_face_box_stays_inside_the_picture():
    from pytest import approx

    from app.media import face_box
    t = {"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.2}
    assert face_box(t, 1000, 1000, False) == approx((370, 170, 260, 260))
    assert face_box(t, 1000, 1000, True) == approx((500 - 208, 300 - 520 * 0.4, 416, 520))
    corner = {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}  # face in the corner: the crop is pushed back inside
    assert face_box(corner, 1000, 1000, True)[:2] == (0, 0)
    huge = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}  # face fills a wide picture: as big as fits, still 4:5
    left, top, w, h = face_box(huge, 1000, 400, True)
    assert (w, h, top) == approx((320, 400, 0)) and 0 <= left <= 1000 - w
    left, top, w, h = face_box(huge, 300, 1000, True)  # tall, narrow picture: width limits it
    assert (w, h) == approx((300, 375))

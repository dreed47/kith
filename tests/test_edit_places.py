"""Editing places (renaming everywhere, merging) and cemeteries."""
import pytest
from conftest import CSRF, client_for, make, sql

from app.db import insert
from app.edit_places import standard


def text(r):
    return r.get_data(as_text=True)


def test_rename_place_everywhere(app, admin):
    pid = make(app, ("Placed", "M", 0))["Placed"]
    sql(app, "UPDATE people SET birthplace = 'Oldtown, XX', deathplace = 'Oldtown, XX' WHERE personID = %s", (pid,))
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Oldtown, XX", "notes": "old notes"})
        _, cid = insert("cemeteries", {"cemname": "Oldtown Cemetery", "place": "Oldtown, XX"})
    assert "Used by 3 records" in text(admin.get("/edit/place?name=Oldtown, XX"))
    assert "Edit place" in text(admin.get("/place?name=Oldtown, XX"))
    r = admin.post("/edit/place", data={"csrf": CSRF, "name": "Oldtown, XX", "place": "Newtown, XX",
                                        "latitude": "41.5", "longitude": "-79.2", "notes": "kept"})
    assert r.status_code == 302 and "Newtown" in r.headers["Location"]
    p = sql(app, "SELECT birthplace, deathplace FROM people WHERE personID = %s", (pid,))[0]
    assert p == {"birthplace": "Newtown, XX", "deathplace": "Newtown, XX"}
    assert sql(app, "SELECT place FROM cemeteries WHERE cemeteryID = %s", (cid,))[0]["place"] == "Newtown, XX"
    row = sql(app, "SELECT * FROM places WHERE place = 'Newtown, XX'")[0]
    assert row["latitude"] == "41.5" and not sql(app, "SELECT * FROM places WHERE place = 'Oldtown, XX'")
    assert sql(app, "SELECT action FROM app_audit WHERE record = 'Newtown, XX'")[0]["action"] == "rename place"


def test_rename_onto_existing_merges(app, admin):
    pid = make(app, ("Misspelt", "F", 0))["Misspelt"]
    sql(app, "UPDATE people SET birthplace = 'Clarrion, YY' WHERE personID = %s", (pid,))
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Clarrion, YY"})
        insert("places", {"gedcom": "tree1", "place": "Clarion, YY"})
    admin.post("/edit/place", data={"csrf": CSRF, "name": "Clarrion, YY", "place": "Clarion, YY", "latitude": "",
                                    "longitude": ""})
    assert sql(app, "SELECT birthplace FROM people WHERE personID = %s", (pid,))[0]["birthplace"] == "Clarion, YY"
    assert [r["place"] for r in sql(app, "SELECT place FROM places WHERE place LIKE 'Clar%%ion, YY'")] == ["Clarion, YY"]


def test_place_without_a_places_row(app, admin):
    pid = make(app, ("Rowless", "M", 0))["Rowless"]
    sql(app, "UPDATE people SET burialplace = 'Nowhere Row, ZZ' WHERE personID = %s", (pid,))
    r = admin.post("/edit/place", data={"csrf": CSRF, "name": "Nowhere Row, ZZ", "place": "Nowhere Row, ZZ",
                                        "latitude": "10", "longitude": "20", "notes": ""})
    assert "Saved." in text(admin.get(r.headers["Location"]))
    assert sql(app, "SELECT latitude FROM places WHERE place = 'Nowhere Row, ZZ'")[0]["latitude"] == "10"
    admin.post("/edit/place", data={"csrf": CSRF, "name": "Nowhere Row, ZZ", "place": "Nowhere Row, ZZ",
                                    "latitude": "11", "longitude": "20"})  # now it has a row: updated in place
    assert sql(app, "SELECT latitude FROM places WHERE place = 'Nowhere Row, ZZ'")[0]["latitude"] == "11"


def test_place_validation_and_rights(app, admin):
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Checkplace"})
    html = text(admin.post("/edit/place", data={"csrf": CSRF, "name": "Checkplace", "place": "",
                                                "latitude": "95", "longitude": "abc"}))
    assert "Enter the place name" in html and "Latitude should be" in html and "Longitude should be" in html
    assert "longer than 248" in text(admin.post("/edit/place", data={"csrf": CSRF, "name": "Checkplace",
                                                                     "place": "x" * 249}))
    assert admin.get("/edit/place?name=No such place anywhere").status_code == 404
    assert client_for(app, "fx_guest").get("/edit/place?name=Checkplace").status_code == 403


def test_cemeteries(app, admin):
    assert "Add cemetery" in text(admin.get("/cemeteries")) and "Add cemetery" in text(admin.get("/edit/cemetery/new"))
    bad = text(admin.post("/edit/cemetery/new", data={"csrf": CSRF, "cemname": "", "city": "x" * 65,
                                                     "latitude": "200"}))
    assert "Enter the cemetery" in bad and "City or township is longer" in bad and "Latitude should be" in bad
    r = admin.post("/edit/cemetery/new", data={"csrf": CSRF, "cemname": "Hilltop", "place": "Hill, PA",
                                               "latitude": "41", "longitude": "-79"})
    cid = int(r.headers["Location"].rsplit("/", 1)[1])
    assert "Edit cemetery" in text(admin.get(f"/cemeteries/{cid}"))
    admin.post(f"/edit/cemetery/{cid}", data={"csrf": CSRF, "cemname": "Hilltop Cemetery", "place": "Hill, PA"})
    assert sql(app, "SELECT cemname FROM cemeteries WHERE cemeteryID = %s", (cid,))[0]["cemname"] == "Hilltop Cemetery"
    assert "Delete cemetery" in text(admin.get(f"/edit/cemetery/{cid}"))
    mid = sql(app, "SELECT mediaID FROM media LIMIT 1")[0]["mediaID"]
    sql(app, "UPDATE media SET cemeteryID = %s WHERE mediaID = %s", (cid, mid))
    assert "headstone photo" in text(admin.get(f"/edit/cemetery/{cid}/delete", follow_redirects=True))
    sql(app, "UPDATE media SET cemeteryID = 0 WHERE mediaID = %s", (mid,))
    assert "Delete Hilltop Cemetery?" in text(admin.get(f"/edit/cemetery/{cid}/delete"))
    admin.post(f"/edit/cemetery/{cid}/delete", data={"csrf": CSRF})
    assert admin.get(f"/edit/cemetery/{cid}").status_code == 404
    assert client_for(app, "fx_editor").get("/edit/cemetery/new").status_code == 403


@pytest.mark.parametrize("name,styled", [
    ("Madison Township, Clarion County, Pennsylvania", "Madison Twp, Clarion Cnty, PA"),
    ("Porter Twp., Clarion Cnty,  PA ", "Porter Twp, Clarion Cnty, PA"),
    ("Clarion ,Clarion Co., Pa", "Clarion, Clarion Cnty, PA"),
    ("Grandview Cemetery,, Sligo, Clarion Cnty, PA", "Grandview Cemetery, Sligo, Clarion Cnty, PA"),
    ("Detroit, Wayne, Michigan, USA", "Detroit, Wayne, MI"),
    ("Armstrong County Memorial Hospital, Kittanning, PA", "Armstrong County Memorial Hospital, Kittanning, PA"),
    ("Hamburg, Germany", "Hamburg, Germany"),
    ("Georgia", "Georgia"),  # might be the country
    ("  Cremated ", "Cremated"),
    (",", ""),
])
def test_house_style(name, styled):
    assert standard(name) == styled


def test_tidy_places(app, admin):
    pid = make(app, ("Tidy", "F", 0))["Tidy"]
    sql(app, "UPDATE people SET birthplace = 'Xville, Xco County, Pennsylvania', deathplace = 'Xville,Xco Cnty, PA',"
             " burialplace = 'xlower, xco cnty, pa', baptplace = 'Xrowless,PA' WHERE personID = %s", (pid,))
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Xville, Xco County, Pennsylvania"})
        insert("places", {"gedcom": "tree1", "place": "Xville,Xco Cnty, PA", "latitude": "41.1",
                              "longitude": "-79.1", "notes": "from the variant"})
        insert("places", {"gedcom": "tree1", "place": "xlower, xco cnty, pa"})
        insert("places", {"gedcom": "tree1", "place": "Xunused, PA"})
        insert("places", {"gedcom": "tree1", "place": "Xmapped, PA", "latitude": "40"})
        insert("places", {"gedcom": "tree1", "place": "Xlong" + "g" * 90 + ", PA"})  # longer than audit's record
        insert("places", {"gedcom": "tree1", "place": ""}, ignore=True)  # TNG leftover: every blank matches
    page = text(admin.get("/edit/places/tidy"))
    assert 'value="Xville, Xco Cnty, PA" maxlength' in page and 'value="Xlower, Xco Cnty, PA"' not in page
    assert 'name="delete" value=""' in page and "(blank name)" in page
    assert 'data-name="apply" data-on="1">Tick all' in page and 'data-name="delete" data-on="">Untick all' in page
    assert 'name="delete" value="Xunused, PA" checked' in page and 'name="delete" value="Xmapped, PA" aria' in page
    assert text(client_for(app, "fx_editor").get("/edit/places/tidy")).count('name="delete"') == 0
    sql(app, "UPDATE people SET altbirthplace = 'Xunused, PA' WHERE personID = %s", (pid,))  # used since loading
    r = admin.post("/edit/places/tidy", data={
        "csrf": CSRF, "apply": ["1", "2", "3", "4"], "to1": "Xville, Xco Cnty, PA",
        "from1": ["Xville, Xco Cnty, PA", "Xville, Xco County, Pennsylvania", "Xville,Xco Cnty, PA"],
        "from2": "xlower, xco cnty, pa", "to2": "Xlower, Xco Cnty, PA", "from3": "Xskip", "to3": " ",
        "from4": "Xrowless,PA", "to4": "Xrowless, PA", "delete": ["Xunused, PA", "Xmapped, PA", "Xlong" + "g" * 90 + ", PA", ""]})
    assert r.status_code == 302
    flashed = text(admin.get(r.headers["Location"]))
    assert "Skipped a name" in flashed and "Renamed 4 place name(s), deleted 3 unused one(s)." in flashed
    assert sql(app, "SELECT baptplace FROM people WHERE personID = %s", (pid,))[0]["baptplace"] == "Xrowless, PA"
    p = sql(app, "SELECT birthplace, deathplace, burialplace FROM people WHERE personID = %s", (pid,))[0]
    assert p == {"birthplace": "Xville, Xco Cnty, PA", "deathplace": "Xville, Xco Cnty, PA",
                 "burialplace": "Xlower, Xco Cnty, PA"}
    rows = sql(app, "SELECT place, latitude, notes FROM places WHERE place LIKE 'X%%' ORDER BY place")
    assert rows == [{"place": "Xlower, Xco Cnty, PA", "latitude": "", "notes": ""},
                    {"place": "Xunused, PA", "latitude": "", "notes": ""},
                    {"place": "Xville, Xco Cnty, PA", "latitude": "41.1", "notes": "from the variant"}]
    assert sql(app, "SELECT COUNT(*) AS n FROM app_audit WHERE action = 'delete place'")[0]["n"] == 3
    assert not sql(app, "SELECT * FROM places WHERE place = ''")


def test_tidy_places_needs_edit_rights(app, guest):
    assert guest.get("/edit/places/tidy").status_code == 403
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Xkeep, PA"})
    client_for(app, "fx_editor").post("/edit/places/tidy", data={"csrf": CSRF, "delete": "Xkeep, PA"})
    assert sql(app, "SELECT place FROM places WHERE place = 'Xkeep, PA'")  # editors without delete rights can't


def test_places_saved_in_house_style(app, admin):
    from werkzeug.datastructures import MultiDict
    from app.edit import person_form
    from app.edit_events import event_form
    from app.edit_family import family_form
    from app.edit_media import media_form
    from app.edit_places import cemetery_form
    typed, styled = "Sligo ,Clarion County, Pennsylvania ", "Sligo, Clarion Cnty, PA"
    with app.test_request_context():
        assert person_form(MultiDict({"firstname": "A", "deathplace": typed}))[0]["deathplace"] == styled
        assert family_form(MultiDict({"husband": "XHUSB", "marrplace": typed}))[0]["marrplace"] == styled
        assert event_form(MultiDict({"eventplace": typed}), "I")[0]["eventplace"] == styled
        assert media_form(MultiDict({"placetaken": typed}))[0]["placetaken"] == styled
        assert cemetery_form(MultiDict({"cemname": "C", "place": typed}))[0]["place"] == styled
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Xrestyle,Pennsylvania"})
    r = admin.post("/edit/place", data={"csrf": CSRF, "name": "Xrestyle,Pennsylvania", "place": "Xrestyle , Penna"})
    assert r.status_code == 302 and sql(app, "SELECT place FROM places WHERE place LIKE 'Xrestyle%%'") == [
        {"place": "Xrestyle, PA"}]

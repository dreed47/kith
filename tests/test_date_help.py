import html as htmllib

from conftest import client_for, make, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def test_hint_endpoint(app, admin, guest):
    assert admin.get("/edit/date?d=abt 1895").get_json() == {"message": "About: sorts as 1895", "ok": True}
    assert admin.get("/edit/date?d=13/13/1900").get_json()["ok"] is False
    assert admin.get("/edit/date").get_json() == {"message": "", "ok": True}
    assert client_for(app, "fx_adder").get("/edit/date?d=1900").status_code == 200
    assert guest.get("/edit/date?d=1900").status_code == 403


def test_date_boxes_have_helpers(app, admin):
    ids = make(app, ("A", "M", 0), family=("A", None))
    for url, names in ((f"/edit/person/{ids['A']}", ("birthdate", "altbirthdate", "deathdate", "burialdate")),
                       (f"/edit/family/{ids['family']}", ("marrdate", "divdate")),
                       (f"/edit/event/new?for={ids['A']}", ("eventdate",))):
        html = text(admin.get(url))
        assert "dates.js" in html, url
        for n in names:
            assert f'id="date-{n}"' in html and f'id="date-{n}-pick"' in html and f'id="date-{n}-hint"' in html, (url, n)


def test_impossible_day_saves_without_error(app, admin):
    # TNG's own editor fails on these with strict MySQL; here they save and the editor is warned.
    ids = make(app, ("A", "M", 0))
    p = sql(app, "SELECT * FROM people WHERE personID = %s", (ids["A"],))[0]
    form = {k: p[k] for k in ("firstname", "lastname", "sex")}
    r = admin.post(f"/edit/person/{ids['A']}", data={**form, "csrf": "t", "changedate": str(p["changedate"]),
                                                      "birthdate": "31 FEB 1900", "deathdate": "1950"})
    assert r.status_code == 302
    after = sql(app, "SELECT birthdate, birthdatetr FROM people WHERE personID = %s", (ids["A"],))[0]
    assert after["birthdate"] == "31 FEB 1900" and str(after["birthdatetr"]) == "0000-00-00"
    page = text(admin.get(r.location))
    assert "\"31 FEB 1900\" isn't recognised as a date" in page and "1950\" isn't" not in page


def test_warnings_on_new_person_family_and_event(app, admin):
    r = admin.post("/edit/person/new", data={"csrf": "t", "firstname": "Warn", "lastname": "Fixture", "deathdate": "sometime"})
    assert "\"sometime\" isn't recognised" in text(admin.get(r.location))
    ids = make(app, ("A", "M", 0), ("B", "F", 0))
    r = admin.post("/edit/family/new", data={"csrf": "t", "husband": ids["A"], "wife": ids["B"], "marrdate": "June-ish"})
    assert "\"June-ish\" isn't recognised" in text(admin.get(r.location))
    fid = sql(app, "SELECT familyID FROM families WHERE husband = %s", (ids["A"],))[0]["familyID"]
    f = sql(app, "SELECT * FROM families WHERE familyID = %s", (fid,))[0]
    r = admin.post(f"/edit/family/{fid}", data={"csrf": "t", "husband": ids["A"], "wife": ids["B"],
                                                 "changedate": str(f["changedate"]), "divdate": "later"})
    assert "\"later\" isn't recognised" in text(admin.get(r.location))
    occu = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'I' AND display = 'Occupation'")[0]["eventtypeID"]
    r = admin.post("/edit/event/new", data={"csrf": "t", "for": ids["A"], "eventtypeID": occu, "eventdate": "wartime"})
    assert "\"wartime\" isn't recognised" in text(admin.get(r.location))
    eid = sql(app, "SELECT eventID FROM events WHERE persfamID = %s", (ids["A"],))[0]["eventID"]
    r = admin.post(f"/edit/event/{eid}", data={"csrf": "t", "eventtypeID": occu, "eventdate": "peacetime"})
    assert "\"peacetime\" isn't recognised" in text(admin.get(r.location))

import html as htmllib

from app.db import insert
from conftest import client_for, make, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def etype(app, tag):
    return sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'I' AND tag = %s", (tag,))[0]["eventtypeID"]


def add_event(app, pid, tag, **v):
    with app.app_context():
        return insert("events", {"gedcom": "tree1", "persfamID": pid, "eventtypeID": etype(app, tag), **v})[1]


def test_dates_shown_consistently(app, admin):
    ids = make(app, ("A", "M", 0))
    sql(app, "UPDATE people SET birthdate = '02 JUL 1921', deathdate = 'ABT 1990' WHERE personID = %s", (ids["A"],))
    html = text(admin.get(f"/person/{ids['A']}"))
    assert "2 Jul 1921" in html and "abt 1990" in html and "02 JUL 1921" not in html


def test_siblings_listed(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Me", "F", 0), ("Bro", "M", 0), family=("Dad", None), kids=("Me", "Bro"))
    html = text(admin.get(f"/person/{ids['Me']}"))
    assert "Siblings" in html and f"/person/{ids['Bro']}" in html
    sib_section = html.split("Siblings")[1].split("</ul>")[0]
    assert f"/person/{ids['Me']}\"" not in sib_section  # not your own sibling
    assert "Siblings" not in text(admin.get(f"/person/{ids['Dad']}"))


def test_long_details_collapse(app, admin):
    ids = make(app, ("A", "M", 0))
    add_event(app, ids["A"], "CENS", eventdate="1900", info="\n".join(f"line {i}" for i in range(10)))
    add_event(app, ids["A"], "OCCU", info="x" * 500)
    add_event(app, ids["A"], "RELI", info="Short and sweet")
    add_event(app, ids["A"], "EDUC", info="\r\n".join(f"crlf {i}" for i in range(10)))  # Windows line endings
    html = text(admin.get(f"/person/{ids['A']}"))
    assert html.count("Show more") == 3 and "line 9" in html and html.count("crlf 0") == 1 and "crlf 9" in html  # full text still on the page, behind "Show more"
    assert html.count("line 0") == 1  # the opening lines aren't repeated inside "Show more"
    assert "Short and sweet</div>" in html


def test_long_notes_collapse(app, admin):
    ids = make(app, ("A", "M", 0))
    admin.post("/edit/note/new", data={"csrf": "t", "for": ids["A"], "note": "word " * 300})
    assert "Show more" in text(admin.get(f"/person/{ids['A']}"))


def test_empty_events_hidden_except_from_editors(app, admin):
    ids = make(app, ("A", "M", 0))
    add_event(app, ids["A"], "DEAT")
    viewer = client_for(app, "fx_nonotes")
    assert "Death" not in text(viewer.get(f"/person/{ids['A']}"))
    html = text(admin.get(f"/person/{ids['A']}"))
    assert "(empty)" in html and "empty-event" in html


def test_ssn_only_for_editors(app, admin):
    ids = make(app, ("A", "M", 0))
    add_event(app, ids["A"], "SSN", info="123-45-6789")
    assert "123-45-6789" not in text(client_for(app, "fx_nonotes").get(f"/person/{ids['A']}"))
    assert "123-45-6789" in text(admin.get(f"/person/{ids['A']}"))


def test_place_suggestions(app, admin, guest):
    assert admin.get("/edit/places?q=S").get_json() == []
    got = admin.get("/edit/places?q=Synthv").get_json()
    assert got[0] == "Synthville, Testshire"
    assert "Synthville, Testshire" in admin.get("/edit/places?q=testshire").get_json()  # matches anywhere in the name
    assert client_for(app, "fx_adder").get("/edit/places?q=Syn").status_code == 200
    assert guest.get("/edit/places?q=Syn").status_code == 403


def test_place_boxes_suggest(app, admin, media_ids):
    ids = make(app, ("A", "M", 0), family=("A", None))
    for url in (f"/edit/person/{ids['A']}", f"/edit/family/{ids['family']}", f"/edit/event/new?for={ids['A']}",
                f"/edit/media/new?for={ids['A']}"):
        html = text(admin.get(url))
        assert "places.js" in html and 'data-places="/edit/places"' in html, url


def save_person(admin, app, pid, **fields):
    p = sql(app, "SELECT * FROM people WHERE personID = %s", (pid,))[0]
    form = {"csrf": "t", "changedate": str(p["changedate"]), "firstname": p["firstname"], "lastname": p["lastname"], **fields}
    return text(admin.get(admin.post(f"/edit/person/{pid}", data=form).location))


def test_living_flag_checks(app, admin):
    ids = make(app, ("A", "M", 0))
    assert "marked living but has a death or burial" in save_person(admin, app, ids["A"], living="1", deathdate="1990")
    assert "marked living but born in 1850" in save_person(admin, app, ids["A"], living="1", birthdate="1850")
    page = save_person(admin, app, ids["A"], birthdate="1980")
    assert "born in 1980 with no death recorded, but not marked living" in page
    page = save_person(admin, app, ids["A"], birthdate="1980", living="1")
    assert "Check:" not in page
    page = save_person(admin, app, ids["A"], birthdate="1900")
    assert "Check:" not in page
    r = admin.post("/edit/person/new", data={"csrf": "t", "firstname": "Old", "lastname": "Fixture", "living": "1", "birthdate": "1800"})
    assert "marked living but born in 1800" in text(admin.get(r.location))

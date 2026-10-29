"""Sources and citations: viewing, editing, merging."""
from conftest import CSRF, client_for, make, sql

from app.db import insert


def text(r):
    return r.get_data(as_text=True)


def new_source(app, title, sid):
    with app.app_context():
        insert("sources", {"gedcom": "tree1", "sourceID": sid, "title": title, "author": "Clerk"})


def cite(app, owner, sid, about="", **v):
    with app.app_context():
        return insert("citations", {"gedcom": "tree1", "persfamID": owner, "sourceID": sid, "eventID": about,
                                        **v})[1]


def test_source_pages(app, admin):
    ids = make(app, ("Cited", "M", 0), ("Livecited", "F", 1), family=("Cited", "Livecited"))
    new_source(app, "Parish Register", "XS10")
    occu = sql(app, "SELECT eventtypeID FROM eventtypes WHERE tag = 'OCCU' AND type = 'I'")[0]["eventtypeID"]
    with app.app_context():
        eid = insert("events", {"gedcom": "tree1", "persfamID": ids["Cited"], "eventtypeID": occu})[1]
    cite(app, ids["Cited"], "XS10", "BIRT", page="p. 4", citetext="Baptized", citedate="1850", note="Seen by Ruth")
    cite(app, ids["Cited"], "XS10", str(eid))
    cite(app, ids["family"], "XS10", "MARR")
    cite(app, ids["Livecited"], "XS10")
    cite(app, ids["Cited"], "XS10", "BAPL", page="LDS only")
    html = text(admin.get("/sources"))
    assert "/sources/XS10" in html and "5 citations" in html
    page = text(admin.get("/sources/XS10"))
    assert "Cited for 4 facts" in page and "LDS only" not in page and "Birth" in page and "Occupation" in page and "Marriage" in page
    assert "Merge a duplicate" in page and "Edit source" in page
    limited = text(client_for(app, "fx_limited_editor").get("/sources/XS10"))
    assert "And 1 about people you can" in limited and "Merge a duplicate" not in limited  # living wife + family
    person = text(admin.get(f"/person/{ids['Cited']}"))
    assert '<a href="/sources/XS10">Parish Register</a>, p. 4' in person and "about: Birth" in person
    assert "Seen by Ruth" in person and "(1850)" in person and "LDS only" not in person and "about: Occupation" in person
    assert admin.get("/sources/NOPE").status_code == 404


def test_edit_and_delete_sources(app, admin):
    assert "Enter a title" in text(admin.post("/edit/source/new", data={"csrf": CSRF, "title": ""}))
    assert "longer than 120" in text(admin.post("/edit/source/new", data={"csrf": CSRF, "title": "T", "callnum": "x" * 121}))
    r = admin.post("/edit/source/new", data={"csrf": CSRF, "title": "Census 1900", "author": "US"})
    sid = r.headers["Location"].rsplit("/", 1)[1]
    assert sql(app, "SELECT author FROM sources WHERE sourceID = %s", (sid,))[0]["author"] == "US"
    r = admin.post(f"/edit/source/{sid}", data={"csrf": CSRF, "title": "Census of 1900", "back": "/sources"})
    assert r.headers["Location"] == "/sources"
    assert "Census of 1900" in text(admin.get(f"/edit/source/{sid}"))
    pid = make(app, ("Censused", "M", 0))["Censused"]
    cite(app, pid, sid)
    assert "still use this source" in text(admin.get(f"/edit/source/{sid}/delete", follow_redirects=True))
    sql(app, "DELETE FROM citations WHERE sourceID = %s", (sid,))
    assert "Delete the source" in text(admin.get(f"/edit/source/{sid}/delete"))
    admin.post(f"/edit/source/{sid}/delete", data={"csrf": CSRF})
    assert not sql(app, "SELECT * FROM sources WHERE sourceID = %s", (sid,))
    assert client_for(app, "fx_editor").get("/edit/source/new").status_code == 403


def test_merge_sources(app, admin):
    new_source(app, "Bible", "XS20")
    new_source(app, "Bible (copy)", "XS21")
    pid = make(app, ("Biblical", "F", 0))["Biblical"]
    cite(app, pid, "XS21", page="flyleaf")
    admin.post("/edit/source/XS20/merge", data={"csrf": CSRF, "other": "XS21"})
    assert sql(app, "SELECT sourceID FROM citations WHERE persfamID = %s", (pid,))[0]["sourceID"] == "XS20"
    assert not sql(app, "SELECT * FROM sources WHERE sourceID = 'XS21'")
    assert admin.post("/edit/source/XS20/merge", data={"csrf": CSRF, "other": "XS20"}).status_code == 400


def test_citations(app, admin):
    new_source(app, "Deed book", "XS30")
    pid = make(app, ("Deeded", "M", 0))["Deeded"]
    assert "about: Birth" in text(admin.get(f"/edit/citation/new?for={pid}&about=BIRT&source=XS30"))
    bad = text(admin.post("/edit/citation/new", data={"csrf": CSRF, "for": pid, "sourceID": "NOPE",
                                                      "page": "x" * 501, "citedate": "y" * 51}))
    assert "Choose a source" in bad and "page is longer" in bad and "date is longer" in bad
    r = admin.post("/edit/citation/new", data={"csrf": CSRF, "for": pid, "about": "BIRT", "sourceID": "XS30",
                                               "page": "vol 2", "citetext": "Born here"})
    assert r.status_code == 302
    c = sql(app, "SELECT * FROM citations WHERE persfamID = %s", (pid,))[0]
    assert (c["eventID"], c["page"], c["ordernum"]) == ("BIRT", "vol 2", 1)
    assert "Edit source citation" in text(admin.get(f"/edit/citation/{c['citationID']}"))
    assert "Choose a source" in text(admin.post(f"/edit/citation/{c['citationID']}", data={"csrf": CSRF}))
    admin.post(f"/edit/citation/{c['citationID']}", data={"csrf": CSRF, "sourceID": "XS30", "page": "vol 3"})
    assert sql(app, "SELECT page FROM citations WHERE citationID = %s", (c["citationID"],))[0]["page"] == "vol 3"
    assert "Remove this source citation" in text(admin.get(f"/edit/citation/{c['citationID']}/delete"))
    admin.post(f"/edit/citation/{c['citationID']}/delete", data={"csrf": CSRF})
    assert not sql(app, "SELECT * FROM citations WHERE persfamID = %s", (pid,))
    assert admin.get("/edit/citation/999999").status_code == 404
    assert admin.get(f"/edit/citation/new?for={pid}&about=MARR").status_code == 400

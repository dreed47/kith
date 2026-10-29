"""Notes about one fact (birth, marriage, a census...) and the note search."""
from conftest import CSRF, client_for, make, sql

from app import edit_events
from app.db import insert
from app.edit_events import snippet


def text(r):
    return r.get_data(as_text=True)


def note(app, owner, text_, about="", secret=0):
    with app.app_context():
        _, xid = insert("xnotes", {"gedcom": "tree1", "note": text_})
        _, lid = insert("notelinks", {"gedcom": "tree1", "persfamID": owner, "xnoteID": xid, "eventID": about,
                                          "secret": secret})
    return lid


def event(app, owner, kind="I"):
    etype = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = %s AND tag = 'CENS'", (kind,))[0]["eventtypeID"]
    with app.app_context():
        return insert("events", {"gedcom": "tree1", "persfamID": owner, "eventtypeID": etype, "eventdate": "1900"})[1]


def test_person_fact_notes(app, admin):
    pid = make(app, ("Noter", "M", 0))["Noter"]
    sql(app, "UPDATE people SET birthdate = '1 JAN 1850' WHERE personID = %s", (pid,))
    eid = event(app, pid)
    note(app, pid, "Born in the snow", "BIRT")
    note(app, pid, "Counted twice", str(eid))
    note(app, pid, "Spelled Notter early on", "NAME")
    note(app, pid, "Died at sea, date unknown", "DEAT")  # no death date or place: no timeline row to sit on
    note(app, pid, "Temple ordinance", "BAPL")
    note(app, pid, "General note")
    html = text(admin.get(f"/person/{pid}"))
    notes_at = html.index("<h2>Notes</h2>")
    assert html.index("Born in the snow") < notes_at and html.index("Counted twice") < notes_at
    assert "About: Name" in html[notes_at:]
    assert html.index("Spelled Notter") > notes_at and html.index("General note") > notes_at
    assert html.index("<h2>Obituary</h2>") < html.index("Died at sea") < notes_at  # death notes are the obituary
    assert "About: Death" not in html and "Temple ordinance" not in html
    assert f"/edit/note/new?for={pid}&amp;about=BIRT" in html and f"about={eid}" in html


def test_family_fact_notes(app, admin):
    ids = make(app, ("Fh", "M", 0), ("Fw", "F", 0), family=("Fh", "Fw"))
    fid = ids["family"]
    sql(app, "UPDATE families SET marrdate = '2 FEB 1880' WHERE familyID = %s", (fid,))
    eid = event(app, fid, "F")
    note(app, fid, "Married in the barn", "MARR")
    note(app, fid, "Listed together", str(eid))
    note(app, fid, "Divorce rumored", "DIV")  # no divorce date: falls back to the Notes section
    html = text(admin.get(f"/family/{fid}"))
    notes_at = html.index("<h2>Notes</h2>")
    assert html.index("Married in the barn") < notes_at and html.index("Listed together") < notes_at
    assert html.index("Divorce rumored") > notes_at and "About: Divorce" in html
    assert f"/edit/note/new?for={fid}&amp;about=MARR" in html


def test_add_note_about_a_fact(app, admin):
    pid = make(app, ("Aboutme", "F", 0))["Aboutme"]
    eid = event(app, pid)
    assert "about: Birth" in text(admin.get(f"/edit/note/new?for={pid}&about=BIRT"))
    r = admin.post("/edit/note/new", data={"csrf": CSRF, "for": pid, "about": "BIRT", "note": "Home birth"})
    assert r.status_code == 302
    link = sql(app, "SELECT * FROM notelinks WHERE persfamID = %s", (pid,))[0]
    assert link["eventID"] == "BIRT"
    assert "about: Birth" in text(admin.get(f"/edit/note/{link['ID']}"))
    assert "about: Census" in text(admin.get(f"/edit/note/new?for={pid}&about={eid}"))
    other = event(app, "XHUSB")
    for bad in ("MARR", str(other), "nonsense"):  # a family fact, someone else's event, not a fact at all
        assert admin.get(f"/edit/note/new?for={pid}&about={bad}").status_code == 400, bad


def test_snippet():
    assert snippet("one two three", "two") == ("one ", "two", " three")
    long = "a" * 400 + "needle" + "b" * 400
    before, match, after = snippet(long, "NEEDLE")
    assert before.startswith("…") and after.endswith("…") and match == "needle" and len(before) == 151
    assert snippet("José was here", "jose") == ("José was here", "", "")  # matched by the DB ignoring accents
    assert snippet("x" * 400, "José")[0].endswith("…")


def test_search_notes(app, admin):
    ids = make(app, ("Searchme", "M", 0), ("Hiddenliving", "F", 1), ("Fsh", "M", 0), family=("Fsh", None))
    note(app, ids["Searchme"], "Worked at the zebrafactory in town", "BIRT")
    note(app, ids["Searchme"], "Secret zebrafactory debts", secret=1)
    note(app, ids["Hiddenliving"], "Living zebrafactory worker")
    note(app, ids["family"], "Family zebrafactory picnic")
    note(app, ids["Searchme"], "LDS zebrafactory", "BAPL")
    html = text(admin.get("/edit/notes?q=zebrafactory"))
    assert "4 notes" in html and "<mark>zebrafactory</mark>" in html and "about: Birth" in html
    assert f'href="/family/{ids["family"]}"' in html and '<span class="tag">private</span>' in html
    assert "LDS zebrafactory" not in html
    limited = text(client_for(app, "fx_limited_editor").get("/edit/notes?q=zebrafactory"))
    assert "2 notes" in limited and "Secret zebrafactory" not in limited and "Living zebrafactory" not in limited
    assert "No notes contain" in text(admin.get("/edit/notes?q=zebra%25factory"))  # a typed % is literal
    assert "Search notes" in text(admin.get("/edit/notes")) and "/edit/notes" in text(admin.get("/search"))
    assert client_for(app, "fx_guest").get("/edit/notes?q=x").status_code == 403


def test_search_notes_pages(app, admin, monkeypatch):
    pid = make(app, ("Pager", "M", 0))["Pager"]
    for i in range(3):
        note(app, pid, f"quokkapage {i}")
    monkeypatch.setattr(edit_events, "SEARCH_PER_PAGE", 2)
    first, second = text(admin.get("/edit/notes?q=quokkapage")), text(admin.get("/edit/notes?q=quokkapage&page=2"))
    assert "Page 1 of 2" in first and "Next" in first and "Previous" in second


def test_biography_and_obituary(app, admin):
    ids = make(app, ("Bio", "F", 0), ("Mate", "M", 0), family=("Mate", "Bio"))
    pid = ids["Bio"]
    order = lambda lid: sql(app, "SELECT ordernum FROM notelinks WHERE ID = %s", (lid,))[0]["ordernum"]
    link = lambda t: sql(app, "SELECT nl.ID FROM notelinks nl JOIN xnotes x ON x.ID = nl.xnoteID"
                              " WHERE x.note = %s", (t,))[0]["ID"]
    assert 'name="bio" value="1" checked' in text(admin.get(f"/edit/note/new?for={pid}&bio=1"))
    assert "<h1>Add biography" in text(admin.get(f"/edit/note/new?for={pid}&bio=1"))
    assert "<h1>Add obituary" in text(admin.get(f"/edit/note/new?for={pid}&about=DEAT"))
    admin.post(f"/edit/note/new?for={pid}", data={"csrf": CSRF, "note": "Plain note"})
    admin.post(f"/edit/note/new?for={pid}", data={"csrf": CSRF, "note": "First bio", "bio": "1"})
    assert order(link("First bio")) == -1 and order(link("Plain note")) > 0
    admin.post(f"/edit/note/new?for={pid}", data={"csrf": CSRF, "note": "Second bio", "bio": "1"})
    assert order(link("Second bio")) == -1 and order(link("First bio")) > order(link("Plain note"))  # demoted
    admin.post(f"/edit/note/new?for={pid}&about=DEAT", data={"csrf": CSRF, "note": "Obit text", "bio": "1"})
    assert order(link("Obit text")) > 0  # only the person's own notes can be the biography
    admin.post(f"/edit/note/new?for={ids['family']}", data={"csrf": CSRF, "note": "Family bio", "bio": "1"})
    assert order(link("Family bio")) > 0

    html = text(admin.get(f"/person/{pid}"))
    assert html.index("<h2>Biography</h2>") < html.index("Second bio") < html.index("family-glance")
    assert html.index("<h2>Obituary</h2>") < html.index("Obit text") < html.index("<h2>Notes</h2>") < html.index("First bio")
    assert f"/edit/note/new?for={pid}&amp;bio=1" not in html  # one biography: edit it, don't add another
    hub = text(admin.get(f"/edit/person/{pid}"))
    assert hub.index("Biography</p>") < hub.index("Second bio") and hub.index("Obituary</p>") < hub.index("Obit text")

    bio = link("Second bio")
    assert 'name="bio" value="1" checked' in text(admin.get(f"/edit/note/{bio}"))
    admin.post(f"/edit/note/{bio}", data={"csrf": CSRF, "note": "Second bio"})  # untick: an ordinary note again
    assert order(bio) > 0
    admin.post(f"/edit/note/{bio}", data={"csrf": CSRF, "note": "Second bio", "bio": "1"})
    admin.post(f"/edit/note/{bio}", data={"csrf": CSRF, "note": "Second bio, edited", "bio": "1"})  # stays put
    assert order(bio) == -1
    obit = link("Obit text")
    admin.post(f"/edit/note/{obit}", data={"csrf": CSRF, "note": "Obit text", "bio": "1"})
    assert order(obit) > 0
    empty = make(app, ("Nobody", "U", 0))["Nobody"]
    html = text(admin.get(f"/person/{empty}"))
    assert f"/edit/note/new?for={empty}&amp;bio=1" in html and f"/edit/note/new?for={empty}&amp;about=DEAT" in html

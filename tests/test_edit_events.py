import html as htmllib
import json

import pytest

from app.db import insert
from conftest import client_for, make, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def etype(app, kind, display):
    return sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = %s AND display = %s", (kind, display))[0]["eventtypeID"]


def event_data(app, **v):
    return {"csrf": "t", "eventtypeID": etype(app, "I", "Occupation"), **v}


def events(app, pid):
    return sql(app, "SELECT * FROM events WHERE persfamID = %s ORDER BY eventID", (pid,))


# --- events ---

def test_add_event_form_lists_types_for_kind(app, admin):
    ids = make(app, ("A", "M", 0), family=("A", None))
    html = text(admin.get(f"/edit/event/new?for={ids['A']}"))
    assert ">Occupation<" in html and ">Military<" in html and "_MILI" not in html  # friendly name
    assert html.count(">Email<") == 1 and ">Email (GEDCOM)<" in html  # TNG's two email types stay distinct
    assert ">Married<" not in html
    html = text(admin.get(f"/edit/event/new?for={ids['family']}"))
    assert ">Married<" in html and ">Occupation<" not in html
    assert admin.get("/edit/event/new?for=XNOBODY").status_code == 404
    assert admin.get("/edit/event/new").status_code == 404


def test_add_event_with_address(app, admin):
    ids = make(app, ("A", "M", 0))
    r = admin.post("/edit/event/new", data=event_data(app, **{
        "for": ids["A"], "eventdate": "abt 1930", "eventplace": "Worktown, Testshire", "age": "30",
        "agency": "Mill", "cause": "", "info": "Weaver\nnight shift", "city": "Worktown", "phone": "555"}))
    assert r.status_code == 302 and r.location.endswith(f"/person/{ids['A']}")
    e = events(app, ids["A"])[0]
    assert (e["eventdate"], str(e["eventdatetr"]), e["agency"], e["info"]) == ("abt 1930", "1930-00-00", "Mill", "Weaver\nnight shift")
    addr = sql(app, "SELECT * FROM addresses WHERE addressID = %s", (e["addressID"],))[0]
    assert (addr["city"], addr["phone"], addr["gedcom"]) == ("Worktown", "555", "tree1")
    assert sql(app, "SELECT 1 FROM places WHERE place = 'Worktown, Testshire'")
    page = text(admin.get(f"/person/{ids['A']}"))
    assert "Occupation" in page and "Weaver" in page and f"/edit/event/{e['eventID']}" in page
    assert '<address class="event-address">Worktown<br>555</address>' in page  # the address shows with the event
    assert '<span class="event-details small">Age given: 30 · Agency: Mill</span>' in page  # no cause: left out
    sql(app, "UPDATE addresses SET address1 = '1 Mill St', state = 'TS', zip = '12345' WHERE addressID = %s",
        (e["addressID"],))
    assert "1 Mill St<br>Worktown, TS 12345<br>555" in text(admin.get(f"/person/{ids['A']}"))
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'add event' AND record = %s", (str(e["eventID"]),))


def test_add_family_event(app, admin):
    ids = make(app, ("A", "M", 0), family=("A", None))
    admin.post("/edit/event/new", data={"csrf": "t", "for": ids["family"], "eventtypeID": etype(app, "F", "Census"),
                                         "eventdate": "4/1/1940"})
    assert events(app, ids["family"])[0]["eventdate"] == "1 APR 1940"  # typed month/day/year, saved the standard way
    assert "Census" in text(admin.get(f"/family/{ids['family']}"))


@pytest.mark.parametrize("changes,message", [
    ({"eventtypeID": ""}, "Choose an event type"),
    ({"eventtypeID": "29"}, "Choose an event type"),  # a family type on a person
    ({"eventdate": "x" * 51}, "eventdate is longer than 50"),
    ({"age": "x" * 13}, "age is longer than 12"),
    ({"zip": "x" * 11}, "zip is longer than 10"),
])
def test_event_validation(app, admin, changes, message):
    ids = make(app, ("A", "M", 0))
    r = admin.post("/edit/event/new", data=event_data(app, **{"for": ids["A"], **changes}))
    assert r.status_code == 200 and message in text(r) and not events(app, ids["A"])


def make_event(app, pid, **v):
    with app.app_context():
        return insert("events", {"gedcom": "tree1", "persfamID": pid,
                                     "eventtypeID": etype(app, "I", "Occupation"), **v})[1]


def test_edit_event_address_lifecycle(app, admin):
    ids = make(app, ("A", "M", 0))
    eid = make_event(app, ids["A"], eventdate="1900")
    html = text(admin.get(f"/edit/event/{eid}"))
    assert 'value="1900"' in html and "Delete event" in html and "<details >" in html  # no address: collapsed
    # add an address
    admin.post(f"/edit/event/{eid}", data=event_data(app, eventdate="1901", city="Addrtown"))
    e = events(app, ids["A"])[0]
    assert e["eventdate"] == "1901" and e["addressID"]
    assert "<details open>" in text(admin.get(f"/edit/event/{eid}"))
    # change it
    admin.post(f"/edit/event/{eid}", data=event_data(app, eventdate="1901", city="Newtown"))
    assert sql(app, "SELECT city FROM addresses WHERE addressID = %s", (e["addressID"],))[0]["city"] == "Newtown"
    # empty it: the address row goes
    admin.post(f"/edit/event/{eid}", data=event_data(app, eventdate="1901"))
    assert events(app, ids["A"])[0]["addressID"] == ""
    assert not sql(app, "SELECT 1 FROM addresses WHERE addressID = %s", (e["addressID"],))
    log = sql(app, "SELECT * FROM app_audit WHERE action = 'edit event' AND record = %s ORDER BY id LIMIT 1", (str(eid),))[0]
    assert json.loads(log["before_json"])["eventdate"] == "1900"


def test_edit_event_validation_keeps_input(app, admin):
    ids = make(app, ("A", "M", 0))
    eid = make_event(app, ids["A"], eventdate="1900")
    r = admin.post(f"/edit/event/{eid}", data=event_data(app, eventdate="1950", cause="x" * 91))
    assert "cause is longer than 90" in text(r) and 'value="1950"' in text(r)
    assert events(app, ids["A"])[0]["eventdate"] == "1900"
    assert admin.get("/edit/event/99999999").status_code == 404


def test_delete_event_and_its_links(app, admin):
    ids = make(app, ("A", "M", 0))
    with app.app_context():
        _, addr = insert("addresses", {"gedcom": "tree1", "city": "Gone"})
    eid = make_event(app, ids["A"], eventdate="1900", addressID=str(addr))
    with app.app_context():
        _, own = insert("xnotes", {"gedcom": "tree1", "note": "event note"})
        _, shared = insert("xnotes", {"gedcom": "tree1", "note": "shared event note"})
        insert("notelinks", {"gedcom": "tree1", "persfamID": ids["A"], "xnoteID": own, "eventID": str(eid)})
        insert("notelinks", {"gedcom": "tree1", "persfamID": ids["A"], "xnoteID": shared, "eventID": str(eid)})
        insert("notelinks", {"gedcom": "tree1", "persfamID": "XGMA", "xnoteID": shared})
        insert("citations", {"gedcom": "tree1", "persfamID": ids["A"], "eventID": str(eid), "sourceID": "S1"})
        insert("medialinks", {"gedcom": "tree1", "personID": ids["A"], "eventID": str(eid), "mediaID": 1,
                                  "linktype": "I"})
    assert "Delete Occupation 1900?" in text(admin.get(f"/edit/event/{eid}/delete"))
    r = admin.post(f"/edit/event/{eid}/delete", data={"csrf": "t"})
    assert r.location.endswith(f"/person/{ids['A']}")
    assert not events(app, ids["A"])
    for q in ("SELECT 1 FROM citations WHERE eventID = %s", "SELECT 1 FROM medialinks WHERE eventID = %s",
              "SELECT 1 FROM notelinks WHERE eventID = %s"):
        assert not sql(app, q, (str(eid),)), q
    assert not sql(app, "SELECT 1 FROM addresses WHERE addressID = %s", (addr,))
    assert not sql(app, "SELECT 1 FROM xnotes WHERE ID = %s", (own,))
    assert sql(app, "SELECT 1 FROM xnotes WHERE ID = %s", (shared,))  # still used by XGMA


def test_delete_event_of_unknown_type(app, admin):
    ids = make(app, ("A", "M", 0))
    with app.app_context():
        _, eid = insert("events", {"gedcom": "tree1", "persfamID": ids["A"], "eventtypeID": 999999})
    assert "Delete event?" in text(admin.get(f"/edit/event/{eid}/delete"))
    admin.post(f"/edit/event/{eid}/delete", data={"csrf": "t"})
    assert not events(app, ids["A"])


# --- notes ---

def notes(app, pid):
    return sql(app, "SELECT nl.*, x.note FROM notelinks nl JOIN xnotes x ON x.ID = nl.xnoteID"
                    " WHERE nl.persfamID = %s ORDER BY nl.ordernum", (pid,))


def test_add_notes_in_order(app, admin):
    ids = make(app, ("A", "M", 0))
    assert "Add note" in text(admin.get(f"/person/{ids['A']}"))
    assert 'name="for"' in text(admin.get(f"/edit/note/new?for={ids['A']}"))
    admin.post("/edit/note/new", data={"csrf": "t", "for": ids["A"], "note": "  First  "})
    admin.post("/edit/note/new", data={"csrf": "t", "for": ids["A"], "note": "Second", "secret": "1"})
    n = notes(app, ids["A"])
    assert [(x["note"], x["secret"], x["ordernum"]) for x in n] == [("First", 0, 1), ("Second", 1, 2)]
    page = text(admin.get(f"/person/{ids['A']}"))
    assert "Private note" in page and f"/edit/note/{n[0]['ID']}" in page


def test_note_validation(app, admin):
    ids = make(app, ("A", "M", 0))
    assert "The note is empty" in text(admin.post("/edit/note/new", data={"csrf": "t", "for": ids["A"], "note": " "}))
    assert "longer than 65000" in text(admin.post("/edit/note/new", data={"csrf": "t", "for": ids["A"], "note": "x" * 65001}))
    assert not notes(app, ids["A"])
    assert admin.get("/edit/note/new?for=XNOBODY").status_code == 404


def test_edit_note_and_shared_warning(app, admin):
    ids = make(app, ("A", "M", 0), ("B", "F", 0))
    with app.app_context():
        _, x = insert("xnotes", {"gedcom": "tree1", "note": "Shared text"})
        _, link = insert("notelinks", {"gedcom": "tree1", "persfamID": ids["A"], "xnoteID": x})
        insert("notelinks", {"gedcom": "tree1", "persfamID": ids["B"], "xnoteID": x})
    assert "shared with 1 other record" in text(admin.get(f"/edit/note/{link}"))
    admin.post(f"/edit/note/{link}", data={"csrf": "t", "note": "Changed text", "secret": "1"})
    assert notes(app, ids["B"])[0]["note"] == "Changed text"     # shared text changes everywhere
    assert notes(app, ids["A"])[0]["secret"] == 1 and notes(app, ids["B"])[0]["secret"] == 0  # privacy is per link
    assert "The note is empty" in text(admin.post(f"/edit/note/{link}", data={"csrf": "t", "note": ""}))
    assert admin.get("/edit/note/99999999").status_code == 404


def test_delete_notes(app, admin):
    ids = make(app, ("A", "M", 0), ("B", "F", 0))
    with app.app_context():
        _, x = insert("xnotes", {"gedcom": "tree1", "note": "y" * 250})
        _, shared_link = insert("notelinks", {"gedcom": "tree1", "persfamID": ids["A"], "xnoteID": x})
        insert("notelinks", {"gedcom": "tree1", "persfamID": ids["B"], "xnoteID": x})
        _, y = insert("xnotes", {"gedcom": "tree1", "note": "solo"})
        _, solo_link = insert("notelinks", {"gedcom": "tree1", "persfamID": ids["A"], "xnoteID": y})
    html = text(admin.get(f"/edit/note/{shared_link}/delete"))
    assert "y" * 200 + "…" in html and "it stays there" in html
    admin.post(f"/edit/note/{shared_link}/delete", data={"csrf": "t"})
    assert sql(app, "SELECT 1 FROM xnotes WHERE ID = %s", (x,)) and notes(app, ids["B"])
    assert "it stays there" not in text(admin.get(f"/edit/note/{solo_link}/delete"))
    r = admin.post(f"/edit/note/{solo_link}/delete", data={"csrf": "t"})
    assert r.location.endswith(f"/person/{ids['A']}") and not notes(app, ids["A"])
    assert not sql(app, "SELECT 1 FROM xnotes WHERE ID = %s", (y,))


def test_family_notes_go_back_to_family(app, admin):
    ids = make(app, ("A", "M", 0), family=("A", None))
    r = admin.post("/edit/note/new", data={"csrf": "t", "for": ids["family"], "note": "Family story"})
    assert r.location.endswith(f"/family/{ids['family']}")
    assert "Family story" in text(admin.get(f"/family/{ids['family']}"))


def test_permissions(app, guest):
    ids = make(app, ("A", "M", 0))
    eid = make_event(app, ids["A"])
    with app.app_context():
        _, x = insert("xnotes", {"gedcom": "tree1", "note": "n"})
        _, link = insert("notelinks", {"gedcom": "tree1", "persfamID": ids["A"], "xnoteID": x})
    for url in (f"/edit/event/new?for={ids['A']}", f"/edit/event/{eid}", f"/edit/event/{eid}/delete",
                f"/edit/note/new?for={ids['A']}", f"/edit/note/{link}", f"/edit/note/{link}/delete"):
        assert guest.get(url).status_code == 403, url
    adder, editor = client_for(app, "fx_adder"), client_for(app, "fx_editor")
    assert adder.get(f"/edit/event/{eid}").status_code == 403 and adder.get(f"/edit/event/new?for={ids['A']}").status_code == 200
    assert editor.get(f"/edit/note/{link}/delete").status_code == 403
    page = text(guest.get("/person/XHUSB"))
    assert "Add note" not in page and "/edit/" not in page

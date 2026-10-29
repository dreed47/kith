"""Kinds of event: add, rename, hide, delete; hidden kinds and existing events."""
from conftest import CSRF, client_for, make, sql


def text(r):
    return r.get_data(as_text=True)


def site_admin(app):
    return client_for(app, "fx_siteadmin")


def test_add_rename_hide_delete(app):
    c = site_admin(app)
    assert "Kinds of event" in text(c.get("/admin/event-types")) and "Military" in text(c.get("/admin/event-types"))
    c.post("/admin/event-types", data={"csrf": CSRF, "type": "I", "name": "Ship arrival"})
    t = sql(app, "SELECT * FROM eventtypes WHERE display = 'Ship arrival'")[0]
    assert (t["tag"], t["description"], t["type"], t["keep"]) == ("EVEN", "Ship arrival", "I", 1)
    for name, message in (("", "Enter a name"), ("x" * 101, "longer than 100"), ("ship ARRIVAL", "already a person")):
        assert message in text(c.post("/admin/event-types", data={"csrf": CSRF, "type": "I", "name": name},
                                      follow_redirects=True)), message
    assert c.post("/admin/event-types", data={"csrf": CSRF, "type": "X", "name": "y"}).status_code == 400
    tid = t["eventtypeID"]
    assert "Used by 0 events" in text(c.get(f"/admin/event-types/{tid}"))
    assert "longer than 100" in text(c.post(f"/admin/event-types/{tid}", data={"csrf": CSRF, "name": "x" * 101}))
    c.post(f"/admin/event-types/{tid}", data={"csrf": CSRF, "name": "Arrived by ship"})  # keep unticked: hidden
    t = sql(app, "SELECT * FROM eventtypes WHERE eventtypeID = %s", (tid,))[0]
    assert (t["display"], t["description"], t["keep"]) == ("Arrived by ship", "Arrived by ship", 0)
    assert '<span class="tag">hidden</span>' in text(c.get("/admin/event-types"))
    c.post(f"/admin/event-types/{tid}/delete", data={"csrf": CSRF})
    assert not sql(app, "SELECT * FROM eventtypes WHERE eventtypeID = %s", (tid,))
    occu = sql(app, "SELECT eventtypeID FROM eventtypes WHERE tag = 'OCCU' AND type = 'I'")[0]["eventtypeID"]
    c.post(f"/admin/event-types/{occu}", data={"csrf": CSRF, "name": "Occupation", "keep": "1"})
    assert sql(app, "SELECT description FROM eventtypes WHERE eventtypeID = %s", (occu,))[0]["description"] == ""
    assert c.get("/admin/event-types/999999").status_code == 404
    assert client_for(app, "fx_admin").get("/admin/event-types").status_code == 403


def test_used_kind_cannot_be_deleted(app):
    c = site_admin(app)
    c.post("/admin/event-types", data={"csrf": CSRF, "type": "F", "name": "Vow renewal"})
    tid = sql(app, "SELECT eventtypeID FROM eventtypes WHERE display = 'Vow renewal'")[0]["eventtypeID"]
    fid = make(app, ("Vowh", "M", 0), ("Voww", "F", 0), family=("Vowh", "Voww"))["family"]
    c.post("/edit/event/new", data={"csrf": CSRF, "for": fid, "eventtypeID": tid, "eventdate": "1990"})
    assert "Delete this kind" not in text(c.get(f"/admin/event-types/{tid}"))
    assert c.post(f"/admin/event-types/{tid}/delete", data={"csrf": CSRF}).status_code == 400


def test_hidden_kind_kept_on_existing_events(app):
    c = site_admin(app)
    c.post("/admin/event-types", data={"csrf": CSRF, "type": "I", "name": "Lodge member"})
    tid = sql(app, "SELECT eventtypeID FROM eventtypes WHERE display = 'Lodge member'")[0]["eventtypeID"]
    pid = make(app, ("Lodger", "M", 0))["Lodger"]
    c.post("/edit/event/new", data={"csrf": CSRF, "for": pid, "eventtypeID": tid, "eventdate": "1920"})
    eid = sql(app, "SELECT eventID FROM events WHERE persfamID = %s", (pid,))[0]["eventID"]
    c.post(f"/admin/event-types/{tid}", data={"csrf": CSRF, "name": "Lodge member"})  # hide it
    assert "Lodge member</option>" not in text(c.get(f"/edit/event/new?for={pid}"))  # not offered for new events
    assert "Lodge member</option>" in text(c.get(f"/edit/event/{eid}"))  # but the event that has it can still be saved
    r = c.post(f"/edit/event/{eid}", data={"csrf": CSRF, "eventtypeID": tid, "eventdate": "1921"})
    assert r.status_code == 302
    assert sql(app, "SELECT eventtypeID FROM events WHERE eventID = %s", (eid,))[0]["eventtypeID"] == tid

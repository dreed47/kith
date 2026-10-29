"""Edit pages show and change only what the editor may see: a living or private person they can't see, and a
private note without the right to private notes, look missing, as on the site's own pages."""
from conftest import client_for, make, sql
from flask import g

from app.db import insert, query


def test_limited_editor_cannot_open_hidden_people(app):
    ids = make(app, ("Hid", "M", 1), ("Hidwife", "F", 1), family=("Hid", "Hidwife"), kids=())
    fid = ids["family"]
    with app.app_context():
        g.user = {"username": "t"}
        _, eid = insert("events", {"gedcom": "tree1", "persfamID": ids["Hid"], "eventtypeID": 1})
        _, xid = insert("xnotes", {"gedcom": "tree1", "note": "hidden note"})
        _, nid = insert("notelinks", {"gedcom": "tree1", "persfamID": ids["Hid"], "xnoteID": xid})
        plain = query("SELECT id FROM fixture_ids WHERE name = 'plain'")[0]["id"]
        insert("medialinks", {"gedcom": "tree1", "linktype": "I", "personID": ids["Hid"], "mediaID": plain})
        _, mid = insert("media", {"gedcom": "tree1", "mediatypeID": "photos", "path": "plain.jpg", "usecollfolder": 0,
                                  "description": "Hidden photo", "mediakey": "Xhiddenphoto"})  # only on Hid's page
        insert("medialinks", {"gedcom": "tree1", "linktype": "I", "personID": ids["Hid"], "mediaID": mid})
    sql(app, "UPDATE families SET living = 1 WHERE familyID = %s", (fid,))
    # Every right but seeing living people, so only visibility is tested.
    sql(app, "UPDATE users SET allow_add = 1, allow_delete = 1 WHERE username = 'fx_limited_editor'")
    limited, editor = client_for(app, "fx_limited_editor"), client_for(app, "fx_admin")
    pages = [f"/edit/person/{ids['Hid']}", f"/edit/person/{ids['Hid']}/delete", f"/edit/family/{fid}",
             f"/edit/family/{fid}/delete", f"/edit/person/new?child_of={fid}", f"/edit/person/new?spouse_of={ids['Hid']}",
             f"/edit/family/new?child={ids['Hid']}", f"/edit/event/{eid}", f"/edit/event/new?for={ids['Hid']}",
             f"/edit/note/{nid}", f"/edit/media/arrange/{ids['Hid']}", f"/edit/media/{mid}", f"/edit/merge?keep={ids['Hid']}&merge={ids['Hidwife']}"]
    try:
        for page in pages:
            assert limited.get(page).status_code == 404, page
            assert editor.get(page).status_code == 200, page  # who may see them still can
        assert limited.post(f"/edit/family/{fid}/children", data={"csrf": "t"}).status_code == 404
    finally:
        sql(app, "UPDATE users SET allow_add = 0, allow_delete = 0 WHERE username = 'fx_limited_editor'")


def test_private_notes_need_their_right(app):
    xhusb_secret = sql(app, "SELECT ID FROM notelinks WHERE persfamID = 'XHUSB' AND secret = 1")[0]["ID"]
    limited = client_for(app, "fx_limited_editor")  # may edit, but not read private notes
    assert limited.get(f"/edit/note/{xhusb_secret}").status_code == 404
    assert client_for(app, "fx_admin").get(f"/edit/note/{xhusb_secret}").status_code == 200
    sql(app, "UPDATE users SET allow_delete = 1 WHERE username = 'fx_limited_editor'")
    try:
        assert limited.get(f"/edit/note/{xhusb_secret}/delete").status_code == 404
    finally:
        sql(app, "UPDATE users SET allow_delete = 0 WHERE username = 'fx_limited_editor'")

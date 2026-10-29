"""Ordering a record's photos and choosing the main picture."""
from conftest import CSRF, client_for, make, sql

from app.db import insert
from app.views import main_photo


def text(r):
    return r.get_data(as_text=True)


def m(mid, kind, default="", order=1, path="a.jpg"):
    return {"mediaID": mid, "path": path, "abspath": 0, "mediatypeID": kind, "defphoto": default, "linkorder": order}


def test_main_photo_rule():
    photo, stone, doc = m(1, "photos", order=2), m(2, "headstones", "1", 1), m(3, "documents", "1", 3)
    assert main_photo([photo, stone]) == photo  # TNG's automatic default is usually a headstone
    assert main_photo([photo, stone, doc]) == doc  # a chosen default that isn't a headstone wins
    assert main_photo([stone, m(4, "documents", order=2)]) == stone  # no photo: the headstone will do
    assert main_photo([m(5, "photos", path="letter.pdf")]) is None


def test_arrange(app, admin):
    pid = make(app, ("Pictured", "F", 0))["Pictured"]
    mids = [r["mediaID"] for r in sql(app, "SELECT mediaID FROM media WHERE path LIKE '%%.jpg' LIMIT 3")]
    with app.app_context():
        links = [insert("medialinks", {"gedcom": "tree1", "personID": pid, "mediaID": mid, "linktype": "I",
                                           "ordernum": n, "defphoto": "1" if n == 1 else ""})[1]
                 for n, mid in enumerate(mids, 1)]
    html = text(admin.get(f"/edit/media/arrange/{pid}"))
    assert f'name="pos{links[2]}" value="3"' in html and "Arrange, choose the main picture" in text(admin.get(f"/person/{pid}"))
    r = admin.post(f"/edit/media/arrange/{pid}", data={"csrf": CSRF, f"pos{links[0]}": "3", f"pos{links[1]}": "",
                                                       f"pos{links[2]}": "1", "main": str(links[2])})
    assert r.status_code == 302
    rows = {r["medialinkID"]: r for r in sql(app, "SELECT * FROM medialinks WHERE personID = %s", (pid,))}
    assert [rows[x]["ordernum"] for x in links] == [2, 3, 1] and [rows[x]["defphoto"] for x in links] == ["", "", "1"]
    admin.post(f"/edit/media/arrange/{pid}", data={"csrf": CSRF})  # no main chosen: defaults left alone
    assert sql(app, "SELECT defphoto FROM medialinks WHERE medialinkID = %s", (links[2],))[0]["defphoto"] == "1"
    assert sql(app, "SELECT action FROM app_audit WHERE action = 'arrange media' AND record = %s", (pid,))
    log = text(client_for(app, "fx_siteadmin").get(f"/admin/activity?record={pid}"))  # rows saved as a list
    assert f"{links[0]}.ordernum" in log and f"{links[2]}.defphoto" in log
    assert admin.get("/edit/media/arrange/NOPE").status_code == 404
    bare = make(app, ("Unpictured", "M", 0))["Unpictured"]
    assert admin.get(f"/edit/media/arrange/{bare}").status_code == 404  # nothing to arrange
    assert client_for(app, "fx_adder").get(f"/edit/media/arrange/{pid}").status_code == 403

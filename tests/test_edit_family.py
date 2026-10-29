import html as htmllib
import json

import pytest

from app.db import insert
from conftest import client_for, make, sql

def family_form(f, **changes):
    form = {k: f[k] for k in ("husband", "wife", "marrdate", "marrplace", "marrtype", "divdate", "divplace")}
    form.update(csrf="t", changedate=str(f["changedate"]), **({"living": "1"} if f["living"] else {}))
    form.update(changes)
    return form


def fam(app, fid):
    return sql(app, "SELECT * FROM families WHERE familyID = %s", (fid,))[0]


def kids_of(app, fid):
    return [r["personID"] for r in sql(app, "SELECT personID FROM children WHERE familyID = %s ORDER BY ordernum", (fid,))]


def haskids(app, pid):
    return [r["haskids"] for r in sql(app, "SELECT haskids FROM children WHERE personID = %s", (pid,))]


# --- edit ---

def test_edit_form(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), ("A", "F", 0), ("B", "M", 0), family=("Dad", "Mom"), kids=("A", "B"))
    html = admin.get(f"/edit/family/{ids['family']}").get_data(as_text=True)
    assert f'value="{ids["Dad"]}"' in html and "Dad " in html and 'data-find="/edit/find"' in html
    assert html.count("Move up") == 2 and html.count("disabled>") == 2  # first can't go up, last can't go down
    assert "Delete this family" in html and "create a new child" in html


def test_edit_marriage_details(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), family=("Dad", "Mom"))
    f = fam(app, ids["family"])
    r = admin.post(f"/edit/family/{f['familyID']}", data=family_form(
        f, marrdate="abt 1890", marrplace="Wedtown, Testshire", marrtype="Civil", divdate="1901", private="1"))
    assert r.status_code == 302 and r.location.endswith(f"/family/{f['familyID']}")
    after = fam(app, f["familyID"])
    assert (after["marrdate"], str(after["marrdatetr"]), after["marrtype"]) == ("abt 1890", "1890-00-00", "Civil")
    assert str(after["divdatetr"]) == "1901-00-00" and after["private"] == 1 and after["changedby"] == "fx_admin"
    assert sql(app, "SELECT 1 FROM places WHERE place = 'Wedtown, Testshire'")
    log = sql(app, "SELECT * FROM app_audit WHERE record = %s AND action = 'edit family'", (f["familyID"],))[0]
    assert json.loads(log["after_json"])["marrdate"] == "abt 1890"


def test_change_spouse_moves_children_marker_and_order(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), ("Mom2", "F", 0), ("Kid", "M", 0),
               family=("Dad", "Mom"), kids=("Kid",))
    # Both mothers appear as someone's child, so their "has children" markers are visible.
    other = make(app, ("P", "M", 0), family=("P", None))
    for m in ("Mom", "Mom2"):
        insert_child(app, other["family"], ids[m])
    sql(app, "UPDATE children SET haskids = 1 WHERE personID = %s", (ids["Mom"],))
    add_family(app, ids["family"] + "B", wife=ids["Mom2"])
    f = fam(app, ids["family"])
    admin.post(f"/edit/family/{f['familyID']}", data=family_form(f, wife=ids["Mom2"].lower()))  # IDs are case-insensitive
    after = fam(app, f["familyID"])
    assert after["wife"] == ids["Mom2"] and after["wifeorder"] == 2  # her second family
    assert haskids(app, ids["Mom"]) == [0] and haskids(app, ids["Mom2"]) == [1]


def add_family(app, fid, husband="", wife=""):
    with app.app_context():
        insert("families", {"gedcom": "tree1", "familyID": fid, "husband": husband, "wife": wife,
                                "husborder": int(bool(husband)), "wifeorder": int(bool(wife))})


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def insert_child(app, fid, pid):
    with app.app_context():
        insert("children", {"gedcom": "tree1", "familyID": fid, "personID": pid, "ordernum": 1})


def test_remove_one_spouse(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), family=("Dad", "Mom"))
    f = fam(app, ids["family"])
    admin.post(f"/edit/family/{f['familyID']}", data=family_form(f, wife=""))
    after = fam(app, f["familyID"])
    assert after["wife"] == "" and after["wifeorder"] == 0 and after["husband"] == ids["Dad"]


@pytest.mark.parametrize("changes,message", [
    ({"husband": "XNOBODY"}, "Husband: no person with ID XNOBODY"),
    ({"husband": "", "wife": ""}, "Choose at least one spouse"),
    ({"wife": "SAME"}, "can't be the same person"),
    ({"marrdate": "x" * 51}, "Marriage date is longer than 50"),
    ({"divplace": "x" * 249}, "Divorce place is longer than 248"),
    ({"marrtype": "x" * 91}, "Marriage type is longer than 90"),
    ({"changedate": "2000-01-01 00:00:00"}, "Someone else saved this family"),
])
def test_edit_validation(app, admin, changes, message):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), family=("Dad", "Mom"))
    f = fam(app, ids["family"])
    if changes.get("wife") == "SAME":
        changes["wife"] = ids["Dad"]
    r = admin.post(f"/edit/family/{f['familyID']}", data=family_form(f, **changes))
    assert r.status_code == 200 and message in text(r)
    assert fam(app, f["familyID"])["changedate"] == f["changedate"]  # nothing saved


def test_child_cannot_be_spouse(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0), family=("Dad", None), kids=("Kid",))
    f = fam(app, ids["family"])
    r = admin.post(f"/edit/family/{f['familyID']}", data=family_form(f, wife=ids["Kid"]))
    assert "can't also be one of its spouses" in text(r)


# --- new family ---

def test_new_family_form_prefill(app, admin):
    ids = make(app, ("Dad", "M", 1))
    html = admin.get(f"/edit/family/new?husband={ids['Dad']}").get_data(as_text=True)
    assert f'value="{ids["Dad"]}"' in html and 'name="living" value="1" checked' in html and "<h1>Add family</h1>" in html
    kid = make(app, ("Kid", "M", 0))
    html = admin.get(f"/edit/family/new?child={kid['Kid']}").get_data(as_text=True)
    assert "Add parents of Kid" in html and f'name="child" value="{kid["Kid"]}"' in html
    assert admin.get("/edit/family/new?child=XNOPE").status_code == 404


def test_new_family_with_existing_spouses(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 1))
    r = admin.post("/edit/family/new", data={"csrf": "t", "husband": ids["Dad"], "wife": ids["Mom"],
                                              "marrdate": "6/1/1920", "marrplace": "Newwed, Testshire", "living": "1"})
    f = sql(app, "SELECT * FROM families WHERE husband = %s", (ids["Dad"],))[0]
    assert r.location.endswith(f"/family/{f['familyID']}") and f["familyID"].startswith("F")
    assert (f["wife"], f["husborder"], f["wifeorder"], f["living"]) == (ids["Mom"], 1, 1, 1)
    assert (f["marrdate"], str(f["marrdatetr"])) == ("1 JUN 1920", "1920-06-01") and sql(app, "SELECT 1 FROM places WHERE place = 'Newwed, Testshire'")


def test_add_parents(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0))
    admin.post("/edit/family/new", data={"csrf": "t", "husband": ids["Dad"], "child": ids["Kid"]})
    f = sql(app, "SELECT * FROM families WHERE husband = %s", (ids["Dad"],))[0]
    assert kids_of(app, f["familyID"]) == [ids["Kid"]] and f["wife"] == "" and f["wifeorder"] == 0
    assert sql(app, "SELECT famc FROM people WHERE personID = %s", (ids["Kid"],))[0]["famc"] == f["familyID"]


def test_new_family_rejects_child_as_parent(app, admin):
    ids = make(app, ("Kid", "M", 0))
    r = admin.post("/edit/family/new", data={"csrf": "t", "husband": ids["Kid"], "child": ids["Kid"]})
    assert "can't also be a parent" in text(r)
    r = admin.post("/edit/family/new", data={"csrf": "t"})
    assert "Choose at least one spouse" in r.get_data(as_text=True)


# --- children ---

def post_child(client, fid, action, pid):
    return client.post(f"/edit/family/{fid}/children", data={"csrf": "t", "action": action, "personID": pid})


def test_add_existing_child(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0), ("GrandKid", "M", 0), family=("Dad", None))
    add_family(app, ids["family"] + "K", wife=ids["Kid"])
    insert_child(app, ids["family"] + "K", ids["GrandKid"])  # Kid already has a child of her own
    r = post_child(admin, ids["family"], "add", ids["Kid"].lower())
    assert r.status_code == 302 and r.location.endswith(f"/edit/family/{ids['family']}#children")
    assert kids_of(app, ids["family"]) == [ids["Kid"]]
    assert haskids(app, ids["Kid"]) == [1]       # her own child row knows she has children
    assert sql(app, "SELECT famc FROM people WHERE personID = %s", (ids["Kid"],))[0]["famc"] == ids["family"]


def test_add_child_refusals(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0), family=("Dad", None), kids=("Kid",))
    for pid, msg in (("XNOPE", "No person with ID XNOPE"), (ids["Kid"], "already a child"),
                     (ids["Dad"], "spouse can't also be a child")):
        r = post_child(admin, ids["family"], "add", pid)
        assert msg in text(admin.get(r.location)), msg
    assert kids_of(app, ids["family"]) == [ids["Kid"]]


def test_remove_child_resets_parent_family_and_markers(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0), family=("Dad", None), kids=("Kid",))
    other = make(app, ("Mom", "F", 0), family=(None, "Mom"))
    insert_child(app, other["family"], ids["Kid"])  # Kid is also in a second parent family
    grand = make(app, ("G", "M", 0), family=("G", None), kids=())
    insert_child(app, grand["family"], ids["Dad"])  # so Dad's own child row exists
    sql(app, "UPDATE children SET haskids = 1 WHERE personID = %s", (ids["Dad"],))
    post_child(admin, ids["family"], "remove", ids["Kid"])
    assert kids_of(app, ids["family"]) == []
    assert sql(app, "SELECT famc FROM people WHERE personID = %s", (ids["Kid"],))[0]["famc"] == other["family"]
    assert haskids(app, ids["Dad"]) == [0]
    assert sql(app, "SELECT 1 FROM people WHERE personID = %s", (ids["Kid"],))  # the person still exists


def test_reorder_children(app, admin):
    ids = make(app, ("Dad", "M", 0), ("A", "F", 0), ("B", "F", 0), ("C", "F", 0), family=("Dad", None),
               kids=("A", "B", "C"))
    fid, a, b, c = ids["family"], ids["A"], ids["B"], ids["C"]
    post_child(admin, fid, "down", a)
    assert kids_of(app, fid) == [b, a, c]
    post_child(admin, fid, "up", c)
    assert kids_of(app, fid) == [b, c, a]
    post_child(admin, fid, "up", b)  # already first: nothing changes
    post_child(admin, fid, "down", a)  # already last
    assert kids_of(app, fid) == [b, c, a]
    assert [r["ordernum"] for r in sql(app, "SELECT ordernum FROM children WHERE familyID = %s ORDER BY ordernum", (fid,))] == [1, 2, 3]


def test_children_bad_requests(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0), family=("Dad", None))
    assert post_child(admin, ids["family"], "explode", ids["Kid"]).status_code == 400
    assert post_child(admin, ids["family"], "remove", ids["Kid"]).status_code == 400  # not a child here
    assert post_child(admin, "XNOFAM", "add", ids["Kid"]).status_code == 404


# --- delete ---

def test_delete_family_keeps_people(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), ("Kid", "F", 0), family=("Dad", "Mom"), kids=("Kid",))
    fid = ids["family"]
    occu = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'F' LIMIT 1")[0]["eventtypeID"]
    with app.app_context():
        insert("events", {"gedcom": "tree1", "persfamID": fid, "eventtypeID": occu})
        _, x = insert("xnotes", {"gedcom": "tree1", "note": "family note"})
        insert("notelinks", {"gedcom": "tree1", "persfamID": fid, "xnoteID": x})
    html = admin.get(f"/edit/family/{fid}/delete").get_data(as_text=True)
    assert "links to its 1 child(ren)" in html and "1 event(s)" in html and "1 note(s)" in html
    r = admin.post(f"/edit/family/{fid}/delete", data={"csrf": "t"})
    assert r.location.endswith(f"/person/{ids['Dad']}")
    assert not sql(app, "SELECT 1 FROM families WHERE familyID = %s", (fid,))
    assert not sql(app, "SELECT 1 FROM events WHERE persfamID = %s", (fid,))
    assert not sql(app, "SELECT 1 FROM xnotes WHERE ID = %s", (x,))
    assert sql(app, "SELECT famc FROM people WHERE personID = %s", (ids["Kid"],))[0]["famc"] == ""
    assert len(sql(app, f"SELECT 1 FROM people WHERE personID IN {tuple(ids[k] for k in ('Dad', 'Mom', 'Kid'))}")) == 3
    log = sql(app, "SELECT * FROM app_audit WHERE action = 'delete family' AND record = %s", (fid,))[0]
    assert json.loads(log["before_json"])["children"][0]["personID"] == ids["Kid"]


def test_delete_family_without_spouses_goes_home(app, admin):
    ids = make(app, ("Kid", "F", 0), family=(None, None), kids=("Kid",))
    r = admin.post(f"/edit/family/{ids['family']}/delete", data={"csrf": "t"})
    assert r.location.endswith("/")
    assert admin.get("/edit/family/XNOFAM/delete").status_code == 404
    assert admin.get("/edit/family/XNOFAM").status_code == 404


# --- permissions and person finder ---

def test_permissions(app, guest):
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0), family=("Dad", None), kids=("Kid",))
    fid = ids["family"]
    for r in (guest.get(f"/edit/family/{fid}"), guest.get("/edit/family/new"), guest.get(f"/edit/family/{fid}/delete"),
              guest.get("/edit/find?q=x"), post_child(guest, fid, "add", ids["Dad"])):
        assert r.status_code == 403
    adder, editor = client_for(app, "fx_adder"), client_for(app, "fx_editor")
    assert post_child(adder, fid, "remove", ids["Kid"]).status_code == 403   # adding isn't editing
    assert post_child(editor, fid, "add", ids["Dad"] + "X").status_code == 403  # editing isn't adding
    assert editor.get(f"/edit/family/{fid}/delete").status_code == 403
    assert adder.get("/edit/find?q=Fixture").status_code == 200


def test_find(app, admin):
    assert admin.get("/edit/find").get_json() == []
    hits = admin.get("/edit/find?q=Wanda").get_json()
    assert {"id": "XWIFE", "label": "Wanda Fixture (? – )"} in hits
    assert [h["id"] for h in admin.get("/edit/find?q=Hal Fixture").get_json()] == ["XHUSB"]
    assert [h["id"] for h in admin.get("/edit/find?q=XGPA").get_json()] == ["XGPA"]


def test_person_page_family_links(app, admin):
    ids = make(app, ("Solo", "F", 0))
    html = admin.get(f"/person/{ids['Solo']}").get_data(as_text=True)
    assert f"/edit/family/new?wife={ids['Solo']}" in html and f"/edit/family/new?child={ids['Solo']}" in html
    html = admin.get("/person/XKID").get_data(as_text=True)
    assert "/edit/family/new?husband=" in html and "Add parents" not in html  # already has parents
    assert "/edit/family/XFAM/delete" in admin.get("/family/XFAM").get_data(as_text=True)


def test_children_oldest_first_until_reordered(app, admin):
    ids = make(app, ("Dad", "M", 0), ("A", "F", 0), ("B", "F", 0), ("C", "F", 0), family=("Dad", None),
               kids=("A", "B", "C"))
    fid, a, b, c = ids["family"], ids["A"], ids["B"], ids["C"]
    for pid, born in ((a, "1910-05-01"), (c, "1900-01-01")):  # B has no birth date, so goes last
        sql(app, "UPDATE people SET birthdatetr = %s WHERE personID = %s", (born, pid))

    def shown(url):
        html = admin.get(url).get_data(as_text=True)
        return sorted((a, b, c), key=html.find)

    assert shown(f"/family/{fid}") == [c, a, b]
    assert shown(f"/edit/family/{fid}") == [c, a, b]
    post_child(admin, fid, "down", c)  # moves from the order shown, then that order sticks
    assert kids_of(app, fid) == [a, c, b]
    assert shown(f"/family/{fid}") == [a, c, b]

"""Relationship naming on a made-up tree (no database), then the pages."""
import html as htmllib
import time

import pytest

from app import relations
from app.relations import blood_name, ordinal, relationship
from conftest import client_for, make, sql

# A made-up family. Families: (id, husband, wife, children)
FAMILIES = [
    ("F1", "GGPA", "GGMA", ["GPA", "GAUNT"]),
    ("F2", "GPA", "GMA", ["DAD", "UNCLE"]),
    ("F3", "DAD", "MOM", ["ME", "SIS"]),
    ("F4", "DAD", "MOM2", ["HALFBRO"]),
    ("F5", "UNCLE", "AUNTIE", ["COUSIN"]),
    ("F6", "COUSIN", "COUSINWIFE", ["COUSINKID"]),
    ("F7", "ME", "WIFE", ["SON"]),
    ("F8", "SON", "DIL", ["GRANDSON"]),
    ("F9", "WIFEDAD", "WIFEMOM", ["WIFE", "WIFEBRO"]),
    ("F10", "SISHUSB", "SIS", ["NIECE"]),
    ("F11", "NIECE_H", "NIECE", ["GRANDNIECE"]),
    ("F12", "GAUNT_H", "GAUNT", ["GCOUSIN1"]),
    ("F13", "STEPDAD", "MOM", []),
    ("F14", "WIFEX", "WIFE", ["STEPSON"]),
    ("F15", "GRANDSON", "GSW", ["GGSON"]),
    ("F16", "GGSON", "", ["GGGSON"]),
    ("F17", "", "", ["LONER"]),
]
SEX = {k: "M" for k in ("GGPA", "GPA", "DAD", "UNCLE", "ME", "HALFBRO", "COUSIN", "SON", "GRANDSON", "WIFEDAD",
                        "WIFEBRO", "SISHUSB", "NIECE_H", "GAUNT_H", "STEPDAD", "WIFEX", "STEPSON", "GGSON", "GGGSON")}
SEX.update({k: "F" for k in ("GGMA", "GMA", "MOM", "MOM2", "SIS", "AUNTIE", "COUSINWIFE", "WIFE", "DIL", "WIFEMOM",
                             "NIECE", "GAUNT", "GSW", "GRANDNIECE")})


@pytest.fixture
def tree(monkeypatch):
    parents, spouses = {}, {}
    for fid, h, w, kids in FAMILIES:
        for k in kids:
            parents.setdefault(k, []).append((fid, h, w))
        if h and w:
            spouses.setdefault(h, []).append((fid, w))
            spouses.setdefault(w, []).append((fid, h))
    monkeypatch.setattr(relations, "graph", lambda: {"parents": parents, "spouses": spouses})


def rel(a, b):
    r = relationship(a, b, lambda p: SEX.get(p, "U"))
    return r and r["label"]


@pytest.mark.parametrize("b,label", [
    ("ME", "self"), ("DAD", "father"), ("MOM", "mother"), ("GPA", "grandfather"), ("GGMA", "great-grandmother"),
    ("SON", "son"), ("GRANDSON", "grandson"), ("GGSON", "great-grandson"), ("GGGSON", "great-great-grandson"),
    ("SIS", "sister"), ("HALFBRO", "half-brother"), ("NIECE", "niece"), ("GRANDNIECE", "grandniece"),
    ("UNCLE", "uncle"), ("GAUNT", "great-aunt"), ("COUSIN", "1st cousin"), ("COUSINKID", "1st cousin, once removed"),
    ("GCOUSIN1", "1st cousin, once removed"),
    ("WIFE", "wife"), ("WIFEDAD", "father-in-law"), ("WIFEBRO", "brother-in-law"), ("STEPSON", "stepson"),
    ("DIL", "daughter-in-law"), ("SISHUSB", "brother-in-law"), ("STEPDAD", "stepfather"),
    ("COUSINWIFE", "1st cousin's wife"), ("NIECE_H", "niece's husband"), ("AUNTIE", "uncle's wife"),
    ("WIFEX", None), ("LONER", None),
])
def test_names_from_me(tree, b, label):
    assert rel("ME", b) == label


def test_other_directions(tree):
    assert rel("COUSINKID", "ME") == "1st cousin, once removed"
    assert rel("GRANDSON", "GPA") == "great-great-grandfather"
    assert rel("WIFEBRO", "ME") == "brother-in-law"        # married his sister
    assert rel("WIFEDAD", "SON") == "grandson"
    assert rel("HALFBRO", "SIS") == "half-sister"
    assert rel("WIFE", "COUSIN") == "husband's 1st cousin"  # a's spouse's relative, no special word


def test_paths_and_common_ancestors(tree):
    r = relationship("ME", "COUSINKID", lambda p: SEX.get(p, "U"))
    assert r["common"] == ["GMA", "GPA"] or r["common"] == ["GPA", "GMA"]
    assert r["path_a"] == ["ME", "DAD", r["common"][0]] and r["path_b"][0] == "COUSINKID"
    half = relationship("ME", "HALFBRO", lambda p: SEX.get(p, "U"))
    assert half["common"] == ["DAD"] and half["half"]
    assert relationship("ME", "WIFEDAD", lambda p: SEX.get(p, "U"))["via"] == "WIFE"


@pytest.mark.parametrize("up_a,up_b,sex,half,label", [
    (1, 0, "U", False, "parent"), (0, 1, "U", False, "child"), (1, 1, "U", False, "sibling"),
    (1, 2, "U", True, "half-niece or nephew"), (2, 1, "U", False, "aunt or uncle"), (5, 1, "M", False, "3rd great-uncle"),
    (1, 3, "U", False, "grandniece or grandnephew"), (3, 1, "U", False, "great-aunt or great-uncle"),
    (1, 5, "F", False, "great-great-grandniece"), (6, 0, "M", False, "4th great-grandfather"),
    (3, 3, "U", True, "half 2nd cousin"), (12, 15, "U", False, "11th cousin, 3 times removed"),
])
def test_blood_names(up_a, up_b, sex, half, label):
    assert blood_name(up_a, up_b, sex, half) == label


def test_ordinals():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st"]


def test_marriage_fallbacks_use_neutral_words(tree):
    assert relationship("ME", "LONER", lambda p: "U") is None
    assert rel("UNKNOWNPERSON", "ME") is None


# --- graph loading and caching, on the test DB ---

def test_graph_cache_and_invalidate(app, monkeypatch):
    with app.app_context():
        relations.invalidate()
        g1 = relations.graph()
        assert ("XFAM", "XHUSB", "XWIFE") in g1["parents"]["XKID"] and ("XFAM", "XWIFE") in g1["spouses"]["XHUSB"]
        assert relations.graph() is g1                  # cached
        relations.invalidate()
        assert relations.graph() is not g1              # reloaded after an edit
        g2 = relations.graph()
        monkeypatch.setattr(time, "time", lambda: relations._cache["at"] + relations.GRAPH_TTL + 1)
        assert relations.graph() is not g2              # and after the TTL


def test_children_of_missing_families_are_skipped(app):
    from app.db import insert
    with app.app_context():
        insert("children", {"gedcom": "tree1", "familyID": "XNOSUCHFAM", "personID": "XORPHAN"})
        relations.invalidate()
        assert "XORPHAN" not in relations.graph()["parents"]


# --- pages ---

def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def linked(app, username, pid):
    sql(app, "UPDATE users SET personID = %s WHERE username = %s", (pid, username))
    return client_for(app, username)


def test_person_page_relation_line(app):
    c = linked(app, "fx_nonotes", "XKID")
    try:
        html = text(c.get("/person/XGPA"))
        assert "<strong>Your grandfather</strong>" in html and "/relationship?a=XKID&b=XGPA" in html
        assert "No relationship to you found" in text(c.get("/person/XORPHAN"))
        assert "relation-line" not in text(c.get("/person/XKID"))  # yourself
    finally:
        linked(app, "fx_nonotes", "")


def test_relationship_page(app, admin):
    c = linked(app, "fx_nonotes", "XKID")
    try:
        html = text(c.get("/relationship?b=XGGPA"))
        assert 'value="XKID"' in html and "great-grandfather" in html and "Common ancestor:" in html
        html = text(c.get("/relationship?a=XKID&b=XWIFE"))
        assert "Common ancestor" in html and "mother" in html
        assert "That's the same person" in text(c.get("/relationship?a=XKID&b=xkid"))
        assert "No relationship found" in text(c.get("/relationship?a=XKID&b=XORPHAN"))
        assert "Choose two people" in text(c.get("/relationship?a=XKID&b=NOPE"))
        html = text(c.get("/relationship?a=XHUSB&b=XWIFE"))
        assert "'s <strong>wife</strong>" in html and "Common ancestor" not in html
        html = text(c.get("/relationship?a=XWIFE&b=XGPA"))
        assert "father-in-law" in html and "Through the spouse" in html
        html = text(c.get("/relationship?a=XGPA&b=XWIFE"))
        assert "daughter-in-law" in html and "Through the marriage of" in html
        row = text(c.get("/relationship?a=XKID&b=XGGPA")).split('class="face-row"')[1].split("</ol>")[0]
        assert [x for x in ("XKID", "XHUSB", "XGPA", "XGGPA") if f"/person/{x}" in row] == ["XKID", "XHUSB", "XGPA", "XGGPA"]
        assert row.index("/person/XKID") < row.index("/person/XGGPA") and 'class="common"' in row
        row = text(c.get("/relationship?a=XWIFE&b=XGPA")).split('class="face-row"')[1].split("</ol>")[0]
        assert row.index("/person/XWIFE") < row.index("/person/XHUSB") < row.index("/person/XGPA")  # her husband's father
        row = text(c.get("/relationship?a=XGPA&b=XWIFE")).split('class="face-row"')[1].split("</ol>")[0]
        assert row.index("/person/XGPA") < row.index("/person/XHUSB") < row.index("/person/XWIFE")
        assert "How are they related?" in text(admin.get("/relationship")) and "Choose two" not in text(admin.get("/relationship"))
    finally:
        linked(app, "fx_nonotes", "")


def test_find_for_everyone_respects_privacy(app, guest):
    assert guest.get("/find?q=Fixture").status_code == 200
    living = sql(app, "SELECT firstname, lastname, personID FROM people WHERE living = 1 AND private = 0 LIMIT 1")[0]
    ids = [h["id"] for h in guest.get(f"/find?q={living['firstname'].split()[0]} {living['lastname']}").get_json()]
    assert living["personID"] not in ids
    assert guest.get("/find").get_json() == []


def test_this_is_me_flow(app, admin):
    sql(app, "UPDATE users SET personID = '' WHERE username = 'fx_guest'")
    me = client_for(app, "fx_guest")
    assert "This is me" in text(me.get("/person/XGMA"))
    me.post("/me/request/XGGPA", data={"csrf": "t"})
    r = me.post("/me/request/XGMA", data={"csrf": "t"})  # a second request replaces the first
    assert "An editor will confirm" in text(me.get(r.location))
    assert "waiting for an editor" in text(me.get("/person/XGMA"))
    assert "This is me" not in text(me.get("/person/XGPA"))  # one request at a time
    assert "Requests (1)" in text(admin.get("/"))
    html = text(admin.get("/me/requests"))
    assert "fx_guest" in html and "/person/XGMA" in html and "XGGPA" not in html
    rid = sql(app, "SELECT id FROM app_link_requests WHERE status = 'pending' AND personID = 'XGMA'")[0]["id"]
    admin.post(f"/me/requests/{rid}", data={"csrf": "t", "action": "approve"})
    assert sql(app, "SELECT personID, mygedcom FROM users WHERE username = 'fx_guest'")[0] == {"personID": "XGMA", "mygedcom": "tree1"}
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'link user'")
    assert "No requests waiting" in text(admin.get("/me/requests")) and "Requests (" not in text(admin.get("/"))
    assert me.post("/me/request/XGPA", data={"csrf": "t"}).status_code == 400  # already linked
    assert admin.post(f"/me/requests/{rid}", data={"csrf": "t", "action": "approve"}).status_code == 400  # already decided
    sql(app, "UPDATE users SET personID = '' WHERE username = 'fx_guest'")


def test_unlink_yourself(app):
    sql(app, "UPDATE users SET personID = 'XGMA', mygedcom = 'tree1' WHERE username = 'fx_guest'")
    me = client_for(app, "fx_guest")
    page = text(me.get("/account/"))
    assert "Your account is linked to" in page and "/person/XGMA" in page and "/me/unlink" in page
    r = me.post("/me/unlink", data={"csrf": "t"})
    assert r.status_code == 302 and "Unlinked." in text(me.get(r.location))
    assert sql(app, "SELECT personID, mygedcom FROM users WHERE username = 'fx_guest'")[0] == {"personID": "", "mygedcom": ""}
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'unlink user'")
    assert "Not linked to anyone" in text(me.get("/account/")) and "This is me" in text(me.get("/person/XGPA"))
    assert me.post("/me/unlink", data={"csrf": "t"}).status_code == 400  # nothing to unlink
    sql(app, "UPDATE users SET personID = 'XGONE' WHERE username = 'fx_guest'")  # a person since deleted
    assert "XGONE <span class=\"muted\">(no longer in the tree)" in text(me.get("/account/"))
    sql(app, "UPDATE users SET personID = '', mygedcom = '' WHERE username = 'fx_guest'")


def test_reject_bad_requests_and_permissions(app, admin):
    sql(app, "UPDATE users SET personID = '' WHERE username = 'fx_adder'")
    me = client_for(app, "fx_adder")
    assert me.post("/me/request/NOPE", data={"csrf": "t"}).status_code == 404
    me.post("/me/request/XWIFE", data={"csrf": "t"})
    rid = sql(app, "SELECT id FROM app_link_requests WHERE status = 'pending' AND personID = 'XWIFE'")[0]["id"]
    assert me.get("/me/requests").status_code == 403
    assert admin.post(f"/me/requests/{rid}", data={"csrf": "t", "action": "maybe"}).status_code == 400
    r = admin.post(f"/me/requests/{rid}", data={"csrf": "t", "action": "reject"})
    assert "Request rejected" in text(admin.get(r.location))
    assert sql(app, "SELECT personID FROM users WHERE username = 'fx_adder'")[0]["personID"] == ""


def test_approving_for_a_deleted_user(app, admin):
    from app.db import insert
    with app.app_context():
        _, uid = insert("users", {"username": "fx_gone", "password_type": "md5"})
        insert_req = __import__("app.db", fromlist=["execute"]).execute
        insert_req("INSERT INTO app_link_requests (userID, personID, requested_at) VALUES (%s, 'XWIFE', NOW())", (uid,))
    sql(app, "DELETE FROM users WHERE userID = %s", (uid,))
    rid = sql(app, "SELECT id FROM app_link_requests WHERE userID = %s", (uid,))[0]["id"]
    r = admin.post(f"/me/requests/{rid}", data={"csrf": "t", "action": "approve"})
    assert "no longer exists" in text(admin.get(r.location))
    assert sql(app, "SELECT status FROM app_link_requests WHERE id = %s", (rid,))[0]["status"] == "rejected"


def test_ancestor_search_stops_at_limit(tree, monkeypatch):
    monkeypatch.setattr(relations, "MAX_GENERATIONS", 2)
    assert rel("GRANDSON", "GPA") is None  # 4 generations up, beyond the limit
    assert rel("SON", "DAD") == "grandfather"


def test_phrase():
    from app.relations import phrase
    assert phrase({"label": "grandfather"}, "your") == "your grandfather"
    assert phrase({"label": "uncle's wife"}, "your") == "your uncle's wife"
    assert phrase({"label": "2nd cousin, once removed's wife"}, "your") == "the wife of your 2nd cousin, once removed"


def test_long_in_law_reads_naturally(app):
    from app.db import insert
    with app.app_context():
        insert("people", {"gedcom": "tree1", "personID": "XCOUS", "firstname": "Cousin", "lastname": "Fixture", "sex": "M"})
        insert("people", {"gedcom": "tree1", "personID": "XCOUSW", "firstname": "Cousinwife", "lastname": "Fixture", "sex": "F"})
        insert("people", {"gedcom": "tree1", "personID": "XUNC", "firstname": "Unc", "lastname": "Fixture", "sex": "M"})
        insert("children", {"gedcom": "tree1", "familyID": "XFAM3", "personID": "XUNC", "ordernum": 2})  # XGPA's brother
        insert("families", {"gedcom": "tree1", "familyID": "XFAMU", "husband": "XUNC"})
        insert("children", {"gedcom": "tree1", "familyID": "XFAMU", "personID": "XCOUS", "ordernum": 1})
        insert("families", {"gedcom": "tree1", "familyID": "XFAMC", "husband": "XCOUS", "wife": "XCOUSW"})
        relations.invalidate()
    c = linked(app, "fx_nonotes", "XKID")
    try:
        html = text(c.get("/person/XCOUSW"))
        assert "<strong>The wife of your 1st cousin, once removed</strong>" in html
        html = text(c.get("/relationship?a=XKID&b=XCOUSW"))
        assert "is the wife of" in html and "'s <strong>1st cousin, once removed</strong>" in html
    finally:
        linked(app, "fx_nonotes", "")

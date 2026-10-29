"""Merging duplicate people."""
from conftest import CSRF, client_for, make, sql

from app import merge
from app.db import insert


def text(r):
    return r.get_data(as_text=True)


def test_needs_edit_and_delete_rights(app):
    assert client_for(app, "fx_editor").get("/edit/merge").status_code == 403


def test_duplicate_finder(app, admin, monkeypatch):
    ids = make(app, ("Dupe", "M", 0), ("Dupe2", "M", 0), ("Nodate", "F", 0), ("Nodate2", "F", 0))
    tag = ids["Dupe"][1:-4]
    sql(app, "UPDATE people SET firstname = 'Dupe', birthdatetr = '1850-01-01' WHERE personID IN (%s, %s)",
        (ids["Dupe"], ids["Dupe2"]))
    sql(app, "UPDATE people SET firstname = 'Nodate' WHERE personID = %s", (ids["Nodate2"],))
    strong = text(admin.get("/edit/merge"))
    assert f"keep={ids['Dupe']}&amp;merge={ids['Dupe2']}" in strong and ids["Nodate"] not in strong
    assert "Page 1 of" in text(admin.get("/edit/merge?weak=1"))  # the real data's name-only pairs fill pages
    monkeypatch.setattr(merge, "DUPES_PER_PAGE", 10000)
    weak = text(admin.get("/edit/merge?weak=1"))
    assert f"keep={ids['Nodate']}&amp;merge={ids['Nodate2']}" in weak and tag in weak


def test_compare(app, admin):
    ids = make(app, ("Keepme", "M", 0), ("Dropme", "M", 0), ("Wife", "F", 0), family=("Keepme", "Wife"))
    sql(app, "UPDATE people SET birthdate = '3 MAR 1850', birthplace = 'Here' WHERE personID = %s", (ids["Dropme"],))
    html = text(admin.get(f"/edit/merge?keep={ids['Keepme']}&merge={ids['Dropme']}"))
    assert '<input type="radio" name="birth" value="b" checked>' in html  # the kept one has no birth: take it
    assert '<input type="radio" name="firstname" value="a" checked>' in html and "3 Mar 1850, Here" in html
    assert "Spouse families" in html
    spouses = text(admin.get(f"/edit/merge?keep={ids['Keepme']}&merge={ids['Wife']}"))
    assert "married to each other" in spouses and "disabled" in spouses
    assert "same person record" in text(admin.get(f"/edit/merge?keep={ids['Wife']}&merge={ids['Wife']}"))
    assert admin.get(f"/edit/merge?keep={ids['Keepme']}&merge=NOPE").status_code == 404


def test_merge(app, admin):
    ids = make(app, ("Keep", "M", 0), ("Dup", "M", 0), ("Spouse", "F", 0), ("Kid", "F", 0), ("Kid2", "M", 0),
               ("Dad", "M", 0), family=("Dad", None), kids=("Keep", "Dup"))
    keep, dup, spouse, parents = ids["Keep"], ids["Dup"], ids["Spouse"], ids["family"]
    sql(app, "UPDATE people SET birthdate = '1 JAN 1850', birthdatetr = '1850-01-01' WHERE personID = %s", (dup,))
    occu = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'I' AND tag = 'OCCU'")[0]["eventtypeID"]
    mid = sql(app, "SELECT mediaID FROM media LIMIT 1")[0]["mediaID"]
    with app.app_context():
        for fid, who, kid, marr in ((keep + "F", keep, ids["Kid"], ""), (dup + "F", dup, ids["Kid2"], "2 FEB 1875")):
            insert("families", {"gedcom": "tree1", "familyID": fid, "husband": who, "wife": spouse,
                                    "husborder": 1, "wifeorder": 1 if who == keep else 2, "marrdate": marr})
            insert("children", {"gedcom": "tree1", "familyID": fid, "personID": kid, "ordernum": 1})
            insert("children", {"gedcom": "tree1", "familyID": fid, "personID": ids["Kid"] if who == dup else kid,
                                    "ordernum": 2}) if who == dup else None
        insert("events", {"gedcom": "tree1", "persfamID": dup, "eventtypeID": occu, "info": "Farmer"})
        _, xid = insert("xnotes", {"gedcom": "tree1", "note": "Dup's note"})
        insert("notelinks", {"gedcom": "tree1", "persfamID": dup, "xnoteID": xid})
        for who in (keep, dup):  # the same photo on both: one link afterwards
            insert("medialinks", {"gedcom": "tree1", "personID": who, "mediaID": mid, "linktype": "I"})
    sql(app, "UPDATE users SET personID = %s WHERE username = 'fx_nonotes'", (dup,))
    r = admin.post("/edit/merge", data={"csrf": CSRF, "keep": keep, "merge": dup, "birth": "b"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/person/{keep}")
    assert not sql(app, "SELECT * FROM people WHERE personID = %s", (dup,))
    k = sql(app, "SELECT * FROM people WHERE personID = %s", (keep,))[0]
    assert k["birthdate"] == "1 JAN 1850" and k["famc"] == parents
    assert len(sql(app, "SELECT * FROM children WHERE familyID = %s", (parents,))) == 1  # was child twice
    fams = sql(app, "SELECT * FROM families WHERE husband = %s", (keep,))
    assert [f["familyID"] for f in fams] == [keep + "F"] and fams[0]["marrdate"] == "2 FEB 1875"
    assert sorted(c["personID"] for c in sql(app, "SELECT personID FROM children WHERE familyID = %s",
                                             (keep + "F",))) == sorted([ids["Kid"], ids["Kid2"]])
    assert sql(app, "SELECT famc FROM people WHERE personID = %s", (ids["Kid2"],))[0]["famc"] == keep + "F"
    assert sql(app, "SELECT wifeorder FROM families WHERE familyID = %s", (keep + "F",))[0]["wifeorder"] == 1
    assert sql(app, "SELECT * FROM events WHERE persfamID = %s AND info = 'Farmer'", (keep,))
    assert sql(app, "SELECT * FROM notelinks WHERE persfamID = %s", (keep,))
    assert len(sql(app, "SELECT * FROM medialinks WHERE personID = %s AND mediaID = %s", (keep, mid))) == 1
    assert sql(app, "SELECT personID FROM users WHERE username = 'fx_nonotes'")[0]["personID"] == keep
    logged = sql(app, "SELECT * FROM app_audit WHERE action = 'merge person' AND record = %s", (keep,))[0]
    assert dup in logged["before_json"] and "families folded together" in logged["before_json"]
    assert "Also combined 1 duplicate family" in text(admin.get(f"/person/{keep}"))


def test_merge_refused(app, admin):
    ids = make(app, ("Husb", "M", 0), ("Wif", "F", 0), family=("Husb", "Wif"))
    assert admin.post("/edit/merge", data={"csrf": CSRF, "keep": ids["Husb"], "merge": ids["Wif"]}).status_code == 400
    assert admin.post("/edit/merge", data={"csrf": CSRF, "keep": ids["Husb"], "merge": "NOPE"}).status_code == 404


def test_merge_plain_cases(app, admin):
    """No values chosen from the duplicate, a family with no spouse, the duplicate as someone else's child, and two
    families with the same spouse where the kept one already has every fact."""
    ids = make(app, ("Plain", "M", 0), ("Plain2", "M", 0), ("Pdad", "M", 0), ("Pwife", "F", 0),
               family=("Pdad", None), kids=("Plain2",))
    keep, dup = ids["Plain"], ids["Plain2"]
    with app.app_context():
        insert("families", {"gedcom": "tree1", "familyID": keep + "S", "husband": keep})  # no spouse
        for fid, who in ((keep + "W", keep), (dup + "W", dup)):
            insert("families", {"gedcom": "tree1", "familyID": fid, "husband": who, "wife": ids["Pwife"],
                                    "marrdate": "1900" if who == keep else ""})
    r = admin.post("/edit/merge", data={"csrf": CSRF, "keep": keep, "merge": dup})
    assert r.status_code == 302 and "Merged" in text(admin.get(f"/person/{keep}"))
    assert sql(app, "SELECT famc FROM people WHERE personID = %s", (keep,))[0]["famc"] == ids["family"]
    assert sql(app, "SELECT marrdate FROM families WHERE familyID = %s", (keep + "W",))[0]["marrdate"] == "1900"
    assert not sql(app, "SELECT * FROM families WHERE familyID = %s", (dup + "W",))

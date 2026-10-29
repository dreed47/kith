import json

from conftest import client_for, make, sql


def person_form(p, **changes):
    form = {k: p[k] for k in ("firstname", "lnprefix", "lastname", "nickname", "title", "prefix", "suffix", "sex",
                              "birthdate", "birthplace", "altbirthdate", "altbirthplace", "deathdate", "deathplace",
                              "burialdate", "burialplace")}
    form.update(csrf="t", changedate=str(p["changedate"]), **({"living": "1"} if p["living"] else {}))
    form.update(changes)
    return form


def test_numeric_dates_saved_standard(app, admin):
    pid = make(app, ("Numdate", "M", 0))["Numdate"]
    p = sql(app, "SELECT * FROM people WHERE personID = %s", (pid,))[0]
    admin.post(f"/edit/person/{pid}", data=person_form(p, birthdate="1/23/1733", deathdate="abt 1/3/1790"))
    after = sql(app, "SELECT birthdate, birthdatetr, deathdate FROM people WHERE personID = %s", (p["personID"],))[0]
    assert (after["birthdate"], str(after["birthdatetr"]), after["deathdate"]) == ("23 JAN 1733", "1733-01-23", "ABT 3 JAN 1790")


def test_guest_cannot_edit(app, guest):
    pid = sql(app, "SELECT personID FROM people LIMIT 1")[0]["personID"]
    assert guest.get(f"/edit/person/{pid}").status_code == 403
    assert guest.post(f"/edit/person/{pid}", data={"csrf": "t"}).status_code == 403
    assert guest.get("/edit/person/new").status_code == 403


def test_edit_person_updates_tng_fields_and_audit(app, admin):
    p = sql(app, "SELECT * FROM people WHERE living = 0 AND birthdate <> '' LIMIT 1")[0]
    r = admin.post(f"/edit/person/{p['personID']}", data=person_form(p, birthdate="bef 1850", birthplace="Testville, Nowhere"))
    assert r.status_code == 302
    after = sql(app, "SELECT * FROM people WHERE personID = %s", (p["personID"],))[0]
    assert after["birthdate"] == "bef 1850" and str(after["birthdatetr"]) == "1849-12-31"
    assert after["changedate"] != p["changedate"] and after["firstname"] == p["firstname"].strip()  # saves trim stray spaces
    assert sql(app, "SELECT 1 FROM places WHERE place = 'Testville, Nowhere'")
    log = sql(app, "SELECT * FROM app_audit WHERE record = %s ORDER BY id DESC LIMIT 1", (p["personID"],))[0]
    assert json.loads(log["before_json"])["birthdate"] == p["birthdate"]
    assert json.loads(log["after_json"])["birthdate"] == "bef 1850"


def test_stale_edit_is_refused(app, admin):
    p = sql(app, "SELECT * FROM people WHERE living = 0 LIMIT 1 OFFSET 5")[0]
    r = admin.post(f"/edit/person/{p['personID']}", data=person_form(p, changedate="2000-01-01 00:00:00", firstname="Changed"))
    assert r.status_code == 200 and "Someone else saved" in r.get_data(as_text=True)
    assert sql(app, "SELECT firstname FROM people WHERE personID = %s", (p["personID"],))[0]["firstname"] == p["firstname"]


def test_marking_living_updates_families(app, admin):
    f = sql(app, "SELECT * FROM families WHERE living = 0 AND husband <> '' AND wife <> '' LIMIT 1")[0]
    p = sql(app, "SELECT * FROM people WHERE personID = %s", (f["husband"],))[0]
    admin.post(f"/edit/person/{p['personID']}", data=person_form(p, living="1"))
    assert sql(app, "SELECT living FROM families WHERE familyID = %s", (f["familyID"],))[0]["living"] == 1


def test_add_child(app, admin):
    f = sql(app, "SELECT * FROM families WHERE husband <> '' AND wife <> '' LIMIT 1 OFFSET 10")[0]
    before = sql(app, "SELECT MAX(CAST(SUBSTRING(personID, 2) AS UNSIGNED)) n FROM people")[0]["n"]
    r = admin.post("/edit/person/new", data={"csrf": "t", "child_of": f["familyID"], "firstname": "Newkid",
                                               "lastname": "Test", "sex": "F", "living": "1", "birthdate": "12 Jul 1950"})
    assert r.status_code == 302
    kid = sql(app, "SELECT * FROM people WHERE firstname = 'Newkid'")[0]
    assert kid["personID"] == f"I{before + 1}" and kid["famc"] == f["familyID"] and str(kid["birthdatetr"]) == "1950-07-12"
    link = sql(app, "SELECT * FROM children WHERE personID = %s", (kid["personID"],))[0]
    top = sql(app, "SELECT MAX(ordernum) n FROM children WHERE familyID = %s", (f["familyID"],))[0]["n"]
    assert link["familyID"] == f["familyID"] and link["ordernum"] == top
    assert r.location.endswith(f"/person/{kid['personID']}")


def test_add_spouse_creates_family(app, admin):
    p = sql(app, "SELECT * FROM people WHERE sex = 'M' AND living = 0 LIMIT 1 OFFSET 20")[0]
    admin.post("/edit/person/new", data={"csrf": "t", "spouse_of": p["personID"], "firstname": "Newwife",
                                           "lastname": "Test", "sex": "F", "living": "1"})
    wife = sql(app, "SELECT * FROM people WHERE firstname = 'Newwife'")[0]
    fam = sql(app, "SELECT * FROM families WHERE wife = %s", (wife["personID"],))[0]
    assert fam["husband"] == p["personID"] and fam["living"] == 1 and fam["wifeorder"] == 1


def test_bad_input_rejected(app, admin):
    p = sql(app, "SELECT * FROM people LIMIT 1 OFFSET 30")[0]
    r = admin.post(f"/edit/person/{p['personID']}", data=person_form(p, firstname="", lastname="", birthdate="x" * 60))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Enter a first or last name" in html and "longer than 50" in html


def test_delete_person_added_as_spouse_removes_empty_family(app, admin):
    p = sql(app, "SELECT * FROM people WHERE sex = 'F' AND living = 0 LIMIT 1 OFFSET 40")[0]
    admin.post("/edit/person/new", data={"csrf": "t", "spouse_of": p["personID"], "firstname": "Tempspouse",
                                           "lastname": "Test", "sex": "M"})
    temp = sql(app, "SELECT * FROM people WHERE firstname = 'Tempspouse'")[0]
    fid = sql(app, "SELECT familyID FROM families WHERE husband = %s", (temp["personID"],))[0]["familyID"]
    assert "Delete Tempspouse" in admin.get(f"/edit/person/{temp['personID']}/delete").get_data(as_text=True)
    assert admin.post(f"/edit/person/{temp['personID']}/delete", data={"csrf": "t"}).status_code == 302
    assert not sql(app, "SELECT 1 FROM people WHERE personID = %s", (temp["personID"],))
    # The family held nothing but the two spouses, so it's gone; the wife herself stays.
    assert not sql(app, "SELECT 1 FROM families WHERE familyID = %s", (fid,))
    assert sql(app, "SELECT 1 FROM people WHERE personID = %s", (p["personID"],))
    log = sql(app, "SELECT * FROM app_audit WHERE action = 'delete person' AND record = %s", (temp["personID"],))[0]
    assert json.loads(log["before_json"])["removed_families"] == [fid]


def test_delete_parent_keeps_family_with_children(app, admin):
    f = sql(app, "SELECT f.* FROM families f WHERE husband <> '' AND wife <> ''"
                 " AND EXISTS (SELECT 1 FROM children c WHERE c.familyID = f.familyID) LIMIT 1 OFFSET 50")[0]
    kids = sql(app, "SELECT personID FROM children WHERE familyID = %s", (f["familyID"],))
    notes = sql(app, "SELECT COUNT(*) n FROM notelinks WHERE persfamID = %s", (f["husband"],))[0]["n"]
    admin.post(f"/edit/person/{f['husband']}/delete", data={"csrf": "t"})
    fam = sql(app, "SELECT * FROM families WHERE familyID = %s", (f["familyID"],))[0]
    assert fam["husband"] == "" and fam["wife"] == f["wife"]
    assert len(sql(app, "SELECT 1 FROM children WHERE familyID = %s", (f["familyID"],))) == len(kids)
    assert not sql(app, "SELECT 1 FROM notelinks WHERE persfamID = %s", (f["husband"],)) or notes == 0
    assert not sql(app, "SELECT 1 FROM events WHERE persfamID = %s", (f["husband"],))


def test_delete_needs_delete_right(app):
    adder = client_for(app, "fx_adder")
    pid = sql(app, "SELECT personID FROM people LIMIT 1")[0]["personID"]
    assert adder.post(f"/edit/person/{pid}/delete", data={"csrf": "t"}).status_code == 403


def test_edit_form_shows_current_values(admin):
    html = admin.get("/edit/person/XGMA").get_data(as_text=True)
    assert 'value="Gina"' in html and 'name="changedate"' in html and 'name="csrf"' in html


def test_missing_person_404(admin):
    assert admin.get("/edit/person/NOPE").status_code == 404
    assert admin.get("/edit/person/NOPE/delete").status_code == 404
    assert admin.get("/edit/person/new?child_of=NOPE").status_code == 404
    assert admin.get("/edit/person/new?spouse_of=NOPE").status_code == 404


def test_new_person_forms_prefill(admin):
    # Child of a family: surname from the father, living ticked.
    html = admin.get("/edit/person/new?child_of=XFAM").get_data(as_text=True)
    assert 'value="Fixture"' in html and 'name="child_of" value="XFAM"' in html and "Add child to family XFAM" in html
    # Spouse of a man: defaults to female.
    html = admin.get("/edit/person/new?spouse_of=XGPA").get_data(as_text=True)
    assert '<option value="F" selected>' in html and "Add spouse of Gus Fixture" in html
    html = admin.get("/edit/person/new").get_data(as_text=True)
    assert "<h1>Add person</h1>" in html and '<option value="U" selected>' in html


def test_standalone_person_and_place(app, admin):
    r = admin.post("/edit/person/new", data={"csrf": "t", "firstname": "Solo", "lastname": "Fixture",
                                              "deathplace": "Lonetown, Testshire", "altbirthdate": "3 MAR 1801"})
    p = sql(app, "SELECT * FROM people WHERE firstname = 'Solo'")[0]
    assert r.status_code == 302 and p["famc"] == "" and p["altbirthtype"] == "CHR" and p["living"] == 0
    assert sql(app, "SELECT 1 FROM places WHERE place = 'Lonetown, Testshire'")


def test_new_person_validation(admin):
    r = admin.post("/edit/person/new", data={"csrf": "t", "firstname": "x" * 128, "deathplace": "y" * 249})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "firstname is longer than 127" in html and "place is longer than 248" in html


def test_surname_change_clears_soundex_key(app, admin):
    p = sql(app, "SELECT * FROM people WHERE personID = 'XGGPA'")[0]
    sql(app, "UPDATE people SET metaphone = 'FKSXR' WHERE personID = 'XGGPA'")
    p = sql(app, "SELECT * FROM people WHERE personID = 'XGGPA'")[0]
    admin.post("/edit/person/XGGPA", data=person_form(p, firstname="Abraham"))
    assert sql(app, "SELECT metaphone FROM people WHERE personID = 'XGGPA'")[0]["metaphone"] == "FKSXR"
    p = sql(app, "SELECT * FROM people WHERE personID = 'XGGPA'")[0]
    admin.post("/edit/person/XGGPA", data=person_form(p, lastname="Fixtures"))
    after = sql(app, "SELECT * FROM people WHERE personID = 'XGGPA'")[0]
    assert after["metaphone"] == "" and after["lastname"] == "Fixtures"


def test_baptism_type_and_cremation(app, admin):
    p = sql(app, "SELECT * FROM people WHERE personID = 'XGMA'")[0]
    admin.post("/edit/person/XGMA", data=person_form(p, altbirthtype="BAPM", altbirthdate="1850", cremated="1"))
    after = sql(app, "SELECT * FROM people WHERE personID = 'XGMA'")[0]
    assert after["altbirthtype"] == "BAPM" and after["burialtype"] == 1
    assert "Baptism" in admin.get("/person/XGMA").get_data(as_text=True)


def test_add_child_to_single_parent_family(app, admin):
    admin.post("/edit/person/new", data={"csrf": "t", "child_of": "XFAM3", "firstname": "Second", "lastname": "Fixture"})
    kid = sql(app, "SELECT personID FROM people WHERE firstname = 'Second' AND lastname = 'Fixture'")[0]
    assert sql(app, "SELECT ordernum FROM children WHERE personID = %s", (kid["personID"],))[0]["ordernum"] == 2
    assert sql(app, "SELECT haskids FROM children WHERE personID = 'XGGPA'") == []  # XGGPA isn't anyone's child


def test_new_ids_retry_on_collision(app, monkeypatch):
    import pymysql
    from flask import g
    from app import edit
    calls = []

    def flaky_insert(table, values):
        calls.append(values["personID"])
        if len(calls) == 1:
            raise pymysql.err.IntegrityError(1062, "Duplicate")
    monkeypatch.setattr(edit, "insert", flaky_insert)
    with app.test_request_context():
        g.user = {"username": "fx_admin"}
        assert edit.insert_with_new_id("people", "personID", "I", {}) == calls[1]

    def always_taken(table, values):
        raise pymysql.err.IntegrityError(1062, "Duplicate")
    monkeypatch.setattr(edit, "insert", always_taken)
    with app.test_request_context():
        import pytest
        with pytest.raises(RuntimeError):
            edit.insert_with_new_id("people", "personID", "I", {})


def test_refresh_haskids_ignores_blank(app):
    from app import edit
    with app.app_context():
        assert edit.refresh_haskids("") is None


def test_delete_removes_sole_notes_but_keeps_shared_ones(app, admin):
    admin.post("/edit/person/new", data={"csrf": "t", "firstname": "Noted", "lastname": "Fixture"})
    pid = sql(app, "SELECT personID FROM people WHERE firstname = 'Noted'")[0]["personID"]
    from app.db import insert
    with app.app_context():
        _, own = insert("xnotes", {"gedcom": "tree1", "note": "only mine"})
        _, shared = insert("xnotes", {"gedcom": "tree1", "note": "shared"})
        insert("notelinks", {"gedcom": "tree1", "persfamID": pid, "xnoteID": own})
        insert("notelinks", {"gedcom": "tree1", "persfamID": pid, "xnoteID": shared})
        insert("notelinks", {"gedcom": "tree1", "persfamID": "XGMA", "xnoteID": shared})
        # a child link to a family that no longer exists must not break the delete
        insert("children", {"gedcom": "tree1", "familyID": "XGONE", "personID": pid})
    assert "2 note(s)" in admin.get(f"/edit/person/{pid}/delete").get_data(as_text=True)
    r = admin.post(f"/edit/person/{pid}/delete", data={"csrf": "t"})
    assert r.status_code == 302 and r.location.endswith("/family/XGONE")
    assert not sql(app, "SELECT 1 FROM xnotes WHERE ID = %s", (own,))
    assert sql(app, "SELECT 1 FROM xnotes WHERE ID = %s", (shared,))
    assert not sql(app, "SELECT 1 FROM notelinks WHERE persfamID = %s", (pid,))


def test_delete_person_without_links(app, admin):
    admin.post("/edit/person/new", data={"csrf": "t", "firstname": "Lonely", "lastname": "Fixture"})
    pid = sql(app, "SELECT personID FROM people WHERE firstname = 'Lonely'")[0]["personID"]
    assert "nothing else; this person has no links" in admin.get(f"/edit/person/{pid}/delete").get_data(as_text=True)
    r = admin.post(f"/edit/person/{pid}/delete", data={"csrf": "t"})
    assert r.location.endswith("/") and "Deleted Lonely Fixture" in admin.get("/").get_data(as_text=True)


def test_remove_a_life_event(app, admin):
    pid = make(app, ("Undo", "M", 0))["Undo"]
    sql(app, "UPDATE people SET birthdate = '1 JAN 1900', birthdatetr = '1900-01-01', birthplace = 'Oops',"
             " deathplace = 'Keep' WHERE personID = %s", (pid,))
    form = admin.get(f"/edit/person/{pid}").get_data(as_text=True)
    assert 'name="remove_birth"' in form and 'name="remove_death"' in form and 'name="remove_burial"' not in form
    p = sql(app, "SELECT * FROM people WHERE personID = %s", (pid,))[0]
    assert admin.post(f"/edit/person/{pid}", data=person_form(p, remove_birth="1")).status_code == 302
    after = sql(app, "SELECT * FROM people WHERE personID = %s", (pid,))[0]
    assert (after["birthdate"], str(after["birthdatetr"]), after["birthplace"], after["deathplace"]) == ("", "0000-00-00", "", "Keep")
    assert "<strong>Birth</strong>" not in admin.get(f"/person/{pid}").get_data(as_text=True)


def test_edit_page_is_a_hub_for_relatives_events_notes_and_media(app, admin):
    ids = make(app, ("A", "M", 0), ("B", "F", 0), ("C", "U", 0), family=("A", "B"), kids=("C",))
    html = admin.get(f"/edit/person/{ids['A']}").get_data(as_text=True)
    for part in ('id="family"', 'id="events"', 'id="notes"', 'id="media"', "Add a relative", "Add a life event",
                 f'value="child_of:{ids["family"]}"', f'value="husband:{ids["A"]}"', f'href="/edit/person/{ids["C"]}"'):
        assert part in html
    assert html.count('id="date-birthdate"') == 1 and 'id="date-rel-birthdate"' in html  # no clashing date boxes
    p = sql(app, "SELECT * FROM people WHERE personID = %s", (ids["A"],))[0]
    r = admin.post(f"/edit/person/{ids['A']}", data=person_form(p, nickname="Al"))
    assert r.status_code == 302 and r.location.endswith(f"/edit/person/{ids['A']}")  # saving stays on the hub


def test_quick_add_relative_from_hub(app, admin):
    ids = make(app, ("A", "F", 0))
    r = admin.post("/edit/person/new?back=edit", data={"csrf": "t", "relation": f"husband:{ids['A']}",
                                                         "return_to": ids["A"], "firstname": "Quickdad", "sex": ""})
    assert r.status_code == 302 and r.location.endswith(f"/edit/person/{ids['A']}#family")
    dad = sql(app, "SELECT * FROM people WHERE firstname = 'Quickdad'")[0]
    assert dad["sex"] == "M"  # left to the relationship
    fam = sql(app, "SELECT * FROM families WHERE husband = %s", (dad["personID"],))[0]
    assert sql(app, "SELECT 1 FROM children WHERE familyID = %s AND personID = %s", (fam["familyID"], ids["A"]))
    r = admin.post("/edit/person/new?back=edit", data={"csrf": "t", "relation": f"child_of:{fam['familyID']}",
                                                         "return_to": ids["A"], "firstname": "Quicksis", "sex": "F"})
    sis = sql(app, "SELECT * FROM people WHERE firstname = 'Quicksis'")[0]
    assert sis["famc"] == fam["familyID"] and sis["sex"] == "F"
    r = admin.post("/edit/person/new?back=edit", data={"csrf": "t", "relation": f"spouse_of:{ids['A']}",
                                                         "return_to": ids["A"], "firstname": ""})
    assert r.status_code == 200 and "Enter a first or last name" in r.get_data(as_text=True)
    assert admin.post("/edit/person/new", data={"csrf": "t", "relation": f"nope:{ids['A']}", "firstname": "X"}).status_code == 400
    assert admin.post("/edit/person/new", data={"csrf": "t", "relation": "wife:NOSUCH", "firstname": "X"}).status_code == 404


def test_events_notes_media_return_to_hub_only_when_asked(app, admin):
    from test_edit_events import event_data
    ids = make(app, ("A", "M", 0))
    pid = ids["A"]
    r = admin.post(f"/edit/event/new?for={pid}&back=edit", data=event_data(app, eventdate="1950", info="Ford"))
    assert r.location.endswith(f"/edit/person/{pid}#events")
    assert admin.post(f"/edit/event/new?for={pid}", data=event_data(app)).location.endswith(f"/person/{pid}")
    eid = sql(app, "SELECT eventID FROM events WHERE persfamID = %s AND info = 'Ford'", (pid,))[0]["eventID"]
    assert f"back=edit" in admin.get(f"/edit/event/{eid}?back=edit").get_data(as_text=True)  # its Delete link too
    r = admin.post(f"/edit/note/new?for={pid}&back=edit", data={"csrf": "t", "note": "Loved fishing"})
    assert r.location.endswith(f"/edit/person/{pid}#notes")
    assert "Loved fishing" in admin.get(f"/edit/person/{pid}").get_data(as_text=True)
    assert admin.get(f"/edit/media/new?for={pid}&back=edit").status_code == 200

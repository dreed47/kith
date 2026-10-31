"""Data checks: one made-up case for every check."""
from conftest import client_for, make, sql

from app import checks
from app.db import insert


def text(r):
    return r.get_data(as_text=True)


def section(html, key):
    start = html.index(f'id="{key}"')
    return html[start:html.index("</section>", start)]


def dates(app, pid, **v):
    sets = ", ".join(f"{k} = %s" for k in v)
    sql(app, f"UPDATE people SET {sets} WHERE personID = %s", (*v.values(), pid))


def test_every_check(app, admin):
    ids = make(app, ("Ckdad", "M", 0), ("Ckmom", "F", 0), ("Ckkid", "M", 0), family=("Ckdad", "Ckmom"),
               kids=("Ckkid",))
    dad, mom, kid, fam = ids["Ckdad"], ids["Ckmom"], ids["Ckkid"], ids["family"]
    dates(app, dad, birthdatetr="1800-01-01", deathdatetr="1700-01-01", birthdate="1800", deathdate="1700",
          burialdatetr="1690-01-01", burialdate="1690")
    dates(app, mom, birthdatetr="1700-01-01", deathdatetr="1749-01-01", altbirthdatetr="1699-01-01")
    dates(app, kid, birthdatetr="1810-01-01", deathdatetr="1950-01-01", birthdate="1810")
    sql(app, "UPDATE families SET marrdatetr = '1805-01-01', marrdate = '1805' WHERE familyID = %s", (fam,))
    old = make(app, ("Olddad", "M", 0), ("Latekid", "F", 0), family=("Olddad", None), kids=("Latekid",))
    dates(app, old["Olddad"], birthdatetr="1700-01-01")
    dates(app, old["Latekid"], birthdatetr="1790-01-01")
    nameless = make(app, ("Noname", "U", 0))["Noname"]
    sql(app, "UPDATE people SET firstname = '', lastname = '' WHERE personID = %s", (nameless,))
    with app.app_context():
        insert("families", {"gedcom": "tree1", "familyID": kid + "E"})
        insert("children", {"gedcom": "tree1", "familyID": fam, "personID": dad})  # his own child
    html = text(admin.get("/edit/checks"))
    for key, who in (("born_after_death", dad), ("buried_before_death", dad), ("christened_before_birth", mom),
                     ("too_old", kid), ("young_parent", kid), ("old_mother", kid), ("old_father", old["Latekid"]),
                     ("after_mother_died", kid), ("after_father_died", kid), ("married_young", dad),
                     ("married_after_death", dad), ("no_name", nameless), ("empty_family", kid + "E"),
                     ("own_child", dad)):
        assert who in section(html, key), key
    assert "born 1810; father " + dad + " born 1800" in section(html, "young_parent")
    assert client_for(app, "fx_guest").get("/edit/checks").status_code == 403


def test_hidden_people_left_out_and_long_lists_cut(app, monkeypatch):
    ids = make(app, ("Hiddenck", "M", 1), ("Hiddenck2", "M", 1))
    for pid in ids.values():
        dates(app, pid, birthdatetr="1900-01-01", deathdatetr="1800-01-01")
    limited = text(client_for(app, "fx_limited_editor").get("/edit/checks"))
    assert ids["Hiddenck"] not in limited
    monkeypatch.setattr(checks, "SHOWN", 1)
    assert "Showing the first 1." in text(client_for(app, "fx_admin").get("/edit/checks"))


def test_obituary_death_date(app, admin):
    ids = make(app, ("Obdiff", "M", 0), ("Obsame", "F", 0), ("Obyear", "M", 0), ("Obnone", "F", 0),
               ("Obsecret", "M", 0))
    for pid, tr, shown in ((ids["Obdiff"], "1923-03-12", "12 MAR 1923"), (ids["Obsame"], "1923-03-11", "11 MAR 1923"),
                           (ids["Obyear"], "1880-00-00", "1880"), (ids["Obnone"], "1900-01-01", "1900"),
                           (ids["Obsecret"], "1950-01-01", "1950")):
        dates(app, pid, deathdatetr=tr, deathdate=shown)
    with app.app_context():
        for pid, note, secret in ((ids["Obdiff"], "He died at home on March 11, 1923. Burial followed.", 0),
                                  (ids["Obsame"], "She passed away March 11, 1923.", 0),
                                  (ids["Obyear"], "Death came on 8 November 1877 after a long illness.", 0),
                                  (ids["Obnone"], "A kind woman, loved by all.", 0),
                                  (ids["Obsecret"], "He died June 1, 1951.", 1)):
            _, xid = insert("xnotes", {"gedcom": "tree1", "note": note})
            insert("notelinks", {"gedcom": "tree1", "persfamID": pid, "xnoteID": xid, "eventID": "DEAT",
                                 "secret": secret})
    found = section(text(admin.get("/edit/checks")), "obituary_death")
    assert ids["Obdiff"] in found and "death 12 MAR 1923; obituary: “He died at home on March 11, 1923”" in found
    assert ids["Obyear"] in found  # a year alone is compared as a year
    assert ids["Obsame"] not in found and ids["Obnone"] not in found
    assert ids["Obsecret"] in found  # fx_admin may read secret notes
    limited = section(text(client_for(app, "fx_limited_editor").get("/edit/checks")), "obituary_death")
    assert ids["Obsecret"] not in limited and ids["Obdiff"] in limited  # no right to secret notes

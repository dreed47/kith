"""GEDCOM import: parsing, the preview, the import itself and undo."""
import io
import json
import os
import re
import time
import zipfile

import pymysql
import pytest
from conftest import CSRF, client_for, make, sql

from app import gedcom_import as gi

GED = """0 HEAD
1 CHAR UTF-8
0 @S1@ SOUR
1 TITL Family Bible
1 AUTH Grandma
0 @N1@ NOTE A shared note
1 CONT on two lines
0 @I1@ INDI
1 NAME Zed Quentin /Importson/ Jr.
2 NICK Q
2 SPFX van
1 SEX M
1 BIRT
2 DATE 1 JAN 1850
2 PLAC Importville, PA
2 SOUR @S1@
3 PAGE p. 1
3 DATA
4 TEXT Born at home
1 BAPM
2 DATE 1850
1 BIRT
2 DATE 1851
1 DEAT
2 DATE 1920
1 CREM
2 PLAC Sea
1 OCCU Farmer
2 DATE 1880
2 NOTE Grew corn
1 EVEN Fair
2 TYPE County fair
1 NOTE @N1@
1 NOTE Inline note with @@ sign
1 NOTE <p>Biography &amp; more</p>
1 SOUR A source written inline
1 OBJE
2 FILE photo.jpg
1 FAMS @F1@
0 @I2@ INDI
1 NAME Ann /Importson/
2 GIVN Annie
1 SEX F
1 FAMS @F1@
0 @I3@ INDI
1 NAME Kid /Importson/
1 SEX X
1 FAMC @F1@
1 FAMS @F2@
0 @I4@ INDI
1 NAME Grandkid /Importson/
1 BIRT
2 DATE 2001
1 FAMC @F2@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
1 CHIL @I99@
1 MARR
2 DATE 1875
2 PLAC Wedtown, PA
1 CENS
2 PLAC Censusville
0 @F2@ FAM
1 HUSB @I3@
1 CHIL @I4@
0 @R1@ REPO
1 NAME Library
0 TRLR
"""


def text(r):
    return r.get_data(as_text=True)


@pytest.fixture
def importer(app, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    yield client_for(app, "fx_siteadmin")
    # Imported people count as just changed; backdate them so other tests' "recently updated" lists stay theirs.
    for imp in sql(app, "SELECT created FROM app_imports"):
        made = json.loads(imp["created"])
        for table, key, kind in (("people", "personID", "people"), ("families", "familyID", "families")):
            if made[kind]:
                sql(app, f"UPDATE {table} SET changedate = '2000-01-01' WHERE {key} IN %s", (tuple(made[kind]),))


def preview(c, data=GED, name="family.ged"):
    body = data.encode() if isinstance(data, str) else data
    return c.post("/admin/import", data={"csrf": CSRF, "file": (io.BytesIO(body), name)},
                  content_type="multipart/form-data")


def token_of(html):
    return re.search(r"/admin/import/([A-Za-z0-9_-]{20,})", html).group(1)


def test_parse():
    recs = gi.parse("0 HEAD\n0 @N1@ NOTE a\n1 CONC b\n1 CONT c @@ d\n\n0 TRLR")
    assert recs[1]["value"] == "ab\nc @ d" and recs[1]["xref"] == "@N1@"
    for bad, message in (("0 HEAD\nnot gedcom", "isn't GEDCOM"), ("0 HEAD\n2 NAME x", "skips a level"),
                         ("0 TRLR", "should start with 0 HEAD"), ("", "should start with 0 HEAD")):
        with pytest.raises(gi.GedcomError, match=message):
            gi.parse(bad)


def test_plain():
    wikitree = ("<p>Born in <b>Ohio</b>.<ref name=\"c\">Census, 1900</ref><ref name=\"c\" /></p><p>Farmer &amp; miller.<br/>Moved west.</p>"
                "<ul><li>One</li><li>Two</li></ul>")
    assert gi.plain(wikitree) == "Born in Ohio. [Census, 1900]\nFarmer & miller.\nMoved west.\n- One\n- Two"
    assert gi.plain("Born <1900 & died > 1950 &amp; more") == "Born <1900 & died > 1950 &amp; more"
    assert gi.plain("<p>a</p>\n\n\n\n<p>b</p>") == "a\n\nb"


def test_decode_and_names():
    assert gi.decode("café".encode()) == ("café", None)
    assert gi.decode("café".encode("latin-1"))[0] == "café" and gi.decode("café".encode("latin-1"))[1]
    assert gi.split_name("John William /Smith/ Jr.") == ("John William", "Smith", "Jr.")
    assert gi.split_name("Cher") == ("Cher", "", "")


def test_years_and_living_bounds(app):
    with app.app_context():
        assert gi.year("1 JAN 1850") == 1850 and gi.year("3/5/1474/5") is None and gi.year("") is None
        by, skipped, _ = gi.plan(gi.parse(GED))
        born = gi.birth_bounds(by)
        # Kid has no dates: bounded by the grandchild (2001 - 12); the grandparents by Zed's own dates.
        assert born["@I3@"] == 1989 and born["@I1@"] == 1850 and born["@I2@"] == 1863
        assert skipped["REPO"] == 1 and skipped["OBJE"] == 1
        zed = gi.person_values(by["INDI"][0], born["@I1@"])
        assert (zed["firstname"], zed["lastname"], zed["suffix"], zed["nickname"], zed["lnprefix"]) == \
            ("Zed Quentin", "Importson", "Jr.", "Q", "van")
        assert zed["birthdate"] == "1 JAN 1850" and zed["altbirthtype"] == "BAPM" and zed["burialtype"] == 1
        assert zed["living"] == 0 and gi.person_values(by["INDI"][3], born["@I4@"])["living"] == 1
        ann = gi.person_values(by["INDI"][1], born["@I2@"])
        assert ann["firstname"] == "Annie" and ann["sex"] == "F" and ann["living"] == 0
        assert gi.person_values(by["INDI"][2])["sex"] == "U"


def test_admins_only(app):
    assert client_for(app, "fx_admin").get("/admin/import").status_code == 403


def test_preview_and_errors(app, importer):
    assert "Import a GEDCOM file" in text(importer.get("/admin/import"))
    r = importer.post("/admin/import", data={"csrf": CSRF}, content_type="multipart/form-data", follow_redirects=True)
    assert "Choose a GEDCOM file" in text(r)
    preview(importer, "0 HEAD\nrubbish", "bad.ged")
    assert "isn&#39;t GEDCOM" in text(importer.get("/admin/import"))
    ids = make(app, ("Zed Quentin", "M", 0))
    sql(app, "UPDATE people SET lastname = 'Importson', birthdatetr = '1850-01-01' WHERE personID = %s",
        (ids["Zed Quentin"],))
    html = text(preview(importer))
    assert "<strong>4</strong> people, <strong>2</strong> families, <strong>1</strong> sources" in html
    assert "REPO (1)" in html and "OBJE (1)" in html and "Possibly already in the tree (1)" in html
    assert "Grandkid Importson" in html and '<span class="tag">living</span>' in html
    assert "accented letters" in text(preview(importer, GED.replace("Zed", "Zéd").encode("latin-1")))
    assert "look like people already in the tree" not in html
    zed_only = "0 HEAD\n0 @I1@ INDI\n1 NAME Zed Quentin /Importson/\n1 BIRT\n2 DATE 1850\n0 TRLR"
    assert "1 of the 1 people in this file look like people already in the tree" in text(preview(importer, zed_only))


def test_import_and_undo(app, importer):
    sql(app, "INSERT INTO places (gedcom, place, temple, geoignore, changedate, changedby)"
             " VALUES (%s, 'Sea', 0, 0, '2000-01-01', 'x')", (app.config["TREE"],))
    html = text(preview(importer))
    r = importer.post(f"/admin/import/{token_of(html)}", data={"csrf": CSRF, "filename": "family.ged",
                                                                     "anyway": "1"})
    assert r.status_code == 302 and "Imported 4 people and 2 families" in text(importer.get("/admin/import"))
    imp = sql(app, "SELECT * FROM app_imports ORDER BY id DESC LIMIT 1")[0]
    made = json.loads(imp["created"])
    zed, ann, kid, grandkid = made["people"]
    f1, f2 = made["families"]
    z = sql(app, "SELECT * FROM people WHERE personID = %s", (zed,))[0]
    assert z["firstname"] == "Zed Quentin" and z["burialtype"] == 1 and z["burialplace"] == "Sea"
    assert sql(app, "SELECT famc FROM people WHERE personID = %s", (kid,))[0]["famc"] == f1
    fam = sql(app, "SELECT * FROM families WHERE familyID = %s", (f1,))[0]
    assert (fam["husband"], fam["wife"], fam["marrplace"], fam["living"]) == (zed, ann, "Wedtown, PA", 0)
    kids = sql(app, "SELECT personID, haskids FROM children WHERE familyID = %s", (f1,))
    assert kids == [{"personID": kid, "haskids": 1}]  # @I99@ isn't in the file
    events = {e["tag"]: e for e in sql(app, "SELECT e.*, et.tag FROM events e JOIN eventtypes et"
                                            " USING (eventtypeID) WHERE persfamID IN (%s, %s)", (zed, f1))}
    assert events["OCCU"]["info"] == "Farmer" and events["EVEN"]["info"] == "County fair: Fair"
    assert events["BIRT"]["eventdate"] == "1851" and events["CENS"]["eventplace"] == "Censusville"
    notes = sql(app, "SELECT nl.eventID, x.note FROM notelinks nl JOIN xnotes x ON x.ID = nl.xnoteID"
                     " WHERE persfamID = %s", (zed,))
    assert {"eventID": "", "note": "A shared note\non two lines"} in notes
    assert {"eventID": "", "note": "Inline note with @ sign"} in notes
    assert {"eventID": "", "note": "Biography & more"} in notes
    assert {"eventID": str(events["OCCU"]["eventID"]), "note": "Grew corn"} in notes
    cites = sql(app, "SELECT * FROM citations WHERE persfamID = %s ORDER BY eventID", (zed,))
    assert cites[0]["description"] == "A source written inline" and cites[1]["eventID"] == "BIRT"
    assert cites[1]["sourceID"] == made["sources"][0] and cites[1]["citetext"] == "Born at home"
    assert sql(app, "SELECT * FROM places WHERE place = 'Importville, PA'")
    assert made["places"] == ["Censusville", "Importville, PA", "Wedtown, PA"]  # not Sea, already here
    sql(app, "UPDATE places SET changedate = NOW() + INTERVAL 1 DAY WHERE place = 'Censusville'")
    sql(app, "UPDATE users SET personID = %s, mygedcom = %s WHERE username = 'fx_nonotes'", (zed, app.config["TREE"]))
    sql(app, "INSERT INTO app_watches (gedcom, personID, userID, created_at) SELECT %s, %s, userID, NOW() FROM users"
             " WHERE username = 'fx_nonotes'", (app.config["TREE"], zed))
    assert "Undo this import" in text(importer.get("/admin/import"))
    importer.post(f"/admin/import/{imp['id']}/undo", data={"csrf": CSRF})
    assert not sql(app, "SELECT * FROM people WHERE personID IN (%s, %s)", (zed, grandkid))
    assert not sql(app, "SELECT * FROM families WHERE familyID = %s", (f2,))
    assert not sql(app, "SELECT * FROM sources WHERE sourceID = %s", (made["sources"][0],))
    assert not sql(app, "SELECT * FROM xnotes WHERE ID IN %s", (tuple(made["xnotes"]),))
    assert '<span class="tag">undone</span>' in text(importer.get("/admin/import"))
    assert sql(app, "SELECT personID FROM users WHERE username = 'fx_nonotes'")[0]["personID"] == ""
    assert not sql(app, "SELECT * FROM app_watches WHERE personID = %s", (zed,))
    places = {r["place"] for r in sql(app, "SELECT place FROM places WHERE place IN"
                                           " ('Importville, PA', 'Wedtown, PA', 'Censusville', 'Sea')")}
    assert places == {"Sea", "Censusville"}  # here before the import; edited since
    sql(app, "DELETE FROM places WHERE place IN ('Sea', 'Censusville')")
    assert importer.post(f"/admin/import/{imp['id']}/undo", data={"csrf": CSRF}).status_code == 404


def run_import(app, importer, data=GED):
    importer.post(f"/admin/import/{token_of(text(preview(importer, data)))}", data={"csrf": CSRF, "anyway": "1"})
    imp = sql(app, "SELECT * FROM app_imports ORDER BY id DESC LIMIT 1")[0]
    return imp, json.loads(imp["created"])


def test_undo_refused_after_changes(app, importer):
    imp, made = run_import(app, importer)
    sql(app, "UPDATE people SET changedate = NOW() + INTERVAL 1 DAY WHERE personID = %s", (made["people"][0],))
    html = text(importer.post(f"/admin/import/{imp['id']}/undo", data={"csrf": CSRF}, follow_redirects=True))
    assert "can&#39;t be undone" in html
    imp, made = run_import(app, importer)
    sql(app, "DELETE FROM families WHERE familyID = %s", (made["families"][1],))  # merged or deleted since
    html = text(importer.post(f"/admin/import/{imp['id']}/undo", data={"csrf": CSRF}, follow_redirects=True))
    assert "can&#39;t be undone" in html


def test_empty_file_and_undo(app, importer):
    importer.post(f"/admin/import/{token_of(text(preview(importer, chr(10).join(['0 HEAD', '0 TRLR']))))}",
                  data={"csrf": CSRF})
    imp = sql(app, "SELECT * FROM app_imports ORDER BY id DESC LIMIT 1")[0]
    importer.post(f"/admin/import/{imp['id']}/undo", data={"csrf": CSRF})
    assert sql(app, "SELECT undone_at FROM app_imports WHERE id = %s", (imp["id"],))[0]["undone_at"]


def test_stashed_files(app, importer):
    for bad in ("bad!", "x" * 5):
        assert importer.post("/admin/import/" + bad, data={"csrf": CSRF}).status_code == 400
    assert importer.post("/admin/import/" + "x" * 30, data={"csrf": CSRF}).status_code == 404
    old = os.path.join(app.instance_path, "imports", "old.ged")
    os.makedirs(os.path.dirname(old), exist_ok=True)
    with open(old, "w") as f:
        f.write("x")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    importer.get("/admin/import")
    preview(importer)
    assert not os.path.exists(old)  # a preview nobody imported, left for over a day


def test_id_taken_meanwhile(app, importer, monkeypatch):
    real, calls = gi.insert, []

    def flaky(table, values, **kw):
        calls.append(table)
        if table == "people" and calls.count("people") == 1:
            raise pymysql.err.IntegrityError(1062, "Duplicate entry")
        return real(table, values, **kw)
    monkeypatch.setattr(gi, "insert", flaky)
    _, made = run_import(app, importer)
    assert len(made["people"]) == 4


def test_birth_bounds_stop_on_a_loop(app):
    """Bad data where someone is their own child: the bound would fall forever, so passes are capped."""
    loop = "0 HEAD\n0 @I1@ INDI\n1 BIRT\n2 DATE 1900\n0 @F1@ FAM\n1 HUSB @I1@\n1 CHIL @I1@\n0 TRLR"
    with app.app_context():
        by, _, _ = gi.plan(gi.parse(loop))
        assert gi.birth_bounds(by)["@I1@"] == 1900 - 12 * 50


SITE_GED = """0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Pa /Sitefile/
2 _MARNM Wedd
1 NAME Pat /Sitefile/ Sr.
2 TYPE aka
2 NOTE Known as Pat at work
1 NAME Pappy
1 _UID 1A2B3C
1 RESI
2 DATE 1900
2 PLAC Ohio
1 RESI
2 PLAC Iowa
1 _MILT
2 DATE 1918
1 _NOPLACE no date or place
1 ENDL
2 DATE 1950
1 FAMS @F1@
0 @I2@ INDI
1 NAME Adopted /Sitefile/
1 _UID 4D5E6F
1 FAMC @F1@
2 PEDI adopted
0 @I3@ INDI
1 NAME Step /Sitefile/
1 FAMC @F1@
0 @I4@ INDI
1 NAME Foster /Sitefile/
1 FAMC @F1@
2 _FREL foster
2 _MREL birth
0 @F1@ FAM
1 HUSB @I1@
1 CHIL @I2@
1 CHIL @I3@
2 _FREL STEP
2 _MREL Natural
1 CHIL @I4@
1 ENGA
2 DATE 1899
0 TRLR
"""


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_zip_uploads(app, importer):
    html = text(preview(importer, zipped({"tree.ged": GED, "__MACOSX/._tree.ged": "junk"}), "tree.zip"))
    assert "<strong>4</strong> people" in html
    importer.post(f"/admin/import/{token_of(html)}", data={"csrf": CSRF, "filename": "tree.zip", "anyway": "1"})
    assert "Imported 4 people" in text(importer.get("/admin/import"))
    for files, message in (({"a.ged": GED, "b.ged": GED}, "holds 2"), ({"x.txt": "x"}, "holds 0")):
        preview(importer, zipped(files), "tree.zip")
        assert message in text(importer.get("/admin/import"))
    preview(importer, b"PK\x03\x04 not really a zip", "tree.zip")
    assert "can&#39;t be opened" in text(importer.get("/admin/import"))
    app.config["MAX_CONTENT_LENGTH"], limit = 100, app.config["MAX_CONTENT_LENGTH"]
    try:
        with app.test_request_context(), pytest.raises(gi.GedcomError, match="too big"):
            gi.unzip(zipped({"big.ged": "0 HEAD\n" * 100}))
    finally:
        app.config["MAX_CONTENT_LENGTH"] = limit


def test_new_kinds_of_event_and_child_relations(app, importer):
    html = text(preview(importer, SITE_GED))
    assert "Residence (person, 2)" in html and "Military service (person, 1)" in html
    assert "Engagement (family, 1)" in html and "_NOPLACE (1)" in html and "ENDL (1)" in html
    assert "Also known as (person, 3)" in html
    importer.post(f"/admin/import/{token_of(html)}", data={"csrf": CSRF})
    imp = sql(app, "SELECT * FROM app_imports ORDER BY id DESC LIMIT 1")[0]
    made = json.loads(imp["created"])
    pa, adopted, step, foster = made["people"]
    events = sql(app, "SELECT et.tag, et.display, e.eventplace FROM events e JOIN eventtypes et USING (eventtypeID)"
                      " WHERE persfamID IN (%s, %s) ORDER BY e.eventID", (pa, made["families"][0]))
    assert [(e["tag"], e["eventplace"]) for e in events] == [("RESI", "Ohio"), ("RESI", "Iowa"), ("_MILT", ""),
                                                             *[("_AKA", "")] * 3, ("ENGA", "")]
    assert events[0]["display"] == "Residence" and len(made["eventtypes"]) == 4
    akas = sql(app, "SELECT e.eventID, e.info FROM events e JOIN eventtypes et USING (eventtypeID)"
                    " WHERE persfamID = %s AND et.tag = '_AKA' ORDER BY e.eventID", (pa,))
    assert [a["info"] for a in akas] == ["Married name: Pa Wedd", "Also known as: Pat Sitefile Sr.", "Pappy"]
    assert sql(app, "SELECT x.note FROM notelinks nl JOIN xnotes x ON x.ID = nl.xnoteID WHERE nl.eventID = %s",
               (str(akas[1]["eventID"]),))[0]["note"] == "Known as Pat at work"
    assert "Pappy" in text(importer.get(f"/person/{pa}"))
    rels = {c["personID"]: (c["frel"], c["mrel"]) for c in sql(app, "SELECT * FROM children WHERE familyID = %s",
                                                                 (made["families"][0],))}
    assert rels == {adopted: ("adopted", "adopted"), step: ("step", "birth"), foster: ("foster", "birth")}
    assert "Adopted child" in text(importer.get(f"/person/{adopted}"))
    assert "Father: step · mother: birth" in text(importer.get(f"/person/{step}"))
    assert "child</p>" not in text(importer.get(f"/person/{pa}"))
    assert made["uids"] == ["1A2B3C", "4D5E6F"]
    again = text(preview(importer, SITE_GED, "again.ged"))
    assert "2 of the 4 people in this file came in with an earlier file on" in again and 'name="anyway"' in again
    r = importer.post(f"/admin/import/{token_of(again)}", data={"csrf": CSRF}, follow_redirects=True)
    assert "Nothing imported" in text(r) and sql(app, "SELECT MAX(id) AS n FROM app_imports")[0]["n"] == imp["id"]
    # A second import reuses the kinds of event the first added. Undoing the first keeps them while the second
    # uses them; after that they stay, unused, for an admin to delete under Event types.
    imp2, made2 = run_import(app, importer, SITE_GED)
    assert made2["eventtypes"] == []
    importer.post(f"/admin/import/{imp['id']}/undo", data={"csrf": CSRF})
    importer.post(f"/admin/import/{imp2['id']}/undo", data={"csrf": CSRF})
    assert len(sql(app, "SELECT * FROM eventtypes WHERE eventtypeID IN %s", (tuple(made["eventtypes"]),))) == 4
    sql(app, "DELETE FROM eventtypes WHERE eventtypeID IN %s", (tuple(made["eventtypes"]),))
    # Undone while nothing else uses them, they go too.
    imp, made = run_import(app, importer, SITE_GED)
    importer.post(f"/admin/import/{imp['id']}/undo", data={"csrf": CSRF})
    assert len(made["eventtypes"]) == 4 and not sql(app, "SELECT * FROM eventtypes WHERE eventtypeID IN %s",
                                                    (tuple(made["eventtypes"]),))


def test_imported_kind_matches_one_by_name(app, importer):
    sql(app, "INSERT INTO eventtypes (tag, description, display, keep, collapse, ordernum, ldsevent, type)"
             " VALUES ('EVEN', 'Residence', 'Residence', 1, 0, 0, 0, 'I')")
    try:
        _, made = run_import(app, importer, SITE_GED)
        assert sql(app, "SELECT COUNT(*) AS n FROM eventtypes WHERE tag = 'RESI'")[0]["n"] == 0
        assert len(made["eventtypes"]) == 3  # military service, engagement and other names only
    finally:
        sql(app, "DELETE FROM events WHERE eventtypeID IN (SELECT eventtypeID FROM eventtypes WHERE tag IN"
                 " ('_MILT', 'ENGA', '_AKA') OR description = 'Residence')")
        sql(app, "DELETE FROM eventtypes WHERE tag IN ('_MILT', 'ENGA', '_AKA') OR description = 'Residence'")

import re

from conftest import client_for, make, sql

from app.db import insert
from app.gedcom import MAX_LINE, text_lines


def record(body, xref):
    """One record's lines, from its 0 line up to the next."""
    m = re.search(rf"^0 @{xref}@ \w+\n(?:[1-9].*\n)*", body, re.MULTILINE)
    return m.group(0) if m else ""


def export(app, user):
    r = client_for(app, user).get("/export.ged")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_text_lines():
    assert text_lines(1, "NOTE", "a\r\nb@c") == ["1 NOTE a", "2 CONT b@@c"]
    assert text_lines(1, "NOTE", "") == ["1 NOTE"]
    long = ("word " * 100).strip()
    out = text_lines(1, "NOTE", long)
    assert all(len(x) <= MAX_LINE + 7 for x in out) and out[1].startswith("2 CONC ")
    # never split next to a space: both sides of each cut are non-space
    pieces = [out[0][len("1 NOTE "):]] + [x[len("2 CONC "):] for x in out[1:]]
    assert "".join(pieces) == long and all(p[0] != " " and p[-1] != " " for p in pieces)
    assert text_lines(1, "NOTE", "x" * 450)[2] == "2 CONC " + "x" * 50  # no spaces at all: cut at the limit


def test_needs_gedcom_right(app):
    guest = client_for(app, "fx_guest")
    assert guest.get("/export.ged").status_code == 403
    assert "/export.ged" not in guest.get("/search").get_data(as_text=True)
    exporter = client_for(app, "fx_exporter")
    assert "/export.ged" not in exporter.get("/search").get_data(as_text=True)  # not in the footer any more
    assert "/export.ged" in exporter.get("/account/").get_data(as_text=True)  # but on their account page
    assert "/export.ged" not in guest.get("/account/").get_data(as_text=True)
    admin_page = client_for(app, "fx_siteadmin").get("/admin/").get_data(as_text=True)
    assert "/export.ged" not in admin_page and "needs the Download GEDCOM right" in admin_page


def test_export_structure(app):
    r = client_for(app, "fx_ged_admin").get("/export.ged")
    assert 'filename="tree1-' in r.headers["Content-Disposition"]
    body = r.get_data(as_text=True)
    assert body.startswith("0 HEAD\n1 SOUR Kith\n") and body.endswith("0 TRLR\n")
    hal = record(body, "XHUSB")
    assert "1 NAME Hal /Fixture/\n1 SEX M\n1 BIRT\n2 PLAC Synthville, Testshire\n" in hal
    assert "1 FAMC @XFAM2@" in hal and "1 FAMS @XFAM@" in hal
    assert "1 NOTE Secret fixture note" in hal and "1 NOTE Open fixture note" in hal
    fam = record(body, "XFAM")
    assert "1 HUSB @XHUSB@\n1 WIFE @XWIFE@\n1 CHIL @XKID@\n1 MARR\n2 DATE 1 JAN 1900\n" in fam
    assert "1 CENS\n2 PLAC Synthville, Testshire" in fam
    assert "XNOFAMILY" not in body and "XNOSUCHFAM" not in body  # links to missing families are dropped


def test_export_child_order(app):
    ids = make(app, ("Dad", "M", 0), ("A", "F", 0), ("B", "F", 0), family=("Dad", None), kids=("A", "B"))
    sql(app, "UPDATE people SET birthdatetr = '1900-01-01' WHERE personID = %s", (ids["B"],))
    fam = record(export(app, "fx_ged_admin"), ids["family"])
    assert fam.index(ids["B"]) < fam.index(ids["A"])  # oldest first, the undated child last, as on the site

def test_export_privacy(app):
    ids = make(app, ("Livgal", "F", 1), ("Privguy", "M", 0), family=("Privguy", "Livgal"))
    sql(app, "UPDATE people SET private = 1, birthdate = '1 JAN 1950' WHERE personID = %s", (ids["Privguy"],))
    sql(app, "UPDATE people SET birthdate = '1 JAN 1990' WHERE personID = %s", (ids["Livgal"],))
    sql(app, "UPDATE families SET living = 1, marrdate = '1 JUN 2010' WHERE familyID = %s", (ids["family"],))
    body = export(app, "fx_exporter")
    assert record(body, ids["Livgal"]).startswith(f"0 @{ids['Livgal']}@ INDI\n1 NAME Living //\n1 SEX F\n1 FAMS")
    priv = record(body, ids["Privguy"])
    assert "Privguy" in priv and "BIRT" not in priv  # private: name kept, details dropped
    assert "2010" not in record(body, ids["family"]) and f"1 WIFE @{ids['Livgal']}@" in record(body, ids["family"])
    hal = record(body, "XHUSB")
    assert "Open fixture note" in hal and "Secret fixture note" not in hal


def test_export_events_sources_and_name_parts(app):
    ids = make(app, ("Gedperson", "M", 0))
    pid = ids["Gedperson"]
    sql(app, "UPDATE people SET lnprefix = 'van', suffix = 'Jr.', nickname = 'Ged', altbirthtype = 'BAPM',"
             " altbirthdate = '2 JAN 1800', burialtype = 1, burialplace = 'Ashes Bay' WHERE personID = %s", (pid,))
    etype = lambda tag: sql(app, "SELECT eventtypeID FROM eventtypes WHERE tag = %s AND type = 'I'"
                            " AND ldsevent = 0 LIMIT 1", (tag,))[0]["eventtypeID"]
    with app.app_context():
        _, fair = insert("eventtypes", {"tag": "EVEN", "description": "County fair", "display": "County fair",
                                            "type": "I", "keep": 1})  # a custom event type
        _, even = insert("events", {"gedcom": "tree1", "persfamID": pid, "eventtypeID": fair, "info": "Fair",
                                        "eventdate": "1820", "age": "20", "agency": "Town", "cause": "Fun"})
        insert("events", {"gedcom": "tree1", "persfamID": pid, "eventtypeID": etype("SSN"), "info": "123-45-6789"})
        insert("events", {"gedcom": "tree1", "persfamID": pid, "eventtypeID": etype("OCCU")})
        insert("sources", {"gedcom": "tree1", "sourceID": "XS1", "title": "Fixture Register", "shorttitle": "FR"})
        for key, src, page, text in (("", "XS1", "p. 4", "Entry"), ("BIRT", "", "Family bible", ""),
                                     (str(even), "XS1", "", ""), ("N999", "XS1", "p. 9", ""), ("BAPL", "XS1", "LDS", ""),
                                     ("NAME", "", "", "")):
            insert("citations", {"gedcom": "tree1", "persfamID": pid, "eventID": key, "sourceID": src,
                                     "page": page, "citetext": text})
        _, xid = insert("xnotes", {"gedcom": "tree1", "note": "Name note"})
        insert("notelinks", {"gedcom": "tree1", "persfamID": pid, "xnoteID": xid, "eventID": "NAME"})
    body = export(app, "fx_exporter")
    rec = record(body, pid)
    tag = pid[1:-len("GEDPERSON")]  # make() uses the test tag as the surname
    assert f"1 NAME Gedperson /van {tag}/ Jr.\n2 NSFX Jr.\n2 NICK Ged\n2 NOTE Name note\n" in rec
    assert "1 BAPM\n2 DATE 2 JAN 1800\n" in rec and "1 CREM\n2 PLAC Ashes Bay\n" in rec
    assert "1 BIRT\n2 SOUR Family bible\n" in rec  # no date or place, but a source: still written
    assert ("1 EVEN Fair\n2 DATE 1820\n2 TYPE County fair\n2 AGE 20\n2 AGNC Town\n2 CAUS Fun\n2 SOUR @XS1@\n") in rec
    assert "1 OCCU\n" in rec and "SSN" not in rec  # SSNs only for editors
    assert "1 SOUR @XS1@\n2 PAGE p. 4\n2 DATA\n3 TEXT Entry\n" in rec
    assert "1 SOUR @XS1@\n2 PAGE p. 9\n" in rec and "LDS" not in rec  # on a note: kept at the top; LDS: never
    assert "0 @XS1@ SOUR\n1 TITL Fixture Register\n1 ABBR FR\n" in body
    assert "SSN" in record(export(app, "fx_ged_admin"), pid)

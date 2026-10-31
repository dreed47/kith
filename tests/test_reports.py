"""Register report and dates & anniversaries."""
import datetime
import re

from conftest import client_for, make, sql

from app import relations, reports
from app.db import insert
from app.reports import roman, when_where


def text(r):
    return r.get_data(as_text=True)


def test_when_where(app):
    with app.app_context():
        assert when_where("1 JAN 1900", "Synthville") == "on 1 Jan 1900 in Synthville"
        assert when_where("JAN 1900") == "in Jan 1900" and when_where("1900") == "in 1900"
        assert when_where("1939-1940") == "in 1939-1940"
        assert when_where("abt 1850", "Here") == "abt 1850 in Here" and when_where("", "Here") == "in Here"


def test_roman():
    assert [roman(n) for n in (1, 4, 9, 14, 19, 23)] == ["i", "iv", "ix", "xiv", "xix", "xxiii"]


def chain(app):
    """Four generations of made-up people: Top -> Mid (+ Midwife) -> Low (+ Lowwife, married 1 Jan 1900) -> End."""
    ids = make(app, ("Top", "M", 0), ("Mid", "M", 0), ("Midwife", "F", 0), ("Low", "M", 0), ("Lowwife", "F", 0),
               ("End", "F", 0))
    sql(app, "UPDATE people SET birthplace = 'Synthville' WHERE personID = %s", (ids["Low"],))
    with app.app_context():
        for fid, husband, wife, child, marr in ((ids["Top"] + "F", ids["Top"], "", ids["Mid"], ""),
                                                 (ids["Mid"] + "F", ids["Mid"], ids["Midwife"], ids["Low"], ""),
                                                 (ids["Low"] + "F", ids["Low"], ids["Lowwife"], ids["End"], "1 JAN 1900")):
            insert("families", {"gedcom": "tree1", "familyID": fid, "husband": husband, "wife": wife,
                                    "marrdate": marr, "marrplace": "Synthville" if marr else ""})
            insert("children", {"gedcom": "tree1", "familyID": fid, "personID": child, "ordernum": 1})
    return ids


def test_register(app, admin):
    ids = chain(app)
    html = text(admin.get(f"/register/{ids['Top']}"))
    for n, who in ((1, "Top"), (2, "Mid"), (3, "Low")):
        assert f'id="p{n}"' in html and f"/person/{ids[who]}" in html
    assert "Generation 3" in html and "Generation 4" not in html  # End has no family: listed, no entry of their own
    assert 'id="p4"' not in html and '<span class="reg-num">4</span>' in html and '+ <a href="#p3">3</a>' in html
    assert "(Mid<sup>2</sup>, Top<sup>1</sup>)" in html
    assert "Low was born in Synthville." in html and "Top had a family with an unknown spouse." in html
    assert f'married <a href="/person/{ids["Lowwife"]}">' in html and ", on 1 Jan 1900 in Synthville." in html
    assert f"/register/{ids['Top']}" in text(admin.get(f"/descendants/{ids['Top']}"))
    assert f"/register/{ids['Low']}" in text(admin.get(f"/person/{ids['Low']}"))


def test_register_generations_and_404(app, admin):
    ids = chain(app)
    html = text(admin.get(f"/register/{ids['Top']}?gens=2"))
    assert "Generation 2" in html and 'id="p3"' not in html and '<span class="reg-num">3</span>' in html
    assert admin.get("/register/NOPE").status_code == 404


def test_register_sentences_and_spouse_parents(app, admin):
    ids = make(app, ("Rroot", "U", 0), ("Rwife", "F", 0), ("Rdad", "M", 0), ("Rmom", "F", 0), ("Rkid", "M", 0),
               family=("Rdad", "Rmom"), kids=("Rwife",))
    root = ids["Rroot"]
    sql(app, "UPDATE people SET altbirthtype = 'BAPM', altbirthdate = '1800', burialtype = 1,"
             " burialplace = 'Sea', deathdate = 'abt 1860' WHERE personID = %s", (root,))
    with app.app_context():
        for fid in ("XRF1", "XRF2"):  # married twice; the same child recorded in both families
            insert("families", {"gedcom": "tree1", "familyID": fid, "husband": root, "wife": ids["Rwife"]})
            insert("children", {"gedcom": "tree1", "familyID": fid, "personID": ids["Rkid"]})
        _, xid = insert("xnotes", {"gedcom": "tree1", "note": "Register note text"})
        insert("notelinks", {"gedcom": "tree1", "persfamID": root, "xnoteID": xid})
    html = text(admin.get(f"/register/{root}"))
    assert "Rroot was baptized in 1800. Rroot died abt 1860. Rroot was cremated in Sea." in html  # no sex: no pronoun
    assert f'daughter of <a href="/person/{ids["Rdad"]}">' in html and "Register note text" in html
    assert html.count('<span class="reg-num">2</span>') == 2  # one number, listed twice


def test_register_privacy(app):
    ids = make(app, ("Livroot", "F", 1), ("Livkid", "M", 1), family=("Livroot", None), kids=("Livkid",))
    sql(app, "UPDATE people SET birthdate = '1 MAY 1980' WHERE personID = %s", (ids["Livroot"],))
    html = text(client_for(app, "fx_guest").get(f"/register/{ids['Livroot']}"))
    assert "Details are private." in html and "1980" not in html and "Living" in html


def test_branch_book(app, admin):
    ids = chain(app)
    html = " ".join(text(admin.get(f"/book/{ids['Top']}?gens=4")).split())
    assert "Descendants of" in html and "4 people in 4 generations" in html
    for n, who in ((1, "Top"), (2, "Mid"), (3, "Low"), (4, "End")):
        assert f'id="p{n}"' in html and f"/person/{ids[who]}" in html
    assert "was born in Synthville" in html and 'class="book-entry"' in html
    assert "3 people in 3 generations" in text(admin.get(f"/book/{ids['Top']}"))  # 3 by default
    assert f"/book/{ids['Top']}" in text(admin.get(f"/register/{ids['Top']}"))
    assert admin.get("/book/NOPE").status_code == 404
    sql(app, "UPDATE people SET living = 1 WHERE personID = %s", (ids["End"],))
    guest = " ".join(text(client_for(app, "fx_guest").get(f"/book/{ids['Top']}?gens=4")).split())
    assert 'id="p4"' in guest and "Details are private." in guest


def test_migration_map(app, admin):
    import json
    ids = chain(app)
    sql(app, "UPDATE people SET birthdate = '1850', birthdatetr = '1850-00-00', birthplace = 'Xmigfrom, PA',"
             " deathplace = 'Xmigto, OH' WHERE personID = %s", (ids["Mid"],))
    sql(app, "UPDATE people SET birthdatetr = '1880-00-00', burialplace = 'Xmigto, OH', living = 1 WHERE personID = %s",
        (ids["Low"],))
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Xmigfrom, PA", "latitude": "41", "longitude": "-79"})
        insert("places", {"gedcom": "tree1", "place": "Xmigto, OH", "latitude": "40", "longitude": "-82"})
    html = text(admin.get(f"/map?pid={ids['Top']}"))
    lines = json.loads(re.search(r"data-lines='([^']*)'", html)[1])
    assert lines == [[[41.0, -79.0], [40.0, -82.0]]] and "Map: Descendants of" in html
    pins = json.loads(re.search(r"data-pins='([^']*)'", html)[1].replace("&#39;", "'"))
    assert {e["label"] for p in pins for e in p["events"]} == {"Born", "Died", "Buried"}
    assert f"/map?pid={ids['Top']}" in text(admin.get(f"/register/{ids['Top']}"))
    guest = client_for(app, "fx_guest")
    shown = json.loads(re.search(r"data-pins='([^']*)'", text(guest.get(f"/map?pid={ids['Top']}")))[1])
    assert {e["label"] for p in shown for e in p["events"]} == {"Born", "Died"}  # Low is living: left out
    tag = sql(app, "SELECT lastname FROM people WHERE personID = %s", (ids["Top"],))[0]["lastname"]
    assert "data-lines" in text(admin.get(f"/map?surname={tag}&born_from=1840&born_to=1860"))
    sql(app, "UPDATE people SET birthdatetr = '1900-00-00', birthplace = 'Xnocoords, PA' WHERE personID = %s",
        (ids["Lowwife"],))
    assert "None of their places" in text(admin.get(f"/map?surname={tag}&born_from=1895&born_to=1905"))
    assert "No one to show." in text(admin.get(f"/map?surname={tag}&born_from=1700&born_to=1710"))
    assert f"/map?surname={tag}" in text(admin.get(f"/surnames/{tag}"))
    assert admin.get("/map").status_code == 404 and admin.get("/map?pid=NOPE").status_code == 404


def test_anniversaries_list(app, admin, monkeypatch):
    ids = make(app, ("Marchborn", "F", 0), ("Monthonly", "M", 0), ("Livmarch", "M", 1))
    for pid, date, tr in ((ids["Marchborn"], "5 MAR 1901", "1901-03-05"), (ids["Monthonly"], "MAR 1902", "1902-03-00"),
                          (ids["Livmarch"], "5 MAR 1990", "1990-03-05")):
        sql(app, "UPDATE people SET birthdate = %s, birthdatetr = %s WHERE personID = %s", (date, tr, pid))
    html = text(admin.get("/anniversaries?month=3&day=5&kind=born"))
    assert "Marchborn" in html and "Monthonly" not in html and f"{datetime.date.today().year - 1901} years ago" in html
    month = text(admin.get("/anniversaries?month=3&day=40&kind=born"))  # a day that can't be: the whole month
    assert "Monthonly" in month and "Day not known" in month and "<h3>5 March</h3>" in month
    assert "Livmarch" not in text(client_for(app, "fx_guest").get("/anniversaries?month=3&day=5&kind=born"))
    fam = make(app, ("Janh", "M", 0), ("Janw", "F", 0), family=("Janh", "Janw"))["family"]
    sql(app, "UPDATE families SET marrdate = '1 JAN 1900', marrdatetr = '1900-01-01' WHERE familyID = %s", (fam,))
    married = text(admin.get("/anniversaries?month=1&day=1&kind=married"))
    assert f"/family/{fam}" in married and "Janh" in married and "Janw" in married
    assert "Marchborn" not in text(admin.get("/anniversaries?month=3&day=5&kind=christened"))
    monkeypatch.setattr(reports, "LIST_LIMIT", 1)
    assert "Showing the first 1." in text(admin.get("/anniversaries?month=3&day=5&kind=born"))
    assert "Nothing recorded for that date." in text(admin.get("/anniversaries?month=2&day=31"))


def test_anniversaries_defaults_to_today(admin):
    today = datetime.date.today()
    for url in ("/anniversaries", "/anniversaries?month=13"):
        assert f"{today.day} {today.strftime('%B')}" in text(admin.get(url))
    assert "/anniversaries" in text(admin.get("/")) and ">Dates</a>" in text(admin.get("/search"))


def test_calendar(admin, monkeypatch):
    today = datetime.date.today()
    monkeypatch.setattr(reports, "dated_events", lambda month, day, kinds: [
        {"kind": "born", "day": 3}, {"kind": "born", "day": 3}, {"kind": "married", "day": 3}, {"kind": "died", "day": 0}])
    html = text(admin.get(f"/anniversaries/calendar?month={today.month}"))
    assert "2 births" in html and "1 marriage<" in html and "death" not in html.split("<table")[1]
    assert 'class="today"' in html and f"/anniversaries?month={today.month}&amp;day=3" in html
    other = today.month % 12 + 1
    assert 'class="today"' not in text(admin.get(f"/anniversaries/calendar?month={other}"))


def test_where_lines_begin(app, admin, monkeypatch):
    ids = chain(app)  # Top -> Mid -> Low -> End: Top has 3 descendants
    monkeypatch.setattr(reports, "LINE_MIN", 3)
    tag = sql(app, "SELECT lastname FROM people WHERE personID = %s", (ids["Top"],))[0]["lastname"]
    sql(app, "UPDATE people SET birthdate = '1800', birthdatetr = '1800-00-00' WHERE personID = %s", (ids["Top"],))
    relations.invalidate()  # the family graph is cached
    html = " ".join(text(admin.get("/lines")).split())
    row = html[html.index(f"<th>{tag}</th>"):][:900]
    assert f"/person/{ids['Top']}" in row and "3 descendants" in row and "born 1800" in row
    assert f"/descendants/{ids['Top']}" in row and f"/map?pid={ids['Top']}" in row
    assert "Where the lines begin" in text(admin.get("/surnames"))
    other = chain(app)  # a second, later line: listed under its own made-up surname, and with Top's name, counted
    sql(app, "UPDATE people SET lastname = %s, birthdate = '1810', birthdatetr = '1810-00-00' WHERE personID = %s",
        (tag, other["Top"]))
    relations.invalidate()
    row = " ".join(text(admin.get("/lines")).split())
    row = row[row.index(f"<th>{tag}</th>"):][:900]
    assert f"/person/{ids['Top']}" in row and "1 other line of this name" in row
    monkeypatch.setattr(reports, "LINES_SHOWN", 1)
    assert "The biggest 1 of" in text(admin.get("/lines"))
    monkeypatch.setattr(reports, "LINE_MIN", 10 ** 6)
    assert "No lines with" in text(admin.get("/lines"))

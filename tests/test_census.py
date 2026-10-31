"""Census by year: the years list and one year's households by place."""
from conftest import client_for, make, sql

from app.db import insert


def text(r):
    return r.get_data(as_text=True)


def census(app, persfam, kind, year, place, info=""):
    with app.app_context():
        et = sql(app, "SELECT eventtypeID FROM eventtypes WHERE tag = 'CENS' AND type = %s", (kind,))[0]["eventtypeID"]
        insert("events", {"gedcom": "tree1", "persfamID": persfam, "eventtypeID": et, "eventdate": year,
                          "eventdatetr": f"{year}-00-00", "eventplace": place, "info": info})


def test_census_households(app, admin):
    ids = make(app, ("Pa", "M", 0), ("Ma", "F", 0), ("Son", "M", 0), ("Lodger", "M", 0), ("Wed", "F", 0),
               family=("Pa", "Ma"), kids=("Son",))
    # Son married by 1777 and counted in his own household that year...
    with app.app_context():
        insert("families", {"gedcom": "tree1", "familyID": ids["Son"] + "F", "husband": ids["Son"], "wife": ids["Wed"],
                            "marrdatetr": "1770-00-00"})
    census(app, ids["family"], "F", "1777", "Xtown, Xcounty, PA", info="Page 3, line 12")
    census(app, ids["Ma"], "I", "1777", "Xtown, Xcounty, PA")
    census(app, ids["Son"], "I", "1777", "Xtown, Xcounty, PA")  # parents counted there: joins them
    census(app, ids["Lodger"], "I", "1777", "Ayville, Xcounty, PA")  # no family: a household of their own
    census(app, ids["Son"], "I", "1778", "")
    census(app, ids["Wed"], "I", "1778", "")  # in 1778 Son is with his wife, not his parents
    html = text(admin.get("/census"))
    assert '/census/1777">1777</a> <span class="muted">4</span>' in html
    assert "Census by year" in text(admin.get("/places"))
    html = " ".join(text(admin.get("/census/1777")).split())
    assert "2 households in 2 places" in html and html.index("Ayville") < html.index("Xtown")
    assert f'/family/{ids["family"]}' in html and "Page 3, line 12" in html and "Counted:" in html
    assert html.count(f'/person/{ids["Son"]}') == 1 and f'/person/{ids["Lodger"]}' in html
    html = text(admin.get("/census/1778"))
    assert "Place not recorded" in html and f'/family/{ids["Son"]}F' in html and f'/family/{ids["family"]}' not in html
    assert admin.get("/census/1666").status_code == 404


def test_census_privacy(app, admin):
    ids = make(app, ("Alive", "F", 1), ("Gone", "M", 0))
    census(app, ids["Alive"], "I", "1779", "Zedton, PA")
    census(app, ids["Gone"], "I", "1779", "Zedton, PA")
    guest = client_for(app, "fx_guest")
    assert f'/person/{ids["Alive"]}' in text(admin.get("/census/1779"))
    html = text(guest.get("/census/1779"))
    assert f'/person/{ids["Alive"]}' not in html and f'/person/{ids["Gone"]}' in html


def test_census_family_only_year(app, admin):
    ids = make(app, ("Hub", "M", 0), ("Wif", "F", 0), family=("Hub", "Wif"))
    census(app, ids["family"], "F", "1781", "Famton, PA")
    html = text(admin.get("/census/1781"))
    assert "1 household in 1 place" in html and f'/family/{ids["family"]}' in html and "Counted:" not in html



def test_parse_lines():
    from app.census_links import parse
    assert parse("John Snider - 20 yrs - son") == ("John Snider", 20)
    assert parse("son: Henry Hilliard - 22 age") == ("Henry Hilliard", 22)
    assert parse("daughter - Almira Hilliard - age 10") == ("Almira Hilliard", 10)
    assert parse("Garrison L Carnahan 55 yrs old - head - coal miner") == ("Garrison L Carnahan", 55)
    assert parse("Fare, Barbra - daughter, 11 yrs") == ("Barbra Fare", 11)
    assert parse("Stella R Bowser 2 Daughter") == ("Stella R Bowser", 2)
    assert parse("Charles Rase 43") == ("Charles Rase", 43)
    assert parse("Josephine Reese 7/12") == ("Josephine Reese", 0)
    assert parse("son: John Hilliard") == ("John Hilliard", None)  # a role, no age
    for line in ("Post Office - Ringgold", "Brookville Route 5", "143 N Elm St", "Film Number: 444763",
                 "married 13 yrs", "Ida Hilliard", "Campbell's Run Road 12 miles", ""):
        assert parse(line) is None, line


def test_fit():
    from app.census_links import fit
    p = {"firstname": "John Henry", "lastname": "SNYDER", "birthdatetr": "1830-00-00"}
    assert fit("John Snider", 1831, p) == 3 and fit("John Snider", 1820, p) == 1
    assert fit("J. Snider", 1830, p) == 3 and fit("Jo Snider", 1830, p) is None  # too short to call alike
    assert fit("Johnn Snider", 1830, p) == 3 and fit("Peter Snider", 1830, p) is None
    undated = {**p, "birthdatetr": "0000-00-00"}
    assert fit("John Snider", 1830, undated) == 2 and fit("John Jones", None, undated) == 1
    assert fit("John Snider", 1830, {**p, "firstname": ""}) is None


def test_census_matches(app, admin):
    ids = make(app, ("Pa", "M", 0), ("Ma", "F", 0), ("Kid", "M", 0), ("Gran", "F", 0), family=("Pa", "Ma"),
               kids=("Kid",))
    tag = "Gramm" + "".join(chr(97 + int(d)) for d in ids["Pa"][2:-2])  # letters: T12 isn't a surname
    for who, year in (("Pa", 1742), ("Ma", 1744), ("Kid", 1772), ("Gran", 1712)):
        sql(app, "UPDATE people SET lastname = %s, birthdate = %s, birthdatetr = %s WHERE personID = %s",
            (tag, str(year), f"{year}-00-00", ids[who]))
    with app.app_context():  # Gran is Ma's mother: a relative, not in Pa's family
        insert("families", {"gedcom": "tree1", "familyID": ids["Gran"] + "F", "wife": ids["Gran"]})
        insert("children", {"gedcom": "tree1", "familyID": ids["Gran"] + "F", "personID": ids["Ma"], "ordernum": 1})
    census(app, ids["Pa"], "I", "1782", "Gramton, PA",
           info=f"Pa {tag} - head - 40 yrs\nGran {tag} - mother-in-law - 70 yrs\nKid {tag} - son - 10 yrs\n"
                f"Zed Nobody - boarder - 30 yrs\nPost Office - Gramton")
    eid = sql(app, "SELECT eventID FROM events WHERE persfamID = %s AND eventplace = 'Gramton, PA'",
              (ids["Pa"],))[0]["eventID"]
    html = " ".join(text(admin.get("/edit/census")).split())
    assert f"Gran {tag} - mother-in-law - 70 yrs" in html and f'value="{ids["Gran"]}"' in html
    assert "Zed Nobody" not in html and "Post Office" not in html  # nobody fits / not a person
    assert "name and age fit" in html and "Census matches" in text(admin.get("/census"))
    post = lambda **f: admin.post("/edit/census", data={"csrf": "x", "event": eid, **f})
    with admin.session_transaction() as s:
        s["csrf"] = "x"
    sql(app, "UPDATE people SET birthdate = '', birthdatetr = '0000-00-00' WHERE personID = %s", (ids["Gran"],))
    assert "and set birth to abt 1712 (70 in 1782)" in " ".join(text(admin.get("/edit/census")).split())
    post(line=1, text=f"Gran {tag} - mother-in-law - 70 yrs", person=ids["Gran"], birth="abt 1712")
    gran = sql(app, "SELECT birthdate, birthdatetr FROM people WHERE personID = %s", (ids["Gran"],))[0]
    assert gran["birthdate"] == "abt 1712" and str(gran["birthdatetr"]) == "1712-00-00"
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'edit person' AND record = %s", (ids["Gran"],))
    post(line=2, text=f"Kid {tag} - son - 10 yrs")  # none of these
    html = " ".join(text(admin.get("/edit/census")).split())
    assert f"Gran {tag} - mother-in-law" in html.split("Answered lately")[1] and "none of these" in html
    page = " ".join(text(admin.get(f"/person/{ids['Gran']}")).split())
    assert f'with <a href="/person/{ids["Pa"]}">' in page and "Gramton" in page
    year = text(admin.get("/census/1782"))
    assert f'/person/{ids["Gran"]}' in year and "Counted:" not in year  # Pa's own household: Gran listed with him
    post(line=2, text=f"Kid {tag} - son - 10 yrs", undo="1")
    assert f"Kid {tag} - son - 10 yrs" in text(admin.get("/edit/census")).split("Answered lately")[0]
    # The transcription changes under an answer: the answer lapses and the line is asked again
    sql(app, "UPDATE events SET info = REPLACE(info, '70 yrs', '71 yrs') WHERE eventID = %s", (eid,))
    assert f"Gran {tag} - mother-in-law - 71 yrs" in text(admin.get("/edit/census")).split("Answered lately")[0]
    assert client_for(app, "fx_guest").get("/edit/census").status_code == 403
    post(line=0, text="gone", undo="1")  # undoing nothing is harmless
    post(line=0, text=f"Pa {tag} - head - 40 yrs", person=ids["Pa"], birth="abt 1999")  # himself: listed once
    assert sql(app, "SELECT birthdate FROM people WHERE personID = %s", (ids["Pa"],))[0]["birthdate"] == "1742"
    assert text(admin.get("/census/1782")).count(f'/person/{ids["Pa"]}"') == 1
    # Kid matched on his parents' family census, which his page shows anyway: once
    census(app, ids["family"], "F", "1783", "Gramton, PA", info=f"Kid {tag} - son - 11 yrs")
    feid = sql(app, "SELECT eventID FROM events WHERE persfamID = %s AND eventdate = '1783'",
               (ids["family"],))[0]["eventID"]
    admin.post("/edit/census", data={"csrf": "x", "event": feid, "line": 0, "text": f"Kid {tag} - son - 11 yrs",
                                     "person": ids["Kid"]})
    assert text(admin.get(f"/person/{ids['Kid']}")).count("1783") == 1
    # A link to a record whose owner is gone shows nothing
    census(app, "XGONE", "I", "1784", "Gramton, PA", info=f"Kid {tag} - son - 12 yrs")
    geid = sql(app, "SELECT eventID FROM events WHERE persfamID = 'XGONE'")[0]["eventID"]
    sql(app, "INSERT INTO app_census_links VALUES ('tree1', %s, 0, 'x', %s, 'fx_admin', NOW())", (geid, ids["Kid"]))
    assert "1784" not in text(admin.get(f"/person/{ids['Kid']}"))

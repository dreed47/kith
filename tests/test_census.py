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


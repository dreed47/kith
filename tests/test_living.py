"""Living-flag report and fixes, and the birth-year reasoning behind them."""
import datetime

from conftest import CSRF, client_for, make, sql

from app.living import latest_births, plausible, too_old, year

NOW = datetime.date.today().year


def text(r):
    return r.get_data(as_text=True)


def test_helpers():
    assert plausible(1850) == 1850 and plausible(5) is None and plausible(NOW + 1) is None
    assert year("1850-00-00") == 1850 and year("0000-00-00") is None
    assert too_old(NOW - 110) and not too_old(NOW - 109) and not too_old(None)
    bounds = latest_births({"gp": None, "p": None, "kid": 1950, "solo": None},
                           [(["gp"], ["p"], None), (["p"], ["kid"], 1930)])
    assert bounds == {"gp": 1906, "p": 1918, "kid": 1950, "solo": None}


def test_report_and_fixes(app, admin):
    ids = make(app, ("Deadliving", "M", 1), ("Oldliving", "F", 1), ("Oldparent", "M", 1), ("Recent", "F", 0),
               ("Fine", "M", 1), family=("Oldparent", None), kids=("Oldliving",))
    sql(app, "UPDATE people SET deathdate = '1990' WHERE personID = %s", (ids["Deadliving"],))
    sql(app, "UPDATE people SET birthdate = '1800', birthdatetr = '1800-00-00' WHERE personID = %s",
        (ids["Oldliving"],))
    sql(app, "UPDATE people SET birthdate = '1990', birthdatetr = '1990-00-00' WHERE personID IN (%s, %s)",
        (ids["Recent"], ids["Fine"]))
    html = text(admin.get("/edit/living"))
    died, old, recent = (html[html.index(f'id="{k}"'):] for k in ("died", "old", "recent"))
    assert ids["Deadliving"] in died[:died.index("</section>")]
    old = old[:old.index("</section>")]
    assert ids["Oldliving"] in old and ids["Oldparent"] in old and "born by 1788 at the latest" in old
    assert ids["Recent"] in recent and ids["Fine"] not in html
    assert html.count('class="tick-all"') == 6 and html.count('class="scroll-list"') == 3  # every group: tick all/untick all
    r = admin.post("/edit/living", data={"csrf": CSRF, "living": "0", "pid": [ids["Deadliving"], ids["Recent"]]})
    assert r.status_code == 302 and "Marked 1 person not living" in text(admin.get("/edit/living"))
    assert sql(app, "SELECT living FROM people WHERE personID = %s", (ids["Deadliving"],))[0]["living"] == 0
    admin.post("/edit/living", data={"csrf": CSRF, "living": "1", "pid": [ids["Recent"], ids["Fine"]]})
    assert sql(app, "SELECT living FROM people WHERE personID = %s", (ids["Recent"],))[0]["living"] == 1
    assert sql(app, "SELECT action FROM app_audit WHERE action = 'mark living'")
    assert "Marked 0 people" in text(admin.post("/edit/living", data={"csrf": CSRF, "living": "1"},
                                                follow_redirects=True))
    assert client_for(app, "fx_guest").get("/edit/living").status_code == 403


def test_long_lists_are_cut(app, admin, monkeypatch):
    from app import living
    make(app, ("Cutdead", "M", 1), ("Cutdead2", "M", 1))
    sql(app, "UPDATE people SET deathdate = '1990' WHERE firstname LIKE 'Cutdead%%'")
    monkeypatch.setattr(living, "SHOWN", 1)
    assert "Showing the first 1" in text(admin.get("/edit/living"))

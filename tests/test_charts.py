"""Pedigree options, descendants and family group sheets."""
from conftest import client_for, make, sql


def text(r):
    return r.get_data(as_text=True)


def test_pedigree_generations_picker(admin):
    html = text(admin.get("/pedigree/XKID?gens=6"))  # only drawn as deep as anyone is known: 4 generations
    assert html.count("ped-node") == 15 and '<strong aria-current="true">6</strong>' in html
    for bad in ("9", "x"):  # out of range or not a number: the default 4
        assert text(admin.get(f"/pedigree/XKID?gens={bad}")).count("ped-node") == 15


def test_pedigree_more_arrow_where_line_continues(admin):
    html = text(admin.get("/pedigree/XKID?gens=3"))
    assert "/pedigree/XGGPA" not in html  # beyond the chart
    assert 'aria-label="Earlier generations of Gus Fixture"' in html  # but Gus's father is known
    assert "Earlier generations of Gina Fixture" not in html


def test_pedigree_dates_places_and_children(admin):
    html = text(admin.get("/pedigree/XHUSB"))
    assert "b. Synthville, Testshire" in html
    assert "Children (step forward)" in html and "/pedigree/XKID?gens=4" in html
    assert "Children (step forward)" not in text(admin.get("/pedigree/XKID"))


def test_pedigree_hover_card(admin):
    html = text(admin.get("/pedigree/XKID"))
    hal = html[html.index('id="pop2"'):html.index('id="pop3"')]
    assert "<dt>Born</dt><dd>Synthville, Testshire</dd>" in hal and "<dt>Married</dt><dd>1 Jan 1900, Synthville" in hal
    assert 'aria-describedby="pop2"' in html
    assert 'class="ped-pop leftward" id="pop8"' in html  # last column opens toward the chart


def test_pedigree_hides_living_details(app, guest):
    ids = make(app, ("Livkid", "M", 1), ("Livdad", "M", 1), family=("Livdad", None), kids=("Livkid",))
    sql(app, "UPDATE people SET birthdate = '1 MAY 1990' WHERE personID IN (%s, %s)", (ids["Livkid"], ids["Livdad"]))
    html = text(guest.get(f"/pedigree/{ids['Livkid']}"))
    assert "Living" in html and "1990" not in html and "Details are private." in html


def test_descendants(admin):
    html = text(admin.get("/descendants/XGGPA"))
    for pid in ("XGPA", "XGMA", "XHUSB", "XWIFE", "XKID"):
        assert f"/person/{pid}" in html
    assert "married 1 Jan 1900" in html  # XHUSB + XWIFE's marriage
    assert '<span class="muted">Family</span>' in html  # XGGPA's family has no wife


def test_descendants_last_generation_links_onward(admin):
    html = text(admin.get("/descendants/XGGPA?gens=3"))
    assert "/person/XKID" not in html
    assert 'aria-label="Later generations of Hal Fixture">1 child →' in html and "/descendants/XHUSB?gens=3" in html


def test_descendants_survives_a_loop(app, admin):
    # Bad data: someone recorded as their own child. The page still ends.
    ids = make(app, ("Loop", "M", 0), ("Loopwife", "F", 0), family=("Loop", "Loopwife"), kids=("Loop",))
    r = admin.get(f"/descendants/{ids['Loop']}?gens=3")
    assert r.status_code == 200 and text(r).count(f'/person/{ids["Loop"]}"') == 3


def test_descendants_missing_404(admin):
    assert admin.get("/descendants/NOPE").status_code == 404
    assert admin.get("/family/NOPE/sheet").status_code == 404


def test_group_sheet(admin):
    html = text(admin.get("/family/XFAM/sheet"))
    assert "Husband: " in html and "Wife: " in html
    assert "Synthville, Testshire" in html and "1 Jan 1900" in html
    assert "/person/XGPA" in html and "/person/XGMA" in html  # Hal's parents
    assert "1. <a href=\"/person/XKID\">" in html
    assert "/family/XFAM/sheet" in text(admin.get("/family/XFAM"))


def test_group_sheet_children_spouses(admin):
    html = text(admin.get("/family/XFAM2/sheet"))
    assert "<th>Spouse</th>" in html and "/person/XWIFE" in html and "married 1 Jan 1900" in html


def test_group_sheet_privacy(app):
    ids = make(app, ("Shkid", "F", 1), ("Shdad", "M", 1), ("Shmom", "F", 0), family=("Shdad", "Shmom"), kids=("Shkid",))
    sql(app, "UPDATE people SET birthdate = '2 FEB 1980' WHERE personID = %s", (ids["Shdad"],))
    sql(app, "UPDATE families SET living = 1, marrdate = '3 MAR 1979' WHERE familyID = %s", (ids["family"],))
    html = text(client_for(app, "fx_guest").get(f"/family/{ids['family']}/sheet"))
    assert "1980" not in html and "1979" not in html and "Private" in html
    assert "No children recorded" not in html


def test_group_sheet_single_parent_no_children(app, admin):
    ids = make(app, ("Lone", "F", 0), family=(None, "Lone"))
    html = text(admin.get(f"/family/{ids['family']}/sheet"))
    assert "Husband:" not in html and "Wife: " in html and "No children recorded" in html
    assert "Nothing recorded." in html

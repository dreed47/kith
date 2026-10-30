"""Vertical pedigree, fan chart, ahnentafel, descendant table and family chart."""
import re

from conftest import client_for, make, sql

from app.charts import fan_sectors, generation_name, short


def text(r):
    return r.get_data(as_text=True)


def family_tree(app):
    """Kid -> Dad + Mom (married 1900) -> Granddad."""
    ids = make(app, ("Kid", "F", 0), ("Dad", "M", 0), ("Mom", "F", 0), family=("Dad", "Mom"), kids=("Kid",))
    up = make(app, ("Granddad", "M", 0), family=("Granddad", None))
    sql(app, "INSERT INTO children (gedcom, familyID, personID, ordernum, frel, mrel, sealdate, sealdatetr,"
             " sealplace, haskids, parentorder) VALUES ('tree1', %s, %s, 1, '', '', '', '0000-00-00', '', 0, 0)",
        (up["family"], ids["Dad"]))
    sql(app, "UPDATE people SET famc = %s, birthdate = '1870', birthplace = 'Old Town', altbirthtype = 'BAPM',"
             " altbirthdate = '1870', burialtype = 1, burialplace = 'Sea' WHERE personID = %s",
        (up["family"], ids["Dad"]))
    sql(app, "UPDATE families SET marrdate = '1900', marrplace = 'Church' WHERE familyID = %s", (ids["family"],))
    return {**ids, "Granddad": up["Granddad"]}


def test_vertical_and_fan(app, admin):
    ids = family_tree(app)
    html = text(admin.get(f"/pedigree/{ids['Kid']}/vertical?gens=3"))
    chart = html[html.index('class="vped"'):]  # past the options, whose links point at this same page
    where = [chart.index(f"/pedigree/{ids[k]}/vertical?gens=3") for k in ("Granddad", "Dad", "Kid")]
    assert where == sorted(where)  # each generation above the one before
    assert 'aria-current="page">Upward' in html and 'data-key="a2" data-from="a4 a5"' in chart
    fan = text(admin.get(f"/pedigree/{ids['Kid']}/fan?gens=3"))
    assert f"/pedigree/{ids['Granddad']}/fan?gens=3" in fan and "fan-slice empty" in fan and "fan-slice mother" in fan
    assert admin.get("/pedigree/NOPE/fan").status_code == 404


def test_fan_geometry():
    sectors = fan_sectors({1: {}, 2: {"x": 1}}, 4)
    assert len(sectors) == 2 + 4 + 8 and sectors[0]["n"] == 2 and sectors[-1]["n"] == 15
    assert sectors[0]["x"] < 0 < sectors[1]["x"]  # father's side on the left, mother's on the right
    assert sectors[0]["person"] and not sectors[1]["person"]
    assert all(-90 <= s["rotate"] <= 90 for s in sectors)  # no upside-down names
    assert short("Christopher Columbus", 8) == "Christo…" and short("Ann", 8) == "Ann"


def test_ahnentafel(app, admin):
    ids = family_tree(app)
    html = text(admin.get(f"/ahnentafel/{ids['Kid']}"))
    assert '<li value="2">' in html and '<li value="4">' in html and "Grandparents" in html
    assert "born 1870, Old Town; baptized 1870; cremated Sea" in html and "married 1900, Church" in html
    assert [generation_name(g) for g in (1, 3, 4, 5, 13)] == [
        "Parents", "Great-grandparents", "2nd great-grandparents", "3rd great-grandparents",
        "11th great-grandparents"]


def test_descendant_table_and_family_chart(app, admin):
    ids = family_tree(app)
    html = text(admin.get(f"/descendants/{ids['Granddad']}/table"))
    assert html.count("<tr>") == 4 and f'href="/person/{ids["Mom"]}"' in html  # Granddad, Dad, Kid + header
    loop = make(app, ("Loopy", "M", 0), ("Loopwife", "F", 0), family=("Loopy", "Loopwife"), kids=("Loopy",))
    assert text(admin.get(f"/descendants/{loop['Loopy']}/table")).count(f"/person/{loop['Loopy']}\"") == 1
    chart = text(admin.get(f"/family/{ids['family']}/chart"))
    assert f"/person/{ids['Granddad']}" in chart and f"/person/{ids['Kid']}" in chart and "married<br>1900" in chart
    assert "Family chart" in text(admin.get(f"/family/{ids['family']}"))
    lone = make(app, ("Lonely", "M", 0), family=("Lonely", None))
    assert "No children recorded" in text(admin.get(f"/family/{lone['family']}/chart"))
    assert admin.get("/family/NOPE/chart").status_code == 404
    guest = text(client_for(app, "fx_guest").get(f"/family/{ids['family']}/chart"))
    assert "married<br>1900" in guest  # not living: guests see it too


def test_tree_menu(app):
    guest = client_for(app, "fx_guest")
    page = text(guest.get("/tree"))
    assert 'href="/tree"' in page and "Whose tree?" in page and 'data-find="/find"' in page
    assert guest.get("/tree?pid=xhusb").location.endswith("/pedigree/XHUSB")
    sql(app, "UPDATE users SET personID = 'XKID' WHERE username = 'fx_nonotes'")
    try:
        assert "Start from someone on the site" in text(guest.get("/tree")) and "/person/XKID" in text(guest.get("/tree"))
        assert client_for(app, "fx_nonotes").get("/tree").location.endswith("/pedigree/XKID")  # linked: own tree
    finally:
        sql(app, "UPDATE users SET personID = '' WHERE username = 'fx_nonotes'")


def test_photos_in_charts(app, admin):
    # XHUSB is linked to the "plain" photo fixture; the others get initials.
    html = text(admin.get("/pedigree/XKID?gens=3"))
    assert re.search(r'<img class="chart-face" src="/media/\d+/thumb"', html) and 'class="chart-face"><span class="avatar' in html
    assert "with-face" in html and ">on</strong>" in html
    vert = text(admin.get("/pedigree/XKID/vertical?gens=3"))
    assert 'img class="chart-face"' in vert
    html = text(admin.get("/pedigree/XKID?gens=3&photos=0"))  # off, and stays off
    assert "chart-face" not in html and ">off</strong>" in html
    assert "chart-face" not in text(admin.get("/pedigree/XKID/vertical"))
    assert "chart-face" in text(admin.get("/pedigree/XKID?photos=1"))
    # People whose details are hidden get no photo.
    sql(app, "UPDATE people SET living = 1 WHERE personID = 'XHUSB'")
    try:
        assert 'img class="chart-face"' not in text(client_for(app, "fx_guest").get("/pedigree/XKID?gens=3"))
    finally:
        sql(app, "UPDATE people SET living = 0 WHERE personID = 'XHUSB'")


def test_hourglass(app, admin):
    ids = family_tree(app)
    html = text(admin.get(f"/hourglass/{ids['Dad']}?gens=2"))
    chart = html[html.index('class="vped"'):]
    where = [chart.index(f"/hourglass/{ids[k]}?gens=2") for k in ("Granddad", "Dad", "Kid")]
    assert where == sorted(where)  # ancestors above, the person, descendants below
    assert 'aria-current="page">Hourglass' in html and "hg-root" in chart and "chartlines.js" in html
    assert f'm. <a href="/hourglass/{ids["Mom"]}?gens=2">' in chart  # the spouse beside the person
    assert f"/hourglass/{ids['Dad']}" in text(admin.get(f"/person/{ids['Dad']}"))
    assert 'data-key="a1" data-from="a2 a3"' in chart and f'data-key="d{ids["Kid"]}" data-from="a1"' in chart
    loop = make(app, ("Loopy", "M", 0), ("Loopwife", "F", 0), family=("Loopy", "Loopwife"), kids=("Loopy",))
    below = text(admin.get(f"/hourglass/{loop['Loopy']}")).split("hg-root")[1]
    assert f"/hourglass/{loop['Loopy']}?" in below.split("</div>")[0]  # the person's own box...
    assert below.count(f"/hourglass/{loop['Loopy']}?") == 1  # ...and not again as their own child
    one_gen = text(admin.get(f"/hourglass/{ids['Granddad']}?gens=1"))
    assert f"/hourglass/{ids['Dad']}?" in one_gen and f"/hourglass/{ids['Kid']}?" not in one_gen
    assert admin.get("/hourglass/NOPE").status_code == 404

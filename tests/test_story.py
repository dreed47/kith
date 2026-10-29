"""Life story: a person's record told in sentences."""
from conftest import client_for, make, sql

from app.db import insert
from app.reports import in_words


def text(r):
    return " ".join(r.get_data(as_text=True).split())  # the template's line breaks collapse, as in a browser


def test_in_words():
    assert [in_words(n) for n in (0, 3, 12, 13)] == ["no", "three", "twelve", "13"]
    assert [in_words(n, True) for n in (1, 12, 13, 21, 22, 23, 111, 112, 101)] == \
        ["first", "twelfth", "13th", "21st", "22nd", "23rd", "111th", "112th", "101st"]


def event(app, pid, tag, **fields):
    with app.app_context():
        t = sql(app, "SELECT eventtypeID FROM eventtypes WHERE tag = %s AND type = 'I' LIMIT 1", (tag,))
        insert("events", {"gedcom": "tree1", "persfamID": pid, "eventtypeID": t[0]["eventtypeID"], **fields})


def test_story(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Mom", "F", 0), ("Sib", "F", 0), ("Kid", "M", 0), ("Sib2", "M", 0),
               ("Wife", "F", 0), ("Son", "M", 0), ("Dau", "F", 0), ("Wife2", "F", 0), ("Lone", "M", 0),
               family=("Dad", "Mom"), kids=("Sib", "Kid", "Sib2"))
    kid = ids["Kid"]
    sql(app, "UPDATE people SET birthdate = '3 MAR 1872', birthdatetr = '1872-03-03', birthplace = 'Cork',"
             " altbirthtype = 'CHR', altbirthdate = '1872', deathdate = '1931', deathdatetr = '1931-00-00',"
             " deathplace = 'Boston', burialplace = 'Calvary' WHERE personID = %s", (kid,))
    with app.app_context():
        for fid, wife, marr, div, children in ((kid + "F1", ids["Wife"], "1896", "1905", ("Son", "Dau")),
                                               (kid + "F2", ids["Wife2"], "", "", ()),
                                               (kid + "F3", "", "", "", ("Lone",))):
            insert("families", {"gedcom": "tree1", "familyID": fid, "husband": kid, "wife": wife,
                                    "marrdate": marr, "marrdatetr": f"{marr or '0000'}-00-00", "marrplace": marr and "Cork",
                                    "divdate": div, "divdatetr": f"{div or '0000'}-00-00"})
            for n, c in enumerate(children, 1):
                insert("children", {"gedcom": "tree1", "familyID": fid, "personID": ids[c], "ordernum": n})
        _, xid = insert("xnotes", {"gedcom": "tree1", "note": "In his own words"})
        insert("notelinks", {"gedcom": "tree1", "persfamID": kid, "xnoteID": xid, "ordernum": -1})
    event(app, kid, "OCCU", eventdate="1900", eventdatetr="1900-00-00", info="Coal Miner")
    event(app, kid, "OCCU", eventdate="1901", eventdatetr="1901-00-00")
    event(app, kid, "OCCU", eventdate="1902", eventdatetr="1902-00-00", info="the mayor")
    event(app, kid, "IMMI", eventdate="1890", eventdatetr="1890-00-00", eventplace="New York", info="On the ship\nAlba")
    event(app, kid, "RELI", info="Presbyterian")
    event(app, kid, "SSN", info="123-45-6789")
    event(app, kid, "OCCU", eventdate="1903", eventdatetr="1903-00-00", info="U.S. Postal Worker")
    for year, place in (("1900", "U.S. Federal Population Census"), ("1910", "Cork"), ("1910", "")):
        event(app, kid, "CENS", eventdate=year, eventdatetr=f"{year}-00-00", eventplace=place, info="Head, 28 yrs")
    event(app, kid, "CENS", eventplace="Kinsale")
    event(app, kid, "_MILI", eventdate="1918", eventdatetr="1918-00-00", eventplace="U.S. World War I Draft Card")
    with app.app_context():
        _, aid = insert("addresses", {"gedcom": "tree1", "address1": "1 Main St"})
    event(app, kid, "ADDR", addressID=aid)  # an address alone makes no sentence
    event(app, kid, "OCCU")  # empty: editors see it on the person page, the story leaves it out
    html = text(admin.get(f"/person/{kid}/story"))
    assert "Cork, one of three children of" in html  # the siblings have no birth dates, so the place is unknown
    for sib, born in (("Sib", "1870-01-01"), ("Sib2", "1875-01-01")):
        sql(app, "UPDATE people SET birthdatetr = %s WHERE personID = %s", (born, ids[sib]))
    html = text(admin.get(f"/person/{kid}/story"))
    link = lambda who: f'<a href="/person/{ids[who]}">{who} {ids[who][1:-len(who)]}</a>'  # noqa: E731
    assert (f"Kid {ids['Kid'][1:-3]} was born on 3 Mar 1872 in Cork, the second of three children of {link('Dad')}"
            f" and {link('Mom')}. He was christened in 1872.") in html
    assert "He immigrated in 1890 in New York, at about age 18. On the ship Alba." in html
    assert ("He married " + link("Wife") + " in 1896 in Cork, at about age 24. They had two children: "
            + link("Son") + " and " + link("Dau") + ".") in html
    assert "He worked as a coal miner in 1900, at about age 28." in html and "He worked in 1901, at about age 29." in html
    assert "He worked as the mayor in 1902" in html and "He and " + link("Wife") + " divorced in 1905." in html
    assert "He worked in 1903, at about age 31. U.S. Postal Worker." in html  # has dots: told as written
    assert "He was counted in the census in 1900 and 1910." in html and "Head, 28 yrs" not in html
    assert "He was counted in the census in Kinsale." in html  # no year, so not merged
    assert "Military in 1918." in html and "Draft Card" not in html and "He lived" not in html
    assert "Religion: Presbyterian." in html and "123-45-6789" not in html
    assert "He had a family with " + link("Wife2") + ". He had one child: " + link("Lone") + "." in html
    assert "He died in 1931 in Boston, at about age 59. He was buried in Calvary." in html
    assert "<h2>Biography</h2>" in html and "In his own words" in html and "edit the facts" in html
    assert f"/person/{kid}/story" in text(admin.get(f"/person/{kid}"))


def test_story_short_records(app, admin):
    ids = make(app, ("Pa", "M", 0), ("Only", "U", 0), ("Bare", "F", 0), family=("Pa", None), kids=("Only",))
    sql(app, "UPDATE people SET altbirthtype = 'BAPM', altbirthdate = '1800', burialtype = 1,"
             " burialplace = 'Sea' WHERE personID = %s", (ids["Only"],))
    event(app, ids["Only"], "_MILI", info="Veteran.")
    event(app, ids["Only"], "EDUC", eventdate="1810-1811", eventdatetr="1810-00-00")
    html = text(admin.get(f"/person/{ids['Only']}/story"))
    assert f'Only {ids["Only"][1:-4]} was the child of <a href="/person/{ids["Pa"]}">' in html
    assert "Only was baptized in 1800." in html and "Only was cremated in Sea." in html  # no sex: no pronoun
    assert "Military: Veteran." in html and "Only went to school in 1810-1811." in html
    assert "There isn't enough in the record yet" in text(admin.get(f"/person/{ids['Bare']}/story"))
    assert "There isn't enough" not in html and "edit the facts" not in text(client_for(app, "fx_nonotes").get(
        f"/person/{ids['Only']}/story"))
    assert admin.get("/person/NOPE/story").status_code == 404


def test_story_privacy(app):
    ids = make(app, ("Oldpa", "M", 0), ("Livekid", "F", 1), family=("Oldpa", None), kids=("Livekid",))
    guest = client_for(app, "fx_guest")
    r = guest.get(f"/person/{ids['Livekid']}/story")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/person/{ids['Livekid']}")
    assert "/story" not in text(guest.get(f"/person/{ids['Livekid']}"))
    html = text(guest.get(f"/person/{ids['Oldpa']}/story"))
    assert "He had one child: " in html and ">Living</a>" in html and "Livekid" not in html

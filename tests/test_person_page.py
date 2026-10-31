import html as htmllib

import pytest

from app.db import insert
from app.views import age_at
from conftest import client_for, make, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


@pytest.mark.parametrize("birth,when,age", [
    ("1898-03-31", "1918-09-12", "20"),
    ("1898-03-31", "1918-03-30", "19"),      # day before the birthday
    ("1898-03-31", "1918-03-31", "20"),      # on the birthday
    ("1898-00-00", "1918-09-12", "about 20"),  # birth month unknown
    ("1898-03-31", "1918-00-00", "about 20"),
    ("0000-00-00", "1918-09-12", None),
    ("1898-03-31", "0000-00-00", None),
    ("1898-03-31", "1890-01-01", None),      # event before birth: no age
    (None, "1918-09-12", None),
])
def test_age_at(birth, when, age):
    assert age_at(birth, when) == age


def occupation(app):
    return sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'I' AND tag = 'OCCU'")[0]["eventtypeID"]


def test_header_summary_and_photo(app, admin, media_ids):
    sql(app, "UPDATE people SET birthdate = '3 MAR 1870', birthdatetr = '1870-03-03', deathdate = '1 JAN 1940',"
             " deathdatetr = '1940-01-01', deathplace = 'Endville, Testshire', burialplace = 'Restplace, Testshire',"
             " nickname = 'Hank' WHERE personID = 'XHUSB'")
    html = text(admin.get("/person/XHUSB"))
    head = html.split('class="card person-head"')[1].split("</section>")[0]
    assert f"/media/{media_ids['plain']}/thumb?s=600" in head and "Photo of Hal Fixture" in head
    assert "1870 – 1940" in head and "(age 69)" in head and "Known as Hank" in head
    assert "Born" in head and "3 Mar 1870" in head and "Died" in head and "Buried" in head and "Restplace" in head
    assert "Add…" in head and "Family with existing spouse" in head and ">Parents already in the tree</a>" not in head  # has parents


def test_no_photo_and_cremated(app, admin):
    ids = make(app, ("A", "M", 0))
    sql(app, "UPDATE people SET burialplace = 'Ashes, Testshire', burialtype = 1 WHERE personID = %s", (ids["A"],))
    html = text(admin.get(f"/person/{ids['A']}"))
    assert "person-photo" not in html and "Cremated" in html and ">Parents already in the tree</a>" in html  # can add parents


def test_family_at_a_glance(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Me", "F", 0), ("Sis", "F", 0), ("Kid", "M", 0), family=("Dad", None), kids=("Me", "Sis"))
    mine = make(app, family=None)
    with app.app_context():
        insert("families", {"gedcom": "tree1", "familyID": ids["family"] + "M", "wife": ids["Me"], "wifeorder": 1,
                                "marrdate": "5 MAY 1950"})
        insert("children", {"gedcom": "tree1", "familyID": ids["family"] + "M", "personID": ids["Kid"], "ordernum": 1})
    html = text(admin.get(f"/person/{ids['Me']}"))
    glance = html.split('class="card family-glance"')[1].split("</section>")[0]
    for part in ("<h2>Parents</h2>", f"/person/{ids['Dad']}", "Siblings", f"/person/{ids['Sis']}",
                 "<h2>Family</h2>", "Married 5 May 1950", "Children", f"/person/{ids['Kid']}"):
        assert part in glance, part
    assert mine == {}


def test_family_at_a_glance_empty_sides(app, admin):
    """One side empty: its heading still shows, saying nothing's recorded, instead of a blank half."""
    ids = make(app, ("Dad", "M", 0), ("Kid", "F", 0), family=("Dad", None), kids=("Kid",))

    def glance(client, pid):
        return text(client.get(f"/person/{pid}")).split('class="card family-glance"')[1].split("</section>")[0]
    dad, kid = glance(admin, ids["Dad"]), glance(admin, ids["Kid"])
    assert "<h2>Parents</h2>" in dad and "No parents recorded." in dad and "No spouse" not in dad
    assert f"parent_of={ids['Dad']}&role=husband" in dad and f"/edit/family/new?child={ids['Dad']}" in dad
    assert "<h2>Spouse</h2>" in kid and "No spouse or children recorded." in kid and "No parents" not in kid
    assert f"spouse_of={ids['Kid']}" in kid and f"/edit/family/new?wife={ids['Kid']}" in kid
    viewer = glance(client_for(app, "fx_exporter"), ids["Dad"])
    assert "No parents recorded." in viewer and "Add father" not in viewer  # can't add: no links


def test_timeline_order_and_ages(app, admin):
    ids = make(app, ("A", "M", 0))
    sql(app, "UPDATE people SET birthdate = '1 JAN 1900', birthdatetr = '1900-01-01', deathdate = '1970',"
             " deathdatetr = '1970-00-00', burialplace = 'Grave, Testshire', altbirthdate = '5 JAN 1900',"
             " altbirthdatetr = '1900-01-05', altbirthtype = 'BAPM' WHERE personID = %s", (ids["A"],))
    occu = occupation(app)
    with app.app_context():
        for date, tr, info in (("1950", "1950-00-00", "Later job"), ("1920", "1920-00-00", "First job"),
                               ("", "0000-00-00", "Undated job")):
            insert("events", {"gedcom": "tree1", "persfamID": ids["A"], "eventtypeID": occu, "eventdate": date,
                                  "eventdatetr": tr, "info": info})
    html = text(admin.get(f"/person/{ids['A']}"))
    tl = html.split('<ol class="timeline">')[1].split("</ol>")[0]
    order = [w for w in ("<strong>Birth", "<strong>Baptism", "First job", "Later job", "Undated job",
                         "<strong>Death", "<strong>Burial") if w in tl]
    assert order == sorted(order, key=tl.index) and len(order) == 7
    assert "age about 20" in tl and "age about 70" in tl and "age 0" in tl  # baptism 4 days after birth
    assert tl.count("/edit/event/") == 3  # only real events have an edit link


def test_private_person_shows_no_summary(app, guest):
    p = sql(app, "SELECT personID FROM people WHERE living = 1 AND private = 0 LIMIT 1")[0]
    html = text(guest.get(f"/person/{p['personID']}"))
    assert "Details for this person are private" in html and 'class="timeline"' not in html and "summary-facts" not in html


def test_header_prefers_photos_collection(app, admin, media_ids):
    ids = make(app, ("A", "M", 0))
    with app.app_context():
        _, stone = insert("media", {"gedcom": "tree1", "mediatypeID": "headstones", "path": "plain.jpg",
                                        "usecollfolder": 0, "mediakey": "Xstone"})
        insert("medialinks", {"gedcom": "tree1", "personID": ids["A"], "mediaID": stone, "linktype": "I",
                                  "defphoto": "1", "ordernum": 1})
    assert f"/media/{stone}/thumb?s=600" in text(admin.get(f"/person/{ids['A']}"))  # only image: use it
    with app.app_context():
        insert("medialinks", {"gedcom": "tree1", "personID": ids["A"], "mediaID": media_ids["nothumb"],
                                  "linktype": "I", "ordernum": 2})
    head = text(admin.get(f"/person/{ids['A']}")).split("person-head")[1].split("</section>")[0]
    assert f"/media/{media_ids['nothumb']}/thumb?s=600" in head  # a portrait beats the default-flagged headstone


def test_life_events_have_edit_links(app):
    ids = make(app, ("Dated", "M", 0))
    sql(app, "UPDATE people SET birthdate = '1 JAN 1900', birthdatetr = '1900-01-01', burialplace = 'Somewhere'"
             " WHERE personID = %s", (ids["Dated"],))
    page = client_for(app, "fx_editor").get(f"/person/{ids['Dated']}").get_data(as_text=True)
    assert f'href="/edit/person/{ids["Dated"]}#event-birth" aria-label="Edit Birth">Edit</a>' in page
    assert f'href="/edit/person/{ids["Dated"]}#event-burial" aria-label="Edit Burial">Edit</a>' in page
    assert 'id="event-birth"' in client_for(app, "fx_editor").get(f"/edit/person/{ids['Dated']}").get_data(as_text=True)
    assert 'aria-label="Edit Birth"' not in client_for(app, "fx_adder").get(f"/person/{ids['Dated']}").get_data(as_text=True)


def test_marriages_in_life_events(app):
    ids = make(app, ("Wed", "M", 0), ("Bride", "F", 0), family=("Wed", "Bride"))
    alone = make(app, ("Solo", "M", 0), ("Hidden", "F", 1), family=("Solo", None))
    sql(app, "UPDATE people SET birthdate = '1 JAN 1900', birthdatetr = '1900-01-01' WHERE personID = %s", (ids["Wed"],))
    sql(app, "UPDATE families SET marrdate = '12 JUN 1925', marrdatetr = '1925-06-12', marrplace = 'Synthville',"
             " divdate = '1930', divdatetr = '1930-00-00' WHERE familyID = %s", (ids["family"],))
    sql(app, "UPDATE families SET marrplace = 'Nowhere' WHERE familyID = %s", (alone["family"],))
    page = text(client_for(app, "fx_editor").get(f"/person/{ids['Wed']}"))
    life = page[page.index("Life events"):]
    assert f'<strong>Marriage</strong> to <a href="/person/{ids["Bride"]}"' in life and "age 25" in life
    assert f'<strong>Divorce</strong> from <a href="/person/{ids["Bride"]}"' in life
    assert f'href="/edit/family/{ids["family"]}#marriage" aria-label="Edit Marriage">Edit</a>' in life
    assert life.index("Marriage") < life.index("Divorce")
    assert f'<strong>Marriage</strong> to <a href="/person/{ids["Wed"]}"' in text(client_for(app, "fx_guest").get(f"/person/{ids['Bride']}"))
    assert "unknown spouse" in text(client_for(app, "fx_guest").get(f"/person/{alone['Solo']}"))
    # A family the viewer can't see (a living spouse) leaves no row.
    sql(app, "UPDATE families SET wife = %s, living = 1 WHERE familyID = %s", (alone["Hidden"], alone["family"]))
    assert "Nowhere" not in text(client_for(app, "fx_guest").get(f"/person/{alone['Solo']}"))
    assert 'id="marriage"' in text(client_for(app, "fx_editor").get(f"/edit/family/{ids['family']}"))


def add(client, **params):
    return client.post("/edit/person/new", data={"csrf": "t", "firstname": "New", "lastname": "Kin", "sex": "U",
                                                 **params})


def parents_of(app, pid):
    return sql(app, "SELECT f.* FROM children c JOIN families f ON f.familyID = c.familyID"
                    " WHERE c.personID = %s", (pid,))


def test_add_relatives_from_a_persons_page(app):
    ed = client_for(app, "fx_admin")
    ids = make(app, ("Hub", "M", 0))
    hub = ids["Hub"]
    head = text(ed.get(f"/person/{hub}"))
    assert ">Father</a>" in head and ">Mother</a>" in head and ">Brother or sister</a>" not in head
    form = text(ed.get(f"/edit/person/new?parent_of={hub}&role=husband&return_to={hub}"))
    assert "Add father of Hub" in form and f'name="lastname" value="{hub[1:-3]}"' in form  # the child's surname
    assert 'name="lastname" value=""' in text(ed.get(f"/edit/person/new?parent_of={hub}&role=wife"))
    # The father makes a parents' family with Hub as its child; back on Hub's page afterwards.
    r = add(ed, parent_of=hub, role="husband", return_to=hub, sex="M")
    assert r.status_code == 302 and r.location.endswith(f"/person/{hub}")
    fam = parents_of(app, hub)
    assert len(fam) == 1 and fam[0]["husband"] and not fam[0]["wife"] and fam[0]["husborder"] == 1
    dad = fam[0]["husband"]
    assert sql(app, "SELECT living FROM families WHERE familyID = %s", (fam[0]["familyID"],))[0]["living"] == 0
    # The mother fills the same family.
    add(ed, parent_of=hub, role="wife", return_to=hub, sex="F", living="1")
    fam = parents_of(app, hub)
    assert len(fam) == 1 and fam[0]["wife"] and fam[0]["living"] == 1
    head = text(ed.get(f"/person/{hub}"))
    assert ">Father</a>" not in head and ">Mother</a>" not in head and ">Brother or sister</a>" in head
    assert ed.get(f"/edit/person/new?parent_of={hub}&role=husband").status_code == 400  # already has one
    # A sibling, and grandparents through the father.
    assert "'s parents: <a" in head
    add(ed, child_of=fam[0]["familyID"], return_to=hub)
    assert len(sql(app, "SELECT * FROM children WHERE familyID = %s", (fam[0]["familyID"],))) == 2
    r = add(ed, parent_of=dad, role="wife", return_to=hub, sex="F")
    assert r.location.endswith(f"/person/{hub}") and parents_of(app, dad)[0]["wife"]
    assert ed.get(f"/edit/person/new?parent_of={hub}&role=nope").status_code == 400
    assert ed.get("/edit/person/new?parent_of=NOSUCH&role=wife").status_code == 404
    # Without return_to, the new person's own page opens, as before.
    r = add(ed, parent_of=dad, role="husband", sex="M")
    assert r.location.endswith(f"/person/{parents_of(app, dad)[0]['husband']}")


def test_family_census_on_members_timelines(app):
    ids = make(app, ("Pa", "M", 0), ("Ma", "F", 0), ("Son", "M", 0), family=("Pa", "Ma"), kids=("Son",))
    hidden = make(app, ("Lone", "M", 0), ("Alive", "F", 1), family=("Lone", "Alive"))
    sql(app, "UPDATE people SET birthdate = '1872', birthdatetr = '1872-00-00' WHERE personID = %s", (ids["Son"],))
    sql(app, "UPDATE families SET living = 1 WHERE familyID = %s", (hidden["family"],))
    with app.app_context():
        cens = sql(app, "SELECT eventtypeID FROM eventtypes WHERE tag = 'CENS' AND type = 'F'")[0]["eventtypeID"]
        other = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'F' AND tag != 'CENS' AND ldsevent = 0"
                         " LIMIT 1")[0]["eventtypeID"]
        for fid, tid, info in ((ids["family"], cens, "Pa 45 head"), (ids["family"], other, "Not a census"),
                               (hidden["family"], cens, "Secret household")):
            insert("events", {"gedcom": "tree1", "persfamID": fid, "eventtypeID": tid, "eventdate": "1880",
                                  "eventdatetr": "1880-00-00", "info": info})
    guest = client_for(app, "fx_guest")
    for who in ("Son", "Pa"):
        life = text(guest.get(f"/person/{ids[who]}")).split('<ol class="timeline">')[1].split("</ol>")[0]
        assert "Pa 45 head" in life and f'with <a href="/family/{ids["family"]}">family</a>' in life
        assert "Not a census" not in life
    assert "age about 8" in text(guest.get(f"/person/{ids['Son']}"))
    assert "Secret household" not in text(guest.get(f"/person/{hidden['Lone']}"))  # a family the viewer can't see


def test_map_of_life_events(app):
    import json
    from app.db import execute
    ids = make(app, ("Mapped", "F", 0), ("Unmapped", "M", 0), ("Alive", "F", 1))
    sql(app, "UPDATE people SET birthplace = 'Xmaptown, PA', birthdate = '2 JUL 1872', deathplace = 'Xmaptown, PA',"
             " burialplace = 'Xnear, PA', altbirthplace = 'Xjunk, PA' WHERE personID IN (%s, %s)",
             (ids["Mapped"], ids["Alive"]))
    sql(app, "UPDATE people SET birthplace = 'Xnowhere, PA' WHERE personID = %s", (ids["Unmapped"],))
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Xmaptown, PA", "latitude": "41.1", "longitude": "-79.5"})
        insert("places", {"gedcom": "tree1", "place": "Xnear, PA", "latitude": "41.2", "longitude": "-79.4"})
        insert("places", {"gedcom": "tree1", "place": "Xjunk, PA", "latitude": "north", "longitude": "-79"})
        insert("places", {"gedcom": "tree1", "place": "Xnowhere, PA", "latitude": "95", "longitude": "-79"})
        execute("REPLACE INTO app_geocode VALUES ('tree1', 'Xnear, PA', 'approximate', 'Sligo, PA', NOW())")
    guest = client_for(app, "fx_guest")
    html = text(guest.get(f"/person/{ids['Mapped']}"))
    pins = json.loads(html.split("data-pins='")[1].split("'")[0])
    assert pins == [{"lat": 41.1, "lon": -79.5, "approx": None,
                     "events": [{"label": "Birth", "date": "2 Jul 1872", "place": "Xmaptown, PA"},
                                {"label": "Death", "date": "", "place": "Xmaptown, PA"}]},
                    {"lat": 41.2, "lon": -79.4, "approx": "Sligo, PA",
                     "events": [{"label": "Burial", "date": "", "place": "Xnear, PA"}]}]
    assert "personmap.js" in html and "leaflet/leaflet.js" in html
    assert "person-map" not in text(guest.get(f"/person/{ids['Unmapped']}"))  # no usable coordinates
    assert "person-map" not in text(guest.get(f"/person/{ids['Alive']}"))  # living: the guest sees no events


def test_relatives_show_their_photo(admin, media_ids):
    html = admin.get("/person/XKID").get_data(as_text=True)  # father XHUSB has a photo, mother XWIFE doesn't
    row = html[html.index('<li class="person-row"><img'):]
    assert row.index(f'/media/{media_ids["plain"]}/thumb') < row.index("/person/XHUSB") < row.index("/person/XWIFE")
    assert '<li class="person-row"><span class="avatar' in html  # initials for her
    family = admin.get("/family/XFAM").get_data(as_text=True)  # and everywhere else a circle shows
    assert f'<li class="person-row"><img class="avatar " src="/media/{media_ids["plain"]}/thumb"' in family


def test_married_names(app, admin):
    ids = make(app, ("Mary", "F", 0), ("Ned", "M", 0), ("Otto", "M", 0), ("Same", "M", 0), ("Alive", "M", 1))
    sql(app, "UPDATE people SET lastname = 'LEWIS' WHERE personID = %s", (ids["Mary"],))
    for who, last in (("Ned", "REED"), ("Otto", "SMITH"), ("Same", "LEWIS"), ("Alive", "JONES")):
        sql(app, "UPDATE people SET lastname = %s WHERE personID = %s", (last, ids[who]))
    with app.app_context():
        for n, who in enumerate(("Ned", "Otto", "Same", "Alive"), 1):
            insert("families", {"gedcom": "tree1", "familyID": f"{ids['Mary']}F{n}", "husband": ids[who],
                                "wife": ids["Mary"], "wifeorder": n, "living": int(who == "Alive")})
    html = text(admin.get(f"/person/{ids['Mary']}"))
    assert "Later Mary REED, then Mary SMITH, then Mary JONES" in html  # her own surname again isn't news
    guest = text(client_for(app, "fx_guest").get(f"/person/{ids['Mary']}"))
    assert "Later Mary REED, then Mary SMITH<" in guest  # a living husband's name stays hidden
    assert "Later" not in text(admin.get(f"/person/{ids['Ned']}"))

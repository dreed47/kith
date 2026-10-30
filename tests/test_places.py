from urllib.parse import quote

from conftest import sql


def test_drill_down(admin, guest):
    top = admin.get("/places").get_data(as_text=True)
    assert "within=Testshire" in top
    html = admin.get("/places?within=Testshire").get_data(as_text=True)
    assert "within=Synthville,+Testshire" in html or "within=Synthville%2C+Testshire" in html
    assert "Up one level" in html
    leaf = admin.get("/places?within=Synthville, Testshire").get_data(as_text=True)
    assert "Events in Synthville, Testshire" in leaf and "within=Testshire" in leaf and "Edit place" in leaf
    assert "Edit place" not in guest.get("/places?within=Synthville, Testshire").get_data(as_text=True)
    assert admin.get("/places?within=Nowhere Land").status_code == 404


def test_place_events_people_and_families(admin):
    html = admin.get("/place?name=Synthville, Testshire").get_data(as_text=True)
    assert "/person/XHUSB" in html and "/family/XFAM" in html
    for label in ("Birth", "Marriage", "Census"):
        assert label in html
    assert 'href="/places?within=Testshire"' in html


def test_place_with_no_events_and_unknown_place(admin):
    assert "No events recorded here" in admin.get("/place?name=Emptyplace, Testshire").get_data(as_text=True)
    assert admin.get("/place?name=Never heard of it").status_code == 404
    for blank in ("/place", "/place?name=", "/place?name=%20", "/place?place=Synthville"):
        assert admin.get(blank).status_code == 404, blank


def test_place_map_and_notes(app, admin):
    p = sql(app, "SELECT place FROM places WHERE latitude <> '' AND longitude <> '' LIMIT 1")[0]["place"]
    assert "openstreetmap.org" in admin.get(f"/place?name={quote(p)}").get_data(as_text=True)


def test_place_hides_living_from_guest(app, guest, admin):
    p = sql(app, "SELECT * FROM people WHERE living = 1 AND private = 0 AND birthplace <> '' LIMIT 1")[0]
    html = guest.get("/place", query_string={"name": p["birthplace"]}).get_data(as_text=True)
    assert f"/person/{p['personID']}\"" not in html
    assert f"/person/{p['personID']}\"" in admin.get("/place", query_string={"name": p["birthplace"]}).get_data(as_text=True)


def test_cemeteries(app, admin):
    assert "Placeless Fixture Cemetery" in admin.get("/cemeteries").get_data(as_text=True)
    c = sql(app, "SELECT c.cemeteryID FROM cemeteries c WHERE c.place <> '' AND c.latitude <> ''"
                 " AND EXISTS (SELECT 1 FROM media m WHERE m.cemeteryID = c.cemeteryID) LIMIT 1")[0]
    html = admin.get(f"/cemeteries/{c['cemeteryID']}").get_data(as_text=True)
    assert "Headstones and media" in html and "openstreetmap.org" in html and "All events at this place" in html
    bare = sql(app, "SELECT cemeteryID FROM cemeteries WHERE cemname = 'Placeless Fixture Cemetery'")[0]
    html = admin.get(f"/cemeteries/{bare['cemeteryID']}").get_data(as_text=True)
    assert "Placeless Fixture Cemetery" in html and "All events at this place" not in html
    assert admin.get("/cemeteries/999999").status_code == 404


def test_cemetery_maps_and_people(app, admin):
    import html
    import json
    import re

    from conftest import client_for, make

    from app.db import insert
    from app.places import buried_in
    ids = make(app, ("Stoned", "M", 0), ("Livestone", "F", 1), ("Byplace", "M", 0))
    sql(app, "UPDATE people SET burialplace = 'Mapville, Testshire' WHERE personID = %s", (ids["Byplace"],))
    with app.app_context():
        cem = insert("cemeteries", {"cemname": "Mapped Cemetery", "city": "Mapville", "state": "Testshire",
                                        "place": "Mapville, Testshire", "latitude": "41.123456", "longitude": "-79.5"})[1]
        bad = insert("cemeteries", {"cemname": "Typo Cemetery", "latitude": "41,2", "longitude": "-79"})[1]
        far = insert("cemeteries", {"cemname": "Offworld Cemetery", "latitude": "200", "longitude": "-79"})[1]
        stone = insert("media", {"gedcom": "tree1", "mediatypeID": "headstones", "path": "s.jpg", "usecollfolder": 1,
                                     "mediakey": "Xmapstone", "cemeteryID": cem, "description": "Map stone"})[1]
        for who in ("Stoned", "Livestone", "Byplace"):  # Byplace: by stone and by place, listed once
            insert("medialinks", {"gedcom": "tree1", "linktype": "I", "personID": ids[who], "mediaID": stone})
    page = admin.get("/cemeteries").get_data(as_text=True)
    pins = json.loads(html.unescape(re.search(r"data-pins='([^']*)'", page).group(1)))
    mine = [p for p in pins if p["name"] == "Mapped Cemetery"]
    assert mine == [{"lat": 41.12346, "lon": -79.5, "name": "Mapped Cemetery", "people": 3, "stones": 1,
                     "where": "Mapville, Testshire", "url": f"/cemeteries/{cem}"}]
    assert not any(p["name"] in ("Typo Cemetery", "Offworld Cemetery") for p in pins)  # no usable coordinates
    one = admin.get(f"/cemeteries/{cem}").get_data(as_text=True)
    assert 'id="cemetery-map"' in one and one.count(f"/person/{ids['Byplace']}") == 1
    assert all(f"/person/{ids[w]}" in one for w in ("Stoned", "Livestone"))
    guest = client_for(app, "fx_exporter").get(f"/cemeteries/{cem}").get_data(as_text=True)  # sees no living
    assert f"/person/{ids['Livestone']}" not in guest and f"/person/{ids['Stoned']}" in guest
    assert 'id="cemetery-map"' not in admin.get(f"/cemeteries/{bad}").get_data(as_text=True)
    assert 'id="cemetery-map"' not in admin.get(f"/cemeteries/{far}").get_data(as_text=True)
    with app.test_request_context():
        assert buried_in([]) == {}

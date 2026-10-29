from app.dates import display
from app.privacy import display_name, families_filter, media_filter, people_filter, rights

from conftest import sql

GUEST = {"allow_living": 0, "allow_private": 0, "allow_private_notes": 0, "personID": ""}
FAMILY = {"allow_living": 1, "allow_private": 1, "allow_private_notes": 1, "personID": ""}
ALIVE = {"personID": "I1", "firstname": "Ann", "lastname": "Marsh", "living": 1, "private": 0}


def test_living_rules():
    assert display_name(ALIVE, GUEST) == "Living" and not rights(ALIVE, GUEST)["both"]
    assert display_name(ALIVE, FAMILY) == "Ann Marsh" and rights(ALIVE, FAMILY)["both"]
    assert display_name(ALIVE, {**GUEST, "personID": "I1"}) == "Ann Marsh"  # you can always see yourself


def test_private_keeps_name_hides_details():
    private = {**ALIVE, "living": 0, "private": 1}
    assert display_name(private, GUEST) == "Ann Marsh" and not rights(private, GUEST)["both"]
    assert rights(private, FAMILY)["both"]


def test_private_notes_right():
    assert not rights(ALIVE, GUEST)["private_notes"] and rights(ALIVE, FAMILY)["private_notes"]
    assert rights(ALIVE, {**GUEST, "personID": "I1"})["private_notes"]


def test_name_parts():
    full = {"firstname": "John", "lnprefix": "van", "lastname": "Dyke", "suffix": "Jr.", "title": "Dr.",
            "living": 0, "private": 0}
    assert display_name(full, GUEST) == "Dr. John van Dyke, Jr."
    assert display_name({**full, "title": "", "prefix": "Rev."}, GUEST) == "Rev. John van Dyke, Jr."
    assert display_name(None, GUEST) == ""


def test_family_without_husband_is_not_mine():
    fam = {"husband": "", "wife": "I9", "living": 1, "private": 0}
    assert not rights(fam, GUEST)["both"]
    assert rights(fam, {**GUEST, "personID": "I9"})["both"]


def test_filters_for_full_rights_user_are_open():
    assert people_filter("p", FAMILY) == ("1=1", [])
    assert families_filter("f", FAMILY) == ("1=1", [])
    assert media_filter("m", FAMILY) == ("1=1", [])


def test_filters_exempt_self():
    me = {**GUEST, "personID": "I5"}
    cond, params = people_filter("p", me)
    assert "personID != %s" in cond and params == ["I5", "I5"]
    cond, params = families_filter("f", me)
    assert "NOT IN (f.husband, f.wife)" in cond and params == ["I5", "I5"]


def test_living_only_user_still_blocks_private():
    living_only = {**FAMILY, "allow_private": 0}
    assert "private" in people_filter("p", living_only)[0] and "living" not in people_filter("p", living_only)[0]
    assert "m.private != 1" in media_filter("m", living_only)[0]


# --- pages, on the test DB ---

def test_living_person_hidden_from_guest(app, guest, admin):
    p = sql(app, "SELECT * FROM people WHERE living = 1 AND private = 0 AND birthdate <> '' LIMIT 1")[0]
    html = guest.get(f"/person/{p['personID']}").get_data(as_text=True)
    assert "Living" in html and p["firstname"] not in html and display(p["birthdate"]) not in html
    assert "Details for this person are private" in html
    found = guest.get("/search", query_string={"q": f"{p['firstname']} {p['lastname']}"}).get_data(as_text=True)
    assert f"/person/{p['personID']}\"" not in found
    html = admin.get(f"/person/{p['personID']}").get_data(as_text=True)
    assert p["firstname"] in html and display(p["birthdate"]) in html


def test_living_family_hidden_from_guest(app, guest, admin):
    f = sql(app, "SELECT * FROM families WHERE living = 1 AND marrdate <> '' LIMIT 1")[0]
    html = guest.get(f"/family/{f['familyID']}").get_data(as_text=True)
    assert "Details for this family are private" in html and display(f["marrdate"]) not in html
    assert display(f["marrdate"]) in admin.get(f"/family/{f['familyID']}").get_data(as_text=True)


def test_media_of_living_person_hidden_from_guest(app, guest, admin):
    m = sql(app, "SELECT m.mediaID FROM media m JOIN medialinks ml ON ml.mediaID = m.mediaID"
                 " JOIN people p ON p.personID = ml.personID WHERE p.living = 1 AND m.alwayson = 0"
                 " AND m.abspath = 0 AND m.path <> '' LIMIT 1")[0]
    for url in (f"/media/{m['mediaID']}", f"/media/{m['mediaID']}/file", f"/media/{m['mediaID']}/thumb"):
        assert guest.get(url).status_code == 404, url
    assert admin.get(f"/media/{m['mediaID']}").status_code == 200


def test_secret_notes_need_right(app, admin):
    from conftest import client_for
    no_notes = client_for(app, "fx_nonotes").get("/person/XHUSB").get_data(as_text=True)
    assert "Open fixture note" in no_notes and "Secret fixture note" not in no_notes
    assert "Secret fixture note" in admin.get("/person/XHUSB").get_data(as_text=True)

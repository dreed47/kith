import html as htmllib
import time

import pytest

from app import search as search_mod
from app.db import insert
from app.search import distance, variants
from conftest import CSRF, client_for, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


@pytest.mark.parametrize("a,b,limit,d", [
    ("LINDQVIST", "LINDQUIST", 1, 1), ("LINDQVIST", "LUNDKVIST", 1, 2), ("ABC", "ABC", 0, 0),
    ("ABC", "ABCDEF", 1, 2), ("KITTEN", "SITTING", 3, 3), ("SHORT", "LONGER", 1, 2),
])
def test_distance(a, b, limit, d):
    assert distance(a, b, limit) == d


@pytest.fixture(scope="module")
def names(app):
    """Surnames for variant tests, plus people to find."""
    with app.app_context():
        for pid, first, last, born, place, living in (
                ("XS1", "Zara", "ZEBULONN", "1890-05-01", "Searchtown, Testshire", 0),
                ("XS2", "Zeke", "ZEBULON", "1905-00-00", "", 0),
                ("XS3", "Zoe", " ZEBULONN", "0000-00-00", "", 1),
                ("XS4", "Zed", "ZEBULOFF", "1950-01-01", "", 0),
                ("XS5", "Ziv", "ZUBE", "1920-01-01", "", 0),
                ("XS6", "Zak", "ZUBA", "1921-01-01", "", 0)):
            insert("people", {"gedcom": "tree1", "personID": pid, "firstname": first, "lastname": last,
                                  "birthdatetr": born, "birthdate": born[:4] if born[:4] != "0000" else "",
                                  "birthplace": place, "living": living, "famc": "XFAM" if pid == "XS1" else ""})
        search_mod.invalidate()
    return True


def test_variants(app, names):
    with app.app_context():
        assert variants("Zebulonn") == [" ZEBULONN", "ZEBULON"]  # one letter, and the stray-space copy
        assert variants("ZUBE") == []                               # short: exact only
        assert variants("Zebulon") == [" ZEBULONN", "ZEBULONN"]


def test_surname_cache(app, names, monkeypatch):
    with app.app_context():
        search_mod.invalidate()
        first = search_mod.surnames()
        assert search_mod.surnames() is first
        monkeypatch.setattr(time, "time", lambda: search_mod._cache["at"] + search_mod.NAMES_TTL + 1)
        assert search_mod.surnames() is not first


def results(client, **args):
    return text(client.get("/search", query_string=args))


def test_name_with_variants_and_exact(admin, names):
    html = results(admin, q="Zebulonn")
    assert "/person/XS1" in html and "/person/XS2" in html and "/person/XS3" in html and "/person/XS4" not in html
    assert "including similar spellings:" in html and "ZEBULON" in html and "exact spelling only" in html
    html = results(admin, q="Zebulonn", exact="1")
    assert "/person/XS1" in html and "/person/XS2" not in html and "include similar spellings" in html


def test_first_and_last_name(admin, names):
    html = results(admin, q="Zeke Zebulonn")
    assert "/person/XS2" in html and "/person/XS1" not in html


def test_middle_names(app, admin):
    """Every given word counts, first or middle: "Walter James Hollis" isn't every Walter Hollis."""
    with app.app_context():
        for pid, first in (("XM1", "Walter James"), ("XM2", "Walter"), ("XM3", "James Walter"), ("XM4", "Walton Jamesina")):
            insert("people", {"gedcom": "tree1", "personID": pid, "firstname": first, "lastname": "MIDDLETON"})
        search_mod.invalidate()

    def found(q):
        html = results(admin, q=q, exact="1")
        return [x for x in ("XM1", "XM2", "XM3", "XM4") if f"/person/{x}" in html]
    assert found("Walter James Middleton") == ["XM1", "XM3"]  # both given names, either order
    assert found("James Middleton") == ["XM1", "XM3", "XM4"]  # a middle name works on its own (as a word start)
    assert found("Walter Middleton") == ["XM1", "XM2", "XM3"]
    assert found("Walter James") == ["XM1", "XM3"]  # given names only, no surname typed
    picked = admin.get("/edit/find?q=Walter James Middleton").get_json()
    assert sorted(p["id"] for p in picked) == ["XM1", "XM3"]


def test_first_name_alone(admin, names):
    assert "/person/XS5" in results(admin, q="Ziv")


def test_id_goes_to_person(admin):
    r = admin.get("/search?q=xkid")
    assert r.status_code == 302 and r.location.endswith("/person/XKID")


def test_filters(admin, names):
    html = results(admin, q="Zebulonn", born_from="1900")
    assert "/person/XS2" in html and "/person/XS1" not in html and "/person/XS3" not in html  # unknown birth excluded
    html = results(admin, q="Zebulonn", born_to="1900")
    assert "/person/XS1" in html and "/person/XS2" not in html and "/person/XS3" not in html
    assert "/person/XS1" in results(admin, place="Searchtown")
    html = results(admin, q="Zebulonn", status="living")
    assert "/person/XS3" in html and "/person/XS1" not in html
    html = results(admin, q="Zebulonn", status="deceased")
    assert "/person/XS1" in html and "/person/XS3" not in html
    assert "/person/XS1" in results(admin, q="Zebulonn", born_from="18x0")  # bad years are ignored


def test_sorting(admin, names):
    def order(**args):
        html = results(admin, q="Zebulonn", **args)
        return sorted(("XS1", "XS2", "XS3"), key=lambda p: html.index(f"/person/{p}\""))
    assert order(sort="born") == ["XS1", "XS2", "XS3"]  # unknown birth last
    assert order(sort="name")[0] == "XS3"               # " ZEBULONN" sorts first by name
    html = results(admin, q="Zebulonn", sort="died")
    assert "Died ▲" in html and 'href="/search?q=Zebulonn&sort=born' in html
    assert "Name ▲" in results(admin, q="Zebulonn", sort="bogus")


def test_results_table(admin, names):
    html = results(admin, q="Zara")
    row = html.split("/person/XS1")[1].split("</tr>")[0]
    assert "1890" in row and "Searchtown, Testshire" in row and "/person/XHUSB" in row and "/person/XWIFE" in row


def test_paging(admin):
    html = results(admin, q="Hollis")
    assert "page 1 of" in html and "Next" in html and "Previous" not in html
    html = results(admin, q="Hollis", page="2")
    assert "Previous" in html and "page=1" in html
    assert "No one found" not in results(admin, q="Hollis", page="0")


def test_empty_and_no_results(admin):
    html = results(admin)
    assert "No one found" not in html and "people" not in html.split("</form>")[1]
    html = results(admin, q="Qqqzzzxxx", exact="1")
    assert "No one found" in html and "Try similar spellings" in html


def test_surname_pages_use_exact_surname(admin, names):
    html = text(admin.get("/surnames/ZEBULONN"))
    assert "<h1>ZEBULONN</h1>" in html and "/person/XS1" in html and "/person/XS2" not in html
    assert 'placeholder="First and/or last name' not in html and "Exact surname" not in html
    assert "/person/XS1" not in text(admin.get("/surnames/ZEBULONN?born_from=1900"))
    assert 'action="/surnames/ZEBULONN"' in html


def test_privacy(app, guest, names):
    html = results(guest, q="Zebulonn")
    assert "/person/XS3" not in html  # living, hidden from guests
    assert "/person/XS3" not in results(guest, q="Zebulonn", status="living")


def test_header_quick_search(admin):
    html = text(admin.get("/"))
    assert 'class="header-search"' in html and 'data-find="/find"' in html and "quicksearch.js" in html


def test_edits_refresh_surnames(app, admin):
    with app.app_context():
        search_mod.surnames()
    admin.post("/edit/person/new", data={"csrf": "t", "firstname": "New", "lastname": "QUIZZICALLY"})
    with app.app_context():
        assert "QUIZZICALLY" in search_mod.surnames()


def test_married_name(app, admin, guest):
    """A wife is found under her husband's surname too, saying so; a hidden husband or family never matches."""
    with app.app_context():
        for pid, first, last, living in (("XW1", "Mavis", "OLDNAMEQ", 0), ("XW2", "Hubert", "WEDLOCKE", 0),
                                         ("XW3", "Greta", "OTHERQ", 0), ("XW4", "Lars", "WEDLOCKE", 1)):
            insert("people", {"gedcom": "tree1", "personID": pid, "firstname": first, "lastname": last,
                                  "living": living})
        insert("families", {"gedcom": "tree1", "familyID": "XWF1", "husband": "XW2", "wife": "XW1", "living": 0})
        insert("families", {"gedcom": "tree1", "familyID": "XWF2", "husband": "XW4", "wife": "XW3", "living": 1})
        search_mod.invalidate()
    html = results(admin, q="Mavis Wedlocke")
    assert "/person/XW1" in html and "married name WEDLOCKE" in html
    assert "/person/XW1" in results(admin, q="Wedlocke", exact="1")
    assert "/person/XW1" in results(admin, q="Mavis Oldnameq")         # maiden name still works
    assert "/person/XW3" in results(admin, q="Greta Wedlocke")
    assert "/person/XW3" not in results(guest, q="Greta Wedlocke")     # living husband: no leak


def test_soundex():
    from app.surname_aliases import soundex
    assert [soundex(n) for n in ("Reed", "Reid", "Robert", "Rupert", "Ashcraft", "Tymczak", "Pfister", "O'Hara", "--")] \
        == ["R300", "R300", "R163", "R163", "A261", "T522", "P236", "O600", ""]


def test_surname_aliases(app, admin, guest, names, monkeypatch):
    with app.app_context():
        for pid, last in (("XA1", "ZUBE"), ("XA2", "ZOOBY"), ("XA3", "ZOOBIE")):
            insert("people", {"gedcom": "tree1", "personID": pid, "firstname": "Al", "lastname": last})
        search_mod.invalidate()
    assert "/person/XA2" not in results(admin, q="Zube")  # too short for the one-letter guess, and two letters off
    assert guest.get("/edit/surnames").status_code == 403
    from app import surname_aliases
    monkeypatch.setattr(surname_aliases, "SUGGESTED", 10 ** 6)  # the dev tree's real names come first
    suggest = text(admin.get("/edit/surnames"))
    assert 'name="name" value="ZOOBIE"><input type="hidden" name="main" value="ZOOBY">' in suggest  # Z100, 2 apart
    page = lambda **f: text(admin.post("/edit/surnames", data={"csrf": CSRF, **f}, follow_redirects=True))
    assert "No one has the surname NOPE" in page(name="nope", main="zube")
    assert "ZOOBY is now listed with ZUBE" in page(name="zooby", main="Zube")
    assert "ZOOBIE is now listed with ZUBE" in page(name="zoobie", main="zooby")  # joins zooby's group, under ZUBE
    assert "already one surname" in page(name="zube", main="zoobie")
    assert 'name="name" value="ZOOBIE"><input' not in text(admin.get("/edit/surnames"))  # linked: no longer suggested
    html = results(admin, q="Zube")
    assert "/person/XA2" in html and "/person/XA3" in html and "including similar spellings: ZOOBIE, ZOOBY" in html
    assert "/person/XA2" not in results(admin, q="Zube", exact="1")  # exact spelling leaves linked ones out too
    assert "/person/XA1" in results(admin, q="Zoobie")  # either spelling finds the others
    assert "/person/XA2" in text(admin.get("/surnames/ZUBE")) and "/person/XA1" in text(admin.get("/surnames/ZOOBY"))
    listing = text(admin.get("/surnames"))
    assert "ZUBE</a> <span class=\"muted\">4</span> <span class=\"muted small\">(also ZOOBIE, ZOOBY)" in listing
    assert ">ZOOBY</a>" not in listing
    assert "ZOOBY is its own surname again" in page(remove="ZOOBY")
    assert "/person/XA2" not in results(admin, q="Zube") and "/person/XA3" in results(admin, q="Zube")
    page(remove="ZOOBY")  # already unlinked: nothing to do
    assert "Surname spellings" in text(client_for(app, "fx_siteadmin").get("/admin/"))

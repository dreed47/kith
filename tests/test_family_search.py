"""Family search: couples by either spouse's name, marriage years and place."""
import html as htmllib

import pytest

from app import search as search_mod
from app.db import insert
from conftest import client_for


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


@pytest.fixture
def couples(app):
    with app.app_context():
        for pid, first, last, living in (("XFH1", "Hank", "QUIGLEYY", 0), ("XFW1", "Mabel", "ORTWINE", 0),
                                         ("XFH2", "Ivan", "QUIGLEY", 0), ("XFW2", "Nora", "PELLWORTH", 0),
                                         ("XFH3", "Liv", "QUIGLEYY", 1)):
            insert("people", {"gedcom": "tree1", "personID": pid, "firstname": first, "lastname": last,
                                  "living": living}, ignore=True)
        for fid, h, w, date, tr, place, living in (
                ("XFS1", "XFH1", "XFW1", "5 JUN 1900", "1900-06-05", "Weddingtown, Testshire", 0),
                ("XFS2", "XFH2", "XFW2", "1950", "1950-00-00", "", 0),
                ("XFS3", "XFH3", "", "", "0000-00-00", "", 1)):
            insert("families", {"gedcom": "tree1", "familyID": fid, "husband": h, "wife": w, "marrdate": date,
                                    "marrdatetr": tr, "marrplace": place, "living": living}, ignore=True)
        search_mod.invalidate()
    return True


def results(client, **args):
    return text(client.get("/search/families", query_string=args))


def found(html):
    return {f for f in ("XFS1", "XFS2", "XFS3") if f"/family/{f}\"" in html}


def test_by_husband_with_variants(admin, couples):
    html = results(admin, husband="Quigleyy")
    assert found(html) == {"XFS1", "XFS2", "XFS3"} and "including similar spellings: QUIGLEY" in html
    html = results(admin, husband="Quigleyy", exact="1")
    assert found(html) == {"XFS1", "XFS3"} and "include similar spellings" in html
    assert found(results(admin, husband="Hank Quigleyy")) == {"XFS1"}


def test_by_wife_years_and_place(admin, couples):
    assert found(results(admin, wife="Mabel Ortwine")) == {"XFS1"}
    assert found(results(admin, husband="Quigleyy", married_from="1940")) == {"XFS2"}  # unknown dates left out
    assert found(results(admin, husband="Quigleyy", married_to="1940")) == {"XFS1"}
    html = results(admin, place="Weddingtown")
    assert found(html) == {"XFS1"}
    row = html.split("/family/XFS1")[0].rsplit("<tr>", 1)[1]
    assert "/person/XFH1" in row and "/person/XFW1" in row and "5 Jun 1900" in row and "Weddingtown" in row


def test_sorting(admin, couples):
    def order(**args):
        html = results(admin, husband="Quigleyy", **args)
        return sorted(found(html), key=lambda f: html.index(f"/family/{f}\""))
    assert order(sort="married") == ["XFS1", "XFS2", "XFS3"]  # unknown date last
    assert order(sort="wife")[-1] == "XFS3"  # no wife last
    assert order()[0] == "XFS2"  # by husband's surname: QUIGLEY before QUIGLEYY
    assert "Husband ▲" in results(admin, husband="Quigleyy", sort="bogus")


def test_privacy(app, guest, couples):
    assert found(results(guest, husband="Quigleyy")) == {"XFS1", "XFS2"}  # the living couple is hidden


def test_empty_none_and_paging(admin):
    html = results(admin)
    assert "No families found" not in html and "<table" not in html
    html = results(admin, husband="Qqqzzzxxx", exact="1")
    assert "No families found" in html and "Try similar spellings" in html
    html = results(admin, husband="Hollis")
    assert "page 1 of" in html and "Next" in html
    assert "Previous" in results(admin, husband="Hollis", page="2")


def test_search_tabs(app, admin):
    html = results(admin)
    assert 'href="/search/families" aria-current="page">Families' in html and "/edit/notes" in html
    assert "/edit/notes" not in results(client_for(app, "fx_guest"))  # notes search is for editors
    assert 'href="/search" aria-current="page">People' in text(admin.get("/search"))
    assert 'href="/edit/notes" aria-current="page">Notes' in text(admin.get("/edit/notes"))


@pytest.mark.parametrize("old, new", [
    ("/famsearch.php?myflastname=HOLLIS&mymfirstname=Jane&mymarrplace=Clarion", "/search/families?husband=HOLLIS&wife=Jane&place=Clarion"),
    ("/famsearchform.php", "/search/families"),
])
def test_old_addresses(admin, old, new):
    r = admin.get(old)
    assert r.status_code == 301 and r.headers["Location"] == new

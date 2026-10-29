"""Search other sites: links to FamilySearch, Find a Grave and others with the person's name and years filled in."""
import html as htmllib
from urllib.parse import parse_qs, urlsplit

from conftest import make, sql

from app.views import search_links


def person(**kw):
    return {"living": 0, "firstname": "Walter James", "lnprefix": "", "lastname": "HOLLIS",
            "birthdatetr": "1898-03-31", "deathdatetr": "1970-03-14", "birthplace": "Brookville, PA", **kw}


def params(links, site):
    return {k: v[0] for k, v in parse_qs(urlsplit(dict(links)[site]).query).items()}


def test_search_links():
    links = search_links(person())
    assert [site for site, _ in links] == ["FamilySearch", "Find a Grave", "Ancestry", "Google"]
    assert params(links, "FamilySearch") == {
        "q.givenName": "Walter James", "q.surname": "HOLLIS", "q.birthLikeDate.from": "1896",
        "q.birthLikeDate.to": "1900", "q.deathLikeDate.from": "1968", "q.deathLikeDate.to": "1972",
        "q.birthLikePlace": "Brookville, PA"}
    assert params(links, "Find a Grave") == {"firstname": "Walter", "lastname": "HOLLIS", "birthyear": "1898",
                                             "birthyearfilter": "2", "deathyear": "1970", "deathyearfilter": "2"}
    assert params(links, "Ancestry") == {"name": "Walter James_HOLLIS", "birth": "1898_Brookville, PA",
                                         "death": "1970"}
    assert params(links, "Google") == {"q": '"Walter James HOLLIS" 1898 1970'}


def test_search_links_missing_facts():
    links = search_links(person(firstname=None, lnprefix="van", lastname="Dyke", birthdatetr="0000-00-00",
                                deathdatetr=None, birthplace=""))
    assert params(links, "FamilySearch") == {"q.surname": "van Dyke"}
    assert params(links, "Find a Grave") == {"lastname": "van Dyke"}
    assert params(links, "Ancestry") == {"name": "_van Dyke"} and params(links, "Google") == {"q": '"van Dyke"'}
    assert search_links(person(firstname="", lastname="")) == []
    assert search_links(person(living=1)) == []  # their name isn't sent anywhere


def test_person_page_links(app, admin):
    ids = make(app, ("Gone", "M", 0), ("Here", "F", 1))
    sql(app, "UPDATE people SET birthdatetr = '1850-01-01' WHERE personID = %s", (ids["Gone"],))
    page = htmllib.unescape(admin.get(f"/person/{ids['Gone']}").get_data(as_text=True))
    assert "Search other sites" in page and "findagrave.com/memorial/search?firstname=Gone" in page
    assert 'target="_blank" rel="noopener noreferrer"' in page
    assert "Search other sites" not in admin.get(f"/person/{ids['Here']}").get_data(as_text=True)

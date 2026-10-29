"""Place coordinates from Nominatim (mocked: tests never call it)."""
import io
import json
import urllib.error
import urllib.parse

import pytest
from conftest import CSRF, sql

from app import geocode
from app.db import execute, insert

ANSWERS = {  # Nominatim's answer per query
    "Xsligo, Clarion County, PA": [{"lat": "41.1", "lon": "-79.5", "display_name": "Xsligo, Clarion County",
                                    "address": {"ISO3166-2-lvl4": "US-PA"}}],
    "Xwrongstate, OH": [{"lat": "1", "lon": "2", "display_name": "somewhere else",
                         "address": {"ISO3166-2-lvl4": "US-IN"}}],
    "Xfranklin, PA": [{"lat": "39.9", "lon": "-77.7", "display_name": "Xfranklin County, Pennsylvania",
                       "address": {"ISO3166-2-lvl4": "US-PA"}}],
    "Xfranklin County, PA": [{"lat": "39.9", "lon": "-77.7", "display_name": "Xfranklin County, Pennsylvania",
                              "address": {"ISO3166-2-lvl4": "US-PA"}}],
    "Xberlin, Germany": [{"lat": "52.5", "lon": "13.4", "display_name": "Berlin", "address": {}}],
}


@pytest.fixture
def nominatim(monkeypatch):
    asked = []

    def urlopen(req, timeout):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        asked.append((q["q"][0], q.get("countrycodes", [""])[0], req.get_header("User-agent")))
        if q["q"][0] == "Xdown, PA":
            raise urllib.error.URLError("refused")
        return io.BytesIO(json.dumps(ANSWERS.get(q["q"][0], [])).encode())

    monkeypatch.setattr(geocode, "PAUSE", 0)
    monkeypatch.setattr(geocode.urllib.request, "urlopen", urlopen)
    return asked


def test_locate(app, nominatim):
    with app.app_context():
        insert("cemeteries", {"cemname": "Xhill Cemetery", "place": "Xhill Cemetery, Xsligo, Clarion Cnty, PA",
                                  "latitude": "41.2", "longitude": "-79.4"})
        assert geocode.locate("Xsligo, Clarion Cnty, PA") == ("found", "41.1", "-79.5", "Xsligo, Clarion County")
        assert geocode.locate("Xchurch, Xsligo, Clarion Cnty, PA")[0] == "approximate"  # the town matched
        assert geocode.locate("Xfranklin, PA")[0] == "approximate"  # a town name answered with a county
        assert geocode.locate("Xfranklin Cnty, PA")[0] == "found"
        assert geocode.locate("Xhill Cemetery") == ("found", "41.2", "-79.4", "cemetery: Xhill Cemetery")
        assert geocode.locate("Xhill Cemetery, Xother spelling, PA")[1] == "41.2"  # by its name
        insert("cemeteries", {"cemname": "Xtwin Cemetery", "latitude": "1", "longitude": "1"})
        insert("cemeteries", {"cemname": "Xtwin Cemetery", "latitude": "2", "longitude": "2"})
        assert geocode.locate("Xtwin Cemetery")[0] == "not found"  # two of that name: can't tell which
        assert geocode.locate("Xwrongstate, OH")[0] == "not found"  # answer was in another state
        assert geocode.locate("Xberlin, Germany")[1] == "52.5"
        assert geocode.locate("PA") == ("not found", "", "", "")  # too vague to ask
        with pytest.raises(geocode.Stop):
            geocode.locate("Xdown, PA")
    assert ("Xsligo, Clarion County, PA", "us", "kith-geocoder (a private family-history site)") in nominatim
    assert ("Xberlin, Germany", "", "kith-geocoder (a private family-history site)") in nominatim
    assert not any(q == "PA" for q, _, _ in nominatim)


def test_run(app, nominatim, monkeypatch):
    with app.app_context():
        for name, extra in (("Xsligo, Clarion Cnty, PA", {}), ("Xnowhere, Xco Cnty, PA", {}),
                            ("Xfailed, PA", {}), ("Xdone, PA", {"latitude": "1", "longitude": "2"}),
                            ("Xskip, PA", {"placelevel": -1})):
            insert("places", {"gedcom": "tree1", "place": name, **extra})
        execute("INSERT INTO app_geocode VALUES ('tree1', 'Xfailed, PA', 'not found', '', NOW())")
        names = [r["place"] for r in geocode.pending(0)]
        assert "Xsligo, Clarion Cnty, PA" in names and "Xnowhere, Xco Cnty, PA" in names
        assert not {"Xfailed, PA", "Xdone, PA", "Xskip, PA"} & set(names)
        assert len(geocode.pending(1)) == 1
        mine = [r for r in geocode.pending(0) if r["place"] in ("Xsligo, Clarion Cnty, PA", "Xnowhere, Xco Cnty, PA")]
    monkeypatch.setattr(geocode, "pending", lambda limit: mine)
    out = app.test_cli_runner().invoke(args=["geocode", "run"]).output
    assert "found 1, approximate 0, not found 1." in out
    assert sql(app, "SELECT latitude FROM places WHERE place = 'Xsligo, Clarion Cnty, PA'")[0]["latitude"] == "41.1"
    assert sql(app, "SELECT status FROM app_geocode WHERE place = 'Xnowhere, Xco Cnty, PA'")[0]["status"] == "not found"
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Xdown, PA"})
    monkeypatch.setattr(geocode, "pending", lambda limit: [{"ID": 0, "place": "Xdown, PA"}])
    assert "Stopped early" in app.test_cli_runner().invoke(args=["geocode", "run"]).output


def test_review_list(app, admin, guest):
    with app.app_context():
        insert("places", {"gedcom": "tree1", "place": "Xrough, PA", "latitude": "40", "longitude": "-79"})
        insert("places", {"gedcom": "tree1", "place": "Xlost, PA"})
        insert("places", {"gedcom": "tree1", "place": "Xexact, PA", "latitude": "41", "longitude": "-78"})
        insert("places", {"gedcom": "tree1", "place": "Xolder, PA", "latitude": "42", "longitude": "-78"})
        execute("DELETE FROM app_geocode")
        assert "No lookups yet." in admin.get("/edit/places/coordinates").get_data(as_text=True)
        execute("INSERT INTO app_geocode VALUES ('tree1', 'Xrough, PA', 'approximate', 'Clarion County', NOW()),"
                " ('tree1', 'Xlost, PA', 'not found', '', NOW()),"
                " ('tree1', 'Xexact, PA', 'found', 'Xexact, Clarion County', NOW()),"
                " ('tree1', 'Xolder, PA', 'found', 'Xolder town', NOW() - INTERVAL 3 DAY)")
    page = admin.get("/edit/places/coordinates").get_data(as_text=True)
    assert "Xrough, PA" in page and "Clarion County (40, -79)" in page and "Xlost, PA" in page
    assert "still have no coordinates" in page
    assert "3 places (1 found, 1 approximate, 1 not found)" in page  # the older lookup is another run
    assert "Xexact, Clarion County (41, -78)" in page and "Xolder town" in page
    assert page.index("Xexact, Clarion County") < page.index("Xolder town")  # newest first
    assert guest.get("/edit/places/coordinates").status_code == 403
    admin.post("/edit/place", data={"csrf": CSRF, "name": "Xlost, PA", "place": "Xlost, PA", "latitude": "41",
                                    "longitude": "-79"})
    assert "Xlost, PA" not in admin.get("/edit/places/coordinates").get_data(as_text=True)  # checked by an editor

import datetime
import html as htmllib
import subprocess

import pytest
from flask import g

import app as kith
from app import home
from app.db import insert
from app.home import ago, on_this_day
from conftest import CSRF, client_for, make, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


NOW = datetime.datetime(2026, 10, 26, 12, 0)


@pytest.mark.parametrize("when,label", [
    (NOW, "today"), (NOW - datetime.timedelta(days=1), "yesterday"), (NOW - datetime.timedelta(days=5), "5 days ago"),
    (NOW - datetime.timedelta(days=20), "2 weeks ago"), (NOW - datetime.timedelta(days=100), "3 months ago"),
    (datetime.datetime(2020, 3, 7), "7 Mar 2020"), (NOW + datetime.timedelta(hours=5), "today"),
])
def test_ago(when, label):
    assert ago(when, NOW) == label


def test_ago_defaults_to_now():
    assert ago(datetime.datetime.now()) == "today"


def linked(app, username, pid):
    sql(app, "UPDATE users SET personID = %s WHERE username = %s", (pid, username))
    return client_for(app, username)


def test_recently_updated(app, admin):
    ids = make(app, ("Recent", "M", 0), ("Spouse", "F", 0))
    admin.post("/edit/person/new", data={"csrf": "t", "firstname": "Justadded", "lastname": "Fixture"})
    admin.post("/edit/family/new", data={"csrf": "t", "husband": ids["Recent"], "wife": ids["Spouse"]})
    html = text(admin.get("/"))
    recent = html.split("Recently updated")[1].split("</section>")[0]
    assert "Justadded Fixture" in recent and "today by fx_admin" in recent
    assert "Recent " in recent and "family" in recent  # the new family, shown by its couple


def test_recently_updated_respects_privacy(app, admin, guest):
    ids = make(app, ("Secretly", "M", 1))
    sql(app, "UPDATE people SET changedate = NOW() WHERE personID = %s", (ids["Secretly"],))
    assert "Secretly" in text(admin.get("/"))
    assert "Secretly" not in text(guest.get("/"))


def test_empty_recent(app, guest, monkeypatch):
    monkeypatch.setattr(home, "recent_changes", lambda: [])
    assert "Nothing yet." in text(guest.get("/"))


def test_dates_to_fix(app, admin, guest):
    ids = make(app, ("Baddate", "M", 0), family=("Baddate", None))
    sql(app, "UPDATE people SET birthdate = '31 FEB 1900', birthdatetr = '0000-00-00' WHERE personID = %s", (ids["Baddate"],))
    sql(app, "UPDATE families SET marrdate = 'June-ish', marrdatetr = '0000-00-00' WHERE familyID = %s", (ids["family"],))
    occu = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'I' AND tag = 'OCCU'")[0]["eventtypeID"]
    cens = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'F' AND tag = 'CENS'")[0]["eventtypeID"]
    with app.app_context():
        _, e1 = insert("events", {"gedcom": "tree1", "persfamID": ids["Baddate"], "eventtypeID": occu, "eventdate": "1/23/1733"})
        _, e2 = insert("events", {"gedcom": "tree1", "persfamID": ids["family"], "eventtypeID": cens, "eventdate": "sometime"})
    home_html = text(admin.get("/"))
    assert "Tidy up" in home_html and 'dates can\'t be read' in home_html and 'href="/dates-to-fix"' in home_html
    html = ""
    for n in range(1, 30):  # read every page
        page = text(admin.get(f"/dates-to-fix?page={n}"))
        html += page
        if "Next</a>" not in page:
            break
    for part in ("31 FEB 1900", "There is no 31 Feb 1900", f"/edit/person/{ids['Baddate']}",
                 "June-ish", f"/edit/family/{ids['family']}", "1/23/1733", f"/edit/event/{e1}",
                 "sometime", f"/edit/event/{e2}", "Save as 23 Jan 1733"):
        assert part in html, part
    assert guest.get("/dates-to-fix").status_code == 403
    assert "Tidy up" not in text(guest.get("/"))


def test_one_click_date_fix(app, admin, guest):
    ids = make(app, ("Slashdate", "F", 0), family=("Slashdate", None))
    pid, fid = ids["Slashdate"], ids["family"]
    # TNG sorted this one (as 1 Mar), but it was probably typed month first: listed with both readings
    sql(app, "UPDATE people SET birthdate = '1/3/1964', birthdatetr = '1964-03-01' WHERE personID = %s", (pid,))
    sql(app, "UPDATE families SET marrdate = '2/14/1988', marrdatetr = '0000-00-00' WHERE familyID = %s", (fid,))
    sql(app, "UPDATE people SET deathdate = 'Yes', deathdatetr = '0000-00-00', altbirthdate = 'Y',"
             " altbirthdatetr = '0000-00-00' WHERE personID = %s", (pid,))  # died, date unknown: not listed. A birth "Y" still is
    sql(app, "UPDATE families SET divdate = 'Y', divdatetr = '0000-00-00' WHERE familyID = %s", (fid,))
    occu = sql(app, "SELECT eventtypeID FROM eventtypes WHERE type = 'I' AND tag = 'OCCU'")[0]["eventtypeID"]
    with app.app_context():
        _, eid = insert("events", {"gedcom": "tree1", "persfamID": pid, "eventtypeID": occu, "eventdate": "Feb 19 1993"})
    with app.test_request_context():
        g.user = {"allow_edit": 1}
        mine = {r["date"]: r["fixes"] for r in home.to_fix() if r["edit"][1] in (pid, fid, eid)}
    assert mine == {"1/3/1964": [("3 JAN 1964", "month/day/year"), ("1 MAR 1964", "day/month/year")], "Y": [],
                    "2/14/1988": [("14 FEB 1988", "")], "Feb 19 1993": [("19 FEB 1993", "")]}
    fix = lambda kind, rid, field, old, new, who=admin: who.post("/dates-to-fix", data={
        "csrf": CSRF, "kind": kind, "id": rid, "field": field, "old": old, "new": new, "page": 2})
    r = fix("person", pid, "birth", "1/3/1964", "1 MAR 1964")
    assert r.status_code == 302 and r.headers["Location"].endswith("/dates-to-fix?page=2")
    row = sql(app, "SELECT birthdate, birthdatetr, changedby FROM people WHERE personID = %s", (pid,))[0]
    assert (row["birthdate"], str(row["birthdatetr"]), row["changedby"]) == ("1 MAR 1964", "1964-03-01", "fx_admin")
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'edit person' AND record = %s", (pid,))
    fix("family", fid, "marr", "2/14/1988", "14 FEB 1988")
    assert str(sql(app, "SELECT marrdatetr FROM families WHERE familyID = %s", (fid,))[0]["marrdatetr"]) == "1988-02-14"
    fix("event", eid, "event", "Feb 19 1993", "19 FEB 1993")
    assert sql(app, "SELECT eventdate FROM events WHERE eventID = %s", (eid,))[0]["eventdate"] == "19 FEB 1993"
    # a stale page, or a value that isn't a reading of the date, changes nothing
    assert "has changed since" in text(admin.get(fix("person", pid, "birth", "1/3/1964", "3 JAN 1964").headers["Location"]))
    sql(app, "UPDATE people SET birthdate = '1/3/1964' WHERE personID = %s", (pid,))
    fix("person", pid, "birth", "1/3/1964", "4 JUL 1776")
    fix("person", "XNOSUCH", "birth", "1/3/1964", "3 JAN 1964")
    assert sql(app, "SELECT birthdate FROM people WHERE personID = %s", (pid,))[0]["birthdate"] == "1/3/1964"
    assert fix("person", pid, "firstname", "1/3/1964", "3 JAN 1964").status_code == 400
    assert fix("nope", pid, "birth", "1/3/1964", "3 JAN 1964").status_code == 400
    assert fix("person", pid, "birth", "1/3/1964", "3 JAN 1964", guest).status_code == 403
    html = text(admin.get("/dates-to-fix?page=1"))  # paging aside, the two readings show as buttons
    for n in range(2, 30):
        if "1/3/1964" in html or "Next</a>" not in html:
            break
        html = text(admin.get(f"/dates-to-fix?page={n}"))
    assert "Save as 3 Jan 1964" in html and "Save as 1 Mar 1964" in html and "day/month/year" in html


def test_dates_to_fix_paging_and_empty(app, admin, monkeypatch):
    monkeypatch.setattr(home, "FIX_PER_PAGE", 2)
    html = text(admin.get("/dates-to-fix"))
    assert "Page 1 of" in html and "Next" in html
    assert "Previous" in text(admin.get("/dates-to-fix?page=2"))
    assert "Nothing to fix" in text(admin.get("/dates-to-fix?page=99999"))
    monkeypatch.setattr(home, "fix_count", lambda: 0)
    assert "Still counting" in text(admin.get("/"))  # no background count yet: links without numbers
    monkeypatch.setitem(home._tidy, "counts", {"places": 0, "typos": 0, "living": 0, "checks": 0})
    assert "Nothing to tidy" in text(admin.get("/"))
    monkeypatch.setitem(home._tidy, "counts", {"places": 0, "typos": 1, "living": 0, "checks": 12})
    html = text(admin.get("/"))
    assert "<strong>1</strong> <a href=\"/edit/places/tidy\">possible misspelled place</a>" in html
    assert "<strong>12</strong> <a href=\"/edit/checks\">other data problems</a>" in html
    assert "living mark" not in html and "Still counting" not in html and "dates can't be read" not in html


def test_tidy_counts_in_background(app, admin, monkeypatch):
    monkeypatch.setitem(home._tidy, "at", 0.0)  # stale: the next editor's home page starts a count
    admin.get("/")
    first = home._tidy["thread"]
    admin.get("/")  # still counting: no second thread
    assert home._tidy["thread"] is first
    first.join(60)
    counts = home._tidy["counts"]
    assert set(counts) == {"places", "typos", "living", "checks"} and all(isinstance(n, int) for n in counts.values())
    admin.get("/")  # fresh: no new count
    assert home._tidy["thread"] is first


def test_your_branch(app):
    c = linked(app, "fx_nonotes", "XKID")
    try:
        branch = text(c.get("/")).split("Your branch")[1].split("</section>")[0]
        for pid in ("XKID", "XHUSB", "XWIFE", "XGPA", "XGMA"):
            assert f"/person/{pid}" in branch, pid
        assert "/pedigree/XKID" in branch and "Grandparents" in branch
    finally:
        linked(app, "fx_nonotes", "")


def test_branch_edge_cases(app):
    c = linked(app, "fx_nonotes", "XGGPA")  # no parents recorded
    try:
        branch = text(c.get("/")).split("Your branch")[1].split("</section>")[0]
        assert "Parents" not in branch
        linked(app, "fx_nonotes", "XNOSUCHPERSON")
        assert "Your branch" not in text(c.get("/"))
        linked(app, "fx_nonotes", "")
        assert "Your branch" not in text(c.get("/"))
    finally:
        linked(app, "fx_nonotes", "")


def test_on_this_day(app, monkeypatch):
    sql(app, "UPDATE people SET birthdatetr = '1850-07-04' WHERE personID = 'XGPA'")
    sql(app, "UPDATE people SET deathdatetr = '1930-07-04' WHERE personID = 'XGGPA'")
    sql(app, "UPDATE families SET marrdatetr = '1900-07-04' WHERE familyID = 'XFAM'")
    sql(app, "UPDATE users SET personID = 'XKID' WHERE username = 'fx_nonotes'")
    try:
        with app.test_request_context():
            g.user = sql(app, "SELECT * FROM users WHERE username = 'fx_nonotes'")[0]
            items, total = on_this_day(datetime.date(2026, 7, 4))
            ours = [x for x in items if x["id"] in ("XGPA", "XGGPA", "XHUSB")]
            assert {(x["kind"], x["years_ago"]) for x in ours} >= {("born", 176), ("died", 96), ("married", 126)}
            assert items[0]["rel"] is not None  # relatives first
            assert next(x for x in ours if x["id"] == "XGPA")["rel"]["label"] == "grandfather"
            monkeypatch.setattr(home, "ON_THIS_DAY", 1)
            items, total = on_this_day(datetime.date(2026, 7, 4))
            assert len(items) == 1 and total >= 3
            g.user = {**g.user, "personID": ""}
            assert all(x["rel"] is None for x in on_this_day(datetime.date(2026, 7, 4))[0])
    finally:
        sql(app, "UPDATE users SET personID = '' WHERE username = 'fx_nonotes'")


def test_on_this_day_on_home(app, admin, monkeypatch):
    sql(app, "UPDATE families SET marrdatetr = '1900-07-04' WHERE familyID = 'XFAM'")
    monkeypatch.setattr(home, "ON_THIS_DAY", 1000)  # so the fixture marriage isn't cut off
    items = _items(app)
    monkeypatch.setattr(home, "on_this_day", lambda: (items, 5))
    html = text(admin.get("/"))
    assert "Hal Fixture" in html and "married" in html and "And 4 more." in html
    monkeypatch.setattr(home, "on_this_day", lambda: ([], 0))
    assert "No births, deaths or marriages recorded on this date" in text(admin.get("/"))


def _items(app):
    with app.test_request_context():
        g.user = sql(app, "SELECT * FROM users WHERE username = 'fx_admin'")[0]
        items, _ = on_this_day(datetime.date(2026, 7, 4))
    return [x for x in items if x["kind"] == "married" and x["family"]["familyID"] == "XFAM"]


def test_recent_photos(app, admin, media_ids, monkeypatch):
    from app.db import insert
    with app.app_context():
        insert("media", {"gedcom": "tree1", "mediatypeID": "photos", "path": "never_uploaded.jpg", "alwayson": 1,
                             "usecollfolder": 1, "mediakey": "Xneverup", "description": "Never uploaded"})
    html = text(admin.get("/"))
    assert "Recent photos" in html and "Never uploaded" not in html
    monkeypatch.setattr(home, "recent_photos", lambda: [])
    assert "Recent photos" not in text(admin.get("/"))


def test_public_welcome_page(app, plain_app):
    c = app.test_client()
    r = c.get("/")
    html = text(r)
    assert r.status_code == 200 and "Family History" in html and 'name="password"' in html
    assert 'Built with <a href="https://github.com/dreed47/kith">Kith</a>' in html  # the credit, even logged out
    assert 'name="csrf"' in html and "header-search" not in html and "Log out" not in html
    for private in ("Fixture", "Recently updated", "On this day", "/person/", "/media/"):
        assert private not in html, private
    assert c.get("/surnames").status_code == 302  # everything else still needs a login
    assert f"Kith</a> {kith.version()}" in html


def test_version(monkeypatch):
    assert kith.version().startswith("0.1.0 (")  # a git checkout: the commit too
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, "", "not a repo"))
    assert kith.version() == "0.1.0"  # a tarball or Docker copy
    def missing(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", missing)
    assert kith.version() == "0.1.0"  # no git installed


def test_public_page_contact_and_defaults(tmp_path, monkeypatch):
    import app as app_module
    from app import create_app
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "theme.css").write_text(":root {}")
    (tmp_path / "plain" / "theme.toml").write_text('site_title = "Smith Tree"\ntagline = "Since 1700"\ncontact = "Email jo@example.com"\n')
    monkeypatch.setattr(app_module, "THEMES_DIR", tmp_path)
    html = text(create_app({"THEME": "plain", "SECRET_KEY": "x"}).test_client().get("/"))
    assert "<h1>Smith Tree</h1>" in html and "Since 1700" in html and "Email <a href" in html
    assert "This is a private family history site" in html
    assert '<a href="mailto:jo@example.com?subject=Account%20request&body=Hi' in html


def test_mailto_escapes():
    from app import mailto
    assert mailto("<b>x</b> a.b@c.org") == '&lt;b&gt;x&lt;/b&gt; <a href="mailto:a.b@c.org' + __import__("app").ASK + '">a.b@c.org</a>'


def test_editor_home_order(admin):
    html = text(admin.get("/"))
    assert html.index("Recently updated") < html.index("Tidy up") < html.index("On this day")
    assert "Make this site yours" not in html


def test_family_member_home_order(app):
    c = linked(app, "fx_nonotes", "XKID")
    try:
        html = text(c.get("/"))
        assert html.index("Your branch") < html.index("On this day") < html.index("Recently updated")
        assert "Tidy up" not in html and "Make this site yours" not in html
    finally:
        linked(app, "fx_nonotes", "")


def test_unlinked_member_gets_linking_hint(app):
    html = text(linked(app, "fx_nonotes", "").get("/"))
    assert "Make this site yours" in html and "This is me" in html and "Your branch" not in html


def test_ago_and_today_use_viewers_zone(app):
    late = datetime.datetime.now().replace(hour=23, minute=30)  # late evening at the server
    tokyo = late.astimezone(datetime.timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Tokyo"))
    with app.test_request_context(headers={"Cookie": "tz=Asia/Tokyo"}):
        assert home.localtime.today() == datetime.datetime.now(datetime.timezone.utc).astimezone(tokyo.tzinfo).date()
        assert home.localtime.local(late) == tokyo.replace(tzinfo=None)
    with app.test_request_context(headers={"Cookie": "tz=Not/AZone"}):
        assert home.localtime.local(late) == late

"""This week in family history: who's in each person's email, in what order, and the Monday send."""
import datetime
import html as htmllib

import pytest
from conftest import CSRF, client_for, make, sql
from flask import g

from app import digest, mail, relations
from app.db import insert

MONDAY = datetime.date(2031, 3, 3)  # a week the fixtures' dates are set into


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def user(app, name):
    return sql(app, "SELECT * FROM users WHERE username = %s", (name,))[0]


def born(app, pid, date):
    sql(app, "UPDATE people SET birthdate = %s, birthdatetr = %s WHERE personID = %s",
        (date.strftime("%-d %b %Y").upper(), date.isoformat(), pid))


def compose_for(app, name, start=MONDAY):
    # Links come from the site's own address, never the host a request claims (here a forged one).
    with app.test_request_context(base_url="https://evil.example"):
        app.config["SITE_URL"] = "https://tree.example"
        try:
            g.user = user(app, name)
            return digest.compose(start)
        finally:
            app.config["SITE_URL"] = ""


@pytest.fixture
def family(app):
    """A father with a son; the son is fx_nonotes' own record. Births in the test week, the father's a century back."""
    ids = make(app, ("Pop", "M", 0), ("Sonny", "M", 0), ("Alive", "F", 1), family=("Pop", None), kids=("Sonny",))
    born(app, ids["Pop"], datetime.date(1931, 3, 5))
    born(app, ids["Sonny"], datetime.date(1961, 3, 3))
    born(app, ids["Alive"], datetime.date(1990, 3, 4))
    sql(app, "UPDATE users SET personID = %s WHERE username = 'fx_nonotes'", (ids["Sonny"],))
    relations.invalidate()  # the family graph is cached
    yield ids
    sql(app, "UPDATE users SET personID = '' WHERE username = 'fx_nonotes'")


def test_relatives_first_with_links(app, family, monkeypatch):
    monkeypatch.setattr(digest, "SHOWN", 2)  # only room for the closest two
    subject, body = compose_for(app, "fx_nonotes")
    assert subject == "This week in family history: 3 Mar – 9 Mar"
    assert body.startswith("This week in family history, 3 Mar – 9 Mar\n\nMonday 3 March\n")
    pop = family["Pop"]
    assert f"1961  Sonny {pop[1:-3]} born, 70 years ago (you)" in body
    assert f"1931  Pop {pop[1:-3]} born, 100 years ago (your father)" in body
    assert f"https://tree.example/person/{pop}" in body
    assert body.index("Monday 3 March") < body.index("Wednesday 5 March")  # shown in date order
    assert "more this week: https://tree.example/anniversaries" in body
    assert "untick the email box under Your account:\nhttps://tree.example/account/" in body


def test_only_who_they_may_see(app, family, monkeypatch):
    monkeypatch.setattr(digest, "SHOWN", 10000)  # everything, not just the closest
    assert family["Alive"] in compose_for(app, "fx_nonotes")[1]
    _, body = compose_for(app, "fx_guest")  # can't see living people
    assert family["Alive"] not in body and family["Pop"] in body


def test_couples_and_quiet_weeks(app, monkeypatch):
    ids = make(app, ("Groom", "M", 0), ("Bride", "F", 0), family=("Groom", "Bride"))
    sql(app, "UPDATE families SET marrdate = '4 MAR 1950', marrdatetr = '1950-03-04' WHERE familyID = %s",
        (ids["family"],))
    monkeypatch.setattr(digest, "SHOWN", 10000)
    _, body = compose_for(app, "fx_admin")
    assert f"Groom {ids['Groom'][1:-5]} & Bride {ids['Bride'][1:-5]} married, 81 years ago" in body
    # Married into the family: the line says which spouse is the relative, without naming hidden living people.
    sql(app, "UPDATE users SET personID = %s WHERE username = 'fx_nonotes'", (ids["Bride"],))
    sql(app, "UPDATE users SET personID = %s WHERE username = 'fx_guest'", (ids["Bride"],))
    try:
        assert "married, 81 years ago (you)" in compose_for(app, "fx_nonotes")[1]
        kin = make(app, ("Kin", "F", 0), ("Spouse", "M", 1), family=("Spouse", "Kin"))
        sql(app, "UPDATE families SET marrdate = '5 MAR 1950', marrdatetr = '1950-03-05' WHERE familyID = %s",
            (kin["family"],))
        sql(app, "UPDATE people SET famc = %s WHERE personID = %s", (ids["family"], kin["Kin"]))
        with app.app_context():
            insert("children", {"gedcom": "tree1", "familyID": ids["family"], "personID": kin["Kin"], "ordernum": 1})
        relations.invalidate()
        assert "(Kin is your daughter)" in compose_for(app, "fx_nonotes")[1]
        sql(app, "UPDATE people SET living = 1 WHERE personID = %s", (kin["Kin"],))
        sql(app, "UPDATE families SET living = 0 WHERE familyID = %s", (kin["family"],))
        body = compose_for(app, "fx_guest")[1]
        assert "Kin" not in body and "(Living is your daughter)" in body
    finally:
        sql(app, "UPDATE users SET personID = '' WHERE username IN ('fx_nonotes', 'fx_guest')")
    assert f"https://tree.example/family/{ids['family']}" in body
    assert digest.closeness(None) == 99 and digest.closeness({"kind": "spouse"}) == 1
    monkeypatch.setattr(digest, "dated_events", lambda m, d: [])
    assert compose_for(app, "fx_admin") is None
    with app.test_request_context():
        g.user = None
        assert digest.send_to(user(app, "fx_admin"), MONDAY) is None
        assert g.user is None  # put back


def test_monday_send(app, monkeypatch):
    sent = []
    runner = app.test_cli_runner()
    assert "Needs KITH_SITE_URL" in runner.invoke(args=["digest", "send"]).output
    monkeypatch.setitem(app.config, "SITE_URL", "https://tree.example")
    monkeypatch.setattr(mail, "enabled", lambda: True)
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append(to) or to != "fail@example.com")
    with app.app_context():
        digest.put_setting("weekly_email", "0")
    assert "turned off" in runner.invoke(args=["digest", "send"]).output and not sent
    with app.app_context():
        digest.put_setting("weekly_email", "1")
    sql(app, "UPDATE users SET email = 'fail@example.com', no_email = 0 WHERE username = 'fx_guest'")
    try:
        out = runner.invoke(args=["digest", "send"]).output
    finally:
        sql(app, "UPDATE users SET email = '' WHERE username = 'fx_guest'")
    assert out.startswith("Sent ") and "failed: fx_guest" in out and "fail@example.com" in sent
    assert "Already sent today" in runner.invoke(args=["digest", "send"]).output
    n = len(sent)
    assert runner.invoke(args=["digest", "send", "--force"]).output.startswith("Sent ") and len(sent) > n
    with app.app_context():
        digest.put_setting("weekly_email", "0")


def test_admin_controls(app, monkeypatch):
    admin = client_for(app, "fx_siteadmin")
    page = text(admin.get("/admin/email"))
    assert "This week in family history" in page and "KITH_SITE_URL isn't set" in page
    admin.post("/admin/email/weekly", data={"csrf": CSRF, "action": "on"})
    assert "The weekly email is on." in text(admin.get("/admin/email"))
    assert sql(app, "SELECT 1 FROM app_audit WHERE record = 'weekly_email'")
    admin.post("/admin/email/weekly", data={"csrf": CSRF, "action": "off"})
    assert ">Turn on</button>" in text(admin.get("/admin/email"))
    admin.post("/admin/email/weekly", data={"csrf": CSRF, "action": "test"})
    assert "couldn't be sent" in text(admin.get("/admin/email"))  # no mail set up here
    monkeypatch.setattr(mail, "enabled", lambda: True)
    monkeypatch.setattr(digest, "send_to", lambda u, start: None)
    sql(app, "UPDATE users SET email = 'me@example.com' WHERE username = 'fx_siteadmin'")
    try:
        admin.post("/admin/email/weekly", data={"csrf": CSRF, "action": "test"})
        assert "nothing to send" in text(admin.get("/admin/email"))
        monkeypatch.setattr(digest, "send_to", lambda u, start: True)
        admin.post("/admin/email/weekly", data={"csrf": CSRF, "action": "test"})
        assert "sent to me@example.com" in text(admin.get("/admin/email"))
    finally:
        sql(app, "UPDATE users SET email = '' WHERE username = 'fx_siteadmin'")
    assert admin.post("/admin/email/weekly", data={"csrf": CSRF, "action": "x"}).status_code == 400


def test_settings_before_any_are_saved(app, monkeypatch):
    import pymysql

    def missing(*a):
        raise pymysql.err.ProgrammingError(1146, "Table doesn't exist")
    monkeypatch.setattr(digest, "query", missing)
    with app.app_context():
        assert digest.setting("weekly_email") == ""

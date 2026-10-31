import datetime
import itertools
import json
import re

import pymysql
from conftest import CSRF, client_for, sql
from test_auth import login

from app import admin as admin_module
from app import settings
from app.admin import changes, day, flat
from app.auth import tng_md5
from app.db import execute, insert

_n = itertools.count(1)


def text(r):
    return re.sub(r"(\.css|\.js|\.svg)\?v=\d+", r"\1", r.get_data(as_text=True))  # file versions on CSS/JS links vary


def site_admin(app):
    return client_for(app, "fx_siteadmin")


def form(**v):
    return {"csrf": CSRF, "username": f"acct{next(_n)}", "password": "longenough", "role": "guest",
            "allow_living": "1", **v}


def account(app, username):
    rows = sql(app, "SELECT * FROM users WHERE username = %s", (username,))
    return rows[0] if rows else None


def waiting(app, name):
    with app.app_context():
        _, uid = insert("users", {"username": name, "realname": name + "XY", "allow_living": -1,
                                      "notes": "I'm a cousin", "dt_registered": "2026-10-01 10:00:00"})
    return uid


def test_admins_only(app, admin):
    assert admin.get("/admin/").status_code == 403  # every right, but role isn't admin
    assert "/admin/" not in text(admin.get("/search"))
    assert "/admin/" in text(site_admin(app).get("/search"))


def test_menu_count(app):
    c = site_admin(app)
    with app.app_context():
        from app.admin import waiting_count
        before = waiting_count()  # other tests leave registrations and feedback behind
    assert ("Admin (" in text(c.get("/search"))) == (before > 0)
    waiting(app, "menucount")
    with app.app_context():
        rows = [insert("app_feedback", {"created_at": "2026-10-01 10:00:00", "userID": 1, "username": "x", "kind": "bug",
                                        "message": "x", "status": status})[1] for status in ("new", "working")]
    assert f"Admin ({before + 2})" in text(c.get("/search"))  # registration + new feedback, not "working"
    with app.app_context():
        execute("DELETE FROM app_feedback WHERE id IN (%s, %s)", rows)  # feedback tests count the whole table


def test_home_counts(app):
    waiting(app, "countme")
    html = text(site_admin(app).get("/admin/"))
    assert "New account registrations" in html and "/admin/users?show=pending" in html


def test_add_account_and_log_in(app):
    c = site_admin(app)
    r = c.post("/admin/users/new", data=form(username="newbie", email="newbie@example.com", personID="xhusb",
                                             allow_ged="1"))
    assert r.status_code == 302
    u = account(app, "newbie")
    assert u["password"] == tng_md5("longenough") and u["personID"] == "XHUSB" and u["allow_ged"] == 1
    assert u["allow_edit"] == 0 and u["dt_activated"]
    logged = sql(app, "SELECT * FROM app_audit WHERE tbl = 'users' AND record = %s", (str(u["userID"]),))[0]
    assert "longenough" not in logged["after_json"] and u["password"] not in logged["after_json"]
    fresh = app.test_client()
    assert login(fresh, "newbie", "longenough").status_code == 302
    assert account(app, "newbie")["lastlogin"]  # stamped at login, as TNG does


def test_account_form_validation(app):
    c = site_admin(app)
    c.post("/admin/users/new", data=form(username="taken", email="dup@example.com"))
    for v, message in (({"username": ""}, "Enter a username."), ({"username": "TAKEN"}, "username is taken"),
                       ({"email": "DUP@example.com"}, "already uses that email"), ({"password": "short"}, "at least 8"),
                       ({"personID": "NOPE"}, "no person NOPE"), ({"realname": "x" * 51}, "Realname is longer")):
        r = c.post("/admin/users/new", data=form(**v))
        assert r.status_code == 200 and message in text(r), message
    assert "Add account" in text(c.get("/admin/users/new"))


def test_edit_keeps_password_unless_given(app):
    c = site_admin(app)
    c.post("/admin/users/new", data=form(username="editme"))
    u = account(app, "editme")
    r = c.post(f"/admin/users/{u['userID']}", data=form(username="editme", password="", role="editor",
                                                        allow_edit="1", disabled="1"))
    assert r.status_code == 302
    after = account(app, "editme")
    assert after["password"] == u["password"] and after["role"] == "editor" and after["allow_edit"] == 1
    assert after["disabled"] == 1
    c.post(f"/admin/users/{u['userID']}", data=form(username="editme", password="anotherone"))
    assert account(app, "editme")["password"] == tng_md5("anotherone")
    assert "Account: editme" in text(c.get(f"/admin/users/{u['userID']}"))


def test_cannot_lock_yourself_out(app):
    c = site_admin(app)
    me = account(app, "fx_siteadmin")
    r = c.post(f"/admin/users/{me['userID']}", data=form(username="fx_siteadmin", password="", role="editor"))
    assert "take away your own admin role" in text(r)
    assert account(app, "fx_siteadmin")["role"] == "admin"
    assert c.get(f"/admin/users/{me['userID']}/delete").status_code == 400


def test_approve_registration(app):
    uid = waiting(app, "approveme")
    c = site_admin(app)
    html = text(c.get(f"/admin/users/{uid}"))
    assert "Approve registration: approveme" in html and "I&#39;m a cousin" in html and "Reject and delete" in html
    r = c.post(f"/admin/users/{uid}", data=form(username="approveme", password="", allow_living="1"))
    assert r.status_code == 302
    u = account(app, "approveme")
    assert u["allow_living"] == 1 and u["dt_activated"]
    assert sql(app, "SELECT action FROM app_audit WHERE tbl = 'users' AND record = %s", (str(uid),))[0]["action"] \
        == "approve user"


def test_user_lists(app):
    waiting(app, "listwaiter")
    c = site_admin(app)
    c.post("/admin/users/new", data=form(username="listoff", disabled="1"))
    off = account(app, "listoff")
    c.post(f"/admin/users/{off['userID']}", data=form(username="listoff", password="", disabled="1"))
    pending = text(c.get("/admin/users?show=pending"))
    assert "listwaiter" in pending and "fx_guest" not in pending and "I&#39;m a cousin" in pending
    assert "listoff" in text(c.get("/admin/users?show=disabled"))
    found = text(c.get("/admin/users?q=listwait"))
    assert "listwaiter" in found and "listoff" not in found
    assert "never, registered 1 Oct 2026" in found
    assert "No accounts here." in text(c.get("/admin/users?q=nobodyatall"))


def test_delete_account(app):
    c = site_admin(app)
    c.post("/admin/users/new", data=form(username="goner"))
    u = account(app, "goner")
    sql(app, "INSERT INTO app_link_requests (userID, personID, requested_at) VALUES (%s, 'XHUSB', NOW())",
        (u["userID"],))
    assert "Delete account goner?" in text(c.get(f"/admin/users/{u['userID']}/delete"))
    assert c.post(f"/admin/users/{u['userID']}/delete", data={"csrf": CSRF}).status_code == 302
    assert account(app, "goner") is None
    assert not sql(app, "SELECT * FROM app_link_requests WHERE userID = %s", (u["userID"],))
    assert c.get(f"/admin/users/{u['userID']}").status_code == 404


def test_reject_ticked_registrations_only(app):
    spam1, spam2 = waiting(app, "spambot1"), waiting(app, "spambot2")
    keep = account(app, "fx_guest")["userID"]
    r = site_admin(app).post("/admin/users/reject", data={"csrf": CSRF, "uid": [spam1, spam2, keep, "x"]})
    assert r.status_code == 302
    assert account(app, "spambot1") is None and account(app, "spambot2") is None and account(app, "fx_guest")
    assert sql(app, "SELECT action FROM app_audit WHERE record = %s", (str(spam1),))[0]["action"] == "reject registration"
    site_admin(app).post("/admin/users/reject", data={"csrf": CSRF, "uid": [spam1]})  # already gone: nothing to do


def test_changes_and_flat():
    row = {"before_json": json.dumps({"firstname": "Al", "changedate": "x", "living": 0}),
           "after_json": json.dumps({"firstname": "Bo", "changedate": "y", "living": 0})}
    assert changes(row) == [("firstname", "Al", "Bo")]
    deleted = {"before_json": json.dumps({"media": {"path": "a.jpg", "changedby": "d"}, "links": [1, 2],
                                          "one": [1]}), "after_json": None}
    assert changes(deleted) == [("links", "2 linked rows", None), ("media.path", "a.jpg", None),
                                ("one", "1 linked row", None)]
    assert flat(None) == {}


def test_day():
    assert day(datetime.datetime(2026, 10, 5, 8, 0)) == "5 Oct 2026" and day("0000-00-00 00:00:00") == "" and day(None) == ""


def test_activity_log(app, monkeypatch):
    c = site_admin(app)
    c.post("/admin/users/new", data=form(username="logged"))
    uid = account(app, "logged")["userID"]
    client_for(app, "fx_admin").post("/edit/person/XGMA", data={
        "csrf": CSRF, "firstname": "Gina", "lastname": "Fixture", "sex": "F",
        "changedate": str(sql(app, "SELECT changedate FROM people WHERE personID = 'XGMA'")[0]["changedate"])})
    html = text(c.get("/admin/activity"))
    assert f'href="/admin/users/{uid}">account {uid}</a>' in html and 'href="/person/XGMA">XGMA</a>' in html
    html = text(c.get("/admin/activity?user=fx_siteadmin&record=" + str(uid)))
    assert f"account {uid}" in html and "XGMA" not in html and "1 change" in html
    assert "Nothing recorded for that filter" in text(c.get("/admin/activity?record=nosuchrecord"))
    monkeypatch.setattr(admin_module, "PER_PAGE", 1)
    paged = text(c.get("/admin/activity?page=2"))
    assert "Newer" in paged and "Older" in paged


def test_site_settings(app):
    c = site_admin(app)
    html = text(c.get("/admin/settings"))
    assert 'placeholder="Our Family History"' in html and "Site settings" in text(c.get("/admin/"))
    r = c.post("/admin/settings", data={"csrf": CSRF, "site_title": "Hollis Family Tree", "contact": "Ask Ruth"})
    assert r.status_code == 302
    assert "Hollis Family Tree" in text(c.get("/search")) and "Our Family History" not in text(c.get("/search"))
    assert "Ask Ruth" in text(app.test_client().get("/"))  # the public welcome page too
    assert sql(app, "SELECT action FROM app_audit WHERE tbl = 'app_settings' ORDER BY id DESC")[0]["action"] \
        == "edit settings"
    assert "longer than 60" in text(c.post("/admin/settings", data={"csrf": CSRF, "site_title": "x" * 61}))
    c.post("/admin/settings", data={"csrf": CSRF})  # all blank: back to the theme's text
    assert "Our Family History" in text(c.get("/search"))
    assert not sql(app, "SELECT * FROM app_settings WHERE name IN ('site_title', 'contact')")


def test_hero_art(app):
    c = site_admin(app)
    assert '<option value="fan.svg" selected>Fan chart' in text(c.get("/admin/settings"))
    assert "--hero-art: url('/static/fan.svg')" in text(app.test_client().get("/"))  # the default, even logged out
    assert c.post("/admin/settings", data={"csrf": CSRF, "hero_art": "../secret.svg"}).status_code == 400
    c.post("/admin/settings", data={"csrf": CSRF, "hero_art": "pedigree.svg"})
    assert "--hero-art: url('/static/pedigree.svg')" in text(app.test_client().get("/"))
    assert '<option value="pedigree.svg" selected>' in text(c.get("/admin/settings"))
    c.post("/admin/settings", data={"csrf": CSRF})  # none sent: kept
    assert sql(app, "SELECT value FROM app_settings WHERE name = 'hero_art'")[0]["value"] == "pedigree.svg"
    c.post("/admin/settings", data={"csrf": CSRF, "hero_art": "fan.svg"})  # the default: nothing stored
    assert not sql(app, "SELECT * FROM app_settings WHERE name = 'hero_art'")
    for f in settings.HERO_ART:
        assert app.test_client().get(f"/static/{f}").status_code == 200


def test_tree_start_setting(app):
    c = site_admin(app)
    assert "Start the Tree page at" in text(c.get("/admin/settings"))
    assert "There is no person NOPE" in text(c.post("/admin/settings", data={"csrf": CSRF, "tree_start": "nope"}))
    c.post("/admin/settings", data={"csrf": CSRF, "tree_start": "xhusb"})
    assert sql(app, "SELECT value FROM app_settings WHERE name = 'tree_start'")[0]["value"] == "XHUSB"
    assert 'value="XHUSB"' in text(c.get("/admin/settings"))
    c.post("/admin/settings", data={"csrf": CSRF})  # empty: back to the "Whose tree?" page
    assert not sql(app, "SELECT * FROM app_settings WHERE name = 'tree_start'")


def test_site_settings_without_table(app, monkeypatch):
    def broken(*_):
        raise pymysql.err.ProgrammingError(1146, "Table 'app_settings' doesn't exist")
    monkeypatch.setattr(settings, "query", broken)
    with app.app_context():
        assert settings.site_settings() == {}


def test_default_theme(app):
    c, guest = site_admin(app), client_for(app, "fx_guest")
    assert '<option value="default" selected>' in text(c.get("/admin/settings"))
    assert c.post("/admin/settings", data={"csrf": CSRF, "theme": "nope"}).status_code == 400
    c.post("/admin/settings", data={"csrf": CSRF, "theme": "evergreen"})
    assert sql(app, "SELECT value FROM app_settings WHERE name = 'theme'")[0]["value"] == "evergreen"
    assert 'href="/themes/evergreen.css"' in text(guest.get("/search"))  # people who haven't chosen
    assert 'href="/themes/evergreen.css"' in text(app.test_client().get("/login"))  # and the login page
    page = text(guest.get("/account/"))
    assert 'value="" data-css="/themes/evergreen.css" checked' in page and 'value="evergreen"' not in page
    assert 'value="default"' in page  # the KITH_THEME one is now just another choice
    c.post("/admin/settings", data={"csrf": CSRF})  # no theme sent: kept
    assert 'href="/themes/evergreen.css"' in text(guest.get("/search"))
    sql(app, "UPDATE app_settings SET value = 'removed' WHERE name = 'theme'")  # its theme was deleted
    assert 'href="/theme/theme.css"' in text(guest.get("/search"))
    c.post("/admin/settings", data={"csrf": CSRF, "theme": "default"})  # the built-in default: nothing stored
    assert not sql(app, "SELECT * FROM app_settings WHERE name = 'theme'")


def test_visits_are_logged_and_shown(app):
    from app.admin import ago
    uid = sql(app, "SELECT userID FROM users WHERE username = 'fx_nonotes'")[0]["userID"]
    sql(app, "DELETE FROM app_visits WHERE userID = %s", (uid,))
    c = client_for(app, "fx_nonotes")
    c.get("/static/app.css")  # stylesheets and pictures aren't a visit
    assert not sql(app, "SELECT 1 FROM app_visits WHERE userID = %s", (uid,))
    c.get("/surnames")
    c.get("/places")  # within a minute: not written again
    visits = sql(app, "SELECT * FROM app_visits WHERE userID = %s", (uid,))
    assert len(visits) == 1 and visits[0]["username"] == "fx_nonotes" and visits[0]["ip"] == "127.0.0.1"
    # A minute on, the same visit carries on; half an hour away starts a new one.
    with c.session_transaction() as s:
        s["seen"] = 0
    sql(app, "UPDATE app_visits SET last_seen = NOW() - INTERVAL 5 MINUTE WHERE userID = %s", (uid,))
    c.get("/surnames")
    assert len(sql(app, "SELECT * FROM app_visits WHERE userID = %s AND last_seen > NOW() - INTERVAL 1 MINUTE",
                   (uid,))) == 1
    with c.session_transaction() as s:
        s["seen"] = 0
    sql(app, "UPDATE app_visits SET last_seen = NOW() - INTERVAL 31 MINUTE WHERE userID = %s", (uid,))
    c.get("/surnames")
    assert len(sql(app, "SELECT * FROM app_visits WHERE userID = %s", (uid,))) == 2
    home = text(site_admin(app).get("/admin/"))
    assert "Recent visits" in home and f'/admin/users/{uid}">fx_nonotes</a>' in home and "just now" in home
    users = text(site_admin(app).get("/admin/users"))
    assert "Last seen" in users and "just now" in users
    now = datetime.datetime(2026, 10, 27, 12, 0)
    assert [ago(now - datetime.timedelta(seconds=s), now) for s in (30, 600, 3600, 7300, 90000, 300000)] == [
        "just now", "10 minutes ago", "1 hour ago", "2 hours ago", "yesterday", "24 Oct 2026"]
    assert ago("0000-00-00", now) == ""


def test_warns_about_insecure_cookies_over_https(app, monkeypatch):
    c = client_for(app, "fx_siteadmin")
    warning = "KITH_SECURE_COOKIES=1"
    assert warning not in text(c.get("/admin/"))  # plain http: nothing to say
    assert warning in text(c.get("/admin/", headers={"X-Forwarded-Proto": "https"}))
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)
    page = text(c.get("/admin/", headers={"X-Forwarded-Proto": "https"}))
    assert "Waiting on someone" in page and warning not in page

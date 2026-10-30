"""Asking for an account, confirming it, forgotten and changed passwords, invitations, and the mail they send."""
import re
import smtplib
import time

import pymysql
import pytest
from conftest import CSRF, client_for, sql
from itsdangerous import URLSafeTimedSerializer
from test_auth import login

from app import account, mail
from app.auth import tng_md5


class FakeSMTP:
    outbox, fail = [], False

    def __init__(self, host, port, timeout):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad password")

    def send_message(self, msg):
        FakeSMTP.outbox.append(msg)


@pytest.fixture
def outbox(app, monkeypatch, tmp_path):
    """Email switched on and caught; rate limits counted afresh in a temp folder."""
    FakeSMTP.outbox, FakeSMTP.fail = [], False
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setitem(app.config, "SMTP_USER", "site@example.com")
    monkeypatch.setitem(app.config, "SITE_URL", "https://tree.example")
    monkeypatch.setitem(app.config, "NOTIFY_EMAIL", "")
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    return FakeSMTP.outbox


def text(r):
    return re.sub(r"(\.css|\.js|\.svg)\?v=\d+", r"\1", r.get_data(as_text=True))  # file versions on CSS/JS links vary


def visitor(app):
    """A logged-out browser with a CSRF token, as the account pages see one."""
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = CSRF
    return c


def stamp(app, age=10):
    return URLSafeTimedSerializer(app.secret_key, salt="account-request").dumps(int(time.time()) - age)


def ask(app, c, **v):
    data = {"csrf": CSRF, "stamp": stamp(app), "realname": "Ann Cousin", "email": "ann@example.com",
            "relation": "Granddaughter of Don Hollis", "username": "anncousin", "password": "goodpass1",
            "password2": "goodpass1", **v}
    return c.post("/account/request", data=data)


def link_in(msg, path):
    return re.search(rf"https://tree\.example(/account/{path}/\S+)", msg.get_content()).group(1)


def test_send(app, outbox):
    with app.app_context():
        assert mail.send("a@example.com", "Hi", "Body") is True
        assert outbox[-1]["To"] == "a@example.com" and outbox[-1]["From"] == "site@example.com"
        FakeSMTP.fail = True
        assert mail.send("a@example.com", "Hi", "Body") is False
        app.config["SMTP_USER"] = ""
        assert mail.send("a@example.com", "Hi", "Body") is False


def test_requests_closed_without_mail(app):
    assert "Account requests are closed" in text(visitor(app).get("/account/request"))


def test_request_confirm_and_approve(app, outbox, monkeypatch):
    monkeypatch.setitem(app.config, "NOTIFY_EMAIL", "admin@example.com")
    sql(app, "DELETE FROM app_settings WHERE name = 'last_request_notice'") if sql(
        app, "SHOW TABLES LIKE 'app_settings'") else None
    c = visitor(app)
    assert "How you&#39;re related" in text(c.get("/account/request"))
    assert "Check your email" in text(ask(app, c, username="annc1", email="annc1@example.com"))
    assert not sql(app, "SELECT * FROM users WHERE username = 'annc1'")  # nothing until confirmed
    msg = outbox[-1]
    assert msg["To"] == "annc1@example.com" and "Granddaughter" not in msg.get_content()  # no visitor text sent
    html = text(c.get(link_in(msg, "confirm")))
    assert "confirmed" in html
    u = sql(app, "SELECT * FROM users WHERE username = 'annc1'")[0]
    assert u["allow_living"] == -1 and u["notes"] == "Granddaughter of Don Hollis"
    assert u["password"] == tng_md5("goodpass1")
    assert outbox[-1]["To"] == "admin@example.com" and "/admin/users?show=pending" in outbox[-1].get_content()
    assert "Link expired" in text(c.get(link_in(msg, "confirm")))  # one use only
    ask(app, visitor(app), username="annc2", email="annc2@example.com")
    visitor(app).get(link_in(outbox[-1], "confirm"))
    assert outbox[-1]["To"] == "annc2@example.com"  # the admin notice goes out at most once a day
    admin = client_for(app, "fx_siteadmin")
    admin.post(f"/admin/users/{u['userID']}", data={"csrf": CSRF, "username": "annc1", "allow_living": "1",
                                                     "role": "guest", "email": "annc1@example.com"})
    assert outbox[-1]["To"] == "annc1@example.com" and "approved" in outbox[-1].get_content()
    assert login(app.test_client(), "annc1", "goodpass1").status_code == 302


def test_bots_get_nowhere(app, outbox):
    for v in ({"website": "http://spam.example"}, {"stamp": stamp(app, age=0)}, {"stamp": "forged"}):
        assert "Check your email" in text(ask(app, visitor(app), username="botty", email="bot@example.com", **v))
    assert not outbox and not sql(app, "SELECT * FROM app_tokens WHERE email = 'bot@example.com'")


def test_request_validation(app, outbox):
    c = visitor(app)
    html = text(ask(app, c, realname="", email="nope", username="fx_guest", password="short", password2="short"))
    for message in ("Fill in your full name", "real email address", "username is taken", "at least 8"):
        assert message in html, message
    assert "don&#39;t match" in text(ask(app, c, password2="different1"))
    assert "longer than 500" in text(ask(app, c, relation="x" * 501))
    assert "Too many requests from here" in text(ask(app, c))  # the fourth try within the hour
    assert not outbox


def test_one_email_per_address_and_existing_accounts(app, outbox):
    sql(app, "UPDATE users SET email = 'taken@example.com' WHERE username = 'fx_editor'")
    ask(app, visitor(app), username="newname1", email="taken@example.com")
    assert "already has one" in outbox[-1].get_content() and "/account/forgot" in outbox[-1].get_content()
    ask(app, visitor(app), username="once1", email="once@example.com")
    ask(app, visitor(app), username="once2", email="once@example.com")
    assert [m["To"] for m in outbox].count("once@example.com") == 1


def test_confirm_without_admin_notice(app, outbox, monkeypatch):
    ask(app, visitor(app), username="quiet1", email="quiet1@example.com")
    visitor(app).get(link_in(outbox[-1], "confirm"))  # NOTIFY_EMAIL empty: no notice
    assert len(outbox) == 1 and sql(app, "SELECT * FROM users WHERE username = 'quiet1'")
    monkeypatch.setitem(app.config, "NOTIFY_EMAIL", "admin@example.com")
    sql(app, "DELETE FROM app_settings WHERE name = 'last_request_notice'")
    ask(app, visitor(app), username="quiet2", email="quiet2@example.com")
    FakeSMTP.fail = True  # the notice fails: it's tried again on the next confirmation
    with app.test_request_context():
        account.notify_admins()
    assert not sql(app, "SELECT * FROM app_settings WHERE name = 'last_request_notice'")


def test_confirm_when_taken_meanwhile(app, outbox):
    ask(app, visitor(app), username="racer", email="racer@example.com")
    url = link_in(outbox[-1], "confirm")
    client_for(app, "fx_siteadmin").post("/admin/users/new", data={
        "csrf": CSRF, "username": "racer", "password": "longenough", "role": "guest"})
    assert "Already taken" in text(visitor(app).get(url))
    assert "Link expired" in text(visitor(app).get("/account/confirm/nonsense"))


def test_forgot_and_reset(app, outbox):
    sql(app, "UPDATE users SET email = 'nonotes@example.com' WHERE username = 'fx_nonotes'")
    c = visitor(app)
    assert "Forgot your password?" in text(c.get("/account/forgot"))
    for who in ("nobody-here", ""):
        assert "If that matches" in text(c.post("/account/forgot", data={"csrf": CSRF, "who": who}))
    assert not outbox
    c.post("/account/forgot", data={"csrf": CSRF, "who": "NONOTES@example.com"})
    url = link_in(outbox[-1], "reset")  # the site's own address, not the request's
    c.post("/account/forgot", data={"csrf": CSRF, "who": "fx_nonotes"})
    assert len(outbox) == 1  # asked twice within minutes: one email
    assert "Choose a password" in text(c.get(url))
    assert "don&#39;t match" in text(c.post(url, data={"csrf": CSRF, "password": "newpass12", "password2": "x"}))
    assert c.post(url, data={"csrf": CSRF, "password": "newpass12", "password2": "newpass12"}).status_code == 302
    assert login(app.test_client(), "fx_nonotes", "newpass12").status_code == 302
    assert "Link expired" in text(c.get(url))
    c.post("/account/forgot", data={"csrf": CSRF, "who": "x"})
    assert "Too many tries" in text(c.post("/account/forgot", data={"csrf": CSRF, "who": "x"}))  # sixth this hour


def test_change_password(app):
    c = client_for(app, "fx_adder")
    sql(app, "UPDATE users SET password = %s WHERE username = 'fx_adder'", (tng_md5("oldpass12"),))
    assert "Change your password" in text(c.get("/account/password"))
    bad = text(c.post("/account/password", data={"csrf": CSRF, "current": "wrong", "password": "newpass12",
                                                 "password2": "newpass12"}))
    assert "current password isn" in bad
    r = c.post("/account/password", data={"csrf": CSRF, "current": "oldpass12", "password": "newpass12",
                                          "password2": "newpass12"})
    assert r.status_code == 302
    assert sql(app, "SELECT password FROM users WHERE username = 'fx_adder'")[0]["password"] == tng_md5("newpass12")
    assert sql(app, "SELECT action FROM app_audit WHERE action = 'change password'")
    assert app.test_client().get("/account/password").status_code == 302  # logged out: to the login page


def test_invitations_and_reset_links(app, outbox):
    admin = client_for(app, "fx_siteadmin")
    form = {"csrf": CSRF, "role": "guest", "invite": "1", "password": ""}
    assert "Enter their email address" in text(admin.post("/admin/users/new", data={**form, "username": "inv0"}))
    assert "Email them an invitation" in text(admin.get("/admin/users/new"))
    r = admin.post("/admin/users/new", data={**form, "username": "invited1", "email": "inv1@example.com"})
    assert r.status_code == 302 and "invited" in outbox[-1]["Subject"]
    url = link_in(outbox[-1], "reset")
    visitor(app).post(url, data={"csrf": CSRF, "password": "chosen123", "password2": "chosen123"})
    assert login(app.test_client(), "invited1", "chosen123").status_code == 302
    uid = sql(app, "SELECT userID FROM users WHERE username = 'invited1'")[0]["userID"]
    assert "Email invited1 a link" in text(admin.get(f"/admin/users/{uid}"))
    admin.post(f"/admin/users/{uid}/send-link", data={"csrf": CSRF})
    assert "Set a new password" in outbox[-1]["Subject"]
    FakeSMTP.fail = True
    assert "didn" in text(admin.post(f"/admin/users/{uid}/send-link", data={"csrf": CSRF}, follow_redirects=True))
    admin.post("/admin/users/new", data={**form, "username": "invited2", "email": "inv2@example.com"})
    assert sql(app, "SELECT * FROM users WHERE username = 'invited2'")  # added even though the email failed
    nomail = sql(app, "SELECT userID FROM users WHERE username = 'fx_guest'")[0]["userID"]
    assert admin.post(f"/admin/users/{nomail}/send-link", data={"csrf": CSRF}).status_code == 400


def test_invite_needs_mail(app):
    html = text(client_for(app, "fx_siteadmin").post("/admin/users/new", data={
        "csrf": CSRF, "role": "guest", "invite": "1", "username": "inv3", "email": "inv3@example.com"}))
    assert "Email isn&#39;t set up" in html


def test_login_page_links(app):
    html = text(app.test_client().get("/login"))
    assert "/account/forgot" in html and "/account/request" in html


def test_strong_passwords_after_cutover(app, outbox, monkeypatch):
    """With KITH_STRONG_PASSWORDS on, new passwords get a salted PBKDF2 hash and old md5 ones are upgraded
    the next time their owner logs in."""
    monkeypatch.setitem(app.config, "STRONG_PASSWORDS", True)
    sql(app, "UPDATE users SET password = %s, password_type = 'md5' WHERE username = 'fx_editor'",
        (tng_md5("oldmd5pass"),))
    assert login(app.test_client(), "fx_editor", "oldmd5pass").status_code == 302
    u = sql(app, "SELECT password, password_type FROM users WHERE username = 'fx_editor'")[0]
    assert u["password_type"] == "pbkdf2" and u["password"].startswith("pbkdf2:sha256:") and len(u["password"]) <= 128
    assert login(app.test_client(), "fx_editor", "oldmd5pass").status_code == 302  # the new hash works
    assert login(app.test_client(), "fx_editor", "wrongpass").status_code == 200
    ask(app, visitor(app), username="strong1", email="strong1@example.com")
    visitor(app).get(link_in(outbox[-1], "confirm"))
    assert sql(app, "SELECT password_type FROM users WHERE username = 'strong1'")[0]["password_type"] == "pbkdf2"


def test_unknown_hash_type_never_matches(app):
    from app.auth import check_password
    assert not check_password("x", {"password_type": "", "password": tng_md5("x")})


def test_secure_cookie_setting(monkeypatch):
    from app import create_app
    monkeypatch.setenv("KITH_SECURE_COOKIES", "1")
    assert create_app({"TESTING": True}).config["SESSION_COOKIE_SECURE"] is True


def test_choose_own_theme(app, monkeypatch):
    c = client_for(app, "fx_guest")
    page = text(c.get("/account/"))
    assert "Your account" in page and "Evergreen" in page and "Large print" in page and 'class="swatches"' in page
    assert 'value="" data-css="/theme/theme.css" checked' in page  # nothing picked: the site's own
    assert 'value="default"' not in page  # the site theme is offered once, as the site default
    assert 'href="/theme/theme.css"' in page
    assert c.post("/account/", data={"csrf": CSRF, "theme": "evergreen"}).status_code == 302
    page = text(c.get("/account/"))
    assert 'id="theme-css" href="/themes/evergreen.css"' in page and 'value="evergreen" data-css' in page
    assert 'value="evergreen" data-css="/themes/evergreen.css" checked' in page
    assert 'href="/themes/evergreen.css"' not in text(client_for(app, "fx_admin").get("/account/"))  # only for them
    assert c.post("/account/", data={"csrf": CSRF, "theme": "nope"}).status_code == 400
    c.post("/account/", data={"csrf": CSRF, "theme": ""})
    assert 'id="theme-css" href="/theme/theme.css"' in text(c.get("/account/"))
    # A picked theme that has since been removed falls back to the site's.
    sql(app, "UPDATE app_user_prefs SET theme = 'removed' WHERE userID ="
             " (SELECT userID FROM users WHERE username = 'fx_guest')")
    assert 'id="theme-css" href="/theme/theme.css"' in text(c.get("/account/"))
    # Before anyone has picked a theme the table doesn't exist; pages still render with the site's.
    def missing(*a):
        raise pymysql.err.ProgrammingError(1146, "Table doesn't exist")
    monkeypatch.setattr(account, "query", missing)
    assert 'id="theme-css" href="/theme/theme.css"' in text(c.get("/surnames"))


def test_light_or_dark(app):
    c = client_for(app, "fx_guest")
    page = text(c.get("/account/"))
    assert 'data-mode=""' in page and 'name="mode" value="" checked' in page  # the device's, until picked
    assert c.post("/account/", data={"csrf": CSRF, "mode": "dark"}).status_code == 302
    page = text(c.get("/account/"))
    assert 'data-mode="dark"' in page and 'name="mode" value="dark" checked' in page
    assert 'data-mode=""' in text(client_for(app, "fx_admin").get("/search"))  # only for them
    assert c.post("/account/", data={"csrf": CSRF, "mode": "purple"}).status_code == 400
    sql(app, "UPDATE app_user_prefs SET mode = 'sepia' WHERE userID = (SELECT userID FROM users WHERE username = 'fx_guest')")
    assert 'data-mode=""' in text(c.get("/search"))  # a stored value it doesn't know: the device's
    c.post("/account/", data={"csrf": CSRF, "mode": ""})
    assert not sql(app, "SELECT mode FROM app_user_prefs WHERE userID = (SELECT userID FROM users"
                        " WHERE username = 'fx_guest')")[0]["mode"]


def test_theme_stylesheets(plain_app):
    c = plain_app.test_client()
    for name in ("default", "heritage", "evergreen", "contrast", "midnight"):
        assert b"--accent" in c.get(f"/themes/{name}.css").data
    assert c.get("/themes/nope.css").status_code == 404
    assert [m["name"] for m in plain_app.extensions["themes"].values()] == ["Evergreen", "Heritage", "Large print",
                                                                             "Midnight", "Modern"]


def test_header_marks_current_page_and_has_account_menu(app):
    page = text(client_for(app, "fx_guest").get("/surnames"))
    assert re.search(r'href="/surnames" aria-current="page"><svg[^>]*>.*?</svg>Surnames', page)
    assert not re.search(r'href="/search" aria-current', page)
    assert "Account menu for fx_guest" in page and "Log out" in page and 'href="/account/"' in page


def test_upload_and_remove_own_picture(app, monkeypatch):
    import io

    from PIL import Image
    c = client_for(app, "fx_guest")
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(buf, "JPEG")
    buf.seek(0)
    assert c.post("/account/", data={"csrf": CSRF, "avatar": (buf, "me.jpg")}).status_code == 302
    page = text(c.get("/surnames"))
    assert re.search(r'<img class="avatar sm" src="/account/avatar\?v=\d+"', page)
    with Image.open(io.BytesIO(c.get("/account/avatar").data)) as im:
        assert im.size == (256, 256)  # cropped square
    assert client_for(app, "fx_admin").get("/account/avatar").status_code == 404  # only your own
    c.post("/account/", data={"csrf": CSRF, "avatar": (io.BytesIO(b"nope"), "x.png")})
    assert "a readable image" in text(c.get("/account/"))
    monkeypatch.setattr(account, "AVATAR_MAX", 10)
    c.post("/account/", data={"csrf": CSRF, "avatar": (io.BytesIO(b"x" * 11), "big.png")})
    assert "larger than 10MB" in text(c.get("/account/"))
    c.post("/account/", data={"csrf": CSRF, "remove_avatar": "1"})
    assert c.get("/account/avatar").status_code == 404
    assert '<span class="avatar sm">' in text(c.get("/surnames"))


def test_email_links_ignore_the_request_host(app, monkeypatch):
    """A reset link built from a forged Host header would hand the token to that host."""
    monkeypatch.setitem(app.config, "SITE_URL", "https://tree.example/")
    with app.test_request_context(base_url="https://evil.example"):
        assert account.link("account.reset", token="abc") == "https://tree.example/account/reset/abc"

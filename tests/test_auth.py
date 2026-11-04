import hashlib

import pytest

from app import auth, create_app

from conftest import CSRF, client_for, sql

USER = {"userID": 1, "username": "ann", "password_type": "md5", "password": hashlib.md5(b"right").hexdigest(),
        "disabled": 0, "allow_living": 1, "allow_private": 1, "allow_private_notes": 1, "personID": ""}


def login(client, username, password, **extra):
    client.get("/login")
    with client.session_transaction() as s:
        token = s["csrf"]
    return client.post("/login", data={"username": username, "password": password, "csrf": token, **extra})


@pytest.fixture
def fake_users(monkeypatch, tmp_path):
    """Login against an in-memory user, throttle state in a temp dir."""
    monkeypatch.setenv("KITH_INSTANCE", str(tmp_path))
    users = {"ann": USER}
    monkeypatch.setattr(auth, "query", lambda sql, args: [] if "app_visits" in sql else [
        u for u in users.values() if args[0] in (u["username"], u["userID"])])
    monkeypatch.setattr(auth, "execute", lambda sql, args=None: None)  # last-login stamp, visits; no real DB here
    return users


def test_tng_password_escaping():
    # TNG hashes the SQL-escaped password, so quotes and backslashes must match its quirk.
    user = {"password_type": "md5", "password": hashlib.md5(b"it\\'s\\\\ok\\0\\n\\r\\Z\\\"").hexdigest()}
    assert auth.check_password("it's\\ok\0\n\r\x1a\"", user)
    assert not auth.check_password("its ok", user)
    assert not auth.check_password("x", {"password_type": "", "password": "x"})


def test_inactive_and_disabled_users_cannot_log_in():
    assert auth.can_log_in({"disabled": 0, "allow_living": 0})
    assert not auth.can_log_in({"disabled": 1, "allow_living": 1})
    assert not auth.can_log_in({"disabled": 0, "allow_living": -1})


def test_open_redirect_blocked(plain_app):
    with plain_app.test_request_context():
        assert auth._safe_next("//evil.example") == "/"
        assert auth._safe_next("/\\evil.example") == "/" and auth._safe_next("/\t/evil.example") == "/"
        assert auth._safe_next("https://evil.example") == "/"
        assert auth._safe_next("") == "/"
        assert auth._safe_next("/person/I1") == "/person/I1"


def test_login_page_and_redirect_when_logged_out(plain_app):
    c = plain_app.test_client()
    r = c.get("/person/I1")
    assert r.status_code == 302 and "/login?next=/person/I1" in r.location
    assert 'name="password"' in c.get("/login").get_data(as_text=True)


def test_login_success_goes_to_next(fake_users):
    c = create_app({"TESTING": True}).test_client()
    r = login(c, "ann", "right", next="/surnames")
    assert r.status_code == 302 and r.location == "/surnames"
    assert "Expires=" in r.headers["Set-Cookie"]  # kept when a phone closes the browser
    with c.session_transaction() as s:
        assert s["uid"] == 1


def test_login_failures(fake_users):
    c = create_app({"TESTING": True}).test_client()
    assert "Login failed" in login(c, "ann", "wrong").get_data(as_text=True)
    assert "Login failed" in login(c, "nobody", "right").get_data(as_text=True)
    fake_users["ann"] = {**USER, "disabled": 1}
    assert "Login failed" in login(c, "ann", "right").get_data(as_text=True)


def test_login_throttle_survives_restart(fake_users):
    make = lambda: create_app({"TESTING": True}).test_client()
    client = make()
    for _ in range(auth.LIMITS["user"]):
        assert "Login failed" in login(client, "ann", "wrong").get_data(as_text=True)
    # A fresh app (a restart) still refuses, even with the right password.
    r = login(make(), "ann", "right")
    assert r.status_code == 200 and "Too many" in r.get_data(as_text=True)
    # Another username from the same IP isn't locked by ann's failures.
    fake_users["other"] = {**USER, "userID": 2, "username": "other"}
    assert login(make(), "other", "right").status_code == 302


def test_success_clears_failures(fake_users):
    c = create_app({"TESTING": True}).test_client()
    for _ in range(auth.LIMITS["user"] - 1):
        login(c, "ann", "wrong")
    assert login(c, "ann", "right").status_code == 302
    for _ in range(auth.LIMITS["user"] - 1):
        login(c, "ann", "wrong")
    assert login(c, "ann", "right").status_code == 302  # the earlier failures were cleared


def test_per_ip_limit_across_usernames(fake_users):
    c = create_app({"TESTING": True}).test_client()
    for i in range(auth.LIMITS["ip"]):
        login(c, f"guess{i}", "wrong")
    assert "Too many" in login(c, "ann", "right").get_data(as_text=True)


def test_trusted_proxy_uses_forwarded_ip(fake_users):
    c = create_app({"TESTING": True, "TRUSTED_PROXIES": 1}).test_client()
    for i in range(auth.LIMITS["ip"]):
        c.post("/login", data={"username": f"g{i}", "password": "x", "csrf": csrf(c)},
               headers={"X-Forwarded-For": "203.0.113.9"})
    assert "Too many" in c.post("/login", data={"username": "ann", "password": "right", "csrf": csrf(c)},
                                headers={"X-Forwarded-For": "203.0.113.9"}).get_data(as_text=True)
    # A different client behind the same proxy isn't blocked by 203.0.113.9's failures.
    r = c.post("/login", data={"username": "ann", "password": "right", "csrf": csrf(c)},
               headers={"X-Forwarded-For": "198.51.100.7"})
    assert r.status_code == 302


def csrf(client):
    client.get("/login")
    with client.session_transaction() as s:
        return s["csrf"]


def test_post_without_csrf_token_rejected(fake_users):
    client = create_app({"TESTING": True}).test_client()
    assert client.post("/login", data={"username": "x", "password": "y"}).status_code == 400
    assert client.post("/login", data={"username": "x", "password": "y", "csrf": "forged"}).status_code == 400
    assert client.post("/logout").status_code == 400


def test_logout(fake_users):
    c = create_app({"TESTING": True}).test_client()
    login(c, "ann", "right")
    r = c.post("/logout", data={"csrf": csrf(c)})
    assert r.status_code == 302 and "/login" in r.location
    with c.session_transaction() as s:
        assert "uid" not in s


def test_disabled_user_session_is_dropped(app):
    # A user disabled while logged in is logged out on their next request.
    c = client_for(app, "fx_editor")
    with c.session_transaction() as s:
        uid = s["uid"]
    sql(app, "UPDATE users SET disabled = 1 WHERE userID = %s", (uid,))
    try:
        assert c.get("/surnames").status_code == 302
        with c.session_transaction() as s:
            assert "uid" not in s
    finally:
        sql(app, "UPDATE users SET disabled = 0 WHERE userID = %s", (uid,))


def test_secret_key_persists(tmp_path, monkeypatch):
    monkeypatch.delenv("KITH_SECRET_KEY", raising=False)
    monkeypatch.setenv("KITH_INSTANCE", str(tmp_path))
    assert create_app().secret_key == create_app().secret_key
    assert oct((tmp_path / "secret_key").stat().st_mode)[-3:] == "600"


def test_secret_key_from_env(monkeypatch):
    monkeypatch.setenv("KITH_SECRET_KEY", "from-env")
    assert create_app().secret_key == "from-env"


def test_old_setting_names_still_work(monkeypatch):
    monkeypatch.delenv("KITH_SECRET_KEY", raising=False)
    monkeypatch.setenv("GENEALOGY_SECRET_KEY", "old-name")
    assert create_app().secret_key == "old-name"
    monkeypatch.setenv("KITH_SECRET_KEY", "new-name")  # the new name wins
    assert create_app().secret_key == "new-name"

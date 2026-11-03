"""The daily version check (mocked: tests never call kithgenealogy.org)."""
import io
import json
import urllib.error

import pytest
from conftest import CSRF, client_for, sql

from app import create_app, release, updates
from app.db import execute


@pytest.fixture
def project(app, monkeypatch):
    """The other end: answers with `latest` (or raises it), and records what each request sent."""
    sent, answer = [], {"latest": "99.0.0"}

    def urlopen(req, timeout):
        sent.append({"url": req.full_url, "method": req.get_method(), "body": json.loads(req.data),
                     "agent": req.get_header("User-agent")})
        if isinstance(answer["latest"], Exception):
            raise answer["latest"]
        return io.BytesIO(answer["latest"] if isinstance(answer["latest"], bytes) else
                          json.dumps({"latest": answer["latest"]}).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    with app.app_context():
        execute("DELETE FROM app_settings WHERE name IN ('update_check', 'site_id', 'latest_version')")
    yield sent, answer
    with app.app_context():
        execute("DELETE FROM app_settings WHERE name IN ('update_check', 'site_id', 'latest_version')")


def test_check_sends_only_id_and_version(app, project):
    sent, _ = project
    with app.app_context():
        assert updates.check() == "99.0.0" and updates.newer() == "99.0.0"
        sid = updates.site_id()
        updates.check()
    assert len(sid) == 32 and sent[0]["body"] == sent[1]["body"] == {"site": sid, "version": release()}
    assert sent[0]["url"] == "https://check.kithgenealogy.org/v1/check" and sent[0]["method"] == "POST"
    assert sent[0]["agent"] == f"kith/{release()}"


def test_bad_answers_are_ignored(app, project):
    _, answer = project
    with app.app_context():
        for bad in (urllib.error.URLError("down"), b"not json", b"[1, 2]", b'{"latest": 3}',
                    "<script>", "1.2", ""):
            answer["latest"] = bad
            assert updates.check() is None
        assert updates.newer() is None  # nothing was stored
        answer["latest"] = release()
        assert updates.check() == release() and updates.newer() is None  # up to date
        answer["latest"] = "0.0.1"
        assert updates.check() == "0.0.1" and updates.newer() is None  # older than this one


def test_turned_off(app, project):
    sent, _ = project
    c = client_for(app, "fx_siteadmin")
    assert 'name="update_check" value="1" checked' in c.get("/admin/settings").get_data(as_text=True)
    with app.app_context():
        updates.check()
    assert "Kith 99.0.0 is out" in c.get("/admin/").get_data(as_text=True)
    c.post("/admin/settings", data={"csrf": CSRF})  # box unticked
    assert sql(app, "SELECT value FROM app_settings WHERE name = 'update_check'")[0]["value"] == "off"
    assert 'name="update_check" value="1" checked' not in c.get("/admin/settings").get_data(as_text=True)
    assert "Kith 99.0.0 is out" not in c.get("/admin/").get_data(as_text=True)
    with app.app_context():
        assert updates.check() is None
    assert len(sent) == 1  # nothing sent while off
    assert "Turned off" in app.test_cli_runner().invoke(args=["updates", "check"]).output
    c.post("/admin/settings", data={"csrf": CSRF, "update_check": "1"})
    assert not sql(app, "SELECT * FROM app_settings WHERE name = 'update_check'")  # on is the default


def test_command(app, project):
    _, answer = project
    runner = app.test_cli_runner()
    assert "Kith 99.0.0 is available" in runner.invoke(args=["updates", "check"]).output
    answer["latest"] = release()
    assert "Up to date" in runner.invoke(args=["updates", "check"]).output
    answer["latest"] = urllib.error.URLError("down")
    assert "No answer" in runner.invoke(args=["updates", "check"]).output


def test_turned_off_by_the_server(app, project):
    sent, _ = project
    c = client_for(app, "fx_siteadmin")
    app.config["UPDATE_CHECK"] = False
    try:
        page = c.get("/admin/settings").get_data(as_text=True)
        assert "KITH_UPDATE_CHECK=0" in page and 'name="update_check" value="1" checked' not in page
        with app.app_context():
            assert updates.check() is None and updates.newer() is None
        assert "Turned off" in app.test_cli_runner().invoke(args=["updates", "check"]).output
        c.post("/admin/settings", data={"csrf": CSRF, "update_check": "1"})  # saving keeps the admin's choice
        assert not sql(app, "SELECT * FROM app_settings WHERE name = 'update_check'")
    finally:
        app.config["UPDATE_CHECK"] = True
    assert not sent
    assert create_app({"TESTING": True}).config["UPDATE_CHECK"] is True

import datetime
import html as htmllib
import io
import json
import urllib.error
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app import feedback
from conftest import client_for, sql


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def png(size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, "purple").save(buf, "PNG")
    buf.seek(0)
    return buf


def send(client, **fields):
    data = {"csrf": "t", "kind": "bug", "message": "Saving broke", "from": "/person/XKID", "screen": "390x844", **fields}
    return client.post("/feedback/new", data=data, content_type="multipart/form-data",
                       headers={"User-Agent": "TestBrowser/1.0"})


def latest(app):
    return sql(app, "SELECT * FROM app_feedback ORDER BY id DESC LIMIT 1")[0]


@pytest.fixture
def github(app, monkeypatch):
    """GitHub switched on, with the API faked; calls are recorded."""
    calls, replies = [], []
    monkeypatch.setitem(app.config, "GITHUB_REPO", "owner/repo")
    monkeypatch.setitem(app.config, "GITHUB_TOKEN", "tok")

    def fake(method, path, body):
        calls.append((method, path, body))
        reply = replies.pop(0) if replies else {"number": 42}
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(feedback, "github_request", fake)
    return calls, replies


def test_form_and_nav(app, admin, guest):
    html = text(guest.get("/feedback/new?from=/person/XKID"))
    assert 'name="from" value="/person/XKID"' in html and "Something's wrong" in html and "feedback.js" in html
    page = text(guest.get("/surnames"))
    assert page.count("/feedback/new?from=/surnames") == 2  # menu and the footer line (easy to find on phones)
    assert "My feedback" in text(guest.get("/surnames")) and "Reports" in text(admin.get("/surnames"))
    assert 'name="from" value="/"' in text(guest.get("/feedback/new?from=//evil.example"))


def test_send_saves_with_context(app):
    c = client_for(app, "fx_editor")
    p = sql(app, "SELECT * FROM people WHERE personID = 'XGMA'")[0]
    c.post("/edit/person/XGMA", data={"csrf": "t", "changedate": str(p["changedate"]), "firstname": p["firstname"],
                                      "lastname": p["lastname"]})  # gives them a recent edit
    r = send(c, message="  The date picker is confusing  ")
    assert r.status_code == 302 and r.location.endswith("/person/XKID")
    fb = latest(app)
    ctx = json.loads(fb["context"])
    assert (fb["kind"], fb["message"], fb["username"], fb["page"], fb["status"]) == \
        ("bug", "The date picker is confusing", "fx_editor", "/person/XKID", "new")
    assert ctx["record"] == "person XKID" and ctx["browser"] == "TestBrowser/1.0" and ctx["screen"] == "390x844"
    assert any("edit person XGMA" in e for e in ctx["recent_edits"])
    assert fb["github_issue"] is None and fb["github_error"] is None  # GitHub not set up here
    assert "Your feedback was sent" in text(c.get(r.location))


def test_page_without_record(app, guest):
    send(guest, **{"from": "/surnames"})
    assert "record" not in json.loads(latest(app)["context"])


@pytest.mark.parametrize("fields,message", [
    ({"kind": "rant"}, "Choose what kind"),
    ({"message": "   "}, "Please describe"),
    ({"message": "x" * 10001}, "under 10000 characters"),
])
def test_validation(app, guest, fields, message):
    before = latest(app)["id"] if sql(app, "SHOW TABLES LIKE 'app_feedback'") and sql(app, "SELECT 1 FROM app_feedback") else 0
    r = send(guest, **fields)
    assert r.status_code == 200 and message in text(r)
    assert not sql(app, "SELECT 1 FROM app_feedback WHERE id > %s", (before,))


def test_screenshots(app, guest, admin, monkeypatch):
    send(guest, screenshot=(png(), "shot.png"))
    fb = latest(app)
    assert fb["screenshot"].endswith(".png")
    with Image.open(io.BytesIO(guest.get(f"/feedback/{fb['id']}/screenshot").data)) as im:
        assert im.size == (40, 30)
    assert admin.get(f"/feedback/{fb['id']}/screenshot").status_code == 200
    assert client_for(app, "fx_adder").get(f"/feedback/{fb['id']}/screenshot").status_code == 404  # someone else's
    assert "isn't a readable image" in text(send(guest, screenshot=(io.BytesIO(b"nope"), "x.png")))
    monkeypatch.setattr(feedback, "SCREENSHOT_MAX", 10)
    assert "larger than 10MB" in text(send(guest, screenshot=(png(), "big.png")))
    send(guest, message="No picture")
    assert guest.get(f"/feedback/{latest(app)['id']}/screenshot").status_code == 404


def test_github_copy(app, admin, github):
    calls, _ = github
    admin.post("/edit/note/new", data={"csrf": "t", "for": "XGMA", "note": "an edit to report"})
    send(admin, kind="idea", message="Please add a dark mode\nfor the evenings", screenshot=(png(), "s.png"))
    fb = latest(app)
    assert fb["github_issue"] == 42
    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/issues") and body["labels"] == ["feedback", "idea"]
    assert body["title"] == "[Idea] Please add a dark mode for the evenings"
    for part in ("**From:** fx_admin", "**Page:** `/person/XKID`", "**Record:** person XKID", "**Screenshot:** on the site",
                 "**Their recent edits:** add note"):
        assert part in body["body"], part


def test_github_failure_and_retry(app, admin, guest, github):
    calls, replies = github
    replies.append(urllib.error.URLError("no network"))
    send(guest, message="Offline report")
    fb = latest(app)
    assert fb["github_issue"] is None and "no network" in fb["github_error"]
    html = text(admin.get(f"/feedback/{fb['id']}"))
    assert "Not copied" in html and "Retry" in html
    assert "GitHub failed" in text(admin.get("/feedback/"))
    admin.post(f"/feedback/{fb['id']}", data={"csrf": "t", "action": "retry"})
    fb = latest(app)
    assert fb["github_issue"] == 42 and fb["github_error"] is None
    assert "/repo/issues/42" in text(admin.get(f"/feedback/{fb['id']}"))
    n = len(calls)
    admin.post(f"/feedback/{fb['id']}", data={"csrf": "t", "action": "retry"})  # already copied: nothing to do
    assert len(calls) == n


def test_status_changes_close_and_reopen_issue(app, admin, guest, github):
    calls, replies = github
    send(guest, message="Close me later")
    fid = latest(app)["id"]
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "working", "reply": "Looking"})
    assert calls[-1][0] == "POST"  # still open: GitHub untouched
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "done", "reply": "Fixed in v2"})
    assert calls[-1] == ("PATCH", "/issues/42", {"state": "closed"})
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "wontdo", "reply": "Fixed in v2"})
    assert calls[-1] == ("PATCH", "/issues/42", {"state": "closed"}) and len([c for c in calls if c[0] == "PATCH"]) == 1
    replies.append(urllib.error.URLError("down"))
    r = admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "new", "reply": ""})
    assert "GitHub wasn't updated (open/closed): <urlopen error down>" in text(admin.get(r.location))
    fb = latest(app)
    assert fb["status"] == "new"
    mine = text(guest.get("/feedback/mine"))
    assert "Close me later" in mine and "New" in mine


def test_lists_and_access(app, admin, guest):
    send(guest, message="Guest sees own")
    fid = latest(app)["id"]
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "done", "reply": "All set"})
    assert "Guest sees own" not in text(admin.get("/feedback/"))  # open only by default
    assert "Guest sees own" in text(admin.get("/feedback/?status=done"))
    assert "Guest sees own" in text(admin.get("/feedback/?status=all"))
    assert "Guest sees own" not in text(admin.get("/feedback/?status=new"))
    mine = text(guest.get("/feedback/mine"))
    assert "Guest sees own" in mine and "Reply: All set" in mine and "From" not in mine
    assert guest.get("/feedback/").status_code == 403
    page = text(guest.get(f"/feedback/{fid}"))
    assert "All set" in page and "Update" not in page and "Details" not in page
    assert guest.post(f"/feedback/{fid}", data={"csrf": "t", "status": "new"}).status_code == 403
    assert client_for(app, "fx_adder").get(f"/feedback/{fid}").status_code == 403
    assert admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "lost"}).status_code == 400
    assert admin.get("/feedback/99999999").status_code == 404
    assert "Not set up" in text(admin.get(f"/feedback/{fid}"))
    assert "haven't sent any feedback" in text(client_for(app, "fx_nonotes").get("/feedback/mine"))
    assert "Nothing here." in text(admin.get("/feedback/?status=bogus"))


def test_github_request_builds_the_api_call(app, monkeypatch):
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"number": 7}'

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), auth=req.get_header("Authorization"),
                    body=json.loads(req.data), timeout=timeout)
        return Reply()
    monkeypatch.setattr(feedback.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setitem(app.config, "GITHUB_REPO", "owner/repo")
    monkeypatch.setitem(app.config, "GITHUB_TOKEN", "tok")
    with app.app_context():
        assert feedback.github_request("POST", "/issues", {"title": "t"}) == {"number": 7}
    assert seen == {"url": "https://api.github.com/repos/owner/repo/issues", "method": "POST",
                    "auth": "Bearer tok", "body": {"title": "t"}, "timeout": 8}


def test_author_comments_and_closes(app, admin, guest, github):
    calls, replies = github
    send(guest, message="Please add X")
    fid = latest(app)["id"]
    guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "  More detail  "})
    assert calls[-1] == ("POST", "/issues/42/comments", {"body": "**fx_guest:** More detail"})
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "On it"})
    guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "   "})  # nothing to add
    rows = sql(app, "SELECT username, body FROM app_feedback_comments WHERE feedback_id = %s ORDER BY id", (fid,))
    assert [(r["username"], r["body"]) for r in rows] == [("fx_guest", "More detail"), ("fx_admin", "On it")]
    page = text(guest.get(f"/feedback/{fid}"))
    assert page.index("More detail") < page.index("On it") and "Close: this is sorted" in page
    assert "Close: this is sorted" not in text(admin.get(f"/feedback/{fid}"))  # not the author

    replies.append(urllib.error.URLError("down"))  # GitHub trouble: the author isn't bothered with it
    r = guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "Again"})
    assert "GitHub" not in text(guest.get(r.location))
    replies.append(urllib.error.URLError("down"))
    r = admin.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "Seen"})
    assert "GitHub wasn't updated (comment)" in text(admin.get(r.location))

    assert admin.post(f"/feedback/{fid}", data={"csrf": "t", "action": "close"}).status_code == 400  # editors set status
    assert client_for(app, "fx_nonotes").post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment",
                                                                         "body": "Me too"}).status_code == 403
    guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "close"})
    assert latest(app)["status"] == "done" and calls[-1] == ("PATCH", "/issues/42", {"state": "closed"})
    n = len(calls)
    guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "close"})  # already closed: nothing happens
    assert len(calls) == n and "Close: this is sorted" not in text(guest.get(f"/feedback/{fid}"))

    send(guest, message="No GitHub copy")
    other = latest(app)["id"]
    sql(app, "UPDATE app_feedback SET github_issue = NULL WHERE id = %s", (other,))
    n = len(calls)
    guest.post(f"/feedback/{other}", data={"csrf": "t", "action": "comment", "body": "Local only"})
    assert len(calls) == n


def test_mine_tab_and_notifications(app, admin, guest, monkeypatch):
    from app import mail
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append((to, subject, body)) or True)
    monkeypatch.setitem(app.config, "NOTIFY_EMAIL", "notify@example.com")
    sql(app, "UPDATE users SET email = 'guest@example.com' WHERE username = 'fx_guest'")
    send(guest, message="Notify me")
    fid = latest(app)["id"]
    r = send(admin, message="Admin's own")
    assert "under Reports &gt; Mine" in admin.get(r.location).get_data(as_text=True)
    mine = text(admin.get("/feedback/?status=mine"))
    assert "Admin's own" in mine and "Notify me" not in mine

    guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "secret words"})
    to, subject, body = sent[-1]
    assert to == "notify@example.com" and subject == f"Feedback #{fid}: fx_guest added a comment"
    assert f"/feedback/{fid}" in body and "secret words" not in body  # fixed text and a link only
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "Reply"})
    assert sent[-1][0] == "guest@example.com"
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "working", "reply": "Soon"})
    assert sent[-1][:2] == ("guest@example.com", f"Feedback #{fid}: fx_admin updated it (now: Working on it)")
    n = len(sent)
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "status": "working", "reply": "Soon"})  # no change: no email
    assert len(sent) == n
    guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "close"})
    assert sent[-1][:2] == ("notify@example.com", f"Feedback #{fid}: fx_guest closed the report")

    monkeypatch.setitem(app.config, "NOTIFY_EMAIL", "")
    sql(app, "UPDATE users SET email = '' WHERE username = 'fx_guest'")
    n = len(sent)
    guest.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "Nobody to tell"})
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "No address"})
    assert len(sent) == n
    sql(app, "UPDATE app_feedback SET userID = -5 WHERE id = %s", (fid,))  # the author's account is gone
    admin.post(f"/feedback/{fid}", data={"csrf": "t", "action": "comment", "body": "Anyone?"})
    assert len(sent) == n


def test_times_shown_in_viewers_zone(app, admin, guest, github):
    send(guest, message="Zone check")
    fb = latest(app)
    sql(app, "UPDATE app_feedback SET created_at = '2026-07-01 12:00:00' WHERE id = %s", (fb["id"],))
    noon = datetime.datetime(2026, 7, 1, 12, 0)  # stored in the server's zone, whatever that is
    denver = noon.astimezone(ZoneInfo("America/Denver")).strftime("%-d %b %Y %H:%M")
    assert "1 Jul 2026 12:00" in text(admin.get(f"/feedback/{fb['id']}"))  # no cookie yet: server's zone
    admin.set_cookie("tz", "America/Denver")
    assert denver in text(admin.get(f"/feedback/{fb['id']}"))
    admin.set_cookie("tz", "../../etc/passwd")  # junk from the browser is ignored
    assert "1 Jul 2026 12:00" in text(admin.get(f"/feedback/{fb['id']}"))
    admin.delete_cookie("tz")

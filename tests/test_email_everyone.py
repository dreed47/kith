"""Admin > Email everyone, and the opt-out on Your account."""
import pytest
from conftest import CSRF, client_for, sql
from test_account import FakeSMTP

from app import mail


@pytest.fixture
def outbox(app, monkeypatch):
    FakeSMTP.outbox, FakeSMTP.fail = [], False
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setitem(app.config, "SMTP_USER", "site@example.com")
    monkeypatch.setitem(app.config, "SITE_URL", "https://tree.example")
    sql(app, "UPDATE users SET email = '', no_email = 0")
    for name in ("fx_siteadmin", "fx_guest", "fx_editor", "fx_adder"):
        sql(app, "UPDATE users SET email = %s WHERE username = %s", (f"{name}@example.com", name))
    sql(app, "UPDATE users SET no_email = 1 WHERE username = 'fx_adder'")  # opted out
    return FakeSMTP.outbox


def text(r):
    return r.get_data(as_text=True)


def to(outbox):
    return sorted(m["To"] for m in outbox)


def test_page_and_recipients(app, outbox):
    c = client_for(app, "fx_siteadmin")
    page = text(c.get("/admin/email"))
    assert "Send to 3 people" in page and "(fx_guest)" in page and "(fx_adder)" not in page
    assert "Email everyone" in text(c.get("/admin/"))
    assert client_for(app, "fx_admin").get("/admin/email").status_code == 403  # site admins only


def test_test_then_send(app, outbox):
    c = client_for(app, "fx_siteadmin")
    page = text(c.post("/admin/email", data={"csrf": CSRF, "subject": "Hello\r\nBcc: x@evil", "body": "News.",
                                             "test": "1"}))
    assert to(outbox) == ["fx_siteadmin@example.com"] and "Test sent" in page and "News." in page  # form kept
    assert outbox[0]["Subject"] == "Hello Bcc: x@evil"  # one line, never a header
    assert "untick the email box" in outbox[0].get_content()
    outbox.clear()
    r = c.post("/admin/email", data={"csrf": CSRF, "subject": "Hello", "body": "News."})
    assert r.status_code == 302
    assert to(outbox) == ["fx_editor@example.com", "fx_guest@example.com", "fx_siteadmin@example.com"]
    assert "Sent to 3 people" in text(c.get("/admin/"))
    row = sql(app, "SELECT after_json FROM app_audit WHERE action = 'email everyone' ORDER BY id DESC")[0]
    assert '"sent": 3' in row["after_json"] and "News." in row["after_json"]


def test_failures_and_errors(app, outbox, monkeypatch):
    c = client_for(app, "fx_siteadmin")
    page = text(c.post("/admin/email", data={"csrf": CSRF, "subject": "", "body": "x" * 10001}))
    assert "Write a subject." in page and "longer than 10,000" in page and not outbox
    FakeSMTP.fail = True
    c.post("/admin/email", data={"csrf": CSRF, "subject": "Hi", "body": "Hi"})
    assert "Sent to 0 people. Couldn&#39;t send to: fx_editor, fx_guest, fx_siteadmin." in text(c.get("/admin/"))
    page = text(c.post("/admin/email", data={"csrf": CSRF, "subject": "Hi", "body": "Hi", "test": "1"}))
    assert "test couldn" in page
    FakeSMTP.fail = False
    sql(app, "UPDATE users SET email = '' WHERE username = 'fx_siteadmin'")
    assert "no email address to send the test" in text(c.post("/admin/email", data={
        "csrf": CSRF, "subject": "Hi", "body": "Hi", "test": "1"}))
    monkeypatch.setitem(app.config, "SMTP_USER", "")
    page = text(c.post("/admin/email", data={"csrf": CSRF, "subject": "Hi", "body": "Hi"}))
    assert "isn&#39;t set up on this server" in page


def test_opt_out_on_account_page(app, outbox):
    c = client_for(app, "fx_guest")
    assert 'name="group_email" value="1" checked' in text(c.get("/account/"))
    c.post("/account/", data={"csrf": CSRF, "theme": ""})  # box unticked
    assert sql(app, "SELECT no_email FROM users WHERE username = 'fx_guest'")[0]["no_email"] == 1
    assert "(fx_guest)" not in text(client_for(app, "fx_siteadmin").get("/admin/email"))
    c.post("/account/", data={"csrf": CSRF, "theme": "", "group_email": "1"})
    assert sql(app, "SELECT no_email FROM users WHERE username = 'fx_guest'")[0]["no_email"] == 0
    sql(app, "UPDATE users SET email = '' WHERE username = 'fx_guest'")
    assert "has no email address" in text(c.get("/account/"))

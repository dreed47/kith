"""Comments on people and photos: who can add and remove them, the emails they send, and merges and deletes."""
import html as htmllib

from conftest import CSRF, client_for, make, sql
from test_edit_media import newest_media, upload


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def comments(app, kind, target):
    return sql(app, "SELECT * FROM app_comments WHERE kind = %s AND target = %s ORDER BY id", (kind, str(target)))


def say(client, kind, target, body):
    return client.post(f"/comments/{kind}/{target}", data={"csrf": CSRF, "body": body})


def test_person_comments(app, admin):
    ids = make(app, ("Ruth", "F", 0))
    pid = ids["Ruth"]
    assert "Know something about this, or spotted a mistake?" in text(admin.get(f"/person/{pid}"))
    r = say(admin, "person", pid, "  That's Aunt May, not Ruth  ")
    assert r.status_code == 302 and r.location.endswith(f"/person/{pid}#comments")
    c = comments(app, "person", pid)[0]
    assert (c["body"], c["username"]) == ("That's Aunt May, not Ruth", "fx_admin")
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'add comment' AND record = %s", (str(c["id"]),))
    say(admin, "person", pid, "   ")  # blank: nothing saved
    assert len(comments(app, "person", pid)) == 1
    guest = client_for(app, "fx_exporter")
    page = text(guest.get(f"/person/{pid}"))
    assert "That's Aunt May, not Ruth" in page and "fx_admin ·" in page and "Remove" not in page
    say(guest, "person", pid, "Agreed")
    mine = comments(app, "person", pid)[1]
    page = text(guest.get(f"/person/{pid}"))
    assert page.count(">Remove</button>") == 1 and f"/comments/{mine['id']}/delete" in page  # only their own
    assert guest.post(f"/comments/{c['id']}/delete", data={"csrf": CSRF}).status_code == 403
    assert guest.post(f"/comments/{mine['id']}/delete", data={"csrf": CSRF}).status_code == 302
    r = admin.post(f"/comments/{c['id']}/delete", data={"csrf": CSRF})  # editors remove anyone's
    assert r.location.endswith(f"/person/{pid}#comments") and not comments(app, "person", pid)
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'delete comment' AND record = %s", (str(c["id"]),))
    assert admin.post(f"/comments/{c['id']}/delete", data={"csrf": CSRF}).status_code == 404


def test_comments_need_the_record(app, admin):
    ids = make(app, ("Alive", "M", 1))
    guest = client_for(app, "fx_exporter")
    assert say(guest, "person", ids["Alive"], "Hi").status_code == 404  # living: the guest can't see them in full
    assert 'id="comments"' not in text(guest.get(f"/person/{ids['Alive']}"))
    say(admin, "person", ids["Alive"], "Hi")
    c = comments(app, "person", ids["Alive"])[0]
    assert guest.post(f"/comments/{c['id']}/delete", data={"csrf": CSRF}).status_code == 404
    for kind, target in (("person", "NOPE"), ("family", "F1"), ("media", "abc"), ("media", "999999999")):
        assert say(admin, kind, target, "Hi").status_code == 404


def test_photo_comments(app, admin):
    ids = make(app, ("Snap", "M", 0))
    upload(admin, ids["Snap"])
    mid = newest_media(app)["mediaID"]
    assert "Know something about this photo" in text(admin.get(f"/media/{mid}"))
    r = say(admin, "media", mid, "Taken at the 1952 reunion")
    assert r.location.endswith(f"/media/{mid}#comments")
    assert "Taken at the 1952 reunion" in text(admin.get(f"/media/{mid}"))
    admin.post(f"/edit/media/{mid}/delete", data={"csrf": CSRF})
    assert not comments(app, "media", mid)
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'delete media' AND record = %s"
                    " AND before_json LIKE '%%1952 reunion%%'", (str(mid),))


def test_comment_emails(app, admin, monkeypatch):
    from app import mail
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append((to, subject, body)) or True)
    monkeypatch.setitem(app.config, "NOTIFY_EMAIL", "notify@example.com")
    sql(app, "UPDATE users SET email = 'exporter@example.com' WHERE username = 'fx_exporter'")
    sql(app, "UPDATE users SET email = 'admin@example.com' WHERE username = 'fx_admin'")
    # fx_exporter, not fx_guest, comments in these tests: feedback tests need fx_guest without edit history.
    try:
        pid = make(app, ("Mailed", "F", 0))["Mailed"]
        say(admin, "person", pid, "Secret words")
        assert [to for to, _, _ in sent] == ["notify@example.com"]
        to, subject, body = sent[0]
        assert subject.startswith("New comment on ") and f"/person/{pid}#comments" in body
        assert "fx_admin added a comment" in body and "Secret words" not in body  # typed text stays on the site
        say(client_for(app, "fx_exporter"), "person", pid, "Reply")
        assert sorted(to for to, _, _ in sent[1:]) == ["admin@example.com", "notify@example.com"]
        monkeypatch.setitem(app.config, "NOTIFY_EMAIL", "admin@example.com, copy@example.com")
        del sent[:]
        say(admin, "person", pid, "Again")  # never emailed about their own comment, even in a list
        assert sorted(to for to, _, _ in sent) == ["copy@example.com", "exporter@example.com"]
        upload(admin, pid)
        mid = newest_media(app)["mediaID"]
        say(client_for(app, "fx_exporter"), "media", mid, "Photo note")
        assert f"/media/{mid}#comments" in sent[-1][2]
    finally:
        sql(app, "UPDATE users SET email = '' WHERE username IN ('fx_exporter', 'fx_admin')")


def test_comments_follow_merge_and_go_with_deletes(app, admin):
    ids = make(app, ("Keep", "M", 0), ("Dup", "M", 0))
    say(admin, "person", ids["Dup"], "On the duplicate")
    assert admin.post("/edit/merge", data={"csrf": CSRF, "keep": ids["Keep"], "merge": ids["Dup"]}).status_code == 302
    assert [c["body"] for c in comments(app, "person", ids["Keep"])] == ["On the duplicate"]
    admin.post(f"/edit/person/{ids['Keep']}/delete", data={"csrf": CSRF})
    assert not comments(app, "person", ids["Keep"])
    assert sql(app, "SELECT 1 FROM app_audit WHERE action = 'delete person' AND record = %s"
                    " AND before_json LIKE '%%On the duplicate%%'", (ids["Keep"],))


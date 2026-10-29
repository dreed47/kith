"""Watching a person: the button, the once-a-day email when someone else changes them, and merges and deletes."""
import html as htmllib

from conftest import CSRF, client_for, make, sql
from test_comments import say
from test_edit import person_form
from test_edit_events import event_data

from app.watch import touched


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def watches(app, pid):
    return sql(app, "SELECT u.username, w.emailed_on FROM app_watches w JOIN users u ON u.userID = w.userID"
                    " WHERE w.personID = %s ORDER BY u.username", (pid,))


def person(app, pid):
    return sql(app, "SELECT * FROM people WHERE personID = %s", (pid,))[0]


def test_touched():
    assert touched("people", "I1", {"people": {}}, None) == {"I1"}
    assert touched("events", "5", None, {"persfamID": "I2"}) == {"I2"}
    assert touched("medialinks", "9/I3", {"personID": "I3"}, None) == {"I3"}
    assert touched("families", "F1", {"husband": "I4", "wife": ""}, {"husband": "I4", "wife": "I5"}) == {"I4", "I5"}
    assert touched("app_comments", "7", None, {"kind": "person", "target": "I6"}) == {"I6"}
    assert touched("app_comments", "8", None, {"kind": "media", "target": "12"}) == set()
    assert touched("backups", "now", None, None) == set()


def test_watch_button_and_account_list(app):
    ids = make(app, ("Watched", "F", 0), ("Alive", "M", 1))
    viewer = client_for(app, "fx_exporter")
    page = text(viewer.get(f"/person/{ids['Watched']}"))
    assert f'action="/watch/{ids["Watched"]}"' in page and ">Watch</button>" in page
    r = viewer.post(f"/watch/{ids['Watched']}", data={"csrf": CSRF})
    assert r.location.endswith(f"/person/{ids['Watched']}") and watches(app, ids["Watched"])[0]["username"] == "fx_exporter"
    page = text(viewer.get(f"/person/{ids['Watched']}"))
    assert "Stop watching" in page and "has no email address yet" in page
    account = text(viewer.get("/account/"))
    assert "People you watch" in account and f"/person/{ids['Watched']}" in account
    viewer.post(f"/watch/{ids['Watched']}", data={"csrf": CSRF})  # again: stop
    assert not watches(app, ids["Watched"]) and "Nobody yet" in text(viewer.get("/account/"))
    # People the viewer can't see in full can't be watched, and show no button.
    assert viewer.post(f"/watch/{ids['Alive']}", data={"csrf": CSRF}).status_code == 404
    assert "/watch/" not in text(viewer.get(f"/person/{ids['Alive']}"))
    assert viewer.post("/watch/XNOBODY", data={"csrf": CSRF}).status_code == 404


def test_watch_emails(app, admin, monkeypatch):
    from app import mail
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append((to, subject, body)) or True)
    ids = make(app, ("Nora", "F", 0), ("Alive", "M", 1))
    nora = ids["Nora"]
    sql(app, "UPDATE users SET email = 'exporter@example.com' WHERE username = 'fx_exporter'")
    sql(app, "UPDATE users SET email = 'admin@example.com' WHERE username = 'fx_admin'")
    try:
        viewer = client_for(app, "fx_exporter")
        viewer.post(f"/watch/{nora}", data={"csrf": CSRF})
        admin.post(f"/watch/{nora}", data={"csrf": CSRF})
        admin.post(f"/edit/person/{nora}", data=person_form(person(app, nora), birthplace="Cork"))
        assert len(sent) == 1  # the watcher, not the editor who made the change
        to, subject, body = sent[0]
        assert to == "exporter@example.com" and subject.startswith(f"Nora {nora[1:-4]} was changed on ")
        assert f"fx_admin changed Nora {nora[1:-4]}" in body and f"/person/{nora}" in body and "Cork" not in body
        assert watches(app, nora)[1]["emailed_on"] is not None
        admin.post("/edit/event/new", data=event_data(app, **{"for": nora, "info": "Farmer"}))
        assert len(sent) == 1  # at most one email a day
        sql(app, "UPDATE app_watches SET emailed_on = '2000-01-01' WHERE personID = %s", (nora,))
        admin.post("/edit/event/new", data=event_data(app, **{"for": nora, "info": "Teacher"}))  # an event counts
        assert len(sent) == 2
        sql(app, "UPDATE app_watches SET emailed_on = NULL WHERE personID = %s", (nora,))
        say(viewer, "person", nora, "Hello")  # the watcher's own comment: only the other watcher hears
        assert sent[-1][0] == "admin@example.com"
        # Someone made living after they started watching: no email about a person they can no longer see.
        sql(app, "UPDATE app_watches SET emailed_on = NULL WHERE personID = %s", (nora,))
        sql(app, "UPDATE people SET living = 1 WHERE personID = %s", (nora,))
        n = len(sent)
        admin.post("/edit/event/new", data=event_data(app, **{"for": nora, "info": "Retired"}))
        assert len(sent) == n
        # A watcher without an email address is skipped, and a failed send is tried again next time.
        sql(app, "UPDATE people SET living = 0 WHERE personID = %s", (nora,))
        sql(app, "UPDATE users SET email = '' WHERE username = 'fx_exporter'")
        admin.post("/edit/event/new", data=event_data(app, **{"for": nora, "info": "Again"}))
        assert len(sent) == n
        sql(app, "UPDATE users SET email = 'exporter@example.com' WHERE username = 'fx_exporter'")
        monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append((to, subject, body)) and False)
        admin.post("/edit/event/new", data=event_data(app, **{"for": nora, "info": "Once more"}))
        assert len(sent) == n + 1 and not any(w["emailed_on"] for w in watches(app, nora))
    finally:
        sql(app, "UPDATE users SET email = '' WHERE username IN ('fx_exporter', 'fx_admin')")


def test_watches_follow_merge_and_go_with_deletes(app, admin):
    ids = make(app, ("Keep", "M", 0), ("Dup", "M", 0))
    viewer = client_for(app, "fx_exporter")
    viewer.post(f"/watch/{ids['Dup']}", data={"csrf": CSRF})
    viewer.post(f"/watch/{ids['Keep']}", data={"csrf": CSRF})  # both: one watch afterwards
    admin.post(f"/watch/{ids['Dup']}", data={"csrf": CSRF})
    assert admin.post("/edit/merge", data={"csrf": CSRF, "keep": ids["Keep"], "merge": ids["Dup"]}).status_code == 302
    assert [w["username"] for w in watches(app, ids["Keep"])] == ["fx_admin", "fx_exporter"]
    assert not watches(app, ids["Dup"])
    admin.post(f"/edit/person/{ids['Keep']}/delete", data={"csrf": CSRF})
    assert not watches(app, ids["Keep"])

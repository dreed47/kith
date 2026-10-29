"""Admin > Backups: the page reads the backup script's status file and asks for work with request files."""
import json

import pytest
from conftest import CSRF, client_for, sql

from app import backups

STATUS = {"updated": "2026-10-27 13:42:00", "running": None, "remote": False,
          "last": {"action": "nightly", "ok": True, "message": "Backup 2026-10-27 made.", "finished": "2026-10-27 03:31:00"},
          "backups": [{"stamp": "2026-10-27_1342", "db_bytes": 2700202, "media": True,
                       "note": "Taken just before restoring 2026-10-26"},
                      {"stamp": "2026-10-26", "db_bytes": 1048576, "media": False}]}


@pytest.fixture
def setup(app, tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    monkeypatch.setitem(app.config, "BACKUP_STATUS", str(status))
    monkeypatch.setattr(backups, "requests_dir", lambda: tmp_path / "requests")
    return status, tmp_path / "requests", client_for(app, "fx_siteadmin")


def text(r):
    return r.get_data(as_text=True)


def test_not_set_up(setup):
    _, _, c = setup
    assert "set up on this machine" in text(c.get("/admin/backups"))
    assert c.get("/admin/backups/2026-10-27/restore").status_code == 404


def test_list_and_back_up_now(setup, app):
    status, requests, c = setup
    status.write_text(json.dumps(STATUS))
    page = text(c.get("/admin/backups"))
    assert "27 Oct 2026, 13:42" in page and "26 Oct 2026, nightly" in page and "2.6 MB" in page
    assert "Backup 27 Oct 2026, nightly made." in page and "Taken just before restoring 26 Oct 2026, nightly" in page and "only on this server" in page and "Back up now" in page
    assert 'http-equiv="refresh"' not in page and "/admin/backups/2026-10-26/restore" in page
    assert c.post("/admin/backups/now", data={"csrf": CSRF}).status_code == 302
    assert [p.name for p in requests.iterdir()] == ["backup"]
    page = text(c.get("/admin/backups"))
    assert "Waiting to start" in page and 'http-equiv="refresh"' in page and "/restore" not in page
    status.write_text(json.dumps({**STATUS, "running": "restore", "remote": True,
                                  "last": {**STATUS["last"], "ok": False, "message": "It broke."}}))
    page = text(c.get("/admin/backups"))
    assert "Restoring now" in page and "Failed:" in page and "also goes to the backup machine" in page
    assert sql(app, "SELECT action FROM app_audit ORDER BY id DESC")[0]["action"] == "ask for backup"
    assert client_for(app, "fx_admin").get("/admin/backups").status_code == 403  # editors aren't site admins


def test_restore(setup, app):
    status, requests, c = setup
    status.write_text(json.dumps({**STATUS, "backups": STATUS["backups"] + [{"stamp": "../x", "db_bytes": 1, "media": 0}]}))
    assert c.get("/admin/backups/2026-10-25/restore").status_code == 404  # no such backup
    assert c.get("/admin/backups/..%2Fx/restore").status_code == 404  # not a stamp, whatever the file says
    page = text(c.get("/admin/backups/2026-10-27_1342/restore"))
    assert "Restore 27 Oct 2026, 13:42?" in page and 'name="media"' in page
    assert "database only" in text(c.get("/admin/backups/2026-10-26/restore"))
    assert "Type RESTORE" in text(c.post("/admin/backups/2026-10-27_1342/restore", data={"csrf": CSRF, "confirm": "yes"}))
    assert not requests.exists()
    c.post("/admin/backups/2026-10-27_1342/restore", data={"csrf": CSRF, "confirm": "restore", "media": "1"})
    c.post("/admin/backups/2026-10-26/restore", data={"csrf": CSRF, "confirm": "RESTORE", "media": "1"})  # has none
    assert sorted(p.name for p in requests.iterdir()) == ["restore+media@2026-10-27_1342", "restore@2026-10-26"]
    assert sql(app, "SELECT action, record FROM app_audit ORDER BY id DESC")[0] == \
        {"action": "ask for restore", "record": "2026-10-26"}


def test_status_paths(app):
    with app.test_request_context():
        assert backups.requests_dir().name == "requests" and backups.status() is None


def test_schedule(setup, app):
    status, requests, c = setup
    status.write_text(json.dumps({**STATUS, "schedule": {"enabled": True, "time": "03:30", "keep_days": 30,
                                                          "timezone": "UTC", "next": "Wed 2026-10-28 03:30:00 UTC"}}))
    page = text(c.get("/admin/backups"))
    assert "Every night at <strong>03:30</strong> (UTC" in page and "Next: Wed 2026-10-28" in page
    assert 'name="enabled" value="1" checked' in page and 'value="30"' in page
    for bad in ({"time": "25:00", "keep_days": "30"}, {"time": "03:30", "keep_days": "0"},
                {"time": "03:30", "keep_days": "1000"}, {"time": "03:30"}):
        assert "Not saved" in text(c.post("/admin/backups/schedule", data={"csrf": CSRF, **bad}, follow_redirects=True))
    assert not requests.exists()
    c.post("/admin/backups/schedule", data={"csrf": CSRF, "time": "01:05", "keep_days": "14"})  # box unticked: off
    assert [p.name for p in requests.iterdir()] == ["schedule@0-0105-14"]
    assert sql(app, "SELECT action FROM app_audit ORDER BY id DESC")[0]["action"] == "change backup schedule"
    status.write_text(json.dumps({**STATUS, "schedule": {"enabled": False, "time": "01:05", "keep_days": 14,
                                                          "timezone": "UTC", "next": ""}}))
    assert "Nightly backups are off" in text(c.get("/admin/backups"))


def test_offsite(setup, app, monkeypatch):
    status, _, c = setup
    status.write_text(json.dumps(STATUS))
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE", "Proxmox backups")
    page = text(c.get("/admin/backups"))
    assert "also backed up by Proxmox backups" in page and "only on this server" not in page


def test_delete(setup, app):
    status, requests, c = setup
    status.write_text(json.dumps(STATUS))
    assert "/admin/backups/2026-10-26/delete" in text(c.get("/admin/backups"))
    assert c.get("/admin/backups/2026-10-25/delete").status_code == 404
    page = text(c.get("/admin/backups/2026-10-26/delete"))
    assert "Delete the backup of 26 Oct 2026, nightly?" in page and "can&#39;t be undone" in page
    c.post("/admin/backups/2026-10-26/delete", data={"csrf": CSRF})
    assert [p.name for p in requests.iterdir()] == ["delete@2026-10-26"]
    assert sql(app, "SELECT action, record FROM app_audit ORDER BY id DESC")[0] == \
        {"action": "ask to delete backup", "record": "2026-10-26"}
    status.write_text(json.dumps({**STATUS, "backups": STATUS["backups"][:1]}))
    assert "/delete" not in text(c.get("/admin/backups"))  # never the only one

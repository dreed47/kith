"""Admin > Backups: the backups on the server, making one now, and putting the site back to one.

The site runs as an unprivileged user and can't read the backups (they hold password hashes) or run the backup
script. It reads the status file the script writes (BACKUP_STATUS), and asks for work by dropping an empty file
whose name says what to do into instance/requests/; a root systemd path unit then runs deploy/backup.sh, which
reads only those names. So the most a page here can do is ask for one of the three things that script allows."""
import json
import os
import re
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, url_for

from .admin import bp
from .edit import audit

STAMP = re.compile(r"\d{4}-\d\d-\d\d(_\d{4})?")
TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
MAX_KEEP = 999  # days; deploy/backup.sh allows no more
CONFIRM = "RESTORE"


def status():
    """What the backup script last wrote, or None where it doesn't run (development) or hasn't yet."""
    try:
        return json.loads(Path(current_app.config["BACKUP_STATUS"]).read_text())
    except (OSError, ValueError):
        return None


def requests_dir():
    return Path(current_app.instance_path) / "requests"


def pending():
    """Requests the script hasn't picked up yet."""
    d = requests_dir()
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def ask(name):
    requests_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    (requests_dir() / name).touch()


def nice(stamp):
    """2026-10-27_1342 as "27 Oct 2026, 13:42"; a nightly one (no time) as "27 Oct 2026, nightly"."""
    day, _, hm = stamp.partition("_")
    y, m, d = day.split("-")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{int(d)} {months[int(m) - 1]} {y}, " + (f"{hm[:2]}:{hm[2:]}" if hm else "nightly")


def pretty(text):
    """The script's messages name backups by stamp; show those as dates people read."""
    return STAMP.sub(lambda m: nice(m.group(0)), text or "")


@bp.get("/backups")
def backups():
    s = status()
    waiting = pending()
    return render_template("admin_backups.html", status=s, pending=waiting, nice=nice, pretty=pretty, max_keep=MAX_KEEP,
                           offsite=current_app.config["BACKUP_OFFSITE"],
                           busy=bool(waiting or (s and s.get("running"))))


@bp.post("/backups/schedule")
def schedule():
    """Nightly backups on or off, when, and how many days to keep. The script checks these again."""
    on = request.form.get("enabled") == "1"
    at = request.form.get("time", "")
    keep = request.form.get("keep_days", type=int)
    if not TIME.fullmatch(at) or not keep or not 1 <= keep <= MAX_KEEP:
        flash(f"Not saved: give a time such as 03:30 and between 1 and {MAX_KEEP} days.")
        return redirect(url_for("admin.backups"))
    ask(f"schedule@{int(on)}-{at.replace(':', '')}-{keep}")
    audit("change backup schedule", "backups", "schedule", None, {"enabled": on, "time": at, "keep_days": keep})
    flash("Schedule sent to the server; it shows below once it's applied.")
    return redirect(url_for("admin.backups"))


@bp.post("/backups/now")
def backup_now():
    ask("backup")
    audit("ask for backup", "backups", "now", None, None)
    flash("Backup asked for. It takes a minute or so; this page shows it when it's done.")
    return redirect(url_for("admin.backups"))


def find(stamp):
    s = status()
    b = next((b for b in (s or {}).get("backups", []) if b["stamp"] == stamp), None)
    if not STAMP.fullmatch(stamp) or not b:
        abort(404)
    return b


@bp.route("/backups/<stamp>/delete", methods=["GET", "POST"])
def delete(stamp):
    find(stamp)
    if request.method == "POST":
        ask(f"delete@{stamp}")
        audit("ask to delete backup", "backups", stamp, None, None)
        flash(f"Deleting the backup of {nice(stamp)}.")
        return redirect(url_for("admin.backups"))
    return render_template("confirm_delete.html", title=f"Delete the backup of {nice(stamp)}?",
                           back=url_for("admin.backups"), details=[],
                           note="Its database copy and its photos and documents are removed from the server. "
                                "This can't be undone.")


@bp.route("/backups/<stamp>/restore", methods=["GET", "POST"])
def restore(stamp):
    b = find(stamp)
    error = None
    if request.method == "POST":
        media = request.form.get("media") == "1" and b["media"]
        if request.form.get("confirm", "").strip().upper() != CONFIRM:
            error = f"Type {CONFIRM} in the box to go ahead."
        else:
            ask(f"restore{'+media' if media else ''}@{stamp}")
            audit("ask for restore", "backups", stamp, None, {"media": media})
            flash(f"Restore of {nice(stamp)} asked for. The site goes offline for a minute or two while it runs; "
                  "then reload this page to see how it went.")
            return redirect(url_for("admin.backups"))
    return render_template("admin_restore.html", b=b, nice=nice, error=error, confirm=CONFIRM)

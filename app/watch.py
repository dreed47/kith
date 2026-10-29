"""Watching a person: an email when someone else changes them, their events, notes, photos, face tags or comments.
At most one email a day per person watched, however many edits there are. Every edit goes through edit.audit, which
calls notify with the people it touched."""
import datetime

from flask import Blueprint, abort, flash, g, redirect, url_for

from . import mail
from .auth import can_log_in
from .db import execute, query
from .privacy import display_name, rights
from .views import one, tree

bp = Blueprint("watch", __name__, url_prefix="/watch")


def watching(pid):
    return bool(one("SELECT 1 AS x FROM app_watches WHERE userID = %s AND gedcom = %s AND personID = %s",
                    (g.user["userID"], tree(), pid)))


def watched():
    """The people this user watches, by name order, for Your account."""
    return query("SELECT p.* FROM app_watches w JOIN people p ON p.gedcom = w.gedcom AND p.personID = w.personID"
                 " WHERE w.userID = %s AND w.gedcom = %s ORDER BY p.lastname, p.firstname", (g.user["userID"], tree()))


@bp.post("/<pid>")
def toggle(pid):
    p = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not p or not rights(p)["both"]:
        abort(404)
    if watching(pid):
        execute("DELETE FROM app_watches WHERE userID = %s AND gedcom = %s AND personID = %s",
                (g.user["userID"], tree(), pid))
        flash(f"You'll no longer get emails about {display_name(p)}.")
    else:
        execute("INSERT INTO app_watches (userID, gedcom, personID, created_at) VALUES (%s, %s, %s, %s)",
                (g.user["userID"], tree(), pid, datetime.datetime.now()))
        flash(f"Watching {display_name(p)}: you'll get an email when someone changes them."
              + ("" if g.user["email"] else " Your account has no email address yet; ask an admin to add one."))
    return redirect(url_for("main.person", pid=pid))


def touched(tbl, record, before, after):
    """IDs of the people an edit touched: the person record itself, whoever its rows belong to, or a family's
    spouses."""
    ids = {record} if tbl == "people" else set()
    for row in (before, after):
        if isinstance(row, dict):
            ids |= {row[k] for k in ("persfamID", "personID", "husband", "wife")
                    if isinstance(row.get(k), str) and row[k]}
            if tbl == "app_comments" and row.get("kind") == "person":
                ids.add(row["target"])
    return ids


def notify(tbl, record, before, after):
    """Email those watching the touched people, except the editor, once a day each. Fixed text, the person's name
    as the watcher may see it, and a link."""
    ids = sorted(touched(tbl, record, before, after))
    if not ids:
        return
    from .account import link, site_name  # here, not at the top: account imports edit, which imports this module
    today = datetime.date.today()
    for w in query("SELECT w.personID AS watched, u.* FROM app_watches w JOIN users u ON u.userID = w.userID"
                   f" WHERE w.gedcom = %s AND w.personID IN ({','.join(['%s'] * len(ids))}) AND w.userID != %s"
                   " AND u.email != '' AND (w.emailed_on IS NULL OR w.emailed_on < %s)",
                   [tree(), *ids, g.user["userID"], today]):
        p = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), w["watched"]))
        if not p or not can_log_in(w) or not rights(p, w)["both"]:
            continue  # deleted, or the watcher may no longer see them
        name = display_name(p, w)
        if mail.send(w["email"], f"{name} was changed on {site_name()}",
                     f"{g.user['username']} changed {name} on {site_name()}. See them here:\n\n"
                     f"{link('main.person', pid=w['watched'])}\n\n"
                     "You get at most one email a day for each person you watch. To stop, open their page and press "
                     "Stop watching."):
            execute("UPDATE app_watches SET emailed_on = %s WHERE userID = %s AND gedcom = %s AND personID = %s",
                    (today, w["userID"], tree(), w["watched"]))

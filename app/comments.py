"""Comments on people and photos ("That's Aunt May, not Ruth"): anyone who can see the record in full can add one;
its author or an editor can remove it. Not in TNG, so they live in our own table and never reach GEDCOM."""
import datetime

from flask import Blueprint, abort, current_app, flash, g, redirect, request, url_for

from . import mail
from .account import link, site_name
from .db import execute, insert, query
from .edit import audit
from .media import get_visible
from .privacy import media_filter, people_filter, rights
from .views import one, people_by_id, tree

bp = Blueprint("comments", __name__, url_prefix="/comments")

BODY_MAX = 5000


def comments_for(kind, target):
    return query("SELECT * FROM app_comments WHERE gedcom = %s AND kind = %s AND target = %s ORDER BY id",
                 (tree(), kind, str(target)))


def recent(limit):
    """Newest comments on people and photos this user may see in full, each with its "person" or "media" row
    (for What's new, news.py)."""
    pcond, pparams = people_filter("cp")
    mcond, mparams = media_filter("cm")
    rows = query("SELECT c.* FROM app_comments c"
                 " LEFT JOIN people cp ON c.kind = 'person' AND cp.gedcom = c.gedcom AND cp.personID = c.target"
                 " LEFT JOIN media cm ON c.kind = 'media' AND cm.mediaID = c.target"
                 f" WHERE c.gedcom = %s AND (cp.personID IS NOT NULL AND {pcond} OR cm.mediaID IS NOT NULL AND {mcond})"
                 " ORDER BY c.id DESC LIMIT %s", [tree(), *pparams, *mparams, limit])
    people = people_by_id([r["target"] for r in rows if r["kind"] == "person"])
    mids = [int(r["target"]) for r in rows if r["kind"] == "media"] or [0]
    media = {str(m["mediaID"]): m for m in query(
        f"SELECT * FROM media WHERE mediaID IN ({','.join(['%s'] * len(mids))})", mids)}
    for r in rows:
        r["person" if r["kind"] == "person" else "media"] = (people if r["kind"] == "person" else media)[r["target"]]
    return rows


def page_for(kind, target):
    """Where the record's comments are shown, after checking the user may see it in full."""
    if kind == "person":
        p = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), target))
        if not p or not rights(p)["both"]:
            abort(404)
        return url_for("main.person", pid=target, _anchor="comments")
    if kind != "media" or not target.isdigit():
        abort(404)
    get_visible(int(target))
    return url_for("media.show", media_id=int(target), _anchor="comments")


def notify(kind, target):
    """Email the site's notification address and earlier commenters on the same record. Fixed text and a link
    only; what was typed stays on the site."""
    earlier = query("SELECT DISTINCT u.email FROM app_comments c JOIN users u ON u.userID = c.userID"
                    " WHERE c.gedcom = %s AND c.kind = %s AND c.target = %s AND c.userID != %s AND u.email != ''",
                    (tree(), kind, target, g.user["userID"]))
    notify = [a.strip() for a in current_app.config["NOTIFY_EMAIL"].split(",")]  # may list several
    to = {*notify, *(r["email"] for r in earlier)} - {"", g.user["email"]}
    endpoint, args = ("main.person", {"pid": target}) if kind == "person" else ("media.show", {"media_id": target})
    for address in sorted(to):
        mail.send(address, f"New comment on {site_name()}",
                  f"{g.user['username']} added a comment on {site_name()}. Read it here:\n\n"
                  f"{link(endpoint, **args, _anchor='comments')}")


@bp.post("/<kind>/<target>")
def add(kind, target):
    back = page_for(kind, target)
    body = request.form.get("body", "").strip()[:BODY_MAX]
    if body:
        row = {"gedcom": tree(), "kind": kind, "target": target, "created_at": datetime.datetime.now(),
               "userID": g.user["userID"], "username": g.user["username"], "body": body}
        _, cid = insert("app_comments", row)
        audit("add comment", "app_comments", str(cid), None, row)
        notify(kind, target)
        flash("Comment added.")
    return redirect(back)


@bp.post("/<int:cid>/delete")
def delete(cid):
    c = one("SELECT * FROM app_comments WHERE id = %s AND gedcom = %s", (cid, tree()))
    if not c:
        abort(404)
    back = page_for(c["kind"], c["target"])
    if c["userID"] != g.user["userID"] and g.user["allow_edit"] != 1:
        abort(403)
    execute("DELETE FROM app_comments WHERE id = %s", (cid,))
    audit("delete comment", "app_comments", str(cid), c, None)
    flash("Comment removed.")
    return redirect(back)

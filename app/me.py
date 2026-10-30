"""'This is me': a user asks to be linked to their own person record; an editor approves or rejects.
Linking matters for privacy (you can always see your own record, even when living), hence the approval.
Unlinking needs no approval: it only takes that away. A user linked to the wrong person unlinks, then asks again."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .db import execute, query, update
from .edit import audit, get_person, require
from .views import one, tree

bp = Blueprint("me", __name__, url_prefix="/me")


def my_link_request():
    """The current user's pending request, if any (used on person pages)."""
    return one("SELECT * FROM app_link_requests WHERE userID = %s AND status = 'pending'", (g.user["userID"],))


def pending_count():
    """How many requests wait for an editor (the menu only asks this for editors)."""
    return one("SELECT COUNT(*) AS n FROM app_link_requests WHERE status = 'pending'")["n"]


@bp.post("/request/<pid>")
def request_link(pid):
    if g.user["personID"]:
        abort(400)  # already linked; an editor can change it
    if not get_person(pid):
        abort(404)
    execute("DELETE FROM app_link_requests WHERE userID = %s AND status = 'pending'", (g.user["userID"],))
    execute("INSERT INTO app_link_requests (userID, personID, requested_at) VALUES (%s, %s, NOW())",
            (g.user["userID"], pid))
    flash("Thanks. An editor will confirm it's you, then this site can show how you're related to everyone.")
    return redirect(url_for("main.person", pid=pid))


@bp.post("/unlink")
def unlink():
    if not g.user["personID"]:
        abort(400)
    update("users", {"personID": "", "mygedcom": ""}, {"userID": g.user["userID"]})
    audit("unlink user", "users", str(g.user["userID"]), {"personID": g.user["personID"]}, {"personID": ""})
    flash("Unlinked. To link to someone else, press \"This is me\" on their page.")
    return redirect(url_for("account.profile"))


@bp.get("/requests")
def requests():
    require("allow_edit")
    rows = query("SELECT r.*, u.username, u.realname, u.email, u.description FROM app_link_requests r"
                 " JOIN users u ON u.userID = r.userID WHERE r.status = 'pending' ORDER BY r.requested_at")
    for r in rows:
        r["person"] = get_person(r["personID"])
    return render_template("link_requests.html", rows=rows)


@bp.post("/requests/<int:rid>")
def decide(rid):
    require("allow_edit")
    req = one("SELECT * FROM app_link_requests WHERE id = %s AND status = 'pending'", (rid,))
    action = request.form.get("action")
    if not req or action not in ("approve", "reject"):
        abort(400)
    user = one("SELECT * FROM users WHERE userID = %s", (req["userID"],))
    if action == "approve" and user:
        # TNG's own fields for "this user is that person"
        update("users", {"personID": req["personID"], "mygedcom": tree()}, {"userID": user["userID"]})
        audit("link user", "users", str(user["userID"]), {"personID": user["personID"]},
              {"personID": req["personID"]})
        flash(f"Linked {user['username']} to {req['personID']}.")
    else:
        flash("Request rejected." if action == "reject" else "That user no longer exists.")
    execute("UPDATE app_link_requests SET status = %s, decided_by = %s, decided_at = NOW() WHERE id = %s",
            ("approved" if action == "approve" and user else "rejected", g.user["username"], rid))
    return redirect(url_for("me.requests"))

"""Feedback: any logged-in user can report a problem or idea from any page; editors work through the list.
Each report is also copied to GitHub Issues when KITH_GITHUB_REPO and KITH_GITHUB_TOKEN are set."""
import datetime
import io
import json
import re
import secrets
import urllib.error
import urllib.request
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from PIL import Image, UnidentifiedImageError

from . import mail
from .account import link, site_name
from .auth import _safe_next
from .db import insert, query, update
from .views import one

bp = Blueprint("feedback", __name__, url_prefix="/feedback")

KINDS = {"bug": "Something's wrong", "idea": "Idea", "question": "Question"}
STATUSES = {"new": "New", "working": "Working on it", "done": "Done", "wontdo": "Won't do"}
CLOSED = {"done", "wontdo"}
MESSAGE_MAX = 10000
SCREENSHOT_MAX = 10 * 1024 * 1024


def is_editor():
    return g.user["allow_edit"] == 1


def screenshot_dir():
    return Path(current_app.instance_path) / "feedback"


def save_screenshot(upload):
    """Re-encode the upload as PNG, so only a real image is kept (and any photo location data is dropped)."""
    data = upload.read(SCREENSHOT_MAX + 1)
    if len(data) > SCREENSHOT_MAX:
        raise ValueError("The screenshot is larger than 10MB.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            screenshot_dir().mkdir(parents=True, exist_ok=True)
            name = f"{g.user['userID']}-{secrets.token_hex(8)}.png"
            im.save(screenshot_dir() / name, "PNG")
            return name
    except (UnidentifiedImageError, OSError):
        raise ValueError("The screenshot isn't a readable image.")


def page_context(page):
    """Which record the page was showing, from its address."""
    m = re.match(r"^/(person|family|pedigree|media|edit/person|edit/family)/([\w-]+)", page or "")
    return {"record": f"{m.group(1)} {m.group(2)}"} if m else {}


def github_request(method, path, body):
    """Call the GitHub API; returns the decoded JSON reply. Raises on any failure."""
    cfg = current_app.config
    req = urllib.request.Request(
        f"https://api.github.com/repos/{cfg['GITHUB_REPO']}{path}", method=method,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {cfg['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
                 "User-Agent": "kith-feedback"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read() or b"{}")


def github_enabled():
    return bool(current_app.config["GITHUB_REPO"] and current_app.config["GITHUB_TOKEN"])


def sync_to_github(fb):
    """Create the issue for a report (once). Failures are stored, not raised: the report itself is safe."""
    if not github_enabled() or fb["github_issue"]:
        return
    ctx = json.loads(fb["context"] or "{}")
    lines = [fb["message"], "", "---", f"**From:** {fb['username']} · {fb['created_at']}",
             f"**Page:** `{fb['page'] or 'unknown'}`"]
    lines += [f"**{k.capitalize()}:** {v}" for k, v in ctx.items() if k != "recent_edits"]
    if ctx.get("recent_edits"):
        lines.append("**Their recent edits:** " + "; ".join(ctx["recent_edits"]))
    if fb["screenshot"]:
        lines.append(f"**Screenshot:** on the site, feedback #{fb['id']}")
    title = f"[{KINDS[fb['kind']]}] {' '.join(fb['message'].split())[:70]}"
    try:
        issue = github_request("POST", "/issues", {"title": title, "body": "\n".join(lines),
                                                   "labels": ["feedback", fb["kind"]]})
        update("app_feedback", {"github_issue": issue["number"], "github_error": None}, {"id": fb["id"]})
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        update("app_feedback", {"github_error": str(e)[:500]}, {"id": fb["id"]})


def get_feedback(fid):
    fb = one("SELECT * FROM app_feedback WHERE id = %s", (fid,))
    if not fb:
        abort(404)
    return fb


@bp.route("/new", methods=["GET", "POST"])
def new():
    page = _safe_next(request.values.get("from", ""))
    errors, v = [], {"kind": request.values.get("kind", "bug"), "message": ""}
    if request.method == "POST":
        v = {"kind": request.form.get("kind", ""), "message": request.form.get("message", "").strip()}
        if v["kind"] not in KINDS:
            errors.append("Choose what kind of feedback this is.")
        if not v["message"]:
            errors.append("Please describe what happened or what you'd like.")
        if len(v["message"]) > MESSAGE_MAX:
            errors.append(f"Please keep it under {MESSAGE_MAX} characters.")
        shot = ""
        if not errors and (upload := request.files.get("screenshot")) and upload.filename:
            try:
                shot = save_screenshot(upload)
            except ValueError as e:
                errors.append(str(e))
        if not errors:
            ctx = {**page_context(page), "browser": request.headers.get("User-Agent", "")[:200],
                   "screen": request.form.get("screen", "")[:20]}
            edits = query("SELECT action, record, at FROM app_audit WHERE username = %s ORDER BY id DESC LIMIT 5",
                          (g.user["username"],))
            if edits:
                ctx["recent_edits"] = [f"{e['action']} {e['record']} ({e['at']:%d %b %H:%M})" for e in edits]
            _, fid = insert("app_feedback", {"created_at": datetime.datetime.now(),
                                             "userID": g.user["userID"], "username": g.user["username"],
                                             "kind": v["kind"], "message": v["message"], "page": page,
                                             "context": json.dumps(ctx), "screenshot": shot, "status": "new",
                                             "reply": "", "github_issue": None, "github_error": None})
            sync_to_github(get_feedback(fid))
            flash("Thanks! Your feedback was sent. You can follow it under "
                  + ("Reports > Mine." if is_editor() else "My feedback."))
            return redirect(page)
    return render_template("feedback_new.html", kinds=KINDS, v=v, errors=errors, page=page)


@bp.get("/mine")
def mine():
    rows = query("SELECT * FROM app_feedback WHERE userID = %s ORDER BY id DESC", (g.user["userID"],))
    return render_template("feedback_list.html", rows=rows, kinds=KINDS, statuses=STATUSES, mine=True, status="")


@bp.get("/")
def index():
    if not is_editor():
        abort(403)
    status = request.args.get("status", "open")
    where, args = {"open": ("status NOT IN ('done', 'wontdo')", ()), "all": ("1=1", ()),
                   "mine": ("userID = %s", (g.user["userID"],))}.get(status, ("status = %s", (status,)))
    rows = query(f"SELECT * FROM app_feedback WHERE {where} ORDER BY id DESC", args)
    return render_template("feedback_list.html", rows=rows, kinds=KINDS, statuses=STATUSES, mine=False, status=status)


def github_in_step(fb, call, *args):
    """Mirror a change on the report's GitHub issue. Failures are only worth telling editors: the change is saved."""
    if not fb["github_issue"]:
        return
    try:
        github_request(*args)
    except (urllib.error.URLError, OSError, ValueError) as e:
        if is_editor():
            flash(f"Saved, but GitHub wasn't updated ({call}): {e}")


def set_status(fb, status):
    if (status in CLOSED) != (fb["status"] in CLOSED):
        github_in_step(fb, "open/closed", "PATCH", f"/issues/{fb['github_issue']}",
                       {"state": "closed" if status in CLOSED else "open"})


def notify(fb, what):
    """Email the other side about activity on a report: its author when someone else acts, else the site's
    notification address (NOTIFY_EMAIL). Fixed text and a link only; what was typed stays on the site."""
    if g.user["userID"] != fb["userID"]:
        author = one("SELECT email FROM users WHERE userID = %s", (fb["userID"],))
        to = author["email"] if author else ""
    else:
        to = current_app.config["NOTIFY_EMAIL"]
    if to:
        mail.send(to, f"Feedback #{fb['id']}: {g.user['username']} {what}",
                  f"{g.user['username']} {what} on feedback #{fb['id']} on {site_name()}. Read it here:\n\n"
                  f"{link('feedback.detail', fid=fb['id'])}")


@bp.route("/<int:fid>", methods=["GET", "POST"])
def detail(fid):
    """The report and its comments. The author and editors can comment; the author can close it;
    editors set its status and reply."""
    fb = get_feedback(fid)
    author = fb["userID"] == g.user["userID"]
    if not is_editor() and not author:
        abort(403)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "comment":
            body = request.form.get("body", "").strip()[:MESSAGE_MAX]
            if body:
                insert("app_feedback_comments", {"feedback_id": fid, "created_at": datetime.datetime.now(),
                                                 "userID": g.user["userID"], "username": g.user["username"],
                                                 "body": body})
                github_in_step(fb, "comment", "POST", f"/issues/{fb['github_issue']}/comments",
                               {"body": f"**{g.user['username']}:** {body}"})
                notify(fb, "added a comment")
                flash("Comment added.")
        elif action == "close" and author:
            if fb["status"] not in CLOSED:
                update("app_feedback", {"status": "done"}, {"id": fid})
                set_status(fb, "done")
                notify(fb, "closed the report")
                flash("Closed. Thank you!")
        elif not is_editor():
            abort(403)
        elif action == "retry":
            sync_to_github(fb)
        else:
            status = request.form.get("status")
            if status not in STATUSES:
                abort(400)
            reply = request.form.get("reply", "").strip()[:MESSAGE_MAX]
            update("app_feedback", {"status": status, "reply": reply}, {"id": fid})
            set_status(fb, status)
            if reply != (fb["reply"] or "") or status != fb["status"]:
                notify(fb, f"updated it (now: {STATUSES[status]})")
            flash("Saved.")
        return redirect(url_for("feedback.detail", fid=fid))
    comments = query("SELECT * FROM app_feedback_comments WHERE feedback_id = %s ORDER BY id", (fid,))
    return render_template("feedback_detail.html", fb=fb, ctx=json.loads(fb["context"] or "{}"), kinds=KINDS,
                           statuses=STATUSES, closed=CLOSED, author=author, comments=comments,
                           github=github_enabled(), repo=current_app.config["GITHUB_REPO"])


@bp.get("/<int:fid>/screenshot")
def screenshot(fid):
    fb = get_feedback(fid)
    if not fb["screenshot"] or (not is_editor() and fb["userID"] != g.user["userID"]):
        abort(404)
    return send_file(screenshot_dir() / fb["screenshot"], mimetype="image/png")

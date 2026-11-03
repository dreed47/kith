"""Site admin, for TNG role "admin" only: user accounts (including approving self-registrations), the queues
waiting on someone, and the edit history. Accounts are TNG's own users rows, so TNG keeps working with them."""
import datetime
import json
import re
import secrets
import subprocess

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import digest, mail
from .account import approved_email, link, send_reset, site_name
from .auth import password_fields
from .db import execute, insert, query, update
from .edit import audit, get_person, now
from . import localtime, updates
from .me import pending_count
from .settings import HERO_ART, SITE_TEXT, default_theme, hero_art, site_settings, stored
from .views import one, people_by_id

bp = Blueprint("admin", __name__, url_prefix="/admin")

PENDING = digest.PENDING
ROLES = {"guest": "Guest", "subm": "Submitter", "contrib": "Contributor", "editor": "Editor", "custom": "Custom",
         "admin": "Admin"}
RIGHTS = {"allow_living": "See living people", "allow_private": "See private people",
          "allow_private_notes": "See private notes", "allow_add": "Add records", "allow_edit": "Edit records",
          "allow_delete": "Delete records", "allow_ged": "Download GEDCOM"}
TEXT = {"username": 100, "realname": 50, "email": 50, "phone": 30, "description": 50}
SECRET = ("password", "reset_pwd_code")  # never copied into the edit history
MIN_PASSWORD = 8
PER_PAGE = 50
RECENT_CHANGES = 25
DOCS_ONLY = re.compile(r"^(README|Changelog|Roadmap|Docs?)\b", re.I)  # changes to the notes, not the site
RECENT_VISITS = 15
NOISE = {"changedate", "changedby", "edituser", "edittime"}  # bookkeeping fields, left out of change lists


@bp.app_template_filter("day")
def day(value):
    """A datetime as "5 Oct 2026"; blank for TNG's zero dates (which arrive as strings)."""
    return localtime.local(value).strftime("%-d %b %Y") if isinstance(value, datetime.datetime) else ""


@bp.app_template_filter("ago")
def ago(value, now=None):
    """A recent datetime as "just now", "5 minutes ago", "3 hours ago", "yesterday", then as a day."""
    if not isinstance(value, datetime.datetime):
        return ""
    secs = ((now or localtime.now()) - localtime.local(value)).total_seconds()
    if secs < 120:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} minutes ago"
    if secs < 86400:
        return f"{int(secs // 3600)} hour{'s' if secs >= 7200 else ''} ago"
    if secs < 2 * 86400:
        return "yesterday"
    return day(value)


@bp.before_request
def admins_only():
    if g.user["role"] != "admin":
        abort(403)


def get_user(uid):
    user = one("SELECT * FROM users WHERE userID = %s", (uid,))
    if not user:
        abort(404)
    return user


def scrub(user):
    return {k: v for k, v in user.items() if k not in SECRET}


def waiting_count():
    """Registrations to approve plus untouched feedback, for the count beside the Admin menu item."""
    return (one("SELECT COUNT(*) AS n FROM users WHERE allow_living = %s", (PENDING,))["n"]
            + one("SELECT COUNT(*) AS n FROM app_feedback WHERE status = 'new'")["n"])


def recent_changes():
    """[(date, what changed)], newest first: the site's latest code changes, from its git history. The commit
    titles are written to be read ("Fan chart: photos in the wedges"). Empty when the code isn't a git checkout."""
    from . import ROOT
    try:  # safe.directory: the checkout is owned by root but the site runs as its own user
        out = subprocess.run(["git", "-C", str(ROOT), "-c", "safe.directory=*", "log", f"-{RECENT_CHANGES * 2}",
                              "--date=short", "--format=%ad%x09%s"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):  # no git installed
        return []
    rows = [line.split("\t", 1) for line in out.splitlines() if "\t" in line]
    return [(datetime.date.fromisoformat(d), what) for d, what in rows if not DOCS_ONLY.match(what)][:RECENT_CHANGES]


@bp.get("/")
def home():
    counts = {"registrations": one("SELECT COUNT(*) AS n FROM users WHERE allow_living = %s", (PENDING,))["n"],
              "links": pending_count(),
              "feedback": one("SELECT COUNT(*) AS n FROM app_feedback WHERE status NOT IN ('done', 'wontdo')")["n"]}
    visits = query("SELECT * FROM app_visits ORDER BY last_seen DESC LIMIT %s", (RECENT_VISITS,))
    # Reached over https (directly, or through a proxy that says so) while the login cookie may still go over http.
    https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    insecure_cookies = https and not current_app.config["SESSION_COOKIE_SECURE"]
    return render_template("admin_home.html", counts=counts, visits=visits, insecure_cookies=insecure_cookies,
                           changes=recent_changes(), newer=updates.newer())


@bp.get("/users")
def users():
    show, q = request.args.get("show", "all"), request.args.get("q", "").strip()
    where, args = ["1=1"], []
    if show == "pending":
        where.append("allow_living = %s")
        args.append(PENDING)
    elif show == "disabled":
        where.append("disabled = 1")
    if q:
        where.append("(username LIKE %s OR realname LIKE %s OR email LIKE %s)")
        args += [f"%{q}%"] * 3
    rows = query("SELECT u.*, (SELECT MAX(v.last_seen) FROM app_visits v WHERE v.userID = u.userID) AS last_seen"
                 f" FROM users u WHERE {' AND '.join(where)}"
                 " ORDER BY allow_living = -1 DESC, role = 'admin' DESC, username", args)
    return render_template("admin_users.html", rows=rows, show=show, q=q, roles=ROLES, pending=PENDING,
                           people=people_by_id({r["personID"] for r in rows}))


def user_form(form, user):
    """Validated column values from the account form, and error messages."""
    uid = user["userID"] if user else 0
    v, errors = {}, []
    for k, n in TEXT.items():
        v[k] = form.get(k, "").strip()
        if len(v[k]) > n:
            errors.append(f"{k.capitalize()} is longer than {n} characters.")
    v["notes"] = form.get("notes", "").strip()
    if not v["username"]:
        errors.append("Enter a username.")
    elif one("SELECT 1 AS x FROM users WHERE LOWER(username) = LOWER(%s) AND userID != %s", (v["username"], uid)):
        errors.append("That username is taken.")
    if v["email"] and one("SELECT 1 AS x FROM users WHERE LOWER(email) = LOWER(%s) AND userID != %s",
                          (v["email"], uid)):
        errors.append("Another account already uses that email address.")  # TNG refuses this too
    v["role"] = form.get("role") if form.get("role") in ROLES else "guest"
    for k in RIGHTS:
        v[k] = int(form.get(k) == "1")
    v["disabled"] = int(form.get("disabled") == "1")
    v["personID"] = form.get("personID", "").strip().upper()
    if v["personID"] and not get_person(v["personID"]):
        errors.append(f"There's no person {v['personID']}.")
    password = form.get("password", "")
    inviting = not user and form.get("invite") == "1"
    if inviting:
        if not mail.enabled():
            errors.append("Email isn't set up, so an invitation can't be sent. Set a password instead.")
        if not v["email"]:
            errors.append("Enter their email address to send the invitation to.")
        v.update(password_fields(secrets.token_urlsafe(24)))  # unusable until they set one
    elif password or not user:
        if len(password) < MIN_PASSWORD:
            errors.append(f"The password needs at least {MIN_PASSWORD} characters.")
        v.update(password_fields(password))
    if uid == g.user["userID"] and (v["role"] != "admin" or v["disabled"]):
        errors.append("You can't take away your own admin role or disable your own account.")
    return v, errors


@bp.route("/users/new", methods=["GET", "POST"])
def new_user():
    v, errors = {"role": "guest", "allow_living": 1}, []
    if request.method == "POST":
        v, errors = user_form(request.form, None)
        if not errors:
            _, uid = insert("users", {**v, "dt_registered": now(), "dt_activated": now()})
            audit("add user", "users", str(uid), None, scrub(get_user(uid)))
            if request.form.get("invite") == "1":
                sent = send_reset(get_user(uid), "invite")
                flash(f"Added {v['username']} and emailed them an invitation." if sent else
                      f"Added {v['username']}, but the invitation email failed. Try 'Email a password link' later.")
            else:
                flash(f"Added {v['username']}. Let them know their username and password.")
            return redirect(url_for("admin.users"))
    return render_template("admin_user.html", u=None, v=v, errors=errors, roles=ROLES, rights=RIGHTS, mail=mail.enabled(),
                           person=get_person(v.get("personID")) if v.get("personID") else None)


@bp.route("/users/<int:uid>", methods=["GET", "POST"])
def user(uid):
    u = get_user(uid)
    v, errors = u, []
    if request.method == "POST":
        v, errors = user_form(request.form, u)
        if not errors:
            if u["allow_living"] == PENDING:  # saving a waiting registration approves it
                v["dt_activated"] = now()
            update("users", v, {"userID": uid})
            audit("approve user" if u["allow_living"] == PENDING else "edit user", "users", str(uid),
                  scrub(u), scrub(get_user(uid)))
            if u["allow_living"] == PENDING:
                approved_email(get_user(uid))
            flash(("Approved, and emailed them." if mail.enabled() and u["email"] else
                   "Approved. Let them know they can log in.") if u["allow_living"] == PENDING else "Saved.")
            return redirect(url_for("admin.users"))
        v = {**u, **v}
    return render_template("admin_user.html", u=u, v=v, errors=errors, roles=ROLES, rights=RIGHTS, pending=PENDING,
                           mail=mail.enabled(),
                           person=get_person(v["personID"]) if v["personID"] else None)


@bp.post("/users/<int:uid>/send-link")
def send_link(uid):
    """Email someone a link to set a new password (a forgotten one, or a first one)."""
    u = get_user(uid)
    if not u["email"] or not mail.enabled():
        abort(400)
    flash(f"Emailed {u['username']} a link to set their password." if send_reset(u)
          else "The email didn't go out. Check the mail settings and try again.")
    return redirect(url_for("admin.user", uid=uid))


def delete_users(ids):
    for uid in ids:
        u = get_user(uid)
        execute("DELETE FROM users WHERE userID = %s", (uid,))
        execute("DELETE FROM app_link_requests WHERE userID = %s", (uid,))
        audit("reject registration" if u["allow_living"] == PENDING else "delete user", "users", str(uid),
              scrub(u), None)


@bp.route("/users/<int:uid>/delete", methods=["GET", "POST"])
def delete_user(uid):
    u = get_user(uid)
    if uid == g.user["userID"]:
        abort(400)  # no deleting yourself
    if request.method == "POST":
        delete_users([uid])
        flash(f"Deleted {u['username']}.")
        return redirect(url_for("admin.users"))
    return render_template("confirm_delete.html", title=f"Delete account {u['username']}?",
                           details=[f"{u['realname'] or u['username']} {u['email']}".strip(),
                                    "Their edits stay; only the login goes."],
                           back=url_for("admin.user", uid=uid))


@bp.post("/users/reject")
def reject_registrations():
    """Delete the ticked waiting registrations (mostly spam)."""
    ids = [int(x) for x in request.form.getlist("uid") if x.isdigit()]
    waiting = {r["userID"] for r in query("SELECT userID FROM users WHERE allow_living = %s", (PENDING,))}
    chosen = [x for x in ids if x in waiting]  # only waiting registrations, whatever the form says
    delete_users(chosen)
    flash(f"Deleted {len(chosen)} registration{'s' if len(chosen) != 1 else ''}.")
    return redirect(url_for("admin.users", show="pending"))


def flat(record):
    """One level of fields. Deletes save the record with its linked rows nested: tables become "table.field",
    lists of linked rows a count. A list of rows (arranging media saves each link's order) becomes "ID.field"."""
    if isinstance(record, list):
        record = {str(next((v for k, v in r.items() if k.endswith("ID")), n)): r for n, r in enumerate(record, 1)}
    out = {}
    for k, v in (record or {}).items():
        if isinstance(v, dict):
            out.update({f"{k}.{kk}": vv for kk, vv in v.items()})
        else:
            out[k] = f"{len(v)} linked row{'s' if len(v) != 1 else ''}" if isinstance(v, list) else v
    return out


EMAIL_LIMITS = {"subject": 150, "body": 10000}


@bp.route("/email", methods=["GET", "POST"])
def email_everyone():
    """One message to every account holder who can log in, has an address and hasn't opted out (TNG's no_email).
    Each gets their own copy, so nobody sees the others' addresses. "Send me a test" goes to the admin only."""
    people = digest.recipients()
    v, errors = {"subject": "", "body": ""}, []
    if request.method == "POST":
        v = {k: request.form.get(k, "").strip() for k in EMAIL_LIMITS}
        v["subject"] = " ".join(v["subject"].split())  # one line: no header tricks
        test = request.form.get("test") == "1"
        errors = [f"Write a {k}." for k in ("subject", "message") if not v["body" if k == "message" else k]]
        errors += [f"The {k} is longer than {n:,} characters." for k, n in EMAIL_LIMITS.items() if len(v[k]) > n]
        if not mail.enabled():
            errors.append("Email isn't set up on this server.")
        if test and not g.user["email"]:
            errors.append("Your account has no email address to send the test to.")
        if not errors:
            body = (f"{v['body']}\n\n-- \n{g.user['realname'] or g.user['username']}, {site_name()}\n{link('main.home')}\n"
                    "You get this because you have an account. To stop these, untick the email box under Your account.")
            to = [g.user] if test else people
            failed = [u["username"] for u in to if not mail.send(u["email"], v["subject"], body)]
            sent = len(to) - len(failed)
            if test:
                flash(f"Test sent to {g.user['email']}." if not failed else "The test couldn't be sent; see the server log.")
            else:
                audit("email everyone", "email", "everyone", None,
                      {"subject": v["subject"], "body": v["body"], "sent": sent, "failed": failed})
                flash(f"Sent to {sent} {'person' if sent == 1 else 'people'}."
                      + (f" Couldn't send to: {', '.join(failed)}." if failed else ""))
                return redirect(url_for("admin.home"))
    return render_template("admin_email.html", people=people, v=v, errors=errors, limits=EMAIL_LIMITS,
                           enabled=mail.enabled(), weekly=digest.setting("weekly_email") == "1",
                           weekly_sent=digest.setting("weekly_email_sent"), site_url=current_app.config["SITE_URL"])


@bp.post("/email/weekly")
def weekly_email():
    """Turn "This week in family history" on or off, or send this week's to yourself to see it."""
    action = request.form.get("action")
    if action in ("on", "off"):
        digest.put_setting("weekly_email", "1" if action == "on" else "0")
        audit("edit settings", "app_settings", "weekly_email", None, {"weekly_email": action})
        flash(f"The weekly email is {action}.")
    elif action == "test":
        result = digest.send_to(g.user, localtime.today()) if g.user["email"] and mail.enabled() else False
        flash({True: f"This week's sent to {g.user['email']}.", None: "Nothing recorded this week, so nothing to send.",
               False: "It couldn't be sent: email needs setting up, or your account has no address."}[result])
    else:
        abort(400)
    return redirect(url_for("admin.email_everyone"))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    themes = current_app.extensions["themes"]
    current, errors = {**site_settings(), "theme": default_theme(), "hero_art": hero_art(),
                       "tree_start": stored("tree_start") or "", "update_check": stored("update_check") or ""}, []
    v = current
    if request.method == "POST":
        v = {k: request.form.get(k, "").strip() for k in (*SITE_TEXT, "theme", "hero_art", "tree_start")}
        v["tree_start"] = v["tree_start"].upper()
        v["update_check"] = "" if request.form.get("update_check") else "off"  # on unless turned off
        v["theme"] = v["theme"] or current["theme"]
        v["hero_art"] = v["hero_art"] or current["hero_art"]
        errors = [f"{label} is longer than {n} characters." for k, (label, n, _) in SITE_TEXT.items() if len(v[k]) > n]
        if v["theme"] not in themes or v["hero_art"] not in HERO_ART:
            abort(400)
        if v["tree_start"] and not get_person(v["tree_start"]):
            errors.append(f"There is no person {v['tree_start']} to start the Tree page at.")
        if not errors:
            # The theme named by KITH_THEME and the fan are the defaults anyway; only another choice needs storing.
            for k, value in {**v, "theme": "" if v["theme"] == current_app.config["THEME"] else v["theme"],
                             "hero_art": "" if v["hero_art"] == "fan.svg" else v["hero_art"]}.items():
                if value:
                    execute("REPLACE INTO app_settings (name, value) VALUES (%s, %s)", (k, value))
                else:
                    execute("DELETE FROM app_settings WHERE name = %s", (k,))
            audit("edit settings", "app_settings", "site", current,
                  {**site_settings(), "theme": default_theme(), "hero_art": hero_art(),
                   "tree_start": stored("tree_start") or "", "update_check": stored("update_check") or ""})
            flash("Saved.")
            return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html", v=v, errors=errors, fields=SITE_TEXT, themes=themes, art=HERO_ART,
                           start=get_person(v["tree_start"]) if v["tree_start"] else None,
                           defaults=current_app.extensions["theme"])


def changes(row):
    """[(field, before, after)] for the fields that differ; for adds and deletes, every field that has a value."""
    before = flat(json.loads(row["before_json"])) if row["before_json"] else {}
    after = flat(json.loads(row["after_json"])) if row["after_json"] else {}
    return [(k, before.get(k), after.get(k)) for k in sorted(before.keys() | after.keys())
            if k.rsplit(".", 1)[-1] not in NOISE and str(before.get(k) or "") != str(after.get(k) or "")]


@bp.get("/activity")
def activity():
    who, record = request.args.get("user", ""), request.args.get("record", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    where, args = ["1=1"], []
    if who:
        where.append("username = %s")
        args.append(who)
    if record:
        where.append("record LIKE %s")
        args.append(f"%{record}%")
    sql_where = " AND ".join(where)
    total = one(f"SELECT COUNT(*) AS n FROM app_audit WHERE {sql_where}", args)["n"]
    rows = query(f"SELECT * FROM app_audit WHERE {sql_where} ORDER BY id DESC LIMIT %s OFFSET %s",
                 [*args, PER_PAGE, (page - 1) * PER_PAGE])
    for r in rows:
        r["changes"] = changes(r)
    users = [r["username"] for r in query("SELECT DISTINCT username FROM app_audit ORDER BY username")]
    return render_template("admin_activity.html", rows=rows, users=users, who=who, record=record, page=page,
                           pages=max((total + PER_PAGE - 1) // PER_PAGE, 1), total=total)

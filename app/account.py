"""Accounts without an admin: asking for one, forgetting a password, changing it, choosing a theme.

Asking for an account is built to keep bots out of the admins' way: a hidden field only bots fill in, a minimum
time on the form, a per-IP limit, and nothing reaches the Admin waiting list until the address is confirmed from
the email we send. Links in emails carry a random token; only its SHA-256 is stored, and it expires."""
import hashlib
import io
import json
import secrets
import time
from pathlib import Path

import pymysql
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from PIL import Image, ImageOps, UnidentifiedImageError

from . import mail
from .auth import allow, can_log_in, check_password, password_fields
from .db import execute, insert, query, update
from .edit import audit, now
from .settings import default_theme, site_settings
from .views import one
from .watch import watched

bp = Blueprint("account", __name__, url_prefix="/account")

HOURS = {"signup": 24, "reset": 1, "invite": 24 * 7}
MIN_FORM_SECONDS = 3  # people take longer than this to fill in the form; scripts don't
RESEND_MINUTES = 10  # one email per address per this long, whatever is asked
MIN_PASSWORD = 8
FIELDS = {"username": 100, "realname": 50, "email": 50, "relation": 500}
LABELS = {"username": "Username", "realname": "Your full name", "email": "Email address",
          "relation": "How you're related to the family"}
AVATAR_MAX = 10 * 1024 * 1024
AVATAR_SIZE = 256
CHECK_EMAIL = ("We've sent a link to that address. Open it within a day to confirm the request; then an admin "
               "will look at it. Nothing arrived? Check the spam folder.")


def site_name():
    return site_settings().get("site_title") or current_app.extensions["theme"].get("site_title") or "Family Tree"


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(kind, user_id=None, email=None, data=None):
    token = secrets.token_urlsafe(32)
    execute("INSERT INTO app_tokens (token_hash, kind, userID, email, data, created, expires)"
            " VALUES (%s, %s, %s, %s, %s, NOW(), NOW() + INTERVAL %s HOUR)",
            (digest(token), kind, user_id, email, json.dumps(data) if data else None, HOURS[kind]))
    return token


def find_token(token, *kinds):
    return one(f"SELECT * FROM app_tokens WHERE token_hash = %s AND expires > NOW()"
               f" AND kind IN ({','.join(['%s'] * len(kinds))})", (digest(token), *kinds))


def recently_sent(email):
    return one("SELECT 1 AS x FROM app_tokens WHERE email = %s AND created > NOW() - INTERVAL %s MINUTE",
               (email, RESEND_MINUTES)) is not None


def password_errors(form):
    password = form.get("password", "")
    if len(password) < MIN_PASSWORD:
        return [f"The password needs at least {MIN_PASSWORD} characters."]
    return [] if password == form.get("password2") else ["The two passwords don't match."]


def link(endpoint, **values):
    """An address for email: the site's own (KITH_SITE_URL), whatever host the request claimed."""
    return current_app.config["SITE_URL"].rstrip("/") + url_for(endpoint, **values)


def form_stamp():
    return URLSafeTimedSerializer(current_app.secret_key, salt="account-request").dumps(int(time.time()))


def looks_like_a_bot(form):
    """The hidden "website" box filled in, or the form sent back too fast or with a forged or day-old stamp."""
    if form.get("website"):
        return True
    try:
        issued = URLSafeTimedSerializer(current_app.secret_key, salt="account-request").loads(
            form.get("stamp", ""), max_age=86400)
    except BadSignature:
        return True
    return time.time() - issued < MIN_FORM_SECONDS


@bp.route("/request", methods=["GET", "POST"])
def request_account():
    if not mail.enabled():
        return render_template("account_message.html", title="Ask for an account",
                               message="Account requests are closed for now. Ask a family member who uses this site.")
    v, errors = {}, []
    if request.method == "POST":
        if looks_like_a_bot(request.form):
            return render_template("account_message.html", title="Check your email", message=CHECK_EMAIL)
        if not allow("account-request", 3):
            errors.append("Too many requests from here. Try again in an hour.")
        v = {k: request.form.get(k, "").strip() for k in FIELDS}
        for k, n in FIELDS.items():
            if not v[k]:
                errors.append(f"Fill in {LABELS[k].lower()}.")
            elif len(v[k]) > n:
                errors.append(f"{LABELS[k]} is longer than {n} characters.")
        if "@" not in v["email"]:
            errors.append("Enter a real email address.")
        if v["username"] and one("SELECT 1 AS x FROM users WHERE LOWER(username) = LOWER(%s)", (v["username"],)):
            errors.append("That username is taken. Pick another.")
        errors += password_errors(request.form)
        if not errors:
            send_request_email(v, password_fields(request.form["password"]))
            return render_template("account_message.html", title="Check your email", message=CHECK_EMAIL)
    return render_template("account_request.html", v=v, errors=errors, labels=LABELS, stamp=form_stamp())


def send_request_email(v, password):
    """The confirmation link, or for an address that already has an account, a pointer to "forgot password"
    (so this form can't tell anyone which addresses have accounts)."""
    if recently_sent(v["email"]):
        return
    if one("SELECT 1 AS x FROM users WHERE LOWER(email) = LOWER(%s)", (v["email"],)):
        new_token("signup", email=v["email"])  # nothing to confirm; it only marks the address as recently mailed
        mail.send(v["email"], f"Your account on {site_name()}",
                  f"Someone asked for a new account on {site_name()} with this email address, but it already "
                  f"has one. If you've forgotten your password, get a new one here:\n\n"
                  f"{link('account.forgot')}\n\nIf it wasn't you, ignore this email.")
        return
    execute("DELETE FROM app_tokens WHERE kind = 'signup' AND email = %s", (v["email"],))
    token = new_token("signup", email=v["email"], data={**v, **password})  # the hash, never the password
    mail.send(v["email"], f"Confirm your account request on {site_name()}",
              f"Someone asked for an account on {site_name()} with this email address. If it was you, confirm "
              f"it by opening this link within a day:\n\n{link('account.confirm', token=token)}\n\n"
              f"An admin will then look at your request. If it wasn't you, ignore this email.")


@bp.get("/confirm/<token>")
def confirm(token):
    t = find_token(token, "signup")
    if not t or not t["data"]:
        return render_template("account_message.html", title="Link expired",
                               message="That link has expired or was already used. You can ask again.")
    v = json.loads(t["data"])
    execute("DELETE FROM app_tokens WHERE token_hash = %s", (t["token_hash"],))
    if one("SELECT 1 AS x FROM users WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s)",
           (v["username"], v["email"])):
        return render_template("account_message.html", title="Already taken",
                               message="Someone took that username or email address in the meantime. Ask again.")
    # TNG's own shape for a registration waiting for approval (allow_living = -1).
    insert("users", {"username": v["username"], "realname": v["realname"], "description": v["realname"],
                         "email": v["email"], "notes": v["relation"], "password": v["password"],
                         "password_type": v.get("password_type", "md5"), "role": "guest", "allow_living": -1,
                         "dt_registered": now()})
    notify_admins()
    return render_template("account_message.html", title="Thanks, that's confirmed",
                           message="An admin will look at your request. You'll get an email when you can log in.")


def notify_admins():
    """At most one email a day to NOTIFY_EMAIL while confirmed requests wait."""
    to = current_app.config["NOTIFY_EMAIL"]
    if not to:
        return
    last = one("SELECT value FROM app_settings WHERE name = 'last_request_notice'")
    if last and time.time() - float(last["value"]) < 86400:
        return
    n = one("SELECT COUNT(*) AS n FROM users WHERE allow_living = -1")["n"]
    if mail.send(to, f"Account requests waiting on {site_name()}",
                 f"{n} account request(s) are waiting for approval:\n\n{link('admin.users', show='pending')}"):
        execute("REPLACE INTO app_settings (name, value) VALUES ('last_request_notice', %s)", (str(time.time()),))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        who = request.form.get("who", "").strip()
        if not allow("forgot", 5):
            return render_template("account_forgot.html", error="Too many tries from here. Try again in an hour.")
        user = one("SELECT * FROM users WHERE (LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s))"
                   " AND email <> ''", (who, who)) if who else None
        if user and can_log_in(user) and not recently_sent(user["email"]):
            send_reset(user)
        return render_template("account_message.html", title="Check your email",
                               message="If that matches an account with an email address, we've sent it a link to "
                                       "set a new password. The link works for an hour.")
    return render_template("account_forgot.html", error=None)


def send_reset(user, kind="reset"):
    token = new_token(kind, user_id=user["userID"], email=user["email"])
    url = link("account.reset", token=token)
    if kind == "invite":
        return mail.send(user["email"], f"You're invited to {site_name()}",
                         f"You have an account on {site_name()}. Your username is {user['username']}.\n\n"
                         f"Choose your password here (the link works for a week):\n\n{url}")
    return mail.send(user["email"], f"Set a new password on {site_name()}",
                     f"Someone asked to reset the password for {user['username']} on {site_name()}. To choose a new "
                     f"one, open this link within an hour:\n\n{url}\n\nIf it wasn't you, ignore this email; your "
                     f"password hasn't changed.")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    t = find_token(token, "reset", "invite")
    user = one("SELECT * FROM users WHERE userID = %s", (t["userID"],)) if t else None
    if not user or not can_log_in(user):
        return render_template("account_message.html", title="Link expired",
                               message="That link has expired or was already used. Ask for a new one.")
    errors = []
    if request.method == "POST":
        errors = password_errors(request.form)
        if not errors:
            update("users", password_fields(request.form["password"]), {"userID": user["userID"]})
            execute("DELETE FROM app_tokens WHERE userID = %s AND kind IN ('reset', 'invite')", (user["userID"],))
            flash("Your password is set. Log in with it.")
            return redirect(url_for("auth.login"))
    return render_template("account_password.html", user=user, errors=errors, current=False)


@bp.route("/password", methods=["GET", "POST"])
def password():
    """Change your own password (logged in)."""
    errors = []
    if request.method == "POST":
        if not check_password(request.form.get("current", ""), g.user):
            errors.append("Your current password isn't right.")
        errors += password_errors(request.form)
        if not errors:
            update("users", password_fields(request.form["password"]), {"userID": g.user["userID"]})
            audit("change password", "users", str(g.user["userID"]), None, None)
            flash("Password changed.")
            return redirect(url_for("account.profile"))
    return render_template("account_password.html", user=g.user, errors=errors, current=True)


def chosen_theme():
    """The theme the logged-in user picked, if it's still installed; None means the site's default."""
    if not g.get("user"):
        return None
    try:
        rows = query("SELECT theme FROM app_user_prefs WHERE userID = %s", (g.user["userID"],))
    except pymysql.err.MySQLError:  # nobody has picked one yet, so the table doesn't exist
        return None
    name = rows[0]["theme"] if rows else None
    return name if name in current_app.extensions["themes"] else None


def avatar_file(user_id):
    return Path(current_app.instance_path) / "avatars" / f"{int(user_id)}.png"


@bp.app_template_global()
def avatar_url():
    """The logged-in user's picture, or None for initials. The file's time in the address so a new one shows at once."""
    f = avatar_file(g.user["userID"])
    return url_for("account.avatar", v=int(f.stat().st_mtime)) if f.exists() else None


def save_avatar(upload):
    """Crop the upload square and re-encode it small as PNG, so only a real image is kept (and no location data)."""
    data = upload.read(AVATAR_MAX + 1)
    if len(data) > AVATAR_MAX:
        raise ValueError("The picture is larger than 10MB.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGBA"), (AVATAR_SIZE, AVATAR_SIZE))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("The picture isn't a readable image.")
    target = avatar_file(g.user["userID"])
    target.parent.mkdir(parents=True, exist_ok=True)
    im.save(target, "PNG")


@bp.get("/avatar")
def avatar():
    f = avatar_file(g.user["userID"])
    if not f.exists():
        abort(404)
    return send_file(f, mimetype="image/png", max_age=86400)


@bp.route("/", methods=["GET", "POST"])
def profile():
    """Your account: your picture, how the site looks for you, whether you get emails sent to everyone, and the way to change
    your password."""
    themes = current_app.extensions["themes"]
    if request.method == "POST":
        choice = request.form.get("theme", "")
        if choice and choice not in themes:
            abort(400)
        if (upload := request.files.get("avatar")) and upload.filename:
            try:
                save_avatar(upload)
            except ValueError as e:
                flash(str(e))
                return redirect(url_for("account.profile"))
        elif request.form.get("remove_avatar") == "1":
            avatar_file(g.user["userID"]).unlink(missing_ok=True)
        execute("REPLACE INTO app_user_prefs (userID, theme) VALUES (%s, %s)", (g.user["userID"], choice or None))
        # TNG's own opt-out from mass email, so TNG and this site agree.
        update("users", {"no_email": int(request.form.get("group_email") != "1")}, {"userID": g.user["userID"]})
        flash("Saved.")
        return redirect(url_for("account.profile"))
    default = default_theme()
    return render_template("account_profile.html", themes=themes, chosen=chosen_theme(), default=default,
                           site=themes[default], watched=watched())


def approved_email(user):
    """Tell someone their request was approved, if email is set up and we have their address."""
    if user["email"]:
        mail.send(user["email"], f"Your account on {site_name()} is ready",
                  f"Your account request was approved. Log in with your username, {user['username']}, here:\n\n"
                  f"{link('auth.login')}")


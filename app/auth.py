import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import execute, query, update

bp = Blueprint("auth", __name__)

# Failed logins live in a small SQLite file in instance/, so limits survive restarts and are shared
# between workers. The TNG database stays read-only.
WINDOW = 900
LIMITS = {"user": 5, "ip": 20}  # per username+IP, and per IP across all usernames, within WINDOW
KEEP = 3600  # longest window anything counts over (account requests, password resets)


@contextmanager
def _throttle():
    Path(current_app.instance_path).mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(current_app.instance_path) / "throttle.db", timeout=5)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS failures (key TEXT, at REAL)")
        conn.execute("DELETE FROM failures WHERE at < ?", (time.time() - KEEP,))
        yield conn


def _keys(username):
    ip = request.remote_addr or "?"
    return {"user": f"user:{username.lower()}|{ip}", "ip": f"ip:{ip}"}


def throttled(username):
    with _throttle() as conn:
        return any(conn.execute("SELECT COUNT(*) FROM failures WHERE key = ? AND at > ?",
                                (k, time.time() - WINDOW)).fetchone()[0] >= LIMITS[kind]
                   for kind, k in _keys(username).items())


def allow(action, limit, window=KEEP):
    """Count one try at an action (e.g. "signup") from this IP; False once it has been tried limit times
    within window seconds."""
    key = f"{action}:{request.remote_addr or '?'}"
    with _throttle() as conn:
        if conn.execute("SELECT COUNT(*) FROM failures WHERE key = ? AND at > ?",
                        (key, time.time() - window)).fetchone()[0] >= limit:
            return False
        conn.execute("INSERT INTO failures VALUES (?, ?)", (key, time.time()))
    return True


def record_failure(username):
    with _throttle() as conn:
        conn.executemany("INSERT INTO failures VALUES (?, ?)", [(k, time.time()) for k in _keys(username).values()])


def clear_failures(username):
    with _throttle() as conn:
        conn.execute("DELETE FROM failures WHERE key = ?", (_keys(username)["user"],))


def tng_escape(s):
    """TNG runs mysqli_real_escape_string on the password *before* hashing it; match that."""
    return "".join({"\0": "\\0", "\n": "\\n", "\r": "\\r", "\\": "\\\\",
                    "'": "\\'", '"': '\\"', "\x1a": "\\Z"}.get(c, c) for c in s)


def tng_md5(password):
    return hashlib.md5(tng_escape(password).encode("utf-8")).hexdigest()


def password_fields(password):
    """users columns for a new password. md5 while TNG still has to log these users in; once
    KITH_STRONG_PASSWORDS is on (at cutover) a salted, slow PBKDF2 hash that TNG can't read."""
    if current_app.config["STRONG_PASSWORDS"]:
        # pbkdf2 rather than werkzeug's default scrypt: its string fits TNG's 128-character password column.
        return {"password": generate_password_hash(password, "pbkdf2:sha256"), "password_type": "pbkdf2"}
    return {"password": tng_md5(password), "password_type": "md5"}


def check_password(password, user):
    if user["password_type"] == "pbkdf2":
        return check_password_hash(user["password"], password)
    # TNG's md5 (its default, and every hash it made for us). TNG 15 also rejects an empty type.
    return user["password_type"] == "md5" and hmac.compare_digest(tng_md5(password), user["password"])


def can_log_in(user):
    # allow_living == -1 is TNG's "registered but not yet activated".
    return not user["disabled"] and user["allow_living"] != -1


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    # Every POST (login, logout, edits) must carry the per-session token from csrf_token().
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Form expired. Go back, reload the page and try again.")


VISIT_GAP = 30  # minutes away before coming back counts as a new visit
SEEN_EVERY = 60  # seconds; the visit's last-seen time is only written this often, not on every page


def record_visit():
    """Like TNG's access log, but one row per visit (who, from where, when it started and last activity)."""
    now = time.time()
    if now - session.get("seen", 0) < SEEN_EVERY:
        return
    session["seen"] = now
    uid = g.user["userID"]
    rows = query("SELECT id FROM app_visits WHERE userID = %s AND last_seen > NOW() - INTERVAL %s MINUTE"
                 " ORDER BY id DESC LIMIT 1", (uid, VISIT_GAP))
    if rows:
        execute("UPDATE app_visits SET last_seen = NOW() WHERE id = %s", (rows[0]["id"],))
    else:
        execute("INSERT INTO app_visits (userID, username, ip, started, last_seen) VALUES (%s, %s, %s, NOW(), NOW())",
                (uid, g.user["username"], request.remote_addr or "?"))


@bp.before_app_request
def load_user():
    g.user = None
    if uid := session.get("uid"):
        rows = query("SELECT * FROM users WHERE userID = %s", (uid,))
        if rows and can_log_in(rows[0]):
            g.user = rows[0]
            if request.endpoint not in ("static", "theme_file", "user_theme"):
                record_visit()
        else:
            session.clear()
    # The whole site requires login, like the TNG config ($requirelogin = 1), except the public welcome page
    # and the account pages for people who can't log in (asking for an account, a forgotten password).
    public = ("auth.login", "static", "theme_file", "user_theme", "main.home", "account.request_account", "account.confirm",
              "account.forgot", "account.reset")
    if g.user is None and request.endpoint not in public:
        return redirect(url_for("auth.login", next=request.full_path))


def _safe_next(target):
    # Only local paths, so the login form can't be used as an open redirect. Browsers read a backslash as a slash,
    # so "/\evil.example" would leave the site like "//evil.example"; control characters are refused too.
    local = target and target.startswith("/") and not target.startswith("//")
    return target if local and "\\" not in target and target.isprintable() else url_for("main.home")


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if throttled(username):
            error = "Too many failed attempts. Try again in 15 minutes."
        else:
            rows = query("SELECT * FROM users WHERE username = %s", (username,))
            user = rows[0] if rows else None
            if user and check_password(request.form.get("password", ""), user) and can_log_in(user):
                session.clear()  # new session (and CSRF token) on login
                session["uid"] = user["userID"]
                session.permanent = True  # a dated cookie, so phones don't drop it when they close the browser
                clear_failures(username)
                execute("UPDATE users SET lastlogin = NOW() WHERE userID = %s", (user["userID"],))  # as TNG does
                if current_app.config["STRONG_PASSWORDS"] and user["password_type"] == "md5":
                    # The only time we have the password itself: swap the old md5 for a strong hash.
                    update("users", password_fields(request.form["password"]), {"userID": user["userID"]})
                return redirect(_safe_next(request.form.get("next")))
            record_failure(username)
            error = "Login failed."
    return render_template("login.html", error=error, next=request.values.get("next", ""))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

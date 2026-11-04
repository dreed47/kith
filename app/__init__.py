import os
import re
import secrets
import subprocess
import tomllib
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, g, request, send_from_directory, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from markupsafe import Markup, escape
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from . import db, privacy

ROOT = Path(__file__).resolve().parent.parent
THEMES_DIR = ROOT / "themes"


def env(name, default=None):
    """A setting from the environment: KITH_<name>, or GENEALOGY_<name> as sites set up before the rename have it."""
    return os.environ.get(f"KITH_{name}", os.environ.get(f"GENEALOGY_{name}", default))


def release():
    """pyproject's version, e.g. "0.2.0"."""
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def version():
    """For the footer: pyproject's version, plus the commit when the code is a git checkout, e.g. "0.2.0 (34f4426)"."""
    v = release()
    try:  # safe.directory: the checkout is owned by root but the site runs as its own user
        commit = subprocess.run(["git", "-C", str(ROOT), "-c", "safe.directory=*", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):  # no git installed
        commit = ""
    return f"{v} ({commit})" if commit else v


def secret_key(instance_dir):
    """KITH_SECRET_KEY if set, else a key generated once and kept in instance/, so restarts don't log users out."""
    if key := env("SECRET_KEY"):
        return key
    path = Path(instance_dir) / "secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return path.read_text().strip()


def load_theme(name):
    theme_dir = THEMES_DIR / name
    if not (theme_dir / "theme.css").is_file():
        raise RuntimeError(f"theme {name!r} not found in {THEMES_DIR}")
    meta = {}
    if (theme_dir / "theme.toml").is_file():
        meta = tomllib.loads((theme_dir / "theme.toml").read_text())
    return theme_dir, meta


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ASK = "?subject=" + quote("Account request") + "&body=" + quote("Hi, I'd like an account on the family tree. My name is ")


def mailto(text):
    """Text with each email address made a link that opens a ready-made account request."""
    return Markup(EMAIL.sub(lambda m: f'<a href="mailto:{m[0]}{ASK}">{m[0]}</a>', str(escape(text))))


def create_app(config=None):
    app = Flask(__name__, instance_path=env("INSTANCE", str(ROOT / "instance")))
    app.config.update(
        THEME=env("THEME", "default"),
        DB_HOST=env("DB_HOST", "127.0.0.1"),
        DB_PORT=int(env("DB_PORT", "3307")),
        DB_USER=env("DB_USER", "genealogy"),
        DB_PASSWORD=env("DB_PASSWORD", "dev"),
        DB_NAME=env("DB_NAME", "kith"),
        TREE=env("TREE", "tree1"),
        MEDIA_ROOT=env("MEDIA_ROOT", str(ROOT / "data" / "media")),
        # Generated thumbnails and deleted-media trash. Keep these out of any web-served folder.
        THUMB_ROOT=env("THUMB_ROOT", ""),
        TRASH_ROOT=env("TRASH_ROOT", ""),
        # Feedback reports are copied to this repo's issues when both are set (token: issues write only).
        GITHUB_REPO=env("GITHUB_REPO", ""),
        GITHUB_TOKEN=env("GITHUB_TOKEN", ""),
        # Outgoing email (account confirmations, password resets, invitations). Off while SMTP_USER is empty.
        SMTP_HOST=env("SMTP_HOST", "smtp.gmail.com"),
        SMTP_PORT=int(env("SMTP_PORT", "587")),
        SMTP_USER=env("SMTP_USER", ""),
        SMTP_PASSWORD=env("SMTP_PASSWORD", ""),
        MAIL_FROM=env("MAIL_FROM", ""),
        # The site's own address (https://tree.example.org), for links in email sent from outside a page visit.
        SITE_URL=env("SITE_URL", ""),
        # Strong password hashes (PBKDF2); 0 keeps writing the md5 ones TNG can read, for a site TNG still logs into.
        # Old md5 hashes are upgraded as each person logs in. SECURE_COOKIES: send the session cookie over https only.
        STRONG_PASSWORDS=env("STRONG_PASSWORDS", "1") == "1",
        SESSION_COOKIE_SECURE=env("SECURE_COOKIES", "0") == "1",
        # Where "new account requests are waiting" notices go (at most one a day). Empty: no notices.
        NOTIFY_EMAIL=env("NOTIFY_EMAIL", ""),
        # What deploy/backup.sh last wrote about the backups (Admin > Backups). Missing where it doesn't run.
        BACKUP_STATUS=env("BACKUP_STATUS", "/var/lib/kith-backup/status.json"),
        # What else backs up this server, e.g. "Proxmox backups", so the Backups page doesn't warn they're only here.
        BACKUP_OFFSITE=env("BACKUP_OFFSITE", ""),
        SESSION_COOKIE_SAMESITE="Lax",
        # Logins last 90 days from the last visit (Flask renews the cookie on each request).
        PERMANENT_SESSION_LIFETIME=timedelta(days=90),
        MAX_CONTENT_LENGTH=int(env("MAX_UPLOAD_MB", "50")) * 1024 * 1024,
        # Set to 1 when running behind the reverse proxy, so client IPs (used by login throttling) are real.
        TRUSTED_PROXIES=int(env("TRUSTED_PROXIES", "0")),
        # 0 turns off the daily version check (app/updates.py) for good, whatever Site settings say.
        UPDATE_CHECK=env("UPDATE_CHECK", "1") != "0",
    )
    if config:
        app.config.update(config)
    app.config["THUMB_ROOT"] = app.config["THUMB_ROOT"] or str(Path(app.config["MEDIA_ROOT"]) / ".thumbs")
    app.config["TRASH_ROOT"] = app.config["TRASH_ROOT"] or str(Path(app.config["MEDIA_ROOT"]) / ".deleted")
    if not app.config["SECRET_KEY"]:  # Flask pre-fills it with None
        app.config["SECRET_KEY"] = secret_key(app.instance_path)
    if n := app.config["TRUSTED_PROXIES"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    theme_dir, meta = load_theme(app.config["THEME"])
    app.extensions["theme"] = meta  # the theme's own values, shown as defaults on the admin settings page
    # Every installed theme, which each user may pick for themselves (Your account). Only its colors and type
    # apply then; template overrides come from the site theme alone.
    app.extensions["themes"] = dict(sorted(
        ((p.name, load_theme(p.name)[1]) for p in THEMES_DIR.iterdir() if (p / "theme.css").is_file()),
        key=lambda t: (t[1].get("name") or t[0]).lower()))
    # Theme templates win; anything a theme doesn't override falls back to app/templates.
    # "app/<name>" always reaches the default, so a theme can extend the template it overrides.
    default = app.jinja_loader
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(theme_dir / "templates"), default, PrefixLoader({"app": default}),
    ])

    @app.context_processor
    def theme_context():
        from .settings import hero_art, site_settings
        from .account import chosen_mode, chosen_theme
        from .settings import default_theme
        name = chosen_theme() or default_theme()
        css = url_for("theme_file", filename="theme.css") if name == app.config["THEME"] else url_for("user_theme", name=name)
        return {"theme": {**meta, **site_settings()}, "theme_css": css,  # admin-edited text wins over theme.toml
                "mode": chosen_mode(), "hero_art": url_for("static", filename=hero_art())}

    # Globals (not context) so imported macros can use them too.
    from .dates import display as display_date
    app.jinja_env.filters["d"] = display_date
    from .localtime import local
    app.jinja_env.filters["local"] = local
    app.jinja_env.filters["mailto"] = mailto
    from .auth import csrf_token
    from .edit import owner_name
    from .media import is_image, is_url
    app.jinja_env.globals.update(pname=privacy.display_name, rights=privacy.rights, is_image=is_image, is_url=is_url,
                                 csrf_token=csrf_token, g=g, owner_name=owner_name)

    from .relations import phrase
    from .views import url_with
    app.jinja_env.globals.update(url_with=url_with, rel_phrase=phrase)
    app.jinja_env.globals["kith_version"] = version()
    from .me import my_link_request, pending_count
    app.jinja_env.globals.update(my_link_request=my_link_request, pending_link_requests=pending_count)
    from .admin import waiting_count
    app.jinja_env.globals.update(admin_waiting=waiting_count)

    @app.get("/theme/<path:filename>")
    def theme_file(filename):
        if filename != "theme.css" and not filename.startswith("static/"):
            abort(404)
        return send_from_directory(theme_dir, filename)

    @app.get("/themes/<name>.css")
    def user_theme(name):
        if name not in app.extensions["themes"]:
            abort(404)
        return send_from_directory(THEMES_DIR / name, "theme.css")

    versioned = {"static": lambda v: Path(app.static_folder) / v["filename"],
                 "theme_file": lambda v: theme_dir / v["filename"],
                 "user_theme": lambda v: THEMES_DIR / v["name"] / "theme.css"}

    @app.url_defaults
    def file_version(endpoint, values):
        """?v=<file's mtime> on CSS/JS/icons, so browsers can keep them for a year and still get a new copy
        the moment a deploy changes the file."""
        if endpoint in versioned and "v" not in values and (p := versioned[endpoint](values)).is_file():
            values["v"] = int(p.stat().st_mtime)

    @app.after_request
    def cache_headers(resp):
        if request.endpoint in versioned and request.args.get("v"):
            resp.cache_control.no_cache = None
            resp.cache_control.public = True
            resp.cache_control.max_age = 365 * 86400
            resp.cache_control.immutable = True
        elif resp.cache_control.public:  # photos and avatars: never let a shared cache (Cloudflare) keep them
            resp.cache_control.public = False
            resp.cache_control.private = True
        return resp

    @app.after_request
    def no_sniffing(response):
        # Browsers must trust our content types, so an uploaded file can't be run as a page.
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    db.init_app(app)
    from . import account, admin, auth, backups, charts, checks, comments, digest, edit, edit_events, edit_family, edit_media, edit_places, event_types, feedback, gedcom, geocode, gedcom_import, home, legacy, living, me, media, merge, news, places, reports, setup, sources, statistics, surname_aliases, census_links, timeline, updates, views, watch  # noqa: F401 (backups/edit_*/gedcom/home/reports add routes)
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(media.bp)
    app.register_blueprint(places.bp)
    app.register_blueprint(edit.bp)
    app.register_blueprint(me.bp)
    app.register_blueprint(feedback.bp)
    app.register_blueprint(comments.bp)
    app.register_blueprint(watch.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(account.bp)
    app.register_blueprint(digest.bp)
    app.register_blueprint(geocode.bp)
    app.register_blueprint(setup.bp)
    app.register_blueprint(updates.bp)
    return app

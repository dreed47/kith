"""Once a day, ask the Kith project whether a newer version is out, so Admin can say so. The question carries this
site's random ID and its version, nothing else (no names, address or tree data); the project counts running sites
from it. Off under Admin > Site settings, or for good with KITH_UPDATE_CHECK=0. The kith-updates timer runs `flask --app app updates check`. The other
end is worker/ in the Kith repo."""
import json
import re
import urllib.error
import urllib.request
import uuid

import click
from flask import Blueprint, current_app

from . import release
from .db import execute
from .settings import stored

bp = Blueprint("updates", __name__)
CHECK_URL = "https://check.kithgenealogy.org/v1/check"
VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")


def enabled():
    """On unless KITH_UPDATE_CHECK=0, or an admin turned it off."""
    return current_app.config["UPDATE_CHECK"] and stored("update_check") != "off"


def site_id():
    """This site's random ID, made on the first check. It stays the same across restarts and updates."""
    sid = stored("site_id")
    if not sid:
        sid = uuid.uuid4().hex
        execute("REPLACE INTO app_settings (name, value) VALUES ('site_id', %s)", (sid,))
    return sid


def parts(v):
    return tuple(int(n) for n in v.split("."))


def check():
    """Ask, and remember the newest version the answer names. None when turned off or the answer didn't come."""
    if not enabled():
        return None
    body = json.dumps({"site": site_id(), "version": release()}).encode()
    req = urllib.request.Request(CHECK_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": f"kith/{release()}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            latest = json.loads(r.read(4096)).get("latest")
    except (OSError, ValueError, AttributeError):  # down, not JSON, or not a JSON object
        return None
    if not isinstance(latest, str) or not VERSION.fullmatch(latest):  # only ever show a plain version number
        return None
    execute("REPLACE INTO app_settings (name, value) VALUES ('latest_version', %s)", (latest,))
    return latest


def newer():
    """The newest version, when it's newer than this one and checking is on; else None."""
    latest = stored("latest_version")
    if enabled() and latest and VERSION.fullmatch(latest) and parts(latest) > parts(release()):
        return latest
    return None


@bp.cli.command("check")
def check_command():
    """Ask whether a newer Kith is out (the timer runs this daily)."""
    if not enabled():
        return click.echo("Turned off (Admin > Site settings, or KITH_UPDATE_CHECK=0).")
    latest = check()
    click.echo("No answer; trying again tomorrow." if latest is None else
               f"Kith {latest} is available." if newer() else f"Up to date ({release()}).")

"""Times shown in each viewer's own time zone. Stored times are the server's local time (what MariaDB's NOW(),
Python's datetime.now() and TNG all write), so they're converted from that. The viewer's zone comes from the
"tz" cookie that static/tz.js sets from the browser; without it (first page ever, or outside a request) times
stay in the server's zone. Genealogy dates (births, deaths) are calendar dates and are never converted."""
import datetime
from zoneinfo import ZoneInfo

from flask import has_request_context, request


def zone():
    """The viewer's ZoneInfo, or None. The cookie is the browser's say-so, so only real zone names count."""
    if not has_request_context():
        return None
    try:
        return ZoneInfo(request.cookies.get("tz", ""))
    except (ValueError, KeyError):  # ZoneInfoNotFoundError is a KeyError; paths and "" are ValueErrors
        return None


def local(value):
    """A stored datetime as the viewer's wall-clock time (still naive, so it formats like before). TNG's zero
    dates arrive as strings and pass through."""
    z = zone()
    return value.astimezone(z).replace(tzinfo=None) if z and isinstance(value, datetime.datetime) else value


def now():
    return local(datetime.datetime.now())


def today():
    return now().date()

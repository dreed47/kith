"""This week in family history: a Monday email of the coming week's births, marriages and deaths, each person's
closest relatives first. Off until an admin turns it on (Admin > Email everyone); genealogy-digest.timer runs
`flask --app app digest send`, which sends at most once per week."""
import datetime

import click
import pymysql
from flask import Blueprint, current_app, g

from . import mail
from .account import link
from .db import execute, query
from .privacy import display_name, rights
from .relations import phrase
from .reports import dated_events
from .views import describe_relationship

bp = Blueprint("digest", __name__)
SHOWN = 25  # a week in a big tree holds hundreds; the closest relatives make the cut
PENDING = -1  # TNG's allow_living value for "registered, not approved yet"


def recipients():
    """Everyone who can log in, has an address and hasn't opted out (TNG's no_email)."""
    return query("SELECT * FROM users WHERE email <> '' AND disabled = 0 AND allow_living <> %s"
                 " AND no_email = 0 ORDER BY realname, username", (PENDING,))


def setting(name):
    try:
        rows = query("SELECT value FROM app_settings WHERE name = %s", (name,))
    except pymysql.err.MySQLError:  # no settings saved yet
        return ""
    return rows[0]["value"] if rows else ""


def put_setting(name, value):
    execute("REPLACE INTO app_settings (name, value) VALUES (%s, %s)", (name, value))


def closeness(rel):
    """Steps between two people: parents 1, grandparents 2, first cousins 4; unrelated last."""
    if rel is None:
        return 99
    return rel.get("up_a", 0) + rel.get("up_b", 0) + (rel["kind"] in ("spouse", "marriage"))


def week_events(start):
    """The coming week's events g.user may see, closest relatives first (then oldest first): ([events], total)."""
    me = g.user["personID"]
    items = []
    for i in range(7):
        day = start + datetime.timedelta(days=i)
        for e in dated_events(day.month, day.day):
            e["day"], e["years_ago"] = day, day.year - e["year"]
            # A couple counts by whichever spouse is the closer relative, and the line names that one.
            people = [e["person"]] if "person" in e else [s for s in e["spouses"] if s]
            rels = [(describe_relationship(me, p["personID"]) if me else None, p) for p in people]
            e["rel"], e["relative"] = min(rels, key=lambda r: closeness(r[0]), default=(None, None))
            e["mine"] = bool(me) and me in (p["personID"] for p in people)
            items.append(e)
    items.sort(key=lambda e: (0 if e["mine"] else closeness(e["rel"]), e["year"]))
    shown = sorted(items[:SHOWN], key=lambda e: (e["day"], e["year"]))
    return shown, len(items)


def line(e):
    if "person" in e:
        who, url = display_name(e["person"]), link("main.person", pid=e["person"]["personID"])
    else:
        who = " & ".join(display_name(s) for s in e["spouses"] if s) or "A couple"
        url = link("main.family", fid=e["family"]["familyID"])
    rel = "you" if e["mine"] else phrase(e["rel"], "your") if e["rel"] and e["rel"]["kind"] != "self" else ""
    if rel and "family" in e and not e["mine"]:
        r = e["relative"]
        rel = f"{r['firstname'].split()[0] if r['firstname'] and rights(r)['living'] else display_name(r)} is {rel}"
    return (f"  {e['year']}  {who} {e['kind']}, {e['years_ago']} years ago" + (f" ({rel})" if rel else "")
            + f"\n        {url}")


def compose(start):
    """Subject and body of g.user's email for the week from start, or None if nothing happened that week."""
    events, total = week_events(start)
    if not events:
        return None
    end = start + datetime.timedelta(days=6)
    span = f"{start:%-d %b} – {end:%-d %b}"
    out, last = [f"This week in family history, {span}"], None
    for e in events:
        if e["day"] != last:
            out.append(f"\n{e['day']:%A %-d %B}")
            last = e["day"]
        out.append(line(e))
    if total > len(events):
        out.append(f"\nAnd {total - len(events)} more this week: {link('main.anniversaries')}")
    out.append("\n-- \nYou get this because you have an account. To stop these, untick the email box under Your account:\n"
               + link("account.profile"))
    return f"This week in family history: {span}", "\n".join(out)


def send_to(user, start):
    """Compose for this user (their own view of the tree) and send. True if sent, False if failed, None if empty."""
    saved, g.user = g.get("user"), user
    try:
        message = compose(start)
    finally:
        g.user = saved
    return None if message is None else mail.send(user["email"], *message)


@bp.cli.command("send")
@click.option("--force", is_flag=True, help="Send even if turned off or already sent this week.")
def send_command(force):
    """Send this week's email to everyone on the list (the timer runs this on Mondays)."""
    start = datetime.date.today()
    base = current_app.config["SITE_URL"]
    if not base or not mail.enabled():
        raise click.ClickException("Needs KITH_SITE_URL and email (KITH_SMTP_USER and the rest).")
    if not force and setting("weekly_email") != "1":
        return click.echo("The weekly email is turned off (Admin > Email everyone).")
    if not force and setting("weekly_email_sent") == start.isoformat():
        return click.echo("Already sent today.")
    with current_app.test_request_context(base_url=base):
        results = {u["username"]: send_to(u, start) for u in recipients()}
        put_setting("weekly_email_sent", start.isoformat())
    failed = [k for k, v in results.items() if v is False]
    click.echo(f"Sent {sum(v is True for v in results.values())}, nothing to say to {sum(v is None for v in results.values())}"
          + (f", failed: {', '.join(failed)}" if failed else "") + ".")

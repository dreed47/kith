"""Reports: the genealogical register (TNG's register.php: descendants numbered and told in sentences), a person's
life story (not in TNG) and dates & anniversaries (TNG's anniversaries.php and calendar.php)."""
import calendar
import datetime
import re

from flask import abort, redirect, render_template, request, url_for
from markupsafe import Markup

from . import localtime
from .dates import display as display_date
from .db import query
from .privacy import families_filter, people_filter, rights
from .views import (DESCEND_GENS, bp, children_by_family, families_of, gens_arg, notes_for, one,
                    people_by_id, person_detail, record_notes, relatives, tree)

KINDS = {"born": ("Birth", "birthdate"), "christened": ("Christening", "altbirthdate"),
         "died": ("Death", "deathdate"), "buried": ("Burial", "burialdate"), "married": ("Marriage", "marrdate")}
DEFAULT_KINDS = ("born", "married", "died")
LIST_LIMIT = 300  # a whole month can hold thousands; the calendar is the way to browse those
# Life story wording for common event types; any other type reads "<Event type> <when and where>: <details>".
# Military records stay plain: many are draft cards of men who never served.
STORY_VERBS = {"OCCU": "worked as", "EDUC": "went to school", "GRAD": "graduated",
               "CENS": "was counted in the census", "RESI": "lived", "ADDR": "lived",
               "IMMI": "immigrated",
               "EMIG": "emigrated", "NATU": "was naturalized", "BAPM": "was baptized", "CONF": "was confirmed",
               "FCOM": "made a first communion", "ADOP": "was adopted", "WILL": "made a will", "RETI": "retired"}
# Identifiers and contact details aren't part of a life; BIRT, DEAT and BURI events repeat the person's own vitals.
STORY_SKIP = {"REFN", "RFN", "SSN", "AFN", "_UID", "_DBID", "EMAIL", "_EMAIL", "URL", "WWW", "SEX",
              "BIRT", "DEAT", "BURI"}
# TNG imports put a record's title where the place goes ("U.S. Federal Population Census"); a story leaves it out.
RECORD_TITLE = re.compile(r"census|draft|regist|records|\bcards?\b|\blists?\b", re.I)
MERGED = {"CENS", "EDUC"}  # one record a year or a decade: told in one sentence that lists the years
NUMBERS = "no one two three four five six seven eight nine ten eleven twelve".split()
ORDINALS = "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split()


@bp.app_template_global()
def when_where(date, place=""):
    """ "on 1 Jan 1900 in Synthville", "in 1900", "about 1850": the date and place part of a sentence."""
    d = display_date(date)
    if re.fullmatch(r"\d{1,2} \w{3} \d{3,4}", d):
        d = f"on {d}"
    elif re.fullmatch(r"((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) )?\d{3,4}(-\d{3,4})?", d):
        d = f"in {d}"
    return " ".join(x for x in (d, f"in {place}" if place else "") if x)


@bp.app_template_global()
def roman(n):
    out = ""
    for value, letters in ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out, n = out + letters, n - value
    return out


@bp.app_template_global()
def in_words(n, ordinal=False):
    """ "three", "third"; past twelve, "13" and "13th"."""
    words = ORDINALS if ordinal else NUMBERS
    if n < len(words):
        return words[n]
    if not ordinal:
        return str(n)
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def descent(root, gens):
    """Walk down from root for gens generations: (numbers {pid: n}, lineage, generations, fams, kids). Every child
    gets the next number when first listed; those with a family of their own are in the next generation."""
    pid = root["personID"]
    numbers, lineage, generations = {pid: 1}, {pid: []}, []
    fams, kids, current = {}, {}, [root]
    new_fams, depth = families_of([pid]), 0
    while current:
        generations.append(current)
        new_kids = children_by_family([f["familyID"] for fs in new_fams.values() for f in fs])
        fams.update(new_fams)
        kids.update(new_kids)
        following = []
        for p in current:
            for f in new_fams.get(p["personID"], []):
                for c in new_kids.get(f["familyID"], []):
                    if c["personID"] not in numbers:  # someone reached twice (cousins married) keeps one number
                        numbers[c["personID"]] = len(numbers) + 1
                        lineage[c["personID"]] = [(p, depth + 1), *lineage[p["personID"]]]
                        following.append(c)
        depth += 1
        new_fams = families_of([c["personID"] for c in following]) if depth < gens else {}
        current = [c for c in following if c["personID"] in new_fams]  # childless and unmarried: the list says it all
    return numbers, lineage, generations, fams, kids


@bp.get("/register/<pid>")
def register(pid):
    """Each generation's people in sentences, with their marriages and numbered children (the "+" marks a child
    with an entry in the next generation)."""
    gens = gens_arg(DESCEND_GENS, 4)
    root = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not root:
        abort(404)
    numbers, lineage, generations, fams, kids = descent(root, gens)
    shown = {p["personID"] for gen in generations for p in gen}
    spouses = people_by_id([x for fs in fams.values() for f in fs for x in (f["husband"], f["wife"])])
    famcs = [s["famc"] for s in spouses.values() if s["famc"] and s["personID"] not in shown]
    parent_fams = {f["familyID"]: f for f in query(
        f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(famcs))})",
        [tree(), *famcs])} if famcs else {}
    parents = people_by_id([x for f in parent_fams.values() for x in (f["husband"], f["wife"])])
    notes = {p["personID"]: record_notes(notes_for(p["personID"], rights(p)))
             for gen in generations for p in gen if rights(p)["both"]}
    return render_template("register.html", root=root, gens=gens, choices=DESCEND_GENS, generations=generations,
                           numbers=numbers, lineage=lineage, fams=fams, kids=kids, spouses=spouses, shown=shown,
                           parent_fams=parent_fams, parents=parents, notes=notes)


def story_life(timeline):
    """The middle of a life story: events and marriages in date order, made ready for sentences."""
    out, merged = [], {}
    for x in timeline:
        e = x.get("event")
        if x["group"] != 1 or e and (e["tag"] in STORY_SKIP or e["empty"]):
            continue
        if e:
            info = " ".join((e["info"] or "").split()) if e["tag"] != "CENS" else ""  # census info is a transcription
            x = {**x, "info": info, "place": "" if RECORD_TITLE.search(x["place"] or "") else x["place"]}
            if not (x["date"] or x["place"] or info):
                continue  # only an address, or a record title: nothing to say
            if e["tag"] == "OCCU" and len(info.split()) <= 3 and "." not in info:
                x["job"] = info.lower()  # "worked as a coal miner"; longer text follows the sentence as written
            year = x["tr"][:4]
            if e["tag"] in MERGED and year != "0000":
                if e["tag"] in merged:
                    if year not in merged[e["tag"]]["years"]:
                        merged[e["tag"]]["years"].append(year)
                    continue
                merged[e["tag"]] = x
                x["years"] = [year]
        out.append(x)
    return out


def story_context(p, r):
    """Everything story.html and _story.html need to tell p's life."""
    pid = p["personID"]
    fam = relatives(pid)
    pf = fam["parent_fams"][0] if fam["parent_fams"] else None
    born = children_by_family([pf["familyID"]]).get(pf["familyID"], []) if pf else []
    place = next((i for i, c in enumerate(born, 1) if c["personID"] == pid), 0)
    if any((c["birthdatetr"] or "0000-00-00") == "0000-00-00" for c in born):  # pymysql gives partial/zero dates as str
        place = 0  # undated children sort last, so the place is a guess: say "one of"
    detail = person_detail(p, r, fam)
    return {"p": p, "r": r, **fam, **detail, "life": story_life(detail["timeline"]), "pf": pf,
            "birth_order": (place, len(born)), "verbs": STORY_VERBS}


@bp.get("/person/<pid>/story")
def story(pid):
    """A person's life told in sentences, built from the record each time it's shown, so nothing written here is
    stored or goes stale. Their own biography and obituary notes follow it word for word."""
    p = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not p:
        abort(404)
    r = rights(p)
    if not r["both"]:
        return redirect(url_for("main.person", pid=pid))
    return render_template("story.html", **story_context(p, r))


@bp.get("/book/<pid>")
def book(pid):
    """A branch book: the life story of each of a person's descendants, numbered as in the register, made to be
    printed (or saved as a PDF) from the browser. People whose details are private get their name only."""
    # shortcut: every story is built on each visit (a second or so per 20 people); cache if big books get used
    gens = gens_arg(DESCEND_GENS, 3)
    root = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not root:
        abort(404)
    numbers, lineage = descent(root, gens)[:2]
    people = people_by_id(list(numbers))
    entries = []
    for x, n in sorted(numbers.items(), key=lambda kv: kv[1]):
        if len(lineage[x]) >= gens:
            continue  # the walk lists one more generation of children, the register's last list; the book stops here
        r = rights(people[x])
        entries.append((n, people[x], Markup(render_template("_story.html", **story_context(people[x], r), number=n))
                        if r["both"] else None))
    return render_template("book.html", root=root, gens=gens, choices=DESCEND_GENS, entries=entries)


def dated_events(month, day=None, kinds=DEFAULT_KINDS):
    """People and families with an event in this month (and on this day, if given) whose date has a year,
    that the user may see: [{kind, label, year, day, date, place, person or family}]."""
    out = []
    for kind in kinds:
        label, col = KINDS[kind]
        alias, table, (cond, params) = (("f", "families", families_filter("f")) if kind == "married"
                                        else ("p", "people", people_filter("p")))
        tr = f"{alias}.{col}tr"
        for row in query(f"SELECT {alias}.*, YEAR({tr}) AS y, DAYOFMONTH({tr}) AS d FROM {table} {alias}"
                         f" WHERE {alias}.gedcom = %s AND MONTH({tr}) = %s AND YEAR({tr}) > 0"
                         + (f" AND DAYOFMONTH({tr}) = %s" if day else "") + f" AND {cond}",
                         [tree(), month, *([day] if day else []), *params]):
            out.append({"kind": kind, "label": label, "year": row["y"], "day": row["d"], "date": row[col],
                        "place": row[col.replace("date", "place")],
                        ("family" if alias == "f" else "person"): row})
    spouses = people_by_id([x for e in out if "family" in e for x in (e["family"]["husband"], e["family"]["wife"])])
    for e in out:
        if "family" in e:
            e["spouses"] = [spouses.get(e["family"]["husband"]), spouses.get(e["family"]["wife"])]
    return out


def chosen():
    """month, day (None for the whole month) and event kinds from the query string. With no month given,
    today's date."""
    today = localtime.today()
    a = request.args
    month = a.get("month", type=int)
    if month not in range(1, 13):
        return today.month, today.day, list(DEFAULT_KINDS)
    day = a.get("day", type=int)
    day = day if day in range(1, 32) else None
    return month, day, [k for k in KINDS if k in a.getlist("kind")] or list(DEFAULT_KINDS)


@bp.get("/anniversaries")
def anniversaries():
    month, day, kinds = chosen()
    events = sorted(dated_events(month, day, kinds), key=lambda e: (e["day"], e["year"]))
    this_year = datetime.date.today().year
    for e in events:
        e["years"] = this_year - e["year"]
    return render_template("anniversaries.html", month=month, day=day, kinds=kinds, all_kinds=KINDS,
                           events=events[:LIST_LIMIT], total=len(events), limit=LIST_LIMIT,
                           month_names=calendar.month_name)


@bp.get("/anniversaries/calendar")
def anniversary_calendar():
    """A month grid with how many events fall on each day, for the current year's weekdays."""
    month, _, kinds = chosen()
    counts = {}
    for e in dated_events(month, None, kinds):
        if e["day"]:  # dates with a month but no day can't go on a calendar
            counts.setdefault(e["day"], {}).setdefault(e["kind"], 0)
            counts[e["day"]][e["kind"]] += 1
    today = localtime.today()
    # shortcut: laid out on this year's weekdays, so 29 Feb only appears in leap years; it's still in the day list.
    weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(today.year, month)
    return render_template("anniversary_calendar.html", month=month, kinds=kinds, all_kinds=KINDS, weeks=weeks,
                           counts=counts, today=today, month_names=calendar.month_name)

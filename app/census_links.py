"""Census phase 3: match the people typed in a census transcription ("John Snider - 20 yrs - son") to people in the
tree. Only suggestions: an editor says yes (the line is that person) or no, and only a yes links them. A linked
census then shows on the person's page and in its household on the census year page."""
import re
from difflib import SequenceMatcher
from functools import lru_cache

from flask import flash, g, redirect, render_template, request, url_for

from .dates import convert_date
from .db import execute, query, update
from .edit import audit, bp, get_person, now, require
from .privacy import rights
from .views import tree

ROLES = {"head", "wife", "husband", "spouse", "son", "daughter", "daughte", "mother", "father", "brother", "sister",
         "boarder", "lodger", "roomer", "servant", "grandson", "granddaughter", "grandmother", "grandfather", "niece",
         "nephew", "cousin", "aunt", "uncle", "stepson", "stepdaughter", "mother-in-law", "father-in-law",
         "son-in-law", "daughter-in-law", "sister-in-law", "brother-in-law", "hired man", "laborer"}
AGE = re.compile(r"\bage\s*(\d{1,3})\b|\b(\d{1,3})\s*(?:yrs?|years?|age|y/o)\b|\b(\d{1,2})/12\b", re.I)
BARE_AGE = re.compile(r"^(.*?[A-Za-z])\s+(\d{1,3})(?:\s+([A-Za-z-]+))?$")  # "Charles Rase 43", "Stella Bowser 2 Daughter"
NAME = re.compile(r"^[A-Z][A-Za-z.'’]*(?:\s+[A-Za-z][A-Za-z.'’]*){1,4}$")
LAST_FIRST = re.compile(r"^([A-Z][A-Za-z'’]+),\s*([A-Z][A-Za-z.'’ ]+)$")  # "Fare, Barbra"
NOT_A_NAME = re.compile(r"\b(farm|farmer|miner|route|street|st|road|post|office|married|children|living|household|"
                        r"film|number|page|line|district|ward)\b", re.I)
SHOWN = 20  # households on the review page at a time; answering moves the rest up


def parse(line):
    """(name, age or None) for a line that reads as a person: a name and an age or a household role."""
    line = line.strip()
    m = AGE.search(line)
    age = None if not m else 0 if m[3] else int(m[1] or m[2])
    if age is None and (bare := BARE_AGE.match(line)) and (not bare[3] or bare[3].lower() in ROLES):
        line, age = bare[1] + (f" - {bare[3]}" if bare[3] else ""), int(bare[2])
    parts = [s.strip(" .,;") for s in re.split(r"\s+-\s*|\s*-\s+|:", line) if s.strip(" .,;")]
    role = any(p.lower() in ROLES for p in parts) or any(p.split(",")[0].strip().lower() in ROLES for p in parts)
    for p in parts:
        p = AGE.sub("", p).replace(" old", "").strip(" ,")
        if lf := LAST_FIRST.match(p):
            p = f"{lf[2].strip()} {lf[1]}"
        if p.lower() not in ROLES and NAME.match(p) and not NOT_A_NAME.search(p):
            return (p, age) if age is not None or role else None
    return None


@lru_cache(maxsize=100_000)  # the same few given names (John, Mary) come up again and again
def similar(a, b):
    if 2 * min(len(a), len(b)) < 0.75 * (len(a) + len(b)):  # lengths too far apart to reach the 0.75 used anywhere
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def fit(name, born, p):
    """How well a person fits a typed name and birth year: 3 first name and year, 2 first name and surname (no year
    to check), 1 first name only or the year is off; None not at all. Married women are under their own surname,
    so a surname that differs doesn't rule anyone out."""
    given, last = name.split()[0], name.split()[-1]
    first = (p["firstname"] or "").split()
    if not first or not (first[0].lower() == given.lower() or (len(given) > 2 and similar(first[0], given) >= 0.8)
                         or (len(given.rstrip(".")) == 1 and first[0][0].lower() == given[0].lower())):
        return None
    year = int(str(p["birthdatetr"] or "0000")[:4])
    if born and year:
        return 3 if abs(year - born) <= 2 else 1
    return 2 if similar(p["lastname"] or "", last) >= 0.75 else 1


def family_maps():
    t = tree()
    fams = {f["familyID"]: f for f in query("SELECT familyID, husband, wife FROM families WHERE gedcom = %s", (t,))}
    kids, parents, spouses = {}, {}, {}
    for c in query("SELECT familyID, personID FROM children WHERE gedcom = %s", (t,)):
        kids.setdefault(c["familyID"], []).append(c["personID"])
        parents.setdefault(c["personID"], []).append(c["familyID"])
    for f in fams.values():
        for x in {f["husband"], f["wife"]} - {""}:
            spouses.setdefault(x, []).append(f["familyID"])
    members = lambda fid: {x for x in (fams[fid]["husband"], fams[fid]["wife"]) if x} | set(kids.get(fid, ())) if (
        fid in fams) else set()
    near = lambda pid: {m for fid in parents.get(pid, []) + spouses.get(pid, []) for m in members(fid)}
    return members, near


def suggestions():
    """Census records with lines still to answer, oldest census first: [{event, year, lines: [{line, text, name,
    age, choices: [(fit, person)]}]}]. Candidates are the household's family, then everyone a family-step from them
    (parents, in-laws, grandchildren); a person already linked to another line of the same record is left out."""
    t = tree()
    people = {p["personID"]: p for p in query(
        "SELECT personID, firstname, lastname, birthdate, birthdatetr, living, private FROM people WHERE gedcom = %s",
        (t,))}
    members, near = family_maps()
    answered = {}
    for a in query("SELECT * FROM app_census_links WHERE gedcom = %s", (t,)):
        answered[(a["eventID"], a["line"])] = a
    out = []
    for e in query("SELECT e.eventID, e.persfamID, e.info, e.eventplace, et.type, LEFT(e.eventdatetr, 4) AS year"
                   " FROM events e JOIN eventtypes et ON et.eventtypeID = e.eventtypeID WHERE e.gedcom = %s"
                   " AND et.tag = 'CENS' AND e.info <> '' ORDER BY e.eventdatetr, e.eventID", (t,)):
        core = members(e["persfamID"]) if e["type"] == "F" else {e["persfamID"]} | near(e["persfamID"])
        wider = {m for p in core for m in near(p)} - core
        lines, taken = [], set()
        for i, text in enumerate(e["info"].splitlines()):
            a = answered.get((e["eventID"], i))
            if a and a["line_text"] == text.strip()[:255]:
                taken.add(a["personID"])
                continue
            got = parse(text)
            if not got:
                continue
            name, age = got
            born = int(e["year"]) - age if age is not None and e["year"] != "0000" else None
            choices = []
            for ring, ids in ((0, core), (1, wider)):
                for pid in ids:
                    if pid in people and (score := fit(name, born, people[pid])):
                        choices.append((score, ring, pid))
            if choices:
                lines.append({"line": i, "text": text.strip(), "name": name, "age": age, "choices": choices})
        for x in lines:
            x["choices"] = [(s, people[pid]) for s, _, pid in sorted(x["choices"], key=lambda c: (-c[0], c[1], c[2]))
                            if pid not in taken][:3]
        lines = [x for x in lines if x["choices"]]
        if lines:
            out.append({"event": e, "year": e["year"], "lines": lines})
    return out


@bp.route("/census", methods=["GET", "POST"])
def census_matches():
    require("allow_edit")
    if request.method == "POST":
        eid, line, text = int(request.form["event"]), int(request.form["line"]), request.form["text"].strip()[:255]
        pid = request.form.get("person") or None
        if request.form.get("undo"):
            before = query("SELECT * FROM app_census_links WHERE gedcom = %s AND eventID = %s AND line = %s",
                           (tree(), eid, line))
            execute("DELETE FROM app_census_links WHERE gedcom = %s AND eventID = %s AND line = %s", (tree(), eid, line))
            audit("undo census match", "app_census_links", f"{eid}:{line}", before[0] if before else None, None)
            flash("Answer undone; the line is back in the list.")
        else:
            row = {"eventID": eid, "line": line, "line_text": text, "personID": pid}
            execute("REPLACE INTO app_census_links (gedcom, eventID, line, line_text, personID, decided_by, decided_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, NOW())", (tree(), eid, line, text, pid, g.user["username"]))
            audit("census match" if pid else "census no match", "app_census_links", f"{eid}:{line}", None, row)
            birth = request.form.get("birth", "")
            p = get_person(pid) if pid and re.fullmatch(r"abt \d{4}", birth) else None
            if p and not p["birthdate"] and rights(p)["both"]:  # only fills an empty birth date, never replaces one
                update("people", {"birthdate": birth, "birthdatetr": convert_date(birth), "changedate": now(),
                                  "changedby": g.user["username"]}, {"gedcom": tree(), "personID": pid})
                audit("edit person", "people", pid, p, get_person(pid))
                flash(f"Birth set to {birth}.")
        return redirect(url_for("edit.census_matches"))
    todo = suggestions()
    recent = query("SELECT l.*, p.firstname, p.lastname, p.living, p.private, p.personID AS pid FROM app_census_links l"
                   " LEFT JOIN people p ON p.gedcom = l.gedcom AND p.personID = l.personID WHERE l.gedcom = %s"
                   " ORDER BY l.decided_at DESC, l.eventID DESC, l.line DESC LIMIT 15", (tree(),))
    return render_template("census_matches.html", todo=todo[:SHOWN], left=len(todo),
                           lines_left=sum(len(x["lines"]) for x in todo), recent=recent)

from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for

from .dates import display as display_date
from .db import query
from .files import is_image, is_url
from .privacy import display_name, families_filter, media_filter, people_filter, rights
from .relations import relationship
from .search import PER_PAGE, SORTS, aliases, same_name, surnames as stored_surnames, variants

bp = Blueprint("main", __name__)

ALT_BIRTH = {"CHR": "Christening", "BAPM": "Baptism"}
# GEDCOM tags TNG shows raw as event names
EVENT_LABELS = {"_MILI": "Military", "_EMAIL": "Email", "EMAIL": "Email (GEDCOM)", "SEX": "Sex"}


def tree():
    # TNG's indexes all lead with gedcom (the tree ID), so every lookup includes it.
    return current_app.config["TREE"]


def one(sql, args=None):
    rows = query(sql, args)
    return rows[0] if rows else None


def people_by_id(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = query(f"SELECT * FROM people WHERE gedcom = %s AND personID IN ({','.join(['%s'] * len(ids))})",
                 [tree(), *ids])
    return {r["personID"]: r for r in rows}


def visible_media(link_ids):
    """Media linked to any of these person/family IDs that the user may see, default photo first."""
    cond, params = media_filter("m")
    return query(
        "SELECT m.*, ml.personID AS linkedto, ml.defphoto, ml.ordernum AS linkorder FROM medialinks ml"
        " JOIN media m ON m.mediaID = ml.mediaID"
        f" WHERE ml.gedcom = %s AND ml.personID IN ({','.join(['%s'] * len(link_ids))}) AND ml.dontshow = 0"
        f" AND {cond} ORDER BY ml.defphoto DESC, ml.ordernum", [tree(), *link_ids, *params])


# Scans and other media that are evidence for a source citation: GEDCOM's OBJE under a citation, which TNG has no
# place for. Never cascaded: rows of a deleted citation or media fall out of every join (their IDs aren't reused),
# and come back working if the deletion is undone from its audit record.


def fact_media(owner_ids):
    """Media this user may see that is about one fact, keyed (person/family ID, fact key as notes use it): TNG's own
    event links (medialinks.eventID) and the scans of citations about that fact."""
    cond, params = media_filter("m")
    marks = ",".join(["%s"] * len(owner_ids))
    rows = query(
        "SELECT m.*, ml.personID AS persfam, ml.eventID AS fact, 0 AS src, ml.ordernum AS o FROM medialinks ml"
        " JOIN media m ON m.mediaID = ml.mediaID"
        f" WHERE ml.gedcom = %s AND ml.personID IN ({marks}) AND ml.eventID <> '' AND ml.dontshow = 0 AND {cond}"
        " UNION ALL SELECT m.*, c.persfamID, c.eventID, 1, c.ordernum * 1000 + cm.ordernum FROM app_citation_media cm"
        " JOIN citations c ON c.citationID = cm.citationID JOIN media m ON m.mediaID = cm.mediaID"
        f" WHERE c.gedcom = %s AND c.persfamID IN ({marks}) AND c.eventID <> '' AND {cond} ORDER BY src, o",
        [tree(), *owner_ids, *params, tree(), *owner_ids, *params])
    out = {}
    for m in rows:
        shown = out.setdefault((m["persfam"], m["fact"]), [])
        if m["mediaID"] not in {x["mediaID"] for x in shown}:
            shown.append(m)
    return out


def citation_media(cids):
    """Scans this user may see attached to each of these citations, by citation ID."""
    if not cids:
        return {}
    cond, params = media_filter("m")
    out = {}
    for m in query("SELECT m.*, cm.citationID FROM app_citation_media cm JOIN media m ON m.mediaID = cm.mediaID"
                   f" WHERE cm.citationID IN ({','.join(['%s'] * len(cids))}) AND {cond} ORDER BY cm.ordernum",
                   [*cids, *params]):
        out.setdefault(m["citationID"], []).append(m)
    return out


EDITOR_ONLY_TAGS = {"SSN"}  # Social Security numbers: shown to editors only
ADDRESS_FIELDS = ("address1", "address2", "city", "state", "zip", "country", "phone", "email", "www")
EVENT_FIELDS = ("eventdate", "eventplace", "info", "age", "agency", "cause", "addressID")


def events_for(persfam_id):
    """Events to show. Empty ones (no date, place or details) are hidden, except to editors so they can tidy them."""
    editor = g.user["allow_edit"] == 1
    rows = query(
        "SELECT e.*, et.display, et.tag, " + ", ".join(f"a.{k} AS addr_{k}" for k in ADDRESS_FIELDS) +
        " FROM events e JOIN eventtypes et ON et.eventtypeID = e.eventtypeID"
        " LEFT JOIN addresses a ON a.addressID = e.addressID"
        " WHERE e.gedcom = %s AND e.persfamID = %s AND et.ldsevent = 0"
        " ORDER BY e.eventdatetr = '0000-00-00', e.eventdatetr", (tree(), persfam_id))
    shown = []
    for e in rows:
        a = {k: (e.pop(f"addr_{k}") or "").strip() for k in ADDRESS_FIELDS}
        town = " ".join(x for x in (", ".join(x for x in (a["city"], a["state"]) if x), a["zip"]) if x)
        e["address"] = [x for x in (a["address1"], a["address2"], town, a["country"], a["phone"], a["email"], a["www"]) if x]
        e["display"] = EVENT_LABELS.get(e["display"], e["display"])
        e["empty"] = not any(e[k] for k in EVENT_FIELDS)
        if editor or not (e["empty"] or e["tag"] in EDITOR_ONLY_TAGS):
            shown.append(e)
    return shown


# What a note can be about besides the whole record or a custom event (whose ID it then holds): TNG's GEDCOM tags.
FACT_LABELS = {"BIRT": "Birth", "CHR": "Christening", "BAPM": "Baptism", "DEAT": "Death", "BURI": "Burial",
               "NAME": "Name", "MARR": "Marriage", "DIV": "Divorce"}
LDS_NOTE_KEYS = {"BAPL", "CONL", "INIT", "ENDL", "SLGC", "SLGS"}  # LDS data is never shown


def notes_for(persfam_id, r):
    """Notes this user may see, keyed by what they're about: "" for the record itself, a custom event's ID,
    or a fact's GEDCOM tag (BIRT, MARR, NAME...)."""
    out = {}
    for n in query("SELECT nl.ID, nl.eventID, nl.ordernum, x.note, nl.secret FROM notelinks nl JOIN xnotes x"
                   " ON x.ID = nl.xnoteID WHERE nl.gedcom = %s AND nl.persfamID = %s ORDER BY nl.ordernum",
                   (tree(), persfam_id)):
        if (not n["secret"] or r["private_notes"]) and n["eventID"] not in LDS_NOTE_KEYS:
            out.setdefault(n["eventID"], []).append(n)
    return out


def record_notes(by_fact):
    """The Notes section: the record's own notes, then those about a fact that isn't shown (e.g. the name),
    labelled with what they're about. Call after the shown facts have taken theirs."""
    rest = list(by_fact.get("", []))
    for key, notes in by_fact.items():
        if key:
            rest += [{**n, "about": FACT_LABELS.get(key, "An event")} for n in notes]
    return rest


def citations_for(persfam_id):
    """The record's citations, each with what it's about ("Birth", a custom event's name, or blank for the
    whole record). LDS ones are left out like the rest of LDS data."""
    rows = query(
        "SELECT c.*, s.title, et.display FROM citations c"
        " LEFT JOIN sources s ON s.sourceID = c.sourceID AND s.gedcom = c.gedcom"
        " LEFT JOIN events e ON e.gedcom = c.gedcom AND c.eventID REGEXP '^[0-9]+$' AND e.eventID = c.eventID"
        " LEFT JOIN eventtypes et ON et.eventtypeID = e.eventtypeID"
        " WHERE c.gedcom = %s AND c.persfamID = %s ORDER BY c.ordernum", (tree(), persfam_id))
    scans = citation_media([c["citationID"] for c in rows])
    for c in rows:
        c["about"] = FACT_LABELS.get(c["eventID"]) or EVENT_LABELS.get(c["display"], c["display"]) or ""
        c["media"] = scans.get(c["citationID"], [])
    return [c for c in rows if c["eventID"] not in LDS_NOTE_KEYS]


def url_with(_clear=False, **changes):
    """This page's URL with some query arguments changed (None removes one); _clear drops them all."""
    args = {} if _clear else request.args.to_dict()
    args.update(changes)
    return url_for(request.endpoint, **(request.view_args or {}), **{k: v for k, v in args.items() if v is not None})


def year_arg(name):
    v = request.args.get(name, "").strip()
    return int(v) if v.isdigit() and len(v) <= 4 else None


def surname_sql(alias, last, similar):
    """SQL (args) for a surname column starting with the typed surname or equal to one of its similar spellings."""
    names = [f"{alias}.lastname LIKE %s"] + ([f"{alias}.lastname IN (" + ",".join(["%s"] * len(similar)) + ")"]
                                             if similar else [])
    return "(" + " OR ".join(names) + ")", [last + "%", *similar]


def married_name(alias, last, similar):
    """Subquery (args) giving the surname of a visible husband in a visible family whose surname fits: how a
    woman is found under her married name. The wife is {alias}."""
    fcond, fparams = families_filter("mf")
    hcond, hparams = people_filter("mh")
    names, name_args = surname_sql("mh", last, similar)
    return (f"SELECT mh.lastname FROM families mf JOIN people mh ON mh.gedcom = mf.gedcom"
            f" AND mh.personID = mf.husband WHERE mf.gedcom = {alias}.gedcom AND mf.wife = {alias}.personID"
            f" AND {names} AND {fcond} AND {hcond} ORDER BY mf.wifeorder LIMIT 1", [*name_args, *fparams, *hparams])


def name_match(alias, words, exact, married=False):
    """SQL (args, surname variants used) for a person whose name fits typed words: one word matches the surname
    or the first name; more words mean given names (first or middle, each the start of a word of the first name)
    and surname, or else all given names ("Walter James"). Surnames also match similar spellings unless exact,
    and with married=True a wife's married (husband's) surname too."""
    last = words[-1]
    similar = [] if exact else variants(last)
    surname_match, surname_args = surname_sql(alias, last, similar)
    if married:
        sub, sub_args = married_name(alias, last, similar)
        surname_match, surname_args = f"({surname_match} OR EXISTS ({sub}))", [*surname_args, *sub_args]
    if len(words) == 1:
        return f"({surname_match} OR {alias}.firstname LIKE %s)", [*surname_args, last + "%"], similar

    def given(ws):
        return (" AND ".join([f"({alias}.firstname LIKE %s OR {alias}.firstname LIKE %s)"] * len(ws)),
                [x for w in ws for x in (w + "%", "% " + w + "%")])
    some, some_args = given(words[:-1])
    every, every_args = given(words)
    return (f"(({some} AND {surname_match}) OR ({every}))", [*some_args, *surname_args, *every_args], similar)


@bp.get("/search")
def search(surname=None, firstname=None):
    """Find people by name (with surname spelling variants unless exact), filtered by birth years, place and
    living status, sorted and paged. surname= and firstname= list everyone with exactly that name (the surname and
    first-name pages; a first name is the first word of it, as in TNG)."""
    a = request.args
    q = a.get("q", "").strip()
    words = q.split()
    exact = a.get("exact") == "1"
    born_from, born_to = year_arg("born_from"), year_arg("born_to")
    place, status = a.get("place", "").strip(), a.get("status", "")
    sort = a.get("sort") if a.get("sort") in SORTS else "name"
    page = max(a.get("page", 1, type=int), 1)

    if len(words) == 1 and one("SELECT 1 AS x FROM people WHERE gedcom = %s AND personID = %s",
                               (tree(), words[0].upper())):
        return redirect(url_for("main.person", pid=words[0].upper()))  # typed an ID: go straight there

    cond, params = people_filter("p")
    where, args, similar = [cond, "p.gedcom = %s"], [*params, tree()], []
    married_col, married_args = "NULL", []
    if surname:
        spellings = same_name(surname) or [surname]  # linked spellings are one surname
        where.append(f"p.lastname IN ({','.join(['%s'] * len(spellings))})")
        args += spellings
        similar = [x for x in spellings if x.upper() != surname.upper()]
    elif firstname:
        where.append(f"{FIRST_WORD} = %s")
        args.append(firstname)
    elif words:
        sql, name_args, similar = name_match("p", words, exact, married=True)
        where.append(sql)
        args += name_args
        married_col, married_args = married_name("p", words[-1], similar)  # to say which married name matched
    if born_from:
        where.append("p.birthdatetr >= %s")
        args.append(f"{born_from:04d}-00-00")
    if born_to:
        where.append("p.birthdatetr BETWEEN '0001-00-00' AND %s")
        args.append(f"{born_to:04d}-12-31")
    if place:
        where.append("(p.birthplace LIKE %s OR p.deathplace LIKE %s OR p.burialplace LIKE %s)")
        args += [f"%{place}%"] * 3
    if status in ("living", "deceased"):
        where.append("p.living = %s")
        args.append(int(status == "living"))

    asked = bool(surname or firstname or words or born_from or born_to or place or status)
    people, total, parents = [], 0, {}
    if asked:
        sql_where = " AND ".join(where)
        total = query(f"SELECT COUNT(*) AS n FROM people p WHERE {sql_where}", args)[0]["n"]
        people = query(f"SELECT p.*, ({married_col}) AS married_as FROM people p WHERE {sql_where}"
                       f" ORDER BY {SORTS[sort]} LIMIT %s OFFSET %s", [*married_args, *args, PER_PAGE, (page - 1) * PER_PAGE])
        famcs = [p["famc"] for p in people if p["famc"]]
        if famcs:
            fams = query(f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(famcs))})",
                         [tree(), *famcs])
            folks = people_by_id([x for f in fams for x in (f["husband"], f["wife"])])
            parents = {f["familyID"]: [folks.get(f["husband"]), folks.get(f["wife"])] for f in fams}
    shown_variants = sorted({n for p in people for n in (p["lastname"], p["married_as"]) if n in similar})
    return render_template("search.html", q=q, exact=exact, born_from=born_from, born_to=born_to, place=place,
                           status=status, sort=sort, page=page, pages=max((total + PER_PAGE - 1) // PER_PAGE, 1),
                           total=total, people=people, parents=parents, asked=asked,
                           listing=surname or (f"{firstname} (first name)" if firstname else None),
                           variants=shown_variants, has_variants=bool(similar), surname=surname)


FAMILY_SORTS = {"husband": "h.lastname IS NULL, h.lastname, h.firstname, f.marrdatetr",
                "wife": "w.lastname IS NULL, w.lastname, w.firstname, f.marrdatetr",
                "married": "f.marrdatetr = '0000-00-00', f.marrdatetr, h.lastname, w.lastname"}


@bp.get("/search/families")
def search_families():
    """Find couples (TNG's family search): by either spouse's name (with surname spelling variants unless exact),
    marriage years and place. Hidden families, and spouses the user can't see, are never matched."""
    a = request.args
    husband, wife = a.get("husband", "").strip(), a.get("wife", "").strip()
    exact = a.get("exact") == "1"
    married_from, married_to, place = year_arg("married_from"), year_arg("married_to"), a.get("place", "").strip()
    sort = a.get("sort") if a.get("sort") in FAMILY_SORTS else "husband"
    page = max(a.get("page", 1, type=int), 1)

    cond, params = families_filter("f")
    where, args, similar = [cond, "f.gedcom = %s"], [*params, tree()], set()
    for alias, text in (("h", husband), ("w", wife)):
        if text:
            pcond, pparams = people_filter(alias)
            sql, name_args, used = name_match(alias, text.split(), exact)
            where.append(f"{sql} AND {pcond}")
            args += [*name_args, *pparams]
            similar |= set(used)
    if married_from:
        where.append("f.marrdatetr >= %s")
        args.append(f"{married_from:04d}-00-00")
    if married_to:
        where.append("f.marrdatetr BETWEEN '0001-00-00' AND %s")
        args.append(f"{married_to:04d}-12-31")
    if place:
        where.append("f.marrplace LIKE %s")
        args.append(f"%{place}%")

    asked = bool(husband or wife or married_from or married_to or place)
    families, total, folks = [], 0, {}
    if asked:
        joins = ("FROM families f LEFT JOIN people h ON h.gedcom = f.gedcom AND h.personID = f.husband"
                 " LEFT JOIN people w ON w.gedcom = f.gedcom AND w.personID = f.wife")
        sql_where = " AND ".join(where)
        total = query(f"SELECT COUNT(*) AS n {joins} WHERE {sql_where}", args)[0]["n"]
        families = query(f"SELECT f.* {joins} WHERE {sql_where} ORDER BY {FAMILY_SORTS[sort]} LIMIT %s OFFSET %s",
                         [*args, PER_PAGE, (page - 1) * PER_PAGE])
        folks = people_by_id([x for f in families for x in (f["husband"], f["wife"])])
    shown_variants = sorted({p["lastname"] for p in folks.values() if p["lastname"] in similar})
    return render_template("search_families.html", husband=husband, wife=wife, exact=exact, married_from=married_from,
                           married_to=married_to, place=place, sort=sort, page=page,
                           pages=max((total + PER_PAGE - 1) // PER_PAGE, 1), total=total, families=families,
                           folks=folks, asked=asked, variants=shown_variants)


def find_people(text):
    """Up to 25 people matching a name (or an exact ID) that the user may see: [{id, label}]."""
    words = text.split()
    if not words:
        return []
    cond, params = people_filter("p")
    if len(words) == 1:
        where, args = "(p.lastname LIKE %s OR p.firstname LIKE %s OR p.personID = %s)", [words[0] + "%"] * 2 + [words[0]]
    else:
        where, args, _ = name_match("p", words, exact=True)
    rows = query(f"SELECT * FROM people p WHERE p.gedcom = %s AND {where} AND {cond}"
                 " ORDER BY lastname, firstname, birthdatetr LIMIT 25", [tree(), *args, *params])
    return [{"id": r["personID"],
             "label": f"{display_name(r)} ({display_date(r['birthdate']) or '?'} – {display_date(r['deathdate'])})"}
            for r in rows]


@bp.get("/find")
def find():
    """Name lookup for pickers available to every logged-in user (the relationship page)."""
    return jsonify(find_people(request.args.get("q", "")))


@bp.get("/surnames")
def surnames():
    cond, params = people_filter("p")
    rows = query(f"SELECT lastname, COUNT(*) AS n FROM people p WHERE lastname <> '' AND {cond}"
                 " GROUP BY lastname ORDER BY lastname", params)
    merged = {}
    for r in rows:  # spellings an editor linked are one surname, listed under the one they chose
        key = aliases().get(r["lastname"].upper(), r["lastname"].upper())
        row = merged.setdefault(key, {"lastname": stored_surnames().get(key, r["lastname"]), "n": 0, "also": []})
        row["n"] += r["n"]
        if r["lastname"].upper() != key:
            row["also"].append(r["lastname"])
    return render_template("surnames.html", surnames=sorted(merged.values(), key=lambda r: r["lastname"].upper()))


@bp.get("/surnames/<path:name>")
def surname(name):
    return search(surname=name)


FIRST_WORD = "SUBSTRING_INDEX(TRIM(p.firstname), ' ', 1)"  # "Mary Ann" counts as Mary, as in TNG
FIRST_TOP = 30


@bp.get("/firstnames")
def firstnames():
    """Every first name with how many people have it, by letter, and the most common ones (TNG's first-name list)."""
    cond, params = people_filter("p")
    rows = query(f"SELECT {FIRST_WORD} AS name, COUNT(*) AS n FROM people p WHERE p.gedcom = %s"
                 f" AND p.firstname <> '' AND {cond} GROUP BY {FIRST_WORD} ORDER BY name", [tree(), *params])
    letters = {}
    for r in rows:
        letter = r["name"][0].upper()
        letters.setdefault(letter if letter.isalpha() else "#", []).append(r)  # "?", "-" and the like together
    return render_template("firstnames.html", letters=letters, top=sorted(rows, key=lambda r: -r["n"])[:FIRST_TOP])


@bp.get("/firstnames/<path:name>")
def firstname(name):
    return search(firstname=name)


def age_at(birth_tr, when_tr):
    """Age in whole years at an event, e.g. "20", or "about 20" when months are unknown. None if not computable."""
    b, w = str(birth_tr or "0000-00-00"), str(when_tr or "0000-00-00")
    if b[:4] == "0000" or w[:4] == "0000":
        return None
    (by, bm, bd), (wy, wm, wd) = (map(int, b.split("-")), map(int, w.split("-")))
    age = wy - by
    if bm and wm and (wm, wd) < (bm, bd):
        age -= 1  # birthday not reached yet that year
    if age < 0:
        return None
    return str(age) if bm and wm else f"about {age}"


def search_links(p):
    """[(site, url)] searching other genealogy sites for this person, name and years filled in. Only for people
    who aren't living: the click sends their name to that site."""
    if p["living"]:
        return []
    given, surname = (p["firstname"] or "").strip(), " ".join(f"{p['lnprefix'] or ''} {p['lastname'] or ''}".split())
    if not (given or surname):
        return []
    born, died = (str(p[k] or "")[:4] for k in ("birthdatetr", "deathdatetr"))
    born, died = (y if y.strip("0") else "" for y in (born, died))
    q = lambda base, **kw: f"{base}?{urlencode({k: v for k, v in kw.items() if v})}"  # noqa: E731
    short = " ".join(filter(None, (given.split(" ")[0], surname)))
    span = (born or str(int(died) - 90), str(int(died) + 1) if died else str(int(born) + 90)) if born or died else ()
    return [
        ("FamilySearch", q("https://www.familysearch.org/search/record/results", **{
            "q.givenName": given, "q.surname": surname, "q.birthLikeDate.from": born and int(born) - 2,
            "q.birthLikeDate.to": born and int(born) + 2, "q.deathLikeDate.from": died and int(died) - 2,
            "q.deathLikeDate.to": died and int(died) + 2, "q.birthLikePlace": p["birthplace"]})),
        ("Find a Grave", q("https://www.findagrave.com/memorial/search", firstname=given.split(" ")[0],
                           lastname=surname, birthyear=born, birthyearfilter=born and 2, deathyear=died,
                           deathyearfilter=died and 2)),
        ("Ancestry", q("https://www.ancestry.com/search/", name=f"{given}_{surname}",
                       birth="_".join(x for x in (born, p["birthplace"]) if x), death=died)),
        ("Google", q("https://www.google.com/search",
                     q=" ".join(x for x in (f'"{" ".join(filter(None, (given, surname)))}"', born, died) if x))),
        # Newspapers: their first name and surname as a phrase, from birth to a year after death (the obituary)
        ("Chronicling America", q("https://www.loc.gov/collections/chronicling-america/", q=f'"{short}"',
                                  dates=span and "/".join(span))),
        ("Newspapers.com", q("https://www.newspapers.com/search/", keyword=f'"{short}"',
                             **{"date-start": span and span[0], "date-end": span and span[1]})),
    ]


def main_photo(media):
    """Picture for the page header: the default photo (one per record, chosen on "Arrange"), unless it's a
    headstone (TNG made the first linked picture the default, and that's mostly a headstone); then the Photos
    collection; then any other image."""
    images = [m for m in media if not is_url(m) and is_image(m["path"])]
    return min(images, key=lambda m: (not (m["defphoto"] == "1" and m["mediatypeID"] != "headstones"),
                                      m["mediatypeID"] != "photos", m["defphoto"] != "1", m["linkorder"]),
               default=None)


def timeline(p, events, families=(), folks=None, households=(), linked=()):
    """Birth and christening first, then events and marriages by date (undated last), then death and burial.
    Marriages and divorces live on the family record; families whose details the user can't see are left out.
    Censuses recorded on a family the person was a child or spouse in (households) show too, and censuses whose
    transcription an editor matched a line of to this person (linked: [(event, family or person it's on)])."""
    entries = []
    alt = p["altbirthtype"] if p["altbirthtype"] in ALT_BIRTH else "CHR"
    for order, (label, key, tag) in enumerate((("Birth", "birth", "BIRT"), (ALT_BIRTH[alt], "altbirth", alt),
                                               ("Death", "death", "DEAT"),
                                               ("Cremation" if p["burialtype"] == 1 else "Burial", "burial", "BURI"))):
        if p[f"{key}date"] or p[f"{key}place"]:
            entries.append({"label": label, "date": p[f"{key}date"], "tr": str(p[f"{key}datetr"]),
                            "place": p[f"{key}place"], "group": 0 if order < 2 else 2, "order": order, "key": tag,
                            "field": key})
    for e in events:
        entries.append({"label": e["display"], "date": e["eventdate"], "tr": str(e["eventdatetr"]),
                        "place": e["eventplace"], "info": e["info"], "address": e["address"], "event": e,
                        "group": 1, "order": 0,
                        "key": str(e["eventID"])})
    for f in families:
        if not rights(f)["both"]:
            continue
        spouse = (folks or {}).get(f["wife"] if f["husband"] == p["personID"] else f["husband"])
        for label, key in (("Marriage", "marr"), ("Divorce", "div")):
            if f[f"{key}date"] or f[f"{key}place"]:
                entries.append({"label": label, "date": f[f"{key}date"], "tr": str(f[f"{key}datetr"]),
                                "place": f[f"{key}place"], "family": f, "spouse": spouse, "group": 1, "order": 0,
                                "key": f"{f['familyID']}-{key}"})
    for f in households:
        if not rights(f)["both"]:
            continue
        for e in events_for(f["familyID"]):
            if e["tag"] == "CENS":
                entries.append({"label": e["display"], "date": e["eventdate"], "tr": str(e["eventdatetr"]),
                                "place": e["eventplace"], "info": e["info"], "address": e["address"], "event": e,
                                "household": f, "group": 1, "order": 0, "key": str(e["eventID"])})
    shown = {x["key"] for x in entries}
    for e, owner in linked:
        if str(e["eventID"]) not in shown and rights(owner)["both"]:
            entries.append({"label": e["display"], "date": e["eventdate"], "tr": str(e["eventdatetr"]),
                            "place": e["eventplace"], "info": e["info"], "address": e["address"], "event": e,
                            "household" if "familyID" in owner else "counted_with": owner, "group": 1, "order": 0,
                            "key": str(e["eventID"])})
    entries.sort(key=lambda x: (x["group"], x["tr"] == "0000-00-00", x["tr"], x["order"]))
    for x in entries:
        x["age"] = age_at(p["birthdatetr"], x["tr"]) if x["label"] != "Birth" else None
    return entries


def linked_census(pid):
    """Census records on someone else (a family or a person) with a line an editor matched to this person."""
    out = []
    for r in query("SELECT DISTINCT e.eventID, e.persfamID FROM app_census_links l JOIN events e"
                   " ON e.gedcom = l.gedcom AND e.eventID = l.eventID WHERE l.gedcom = %s AND l.personID = %s"
                   " AND e.persfamID <> %s", (tree(), pid, pid)):
        owner = one("SELECT * FROM families WHERE gedcom = %s AND familyID = %s", (tree(), r["persfamID"])) or one(
            "SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), r["persfamID"]))
        e = next((e for e in events_for(r["persfamID"]) if e["eventID"] == r["eventID"]), None)
        if owner and e:
            out.append((e, owner))
    return out


def child_order():
    """ORDER BY for a family's children (aliases c, p): oldest first, undated last, unless an editor has used the
    up/down arrows on that family (each move is in the edit history), then their order."""
    return ("IF(EXISTS(SELECT 1 FROM app_audit a WHERE a.tbl = 'children' AND a.action = 'reorder children'"
            " AND a.record = c.familyID), c.ordernum, 0),"
            " IFNULL(p.birthdatetr, '0000-00-00') = '0000-00-00', p.birthdatetr, c.ordernum")


def relatives(pid):
    """A person's parent and spouse families, their siblings and children, and everyone in those families
    (for the person page and the edit page)."""
    parent_fams = query(
        "SELECT f.* FROM children c JOIN families f ON f.familyID = c.familyID AND f.gedcom = c.gedcom"
        " WHERE c.gedcom = %s AND c.personID = %s ORDER BY c.parentorder", (tree(), pid))
    spouse_fams = query(
        "SELECT * FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)"
        " ORDER BY IF(husband = %s, husborder, wifeorder)", (tree(), pid, pid, pid))
    kids = {}
    if spouse_fams:
        ids = [f["familyID"] for f in spouse_fams]
        for c in query("SELECT c.familyID, p.* FROM children c JOIN people p"
                       " ON p.personID = c.personID AND p.gedcom = c.gedcom"
                       f" WHERE c.gedcom = %s AND c.familyID IN ({','.join(['%s'] * len(ids))})"
                       f" ORDER BY {child_order()}", [tree(), *ids]):
            kids.setdefault(c["familyID"], []).append(c)
    siblings = {}
    if parent_fams:
        ids = [f["familyID"] for f in parent_fams]
        for c in query("SELECT c.familyID, p.* FROM children c JOIN people p"
                       " ON p.personID = c.personID AND p.gedcom = c.gedcom"
                       f" WHERE c.gedcom = %s AND c.familyID IN ({','.join(['%s'] * len(ids))}) AND c.personID != %s"
                       f" ORDER BY {child_order()}", [tree(), *ids, pid]):
            siblings.setdefault(c["familyID"], []).append(c)
    folks = people_by_id([x for f in parent_fams + spouse_fams for x in (f["husband"], f["wife"])])
    return {"parent_fams": parent_fams, "spouse_fams": spouse_fams, "kids": kids, "siblings": siblings, "folks": folks}


def face_tag(pid, m):
    """The person's face tag on picture m, if they're tagged in it (the page then shows their face, not the group)."""
    if not m:
        return None
    return one("SELECT * FROM app_face_tags WHERE gedcom = %s AND personID = %s AND mediaID = %s ORDER BY id LIMIT 1",
               (tree(), pid, m["mediaID"]))


def coordinates(names):
    """{place name, lower case: (lat, lon, approx)} for the names that have coordinates. approx is what the lookup
    matched when only the town or county could be found (app/geocode.py). A place with no coordinates of its own
    shows at the nearest place it's in ("Madison Twp, Clarion Cnty, PA" at "Clarion Cnty, PA", approx naming it),
    but never at just a state or country."""
    names = list({n for n in names if n})
    spots = _coordinates(names)
    parents = {}
    for n in names:
        segs = [s.strip() for s in n.split(",") if s.strip()]
        if n.lower() not in spots:
            parents[n] = [", ".join(segs[i:]) for i in range(1, len(segs) - 1)]
    found = _coordinates({x for ps in parents.values() for x in ps})
    for n, ps in parents.items():
        if near := next((x for x in ps if x.lower() in found), None):
            spots[n.lower()] = (*found[near.lower()][:2], near)
    return spots


def _coordinates(names):
    names = list(names)
    if not names:
        return {}
    spots = {}
    for r in query("SELECT p.place, p.latitude, p.longitude, a.matched FROM places p"
                   " LEFT JOIN app_geocode a ON a.gedcom = p.gedcom AND a.place = p.place AND a.status = 'approximate'"
                   f" WHERE p.gedcom = %s AND p.place IN ({','.join(['%s'] * len(names))})", [tree(), *names]):
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (TypeError, ValueError):  # blank, or typed in wrong in TNG
            continue
        if abs(lat) <= 90 and abs(lon) <= 180:
            spots[r["place"].lower()] = (round(lat, 5), round(lon, 5), r["matched"])
    return spots


def map_pins(entries, spots=None):
    """Pins for the events whose place has coordinates, one per spot (events in the same town share it):
    [{lat, lon, approx, events: [the entry's label, date, place, and name and url if it has them]}]."""
    spots = coordinates(x["place"] for x in entries) if spots is None else spots
    pins = {}
    for x in entries:
        spot = spots.get((x["place"] or "").lower())
        if spot:
            pin = pins.setdefault(spot[:2], {"lat": spot[0], "lon": spot[1], "approx": spot[2], "events": []})
            pin["events"].append({"label": x["label"], "date": display_date(x["date"]), "place": x["place"],
                                  **{k: x[k] for k in ("name", "url") if k in x}})
    return list(pins.values())


def life_route(entries, spots):
    """The places of a life in the order of its events, for a line through them on the map: (stops: [{label,
    place}], lines: [[from, to, approximate]]). Events with no date are left out (their place in the order isn't
    known), except birth, death and burial; a stop is added only where the place changes."""
    stops = []
    for x in entries:
        spot = spots.get((x["place"] or "").lower())
        if not spot or (x["group"] == 1 and x["tr"] == "0000-00-00"):
            continue
        if not stops or stops[-1]["spot"][:2] != spot[:2]:
            stops.append({"label": x["label"], "place": x["place"], "spot": spot})
    lines = [[a["spot"][:2], b["spot"][:2], bool(a["spot"][2] or b["spot"][2])] for a, b in zip(stops, stops[1:])]
    return [{k: s[k] for k in ("label", "place")} for s in stops], lines


def married_names(p, fam):
    """["Mary REED", ...]: her first name with each husband's surname, if it differs from hers, first marriage
    first. Husbands whose name the user can't see, and families they can't see, are left out."""
    out = []
    for f in fam["spouse_fams"]:
        h = fam["folks"].get(f["husband"])
        if (f["wife"] == p["personID"] and h and h["lastname"] and rights(f)["both"] and rights(h)["living"]
                and h["lastname"].upper() != (p["lastname"] or "").upper()):
            name = " ".join(x for x in (p["firstname"], h["lnprefix"], h["lastname"]) if x)
            out += [name] if name not in out else []
    return out


def person_detail(p, r, fam):
    """Life events (each with its notes), the other notes, sources and media of someone the user may see in full."""
    pid = p["personID"]
    media = visible_media([pid])
    notes = notes_for(pid, r)
    biography = [n for n in notes.get("", []) if n["ordernum"] < 0]  # see edit_events.set_biography
    notes[""] = [n for n in notes.get("", []) if n["ordernum"] >= 0]
    obituary = notes.pop("DEAT", [])  # notes about the death: GEDCOM's place for an obituary
    life = timeline(p, events_for(pid), fam["spouse_fams"], fam["folks"], fam["parent_fams"] + fam["spouse_fams"],
                    linked_census(pid))
    scans = fact_media([pid, *(f["familyID"] for f in fam["parent_fams"] + fam["spouse_fams"])])
    for x in life:
        x["notes"] = notes.pop(x["key"], [])
        f = x.get("family") or x.get("household")
        # a marriage's key is "F12-marr"; on its family it's the fact MARR
        x["scans"] = scans.get((f["familyID"], x["key"].rsplit("-", 1)[1].upper() if x.get("family") else x["key"])
                               if f else (pid, x["key"]), [])
    photo = main_photo(media)
    spots = coordinates(x["place"] for x in life)
    stops, lines = life_route(life, spots)
    return {"timeline": life, "pins": map_pins(life, spots), "route": stops if lines else [], "route_lines": lines, "notes": record_notes(notes), "biography": biography, "obituary": obituary,
            "citations": citations_for(pid), "media": media,
            "photo": photo, "photo_tag": face_tag(pid, photo),
            "age_at_death": age_at(p["birthdatetr"], p["deathdatetr"]), "married_names": married_names(p, fam)}


@bp.get("/person/<pid>")
def person(pid):
    p = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not p:
        abort(404)
    r = rights(p)
    fam = relatives(pid)
    detail = person_detail(p, r, fam) if r["both"] else {}
    me = g.user["personID"]
    from .comments import comments_for  # here, not at the top: comments and watch import this module
    from .watch import watching
    return render_template("person.html", p=p, r=r, **fam, search_links=search_links(p) if r["both"] else [],
                           comments=comments_for("person", pid) if r["both"] else [],
                           watching=watching(pid) if r["both"] else False,
                           rel=describe_relationship(me, pid) if me and me != pid else None, **detail)


@bp.get("/family/<fid>")
def family(fid):
    f = one("SELECT * FROM families WHERE gedcom = %s AND familyID = %s", (tree(), fid))
    if not f:
        abort(404)
    r = rights(f)
    folks = people_by_id([f["husband"], f["wife"]])
    children = query("SELECT p.* FROM children c JOIN people p ON p.personID = c.personID"
                     f" AND p.gedcom = c.gedcom WHERE c.gedcom = %s AND c.familyID = %s ORDER BY {child_order()}",
                     (tree(), fid))
    detail = {}
    if r["both"]:
        notes = notes_for(fid, r)
        scans = fact_media([fid])
        vitals = [(label, f[f"{k}date"], f[f"{k}place"], tag, notes.pop(tag, []), scans.get((fid, tag), []))
                  for label, k, tag in (("Marriage", "marr", "MARR"), ("Divorce", "div", "DIV"))
                  if f[f"{k}date"] or f[f"{k}place"]]
        events = events_for(fid)
        for e in events:
            e["notes"] = notes.pop(str(e["eventID"]), [])
            e["scans"] = scans.get((fid, str(e["eventID"])), [])
        detail = {"vitals": vitals, "events": events, "notes": record_notes(notes),
                  "citations": citations_for(fid), "media": visible_media([fid])}
    return render_template("family.html", f=f, r=r, husband=folks.get(f["husband"]), wife=folks.get(f["wife"]),
                           children=children, **detail)


@bp.get("/family/<fid>/sheet")
def group_sheet(fid):
    """Printable family group sheet: both spouses with their vitals and parents, the marriage, and every child
    with their vitals and spouses."""
    f = one("SELECT * FROM families WHERE gedcom = %s AND familyID = %s", (tree(), fid))
    if not f:
        abort(404)
    folks = people_by_id([f["husband"], f["wife"]])
    spouses = [folks[x] for x in (f["husband"], f["wife"]) if x in folks]
    famcs = [p["famc"] for p in spouses] + ["", ""]  # padded to two; a blank ID matches no family
    parent_fams = {x["familyID"]: x for x in query(
        "SELECT * FROM families WHERE gedcom = %s AND familyID IN (%s, %s)", (tree(), famcs[0], famcs[1]))}
    children = children_by_family([fid]).get(fid, [])
    child_fams = families_of([c["personID"] for c in children])
    others = people_by_id([x for fam in [*parent_fams.values(), *(x for fs in child_fams.values() for x in fs)]
                           for x in (fam["husband"], fam["wife"])])
    return render_template("group_sheet.html", f=f, spouses=spouses, parent_fams=parent_fams, children=children,
                           child_fams=child_fams, others=others, alt_birth=ALT_BIRTH)


def gens_arg(allowed, default):
    n = request.args.get("gens", default, type=int)
    return n if n in allowed else default


def children_of(pid):
    """A person's children across all their families, in family then birth order."""
    return query("SELECT p.* FROM families f JOIN children c ON c.gedcom = f.gedcom AND c.familyID = f.familyID"
                 " JOIN people p ON p.gedcom = c.gedcom AND p.personID = c.personID"
                 " WHERE f.gedcom = %s AND (f.husband = %s OR f.wife = %s)"
                 f" ORDER BY IF(f.husband = %s, f.husborder, f.wifeorder), {child_order()}", (tree(), pid, pid, pid))


PEDIGREE_GENS = range(3, 7)


def chart_photos(people):
    """{personID: (photo, face tag or None)} for chart boxes, when this viewer has photos on (?photos=1/0, kept for
    the session). Only for people whose details they may see, as on the person page. A small circle needs a face:
    the main picture an editor chose (unless it's a headstone, TNG's old default), else the Photos collection,
    preferring photos the person is tagged in; never headstones or documents. A tag crops to their face."""
    if "photos" in request.args:
        session["chart_photos"] = request.args["photos"] != "0"
    if not session.get("chart_photos", True):
        return None
    return faces(people)


@bp.app_template_global()
def face_photo(p):
    """(photo, face tag or None) for one person's small circle anywhere on the site, or None for initials.
    Remembered for the request, so someone listed twice costs one lookup."""
    cache = g.setdefault("face_photos", {})
    if p["personID"] not in cache:
        cache[p["personID"]] = faces([p]).get(p["personID"])
    return cache[p["personID"]]


def faces(people):
    """{personID: (photo, face tag or None)}: the picture for each person's small circle (see chart_photos)."""
    ids = [p["personID"] for p in people if p and rights(p)["both"]]
    chosen = [m for m in (visible_media(ids) if ids else []) if not is_url(m) and is_image(m["path"])
              and (m["mediatypeID"] == "photos" or (m["defphoto"] == "1" and m["mediatypeID"] != "headstones"))]
    tags = {}
    if chosen:
        mids = {m["mediaID"] for m in chosen}
        for t in query(f"SELECT * FROM app_face_tags WHERE gedcom = %s AND personID IN ({','.join(['%s'] * len(ids))})"
                       f" AND mediaID IN ({','.join(['%s'] * len(mids))}) ORDER BY id", [tree(), *ids, *mids]):
            tags.setdefault((t["personID"], t["mediaID"]), t)
    best = {}
    for m in sorted(chosen, key=lambda m: (m["defphoto"] != "1", (m["linkedto"], m["mediaID"]) not in tags,
                                           m["linkorder"])):
        best.setdefault(m["linkedto"], (m, tags.get((m["linkedto"], m["mediaID"]))))
    return best


@bp.get("/tree")
def tree_home():
    """The Tree menu: an hourglass chart (recentres on whoever you click) of you if you're linked to your record,
    else of the starting person an admin chose (Site settings), else a page to choose whose."""
    from .settings import stored  # here: settings is imported by modules this one is imported by
    pid = request.args.get("pid", "").strip().upper() or g.user["personID"]
    if pid:
        return redirect(url_for("main.hourglass", pid=pid))
    if start := stored("tree_start"):
        return redirect(url_for("main.hourglass", pid=start, start=1))
    linked = [r["personID"] for r in query("SELECT DISTINCT personID FROM users WHERE personID <> ''")]
    starts = sorted((p for p in people_by_id(linked).values() if rights(p)["living"]),
                    key=lambda p: (p["lastname"], p["firstname"]))
    return render_template("tree.html", starts=starts)


@bp.get("/pedigree/<pid>")
def pedigree(pid):
    """Ahnentafel-numbered: person 1, father 2n, mother 2n+1. One generation past the chart is looked up
    so the last column can show whose line goes further back."""
    gens = gens_arg(PEDIGREE_GENS, 4)
    root = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not root:
        abort(404)
    slots, parents = ancestors(root, gens + 1)
    more = {n for n in range(2 ** (gens - 1), 2 ** gens) if slots.get(2 * n) or slots.get(2 * n + 1)}
    # Columns stop at the furthest generation anyone is known in, so empty columns don't widen the chart.
    depth = min(gens, max(n.bit_length() for n, p in slots.items() if p))
    return render_template("pedigree.html", root=root, slots=slots, parents=parents, gens=gens, depth=depth,
                           choices=PEDIGREE_GENS, more=more, children=children_of(pid), alt_birth=ALT_BIRTH,
                           photos=chart_photos(p for n, p in slots.items() if n < 2 ** depth))


def ancestors(root, gens):
    """Ahnentafel slots for gens generations (root = 1, father 2n, mother 2n+1, missing ones absent) and, for each
    slot, the family of that person's parents (for their marriage): (slots, parents)."""
    slots, parents = {1: root}, {}
    for gen in range(1, gens):
        nums = [n for n in range(2 ** (gen - 1), 2 ** gen) if slots.get(n) and slots[n]["famc"]]
        if not nums:
            break
        famcs = [slots[n]["famc"] for n in nums]
        fams = {f["familyID"]: f for f in query(
            f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(famcs))})",
            [tree(), *famcs])}
        folks = people_by_id([x for f in fams.values() for x in (f["husband"], f["wife"])])
        for n in nums:
            if fam := fams.get(slots[n]["famc"]):
                slots[2 * n], slots[2 * n + 1] = folks.get(fam["husband"]), folks.get(fam["wife"])
                parents[n] = fam
    return slots, parents


DESCEND_GENS = range(2, 9)


def families_of(ids):
    """Each person's families as a spouse, in their own marriage order: {personID: [family, ...]}."""
    if not ids:
        return {}
    marks = ",".join(["%s"] * len(ids))
    out = {}
    for f in query(f"SELECT * FROM families WHERE gedcom = %s AND (husband IN ({marks}) OR wife IN ({marks}))"
                   " ORDER BY husborder, wifeorder", [tree(), *ids, *ids]):
        for x in (f["husband"], f["wife"]):
            if x in ids:
                out.setdefault(x, []).append(f)
    return out


def children_by_family(fids):
    if not fids:
        return {}
    out = {}
    for c in query("SELECT c.familyID, p.* FROM children c JOIN people p ON p.personID = c.personID"
                   f" AND p.gedcom = c.gedcom WHERE c.gedcom = %s AND c.familyID IN ({','.join(['%s'] * len(fids))})"
                   f" ORDER BY {child_order()}", [tree(), *fids]):
        out.setdefault(c["familyID"], []).append(c)
    return out


@bp.get("/descendants/<pid>")
def descendants(pid):
    """Indented descendancy, one batch of queries per generation. The generation past the last shown is
    loaded too, so the last people can show whether their line continues."""
    gens = gens_arg(DESCEND_GENS, 4)
    root = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not root:
        abort(404)
    fams, kids, seen, ids = {}, {}, {pid}, [pid]
    for _ in range(gens):
        new_fams = families_of(ids)
        fams.update(new_fams)
        new_kids = children_by_family([f["familyID"] for fs in new_fams.values() for f in fs])
        kids.update(new_kids)
        ids = [c["personID"] for cs in new_kids.values() for c in cs if c["personID"] not in seen]
        seen.update(ids)  # a loop in bad data (someone their own descendant) stops instead of repeating
    spouses = people_by_id([x for fs in fams.values() for f in fs for x in (f["husband"], f["wife"])])
    return render_template("descendants.html", root=root, gens=gens, choices=DESCEND_GENS, fams=fams, kids=kids,
                           spouses=spouses)


def sex_of(pid):
    row = one("SELECT sex FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    return row["sex"] if row else "U"


def describe_relationship(a, b):
    """relations.relationship plus the people along the way, for templates. None if unrelated."""
    rel = relationship(a, b, sex_of)
    if rel and rel["kind"] in ("blood", "marriage"):
        rel["people"] = people_by_id({*rel["path_a"], *rel["path_b"], *rel["common"], rel.get("via")})
    return rel


@bp.get("/relationship")
def relationship_page():
    """How any two people are related. Defaults the first person to the logged-in user."""
    a = request.args.get("a", "").strip().upper() or g.user["personID"]
    b = request.args.get("b", "").strip().upper()
    people = people_by_id([a, b])
    rel = describe_relationship(a, b) if a in people and b in people else None
    row = []
    if rel and rel["kind"] in ("blood", "marriage"):
        # One line of faces: the first person, up to the common ancestor, down to the second; a marriage adds the
        # spouse at the end it's on.
        row = rel["path_a"] + rel["path_b"][::-1][1:]
        if rel["kind"] == "marriage":
            row = [a, *row] if rel["side"] == "theirs" else [*row, b]
        found = people_by_id(row)
        row = [found[x] for x in row if x in found]
    return render_template("relationship.html", a=a, b=b, pa=people.get(a), pb=people.get(b), rel=rel,
                           asked=bool(a and b), row=row)

"""Importing a GEDCOM file (from a genealogy site or program) into the tree, for admins.

Everything comes in as new records with new IDs, linked among themselves; nothing already in the tree is changed.
People who are already here show up as possible duplicates on the preview, to combine afterwards with Merge.
Each import is recorded with the IDs it created, so it can be undone while nobody has edited those records.

A fact on a person or family with a date or place, of a kind this tree doesn't have yet (residence, military
service, a program's own _TAGs), brings its kind of event in with it.

shortcut: no photos or documents (OBJE: a GEDCOM carries only file names, not the files), repositories, LDS data,
or associations; the preview counts what's left out."""
import html
import io
import json
import re
import secrets
import time
import zipfile
from collections import Counter
from pathlib import Path

import pymysql
from flask import abort, current_app, flash, g, redirect, render_template, request, url_for

from .admin import bp
from .dates import convert_date
from .db import execute, insert, query
from .edit import add_places, audit, now, unlink_users
from .edit_places import PLACE_COLUMNS
from .living import latest_births, plausible, too_old
from .views import one, tree

VITALS = {"BIRT": "birth", "CHR": "altbirth", "BAPM": "altbirth", "DEAT": "death", "BURI": "burial", "CREM": "burial"}
FAMILY_VITALS = {"MARR": "marr", "DIV": "div"}
NAME_PARTS = {"NPFX": "prefix", "NSFX": "suffix", "NICK": "nickname", "SPFX": "lnprefix"}
HANDLED = {"NAME", "SEX", "FAMC", "FAMS", "NOTE", "SOUR", "HUSB", "WIFE", "CHIL", "CHAN", "_UID", "RIN", "UID"}
LDS = {"BAPL", "CONL", "ENDL", "INIT", "SLGC", "SLGS", "WAC"}
# Names for kinds of event a file may bring in; any other tag is named as itself (rename it under Event types).
EVENT_NAMES = {"RESI": "Residence", "EMIG": "Emigration", "NATU": "Naturalization", "_MILT": "Military service",
               "PROB": "Probate", "RETI": "Retirement", "CONF": "Confirmation", "CHRA": "Adult christening",
               "BARM": "Bar mitzvah", "BASM": "Bat mitzvah", "BLES": "Blessing", "ORDN": "Ordination",
               "NATI": "Nationality", "ARVL": "Arrival", "DPRT": "Departure", "_DEG": "Degree",
               "ENGA": "Engagement", "MARB": "Marriage banns", "MARC": "Marriage contract",
               "MARS": "Marriage settlement", "ANUL": "Annulment", "_AKA": "Also known as"}
NAME_TYPES = {"married": "Married name", "maiden": "Maiden name", "birth": "Birth name", "aka": "Also known as",
              "immigrant": "Immigrant name"}
KINDS = {"I": "person", "F": "family"}


class GedcomError(ValueError):
    pass


def parse(text):
    """GEDCOM lines into records: [{"tag", "xref", "value", "children": [...]}]. CONT and CONC lines are joined
    onto the line they continue."""
    records, stack = [], []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        m = re.match(r"\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?: (.*))?$", raw)
        if not m:
            raise GedcomError(f"Line {number} isn't GEDCOM: {raw[:60]!r}")
        level, xref, tag, value = int(m[1]), m[2], m[3].upper(), (m[4] or "").replace("@@", "@")
        if tag in ("CONT", "CONC") and 0 < level <= len(stack):
            stack[level - 1]["value"] += ("\n" if tag == "CONT" else "") + value
            continue
        if level > len(stack):
            raise GedcomError(f"Line {number} skips a level: {raw[:60]!r}")
        node = {"tag": tag, "xref": xref, "value": value, "children": []}
        del stack[level:]
        (stack[-1]["children"] if stack else records).append(node)
        stack.append(node)
    if not records or records[0]["tag"] != "HEAD":
        raise GedcomError("That isn't a GEDCOM file (it should start with 0 HEAD).")
    return records


def unzip(data):
    """A zip holding one GEDCOM file (how Ancestry exports a tree) becomes that file; anything else is left alone."""
    if not data.startswith(b"PK\x03\x04"):
        return data
    limit = current_app.config.get("MAX_CONTENT_LENGTH") or 50 * 1024 * 1024
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            geds = [i for i in z.infolist() if i.filename.lower().endswith(".ged")
                    and not i.filename.rsplit("/", 1)[-1].startswith("._")]  # macOS adds ._ copies
            if len(geds) != 1:
                raise GedcomError("The zip file should hold one GEDCOM (.ged) file; this one holds"
                                  f" {len(geds)}.")
            with z.open(geds[0]) as f:
                out = f.read(limit + 1)  # not the size the zip claims: that can lie
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
        raise GedcomError("That zip file can't be opened (damaged, or locked with a password).")
    if len(out) > limit:
        raise GedcomError("The GEDCOM file inside the zip is too big.")
    return out


def decode(data):
    """(text, warning). GEDCOM 5.5.1 files are UTF-8; older ones (ANSEL, ANSI) are read as Latin-1, which keeps
    plain letters right but can garble accents."""
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("latin-1"), "The file isn't UTF-8, so accented letters may come out wrong."


def first(node, tag):
    return next((c for c in node["children"] if c["tag"] == tag), None)


def val(node, tag):
    c = first(node, tag) if node else None
    return c["value"].strip() if c else ""


def split_name(value):
    """ "John William /Smith/ Jr." -> ("John William", "Smith", "Jr.")"""
    m = re.match(r"([^/]*)/([^/]*)/?(.*)", value)
    return tuple(x.strip() for x in m.groups()) if m else (value.strip(), "", "")


def year(date):
    """The year of a GEDCOM date as evidence, or None."""
    return plausible(int(str(convert_date(date))[:4]))


def dates_in(node):
    """Every year written anywhere in a record (its events, at any depth)."""
    out = [y for c in node["children"] if c["tag"] == "DATE" and (y := year(c["value"]))]
    return out + [y for c in node["children"] if c["tag"] != "DATE" for y in dates_in(c)]


def birth_bounds(by):
    """For each person (by xref), the latest year they can have been born (see living.latest_births)."""
    own = {p["xref"]: min(dates_in(p), default=None) for p in by["INDI"]}
    return latest_births(own, [([val(f, k) for k in ("HUSB", "WIFE") if val(f, k)],
                                [c["value"] for c in f["children"] if c["tag"] == "CHIL"],
                                min(dates_in(f), default=None)) for f in by["FAM"]])


def person_values(indi, latest_birth=None):
    name = first(indi, "NAME")
    given, last, suffix = split_name(name["value"]) if name else ("", "", "")
    v = {"firstname": val(name, "GIVN") or given, "lastname": val(name, "SURN") or last, "suffix": suffix,
         "sex": val(indi, "SEX")[:1].upper() if val(indi, "SEX")[:1].upper() in ("M", "F") else "U"}
    for tag, col in NAME_PARTS.items():
        if val(name, tag):
            v[col] = val(name, tag)
    for c in indi["children"]:
        key = VITALS.get(c["tag"])
        if key and not v.get(f"{key}date") and not v.get(f"{key}place"):
            date, place = val(c, "DATE"), val(c, "PLAC")
            v.update({f"{key}date": date[:50], f"{key}datetr": convert_date(date), f"{key}place": place[:248]})
            if key == "altbirth":
                v["altbirthtype"] = c["tag"]
            if key == "burial":
                v["burialtype"] = int(c["tag"] == "CREM")
    died = any(c["tag"] in ("DEAT", "BURI", "CREM") for c in indi["children"])
    v["living"] = int(not died and not too_old(latest_birth))
    return v


def other_names(indi):
    """A person's names besides the first, as "Also known as" events: [(text, the NAME line, for its notes and
    sources, or None)]. A later NAME (with its TYPE: married, maiden...), or a _MARNM married surname."""
    out = []
    for n, name in enumerate(c for c in indi["children"] if c["tag"] == "NAME"):
        given, last, suffix = split_name(name["value"])
        given, last = val(name, "GIVN") or given, val(name, "SURN") or last
        if n:
            kind = val(name, "TYPE")
            text = " ".join(x for x in (given, last, suffix) if x)
            out.append((f"{NAME_TYPES.get(kind.lower(), kind)}: {text}" if kind else text, name))
        if val(name, "_MARNM"):
            out.append((f"Married name: {' '.join(x for x in (given, val(name, '_MARNM')) if x)}", None))
    return [(text, name) for text, name in out if text]


TAGS = re.compile(r"</?(p|br|div|span|b|i|u|em|strong|a|ul|ol|li|ref|references|sup|sub|font|center|small|big"
                  r"|blockquote|h[1-6]|table|tr|td|th|tbody|thead|hr)\b[^>]*>", re.I)


def plain(text):
    """Note text without the HTML some sites write into it (WikiTree biographies): paragraphs and line breaks
    become new lines, list items "- ", citations [in brackets], other tags go, &amp; and the like become characters. Text with none of
    these tags is left as it is, so a "<" in ordinary notes survives."""
    if not TAGS.search(text):
        return text
    text = re.sub(r"<(br|hr)\b[^>]*>|</(p|div|li|h[1-6]|tr|blockquote)>", "\n", text, flags=re.I)
    text = re.sub(r"<li\b[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<ref\b[^>]*/>", "", text, flags=re.I)  # a repeat of a citation already given
    text = re.sub(r"<ref\b[^>]*>(.*?)</ref>", r" [\1]", text, flags=re.I | re.S)
    text = html.unescape(TAGS.sub("", text))
    return re.sub(r"\n[ \t]*\n(\s*\n)+", "\n\n", text).strip()


def plan(records):
    """What the file holds and what won't come in: counts, people, left-out tags (tag -> times) and kinds of event
    the import will add ((tag, I or F) -> times)."""
    by = {k: [r for r in records if r["tag"] == k] for k in ("INDI", "FAM", "SOUR", "NOTE")}
    types = event_types()
    skipped = Counter(r["tag"] for r in records if r["tag"] not in ("HEAD", "TRLR", "SUBM", "INDI", "FAM", "SOUR",
                                                                     "NOTE"))
    facts = [(c, kind) for kind, recs in (("I", by["INDI"]), ("F", by["FAM"])) for r in recs for c in r["children"]
             if not (c["tag"] in HANDLED or c["tag"] in VITALS and kind == "I"
                     or c["tag"] in FAMILY_VITALS and kind == "F" or (c["tag"], kind) in types)]
    eventish = {(c["tag"], kind) for c, kind in facts if c["tag"] not in LDS
                and re.fullmatch(r"_?[A-Z][A-Z0-9_]{0,8}", c["tag"]) and (first(c, "DATE") or first(c, "PLAC"))}
    new = Counter((c["tag"], kind) for c, kind in facts if (c["tag"], kind) in eventish)
    aka = sum(len(other_names(p)) for p in by["INDI"])
    if aka and ("_AKA", "I") not in types:
        new["_AKA", "I"] = aka
    skipped.update(c["tag"] for c, kind in facts if (c["tag"], kind) not in eventish)
    return by, skipped, new


def event_types():
    """(GEDCOM tag, I or F) -> TNG event type, for the kinds of event this tree knows (not LDS)."""
    out = {}
    for t in query("SELECT eventtypeID, tag, type FROM eventtypes WHERE ldsevent = 0 ORDER BY eventtypeID"):
        out.setdefault((t["tag"], t["type"]), t["eventtypeID"])
    return out


def add_event_types(new):
    """Create the kinds of event in new, or use one this tree already has by that name: {(tag, kind): ID}, and the
    IDs created."""
    out, created = {}, []
    for tag, kind in new:
        name = EVENT_NAMES.get(tag, tag)
        hit = one("SELECT eventtypeID FROM eventtypes WHERE type = %s AND ldsevent = 0"
                  " AND (LOWER(display) = LOWER(%s) OR LOWER(description) = LOWER(%s))", (kind, name, name))
        if hit:
            out[tag, kind] = hit["eventtypeID"]
        else:
            _, out[tag, kind] = insert("eventtypes", {"tag": tag, "description": name, "display": name, "type": kind,
                                                       "keep": 1})
            created.append(out[tag, kind])
    return out, created


def child_relations(by):
    """(child, family) -> (frel, mrel) when the file says, as PEDI under a person's FAMC or as Ancestry's _FREL and
    _MREL (under a family's CHIL, or a person's FAMC as Kith writes them). Adopted, foster, step...; birth is the
    default."""
    def rel(value):
        v = value.strip().lower()
        return {"natural": "birth", "biological": "birth", "unknown": ""}.get(v, v)[:20]
    out = {}
    for p in by["INDI"]:
        for c in p["children"]:
            if c["tag"] == "FAMC" and val(c, "PEDI"):
                out[p["xref"], c["value"]] = (rel(val(c, "PEDI")),) * 2
            elif c["tag"] == "FAMC" and (val(c, "_FREL") or val(c, "_MREL")):
                out[p["xref"], c["value"]] = (rel(val(c, "_FREL")), rel(val(c, "_MREL")))
    for f in by["FAM"]:
        for c in f["children"]:
            if c["tag"] == "CHIL" and (val(c, "_FREL") or val(c, "_MREL")):
                out[c["value"], f["xref"]] = (rel(val(c, "_FREL")), rel(val(c, "_MREL")))
    return out


def possible_duplicates(people):
    """Imported people with the same first and last name and birth year as someone already in the tree."""
    out = []
    for p in people:
        v = person_values(p)
        year = str(v.get("birthdatetr") or "0000")[:4]
        if v["firstname"] and year != "0000":
            hit = one("SELECT * FROM people WHERE gedcom = %s AND firstname = %s AND lastname = %s"
                      " AND YEAR(birthdatetr) = %s LIMIT 1", (tree(), v["firstname"], v["lastname"], int(year)))
            if hit:
                out.append((v, hit))
    return out


def earlier_import(people):
    """The earlier import, not undone, that most of these people came in with, going by the _UID genealogy programs
    give each person: (import, how many), or None."""
    uids = {val(p, "_UID") for p in people} - {""}
    best = None
    for imp in query("SELECT id, at, filename, created FROM app_imports WHERE undone_at IS NULL") if uids else []:
        n = len(uids & set(json.loads(imp["created"]).get("uids", [])))
        if n and (not best or n > best[1]):
            best = (imp, n)
    return best


def imported_before(people, dupes=None):
    """Whether this file seems to have come in already: an earlier import has its people's _UIDs, or most of them
    match someone here by name and birth year. Importing it then needs a tick ("import anyway")."""
    if dupes is None:
        dupes = possible_duplicates(people)
    return bool(earlier_import(people)) or len(dupes) * 2 > len(people)


def upload_dir():
    """Uploaded files wait here between preview and import. They hold living people's details, so any left
    behind by a preview that wasn't imported are removed after a day."""
    path = Path(current_app.instance_path) / "imports"
    path.mkdir(parents=True, exist_ok=True)
    for old in path.glob("*.ged"):
        if time.time() - old.stat().st_mtime > 86400:
            old.unlink()
    return path


def stashed(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", token or ""):
        abort(400)
    path = upload_dir() / f"{token}.ged"
    if not path.is_file():
        abort(404)
    return path


@bp.route("/import", methods=["GET", "POST"])
def import_gedcom():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a GEDCOM file.")
            return redirect(url_for("admin.import_gedcom"))
        try:
            data = unzip(upload.read())
            text, warning = decode(data)
            records = parse(text)
        except GedcomError as e:
            flash(str(e))
            return redirect(url_for("admin.import_gedcom"))
        token = secrets.token_urlsafe(24)
        (upload_dir() / f"{token}.ged").write_bytes(data)
        by, skipped, new = plan(records)
        born, dupes = birth_bounds(by), possible_duplicates(by["INDI"])
        return render_template("admin_import_preview.html", token=token, filename=upload.filename, by=by,
                               people=[person_values(p, born[p["xref"]]) for p in by["INDI"][:100]],
                               warning=warning, skipped=sorted(skipped.items()), dupes=dupes,
                               again=earlier_import(by["INDI"]), seen=imported_before(by["INDI"], dupes),
                               new=[(EVENT_NAMES.get(tag, tag), KINDS[kind], n)
                                    for (tag, kind), n in sorted(new.items())])
    history = query("SELECT * FROM app_imports ORDER BY id DESC")
    for h in history:
        h["counts"] = {k: len(v) for k, v in json.loads(h["created"]).items()}
    return render_template("admin_import.html", history=history)


def id_allocator():
    """insert(table, values) with the next free ID, counting up from the highest in use once per kind instead of
    asking each time (an import makes thousands). If someone else takes a number meanwhile, it skips on."""
    counters = {}

    def next_id(table, key, prefix, values):
        if key not in counters:
            counters[key] = one(f"SELECT COALESCE(MAX(CAST(SUBSTRING({key}, %s) AS UNSIGNED)), 0) AS n FROM {table}"
                                f" WHERE gedcom = %s AND {key} LIKE %s", (len(prefix) + 1, tree(), prefix + "%"))["n"]
        while True:
            counters[key] += 1
            try:
                insert(table, {**values, "gedcom": tree(), key: f"{prefix}{counters[key]}"})
                return f"{prefix}{counters[key]}"
            except pymysql.err.IntegrityError:
                continue

    return next_id


def write(records):
    """Create everything; returns the created IDs by kind, for undo."""
    by, _, new = plan(records)
    types, t = event_types(), tree()
    made = {"people": [], "families": [], "sources": [], "xnotes": [], "events": []}
    added, made["eventtypes"] = add_event_types(new)
    made["uids"] = [u for u in (val(p, "_UID") for p in by["INDI"]) if u]  # to spot this file coming in again
    types.update(added)
    ids, places, next_id = {}, set(), id_allocator()
    for s in by["SOUR"]:
        ids[s["xref"]] = next_id("sources", "sourceID", "S", {
            "title": val(s, "TITL") or s["value"], "author": val(s, "AUTH"), "publisher": val(s, "PUBL"),
            "shorttitle": val(s, "ABBR"), "actualtext": plain(val(s, "TEXT")),
            "changedate": now(), "changedby": g.user["username"]})
        made["sources"].append(ids[s["xref"]])
    shared_notes = {}
    for n in by["NOTE"]:
        _, shared_notes[n["xref"]] = insert("xnotes", {"gedcom": t, "note": plain(n["value"])})
        made["xnotes"].append(shared_notes[n["xref"]])

    def notes_and_sources(node, owner, event_id=""):
        for c in node["children"]:
            if c["tag"] == "NOTE":
                xid = shared_notes.get(c["value"].strip())
                if xid is None:
                    _, xid = insert("xnotes", {"gedcom": t, "note": plain(c["value"])})
                    made["xnotes"].append(xid)
                insert("notelinks", {"gedcom": t, "persfamID": owner, "xnoteID": xid, "eventID": event_id})
            elif c["tag"] == "SOUR":
                ref = c["value"].strip()
                insert("citations", {"gedcom": t, "persfamID": owner, "eventID": event_id,
                                         "sourceID": ids.get(ref, ""), "page": val(c, "PAGE"),
                                         "citetext": plain(val(first(c, "DATA"), "TEXT")),
                                         "description": "" if ref in ids else ref})  # a source written inline

    def events(node, owner, kind, vitals):
        """The first birth, death, marriage... went onto the record itself: only its notes and sources go here
        (keyed the way the person page looks for them). Every other event becomes an event row."""
        used = set()
        for c in node["children"]:
            if c["tag"] in vitals and vitals[c["tag"]] not in used:
                used.add(vitals[c["tag"]])
                notes_and_sources(c, owner, "BURI" if c["tag"] == "CREM" else c["tag"])
            elif (c["tag"], kind) in types and c["tag"] not in HANDLED:  # TNG even has a "SEX" event type
                date, kind_of = val(c, "DATE"), val(c, "TYPE")
                _, eid = insert("events", {"gedcom": t, "persfamID": owner, "eventtypeID": types[c["tag"], kind],
                                               "eventdate": date[:50], "eventdatetr": convert_date(date),
                                               "eventplace": val(c, "PLAC")[:248],
                                               "info": f"{kind_of}: {c['value']}" if kind_of and c["value"] else
                                               kind_of or c["value"],
                                               "age": val(c, "AGE")[:12], "agency": val(c, "AGNC")[:120],
                                               "cause": val(c, "CAUS")[:90]})
                made["events"].append(eid)
                places.add(val(c, "PLAC")[:248])
                notes_and_sources(c, owner, str(eid))
        for text, name in other_names(node) if kind == "I" else []:
            _, eid = insert("events", {"gedcom": t, "persfamID": owner, "eventtypeID": types["_AKA", "I"],
                                       "info": text})
            made["events"].append(eid)
            if name:
                notes_and_sources(name, owner, str(eid))
        notes_and_sources(node, owner)

    living, people_fams = {}, Counter()
    born = birth_bounds(by)
    for p in by["INDI"]:
        v = person_values(p, born[p["xref"]])
        pid = next_id("people", "personID", "I", {**v, "changedate": now(), "changedby": g.user["username"]})
        ids[p["xref"]], living[pid] = pid, v["living"]
        made["people"].append(pid)
        places.update(v.get(f"{k}place", "") for k in ("birth", "altbirth", "death", "burial"))
        events(p, pid, "I", VITALS)
    # Worked out here rather than per person afterwards: who has children (TNG's haskids on their own child row)
    # and each child's first family (famc).
    parents_with_kids = {ids.get(val(f, k)) for f in by["FAM"] if first(f, "CHIL") for k in ("HUSB", "WIFE")}
    famc, rels = {}, child_relations(by)
    for f in by["FAM"]:
        husband, wife = (ids.get(val(f, k), "") for k in ("HUSB", "WIFE"))
        v = {"husband": husband, "wife": wife, "changedate": now(), "changedby": g.user["username"],
             "husborder": people_fams[husband] + 1 if husband else 0, "wifeorder": people_fams[wife] + 1 if wife else 0,
             "living": int(any(living.get(x) for x in (husband, wife)))}
        people_fams.update(x for x in (husband, wife) if x)
        for tag, key in FAMILY_VITALS.items():
            date, place = val(first(f, tag), "DATE"), val(first(f, tag), "PLAC")
            v.update({f"{key}date": date[:50], f"{key}datetr": convert_date(date), f"{key}place": place[:248]})
            places.add(place[:248])
        fid = next_id("families", "familyID", "F", v)
        made["families"].append(fid)
        for n, c in enumerate((c for c in f["children"] if c["tag"] == "CHIL" and c["value"] in ids), 1):
            kid = ids[c["value"]]
            frel, mrel = rels.get((c["value"], f["xref"]), ("", ""))
            insert("children", {"gedcom": t, "familyID": fid, "personID": kid, "ordernum": n, "frel": frel,
                                    "mrel": mrel, "haskids": int(kid in parents_with_kids)})
            famc.setdefault(kid, fid)
        events(f, fid, "F", FAMILY_VITALS)
    places.discard("")
    known = {r["place"].rstrip().lower() for chunk in chunks(sorted(places))
             for r in query(f"SELECT place FROM places WHERE gedcom = %s AND place IN ({marks(chunk)})", (t, *chunk))}
    made["places"] = sorted(p for p in places if p.rstrip().lower() not in known)  # for undo
    add_places(*places)
    pairs = list(famc.items())
    for i in range(0, len(pairs), 500):  # 500 children per UPDATE
        chunk = pairs[i:i + 500]
        execute(f"UPDATE people SET famc = CASE personID {' '.join(['WHEN %s THEN %s'] * len(chunk))} END"
                f" WHERE gedcom = %s AND personID IN ({marks(chunk)})",
                (*[x for pair in chunk for x in pair], t, *[kid for kid, _ in chunk]))
    return made


@bp.post("/import/<token>")
def confirm_import(token):
    path = stashed(token)
    records = parse(decode(path.read_bytes())[0])
    if request.form.get("anyway") != "1" and imported_before(plan(records)[0]["INDI"]):
        flash("Nothing imported: this file looks already imported. Undo the earlier import first, or tick"
              " \"Import anyway\".")
        return redirect(url_for("admin.import_gedcom"))
    made = write(records)
    path.unlink()
    # Not insert(): that fills unset columns TNG-style, and undone_at must stay NULL.
    _, import_id = execute("INSERT INTO app_imports (at, username, filename, created) VALUES (%s, %s, %s, %s)",
                           (now(), g.user["username"], request.form.get("filename", "")[:255], json.dumps(made)))
    audit("import gedcom", "app_imports", str(import_id), None, {k: len(v) for k, v in made.items()})
    flash(f"Imported {len(made['people'])} people and {len(made['families'])} families.")
    return redirect(url_for("admin.import_gedcom"))


def marks(ids):
    return ",".join(["%s"] * len(ids))


def chunks(items, n=500):
    return (items[i:i + n] for i in range(0, len(items), n))


def unused_places(names):
    """Of these place names, the ones no record or cemetery uses (the database ignores case and trailing spaces)."""
    used = set()
    for chunk in chunks(names):
        for table, col in (*PLACE_COLUMNS, ("cemeteries", "place")):
            where, args = ("", chunk) if table == "cemeteries" else ("gedcom = %s AND ", (tree(), *chunk))
            used.update(r["p"].rstrip().lower() for r in query(
                f"SELECT DISTINCT {col} AS p FROM {table} WHERE {where}{col} IN ({marks(chunk)})", args))
    return [n for n in names if n.rstrip().lower() not in used]


@bp.post("/import/<int:import_id>/undo")
def undo_import(import_id):
    imp = one("SELECT * FROM app_imports WHERE id = %s AND undone_at IS NULL", (import_id,))
    if not imp:
        abort(404)
    made, t = json.loads(imp["created"]), tree()
    for table, key, kind in (("people", "personID", "people"), ("families", "familyID", "families")):
        if not made[kind]:
            continue
        still = one(f"SELECT COUNT(*) AS n, SUM(changedate > %s) AS changed FROM {table} WHERE gedcom = %s"
                    f" AND {key} IN ({marks(made[kind])})", (imp["at"], t, *made[kind]))
        if still["n"] != len(made[kind]) or still["changed"]:  # edited, merged or deleted since
            flash(f"Some imported {kind} were changed, merged or deleted after the import, so it can't be undone"
                  " automatically.")
            return redirect(url_for("admin.import_gedcom"))
    owners = made["people"] + made["families"]
    if made["people"]:  # what the site added to imported people since: as when deleting a person by hand
        people = made["people"]
        for table in ("app_face_tags", "app_watches", "app_census_links"):
            execute(f"DELETE FROM {table} WHERE gedcom = %s AND personID IN ({marks(people)})", (t, *people))
        execute(f"DELETE FROM app_comments WHERE gedcom = %s AND kind = 'person' AND target IN ({marks(people)})",
                (t, *people))
        unlink_users(people)
    if owners:
        for table, col in (("events", "persfamID"), ("notelinks", "persfamID"),
                           ("citations", "persfamID"), ("medialinks", "personID")):
            execute(f"DELETE FROM {table} WHERE gedcom = %s AND {col} IN ({marks(owners)})", (t, *owners))
        execute(f"DELETE FROM children WHERE gedcom = %s AND (personID IN ({marks(owners)})"
                f" OR familyID IN ({marks(owners)}))", (t, *owners, *owners))
        execute(f"DELETE FROM families WHERE gedcom = %s AND familyID IN ({marks(owners)})", (t, *owners))
        execute(f"DELETE FROM people WHERE gedcom = %s AND personID IN ({marks(owners)})", (t, *owners))
    if made["xnotes"]:
        execute(f"DELETE FROM xnotes WHERE ID IN ({marks(made['xnotes'])})"
                " AND NOT EXISTS (SELECT 1 FROM notelinks nl WHERE nl.xnoteID = xnotes.ID)", made["xnotes"])
    if made["sources"]:
        execute(f"DELETE FROM sources WHERE gedcom = %s AND sourceID IN ({marks(made['sources'])})"
                " AND NOT EXISTS (SELECT 1 FROM citations c WHERE c.gedcom = sources.gedcom"
                " AND c.sourceID = sources.sourceID)", (t, *made["sources"]))
    for chunk in chunks(unused_places(made.get("places", []))):  # unless someone edited the place since
        execute(f"DELETE FROM places WHERE gedcom = %s AND changedate <= %s AND place IN ({marks(chunk)})",
                (t, imp["at"], *chunk))
    if made.get("eventtypes"):  # imports before kinds of event came in with them have none
        execute(f"DELETE FROM eventtypes WHERE eventtypeID IN ({marks(made['eventtypes'])}) AND NOT EXISTS"
                " (SELECT 1 FROM events e WHERE e.eventtypeID = eventtypes.eventtypeID)", made["eventtypes"])
    execute("UPDATE app_imports SET undone_at = NOW() WHERE id = %s", (import_id,))
    audit("undo import", "app_imports", str(import_id), {k: len(v) for k, v in made.items()}, None)
    flash(f"Undone: removed {len(made['people'])} people and {len(made['families'])} families.")
    return redirect(url_for("admin.import_gedcom"))

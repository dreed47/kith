"""Whole-tree GEDCOM 5.5.1 download for users with TNG's "allow_ged" right. Filtered like TNG's gedcom.php:
people the user can't see living become "Living" and private ones keep only their name (both keep their family
links), secret notes need the private-notes right, and LDS data is never exported.

Photos and documents are OBJE records naming each file by its path under the media folder (the files themselves
aren't in a GEDCOM), referenced from the person or family, from the event TNG linked them to, and from the source
citations they're scans of.

shortcut: no repositories or addresses; add them if someone needs a fuller file.
"""
import datetime
from collections import defaultdict
from pathlib import Path

from flask import Response, abort, g

from .db import query
from .files import is_url
from .privacy import media_filter, rights
from .views import EDITOR_ONLY_TAGS, bp, child_order, tree

MAX_LINE = 200  # GEDCOM allows 255; longer text continues on CONC lines
LDS_KEYS = ("BAPL", "CONL", "INIT", "ENDL", "SLGC", "SLGS")
MEDIA_TYPES = {"photos": "photo", "headstones": "tombstone"}  # collection -> GEDCOM's SOURCE_MEDIA_TYPE


def text_lines(level, tag, value=""):
    """One tag with its value; newlines become CONT lines and long lines CONC lines, never split at a space
    (some readers trim around CONC)."""
    out = []
    for i, part in enumerate(str(value).replace("\r\n", "\n").replace("\r", "\n").replace("@", "@@").split("\n")):
        chunks = []
        while len(part) > MAX_LINE:
            cut = MAX_LINE
            while cut > 1 and " " in (part[cut - 1], part[cut]):
                cut -= 1
            chunks.append(part[:cut])
            part = part[cut:]
        chunks.append(part)
        for j, chunk in enumerate(chunks):
            lvl, t = (level, tag) if i == j == 0 else (level + 1, "CONC" if j else "CONT")
            out.append(f"{lvl} {t} {chunk}".rstrip())
    return out


def event_lines(level, tag, date, place, extra=()):
    """An event with its date and place; nothing at all when it has neither and no other details."""
    body = [*(text_lines(level + 1, "DATE", date) if date else []),
            *(text_lines(level + 1, "PLAC", place) if place else []), *extra]
    return [f"{level} {tag}", *body] if body else []


def child_relation(frel, mrel):
    """GEDCOM's PEDI when both parents agree on one it knows; otherwise Ancestry's _FREL and _MREL (step...)."""
    frel, mrel = frel or "birth", mrel or "birth"
    if frel == mrel == "birth":
        return []
    if frel == mrel and frel in ("adopted", "foster", "sealing"):
        return [f"2 PEDI {frel}"]
    return [f"2 _FREL {frel}", f"2 _MREL {mrel}"]


def gedcom_lines(user):
    def rows(sql):
        return query(sql, (tree(),))

    people = {p["personID"]: p for p in rows("SELECT * FROM people WHERE gedcom = %s ORDER BY ID")}
    families = {f["familyID"]: f for f in rows("SELECT * FROM families WHERE gedcom = %s ORDER BY ID")}
    sources = rows("SELECT * FROM sources WHERE gedcom = %s ORDER BY ID")
    source_ids = {s["sourceID"] for s in sources}
    famc, fams, chil = defaultdict(list), defaultdict(list), defaultdict(list)
    for c in rows("SELECT c.* FROM children c JOIN people p ON p.gedcom = c.gedcom AND p.personID = c.personID"
                  f" WHERE c.gedcom = %s ORDER BY {child_order()}"):
        if c["personID"] in people and c["familyID"] in families:
            famc[c["personID"]].append((c["parentorder"], c["familyID"], c["frel"].lower(), c["mrel"].lower()))
            chil[c["familyID"]].append(c["personID"])
    for f in families.values():
        for x, order in ((f["husband"], f["husborder"]), (f["wife"], f["wifeorder"])):
            if x in people:
                fams[x].append((order, f["familyID"]))
    # Notes and citations hang off a record (eventID blank), a custom event (its number) or a vital (its tag).
    editor = user["allow_edit"] == 1
    events = defaultdict(list)
    for e in rows("SELECT e.*, et.tag, et.description FROM events e JOIN eventtypes et"
                  " ON et.eventtypeID = e.eventtypeID WHERE e.gedcom = %s AND et.ldsevent = 0"
                  " ORDER BY e.eventdatetr = '0000-00-00', e.eventdatetr, e.eventID"):
        if editor or e["tag"] not in EDITOR_ONLY_TAGS:
            events[e["persfamID"]].append(e)
    notes, cites, keys = defaultdict(list), defaultdict(list), defaultdict(set)
    for n in rows("SELECT nl.persfamID, nl.eventID, nl.secret, x.note FROM notelinks nl JOIN xnotes x"
                  " ON x.ID = nl.xnoteID WHERE nl.gedcom = %s ORDER BY nl.ordernum"):
        notes[n["persfamID"], n["eventID"]].append(n)
        keys[n["persfamID"]].add(n["eventID"])
    for c in rows("SELECT * FROM citations WHERE gedcom = %s ORDER BY ordernum"):
        cites[c["persfamID"], c["eventID"]].append(c)
        keys[c["persfamID"]].add(c["eventID"])
    cond, params = media_filter("m", user)
    media = {m["mediaID"]: m for m in query(
        f"SELECT * FROM media m WHERE m.gedcom = %s AND m.path <> '' AND {cond}", [tree(), *params])}
    objs, scans, used = defaultdict(list), defaultdict(list), set()
    for ml in rows("SELECT * FROM medialinks WHERE gedcom = %s AND dontshow = 0 ORDER BY ordernum"):
        if ml["mediaID"] in media:
            objs[ml["personID"], ml["eventID"]].append(ml["mediaID"])
            keys[ml["personID"]].add(ml["eventID"])
    for cm in query("SELECT * FROM app_citation_media ORDER BY ordernum"):
        if cm["mediaID"] in media:
            scans[cm["citationID"]].append(cm["mediaID"])

    def obje(level, ids):
        used.update(ids)
        return [f"{level} OBJE @M{mid}@" for mid in ids]

    def attached(level, owner, key, r):
        out = []
        for n in notes.get((owner, key), []):
            if not n["secret"] or r["private_notes"]:
                out += text_lines(level, "NOTE", n["note"])
        for c in cites.get((owner, key), []):
            if c["sourceID"] in source_ids:
                out.append(f"{level} SOUR @{c['sourceID']}@")
                out += text_lines(level + 1, "PAGE", c["page"]) if c["page"] else []
                out += [f"{level + 1} DATA", *text_lines(level + 2, "TEXT", c["citetext"])] if c["citetext"] else []
            elif c["description"] or c["page"]:  # a citation with no source record: its text stands in
                out += text_lines(level, "SOUR", " ".join(x for x in (c["description"], c["page"]) if x))
            else:
                continue
            out += obje(level + 1, scans.get(c["citationID"], []))
        return out + obje(level, objs.get((owner, key), []))

    def details(owner, r, vitals, done=()):
        """Vitals (tag, key their notes use, date, place), then custom events, then record-level notes and
        sources. done: keys already written elsewhere."""
        out = []
        for tag, key, date, place in vitals:
            out += event_lines(1, tag, date, place, attached(2, owner, key, r))
        for e in events.get(owner, []):
            extra = [*(text_lines(2, "TYPE", e["description"]) if e["tag"] == "EVEN" and e["description"] else []),
                     *(text_lines(2, "AGE", e["age"]) if e["age"] else []),
                     *(text_lines(2, "AGNC", e["agency"]) if e["agency"] else []),
                     *(text_lines(2, "CAUS", e["cause"]) if e["cause"] else []),
                     *attached(2, owner, str(e["eventID"]), r)]
            out += [*text_lines(1, e["tag"], e["info"]),
                    *(text_lines(2, "DATE", e["eventdate"]) if e["eventdate"] else []),
                    *(text_lines(2, "PLAC", e["eventplace"]) if e["eventplace"] else []), *extra]
        out += attached(1, owner, "", r)
        written = {"", *done, *(v[1] for v in vitals), *(str(e["eventID"]) for e in events.get(owner, []))}
        for key in sorted(keys.get(owner, set()) - written):  # on something not exported (e.g. a note): top level
            if key not in LDS_KEYS:
                out += attached(1, owner, key, r)
        return out

    yield from ["0 HEAD", "1 SOUR Kith", "1 GEDC", "2 VERS 5.5.1", "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8",
                f"1 DATE {datetime.date.today().strftime('%d %b %Y').upper()}", "1 SUBM @SUBM1@",
                "0 @SUBM1@ SUBM", "1 NAME Family tree export"]
    for pid, p in people.items():
        r = rights(p, user)
        yield f"0 @{pid}@ INDI"
        if not r["living"]:
            yield "1 NAME Living //"
        else:
            last = " ".join(x.strip() for x in (p["lnprefix"], p["lastname"]) if x.strip())
            yield from text_lines(1, "NAME", " ".join(x.strip() for x in (p["firstname"], f"/{last}/", p["suffix"]) if x.strip()))
            for tag, key in (("NPFX", "prefix"), ("NSFX", "suffix"), ("NICK", "nickname")):
                if p[key]:
                    yield from text_lines(2, tag, p[key])
            if r["both"]:
                yield from attached(2, pid, "NAME", r)
        yield f"1 SEX {p['sex'] if p['sex'] in ('M', 'F') else 'U'}"
        if r["both"]:
            alt = p["altbirthtype"] if p["altbirthtype"] in ("CHR", "BAPM") else "CHR"
            burial = "CREM" if p["burialtype"] == 1 else "BURI"
            yield from details(pid, r, [("BIRT", "BIRT", p["birthdate"], p["birthplace"]),
                                        (alt, "CHR", p["altbirthdate"], p["altbirthplace"]),
                                        ("DEAT", "DEAT", p["deathdate"], p["deathplace"]),
                                        (burial, "BURI", p["burialdate"], p["burialplace"])], done=["NAME"])
        for _, f, frel, mrel in sorted(famc[pid]):
            yield f"1 FAMC @{f}@"
            if r["both"]:
                yield from child_relation(frel, mrel)
        yield from (f"1 FAMS @{f}@" for _, f in sorted(fams[pid]))
    for fid, f in families.items():
        r = rights(f, user)
        yield f"0 @{fid}@ FAM"
        for tag, x in (("HUSB", f["husband"]), ("WIFE", f["wife"])):
            if x in people:
                yield f"1 {tag} @{x}@"
        yield from (f"1 CHIL @{c}@" for c in chil[fid])
        if r["both"]:
            yield from details(fid, r, [("MARR", "MARR", f["marrdate"], f["marrplace"]),
                                        ("DIV", "DIV", f["divdate"], f["divplace"])])
    for s in sources:
        yield f"0 @{s['sourceID']}@ SOUR"
        for tag, key in (("TITL", "title"), ("ABBR", "shorttitle"), ("AUTH", "author"), ("PUBL", "publisher"),
                         ("TEXT", "actualtext")):
            if s[key]:
                yield from text_lines(1, tag, s[key])
    for mid in sorted(used):
        m = media[mid]
        yield f"0 @M{mid}@ OBJE"
        yield from text_lines(1, "FILE", m["path"] if is_url(m) else
                              f"{m['mediatypeID'] if m['usecollfolder'] else 'media'}/{m['path']}")
        if ext := Path(m["path"]).suffix.lstrip(".").lower():
            yield f"2 FORM {ext}"
            if m["mediatypeID"] in MEDIA_TYPES:
                yield f"3 TYPE {MEDIA_TYPES[m['mediatypeID']]}"
        if m["description"]:
            yield from text_lines(2, "TITL", m["description"])
    yield "0 TRLR"


@bp.get("/export.ged")
def export():
    if g.user["allow_ged"] != 1:
        abort(403)
    body = "\n".join(gedcom_lines(g.user)) + "\n"
    name = f"{tree()}-{datetime.date.today():%Y-%m-%d}.ged"
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})

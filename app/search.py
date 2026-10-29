"""People search: name words with spelling variants of surnames, filters, sorting and paging."""
import time

from flask import current_app

from .db import query

NAMES_TTL = 300  # seconds; edits refresh it at once via invalidate()
_cache = {"at": 0.0, "names": None, "aliases": None}
PER_PAGE = 50
SORTS = {"name": "p.lastname, p.firstname, p.birthdatetr",
         "born": "p.birthdatetr = '0000-00-00', p.birthdatetr, p.lastname, p.firstname",
         "died": "p.deathdatetr = '0000-00-00', p.deathdatetr, p.lastname, p.firstname"}


def invalidate():
    _cache["at"] = 0.0


def surnames():
    """Distinct surnames in upper case -> as stored. shortcut: per-process cache, like relations.graph."""
    if _cache["names"] is None or time.time() - _cache["at"] > NAMES_TTL:
        rows = query("SELECT DISTINCT lastname FROM people WHERE gedcom = %s AND lastname <> ''",
                     (current_app.config["TREE"],))
        alias_rows = query("SELECT name, main FROM app_surname_aliases WHERE gedcom = %s",
                           (current_app.config["TREE"],))
        _cache.update(at=time.time(), names={r["lastname"].upper(): r["lastname"] for r in rows},
                      aliases={r["name"].upper(): r["main"].upper() for r in alias_rows})
    return _cache["names"]


def aliases():
    """{spelling: the spelling it's listed under}, upper case, for every spelling an editor linked to another."""
    surnames()
    return _cache["aliases"]


def same_name(word):
    """Stored spellings an editor said are one surname with word (its own included), or [] if none."""
    w = word.strip().upper()
    main = aliases().get(w, w)
    group = {main, *(k for k, v in aliases().items() if v == main)}
    return sorted(surnames()[k] for k in group if k in surnames()) if len(group) > 1 else []


def distance(a, b, limit):
    """Edit distance between a and b, or limit + 1 once it's clearly larger (keeps the scan fast)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def variants(word):
    """Other stored surnames spelled like word: one letter different (LINDQVIST -> LINDQUIST), or the same name
    with stray spaces (" HOLLIS"). Shorter names only get the stray-space kind: one letter changes them too
    much (MARSH/MARCH, PELL/PELT). Two letters drifts into other families even when long (LINDQVIST/LUNDKVIST).
    Spellings an editor linked as one surname (same_name) are always included, however different."""
    w = word.strip().upper()
    limit = 1 if len(w) >= 7 else 0
    return sorted({stored for upper, stored in surnames().items()
                   if upper != w and distance(w, upper.strip(), limit) <= limit}
                  | {stored for stored in same_name(w) if stored.upper() != w})

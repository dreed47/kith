"""Merging two records of the same person (TNG's admin_merge.php): pick which values to keep, move everything that
pointed at the duplicate to the kept person, then delete the duplicate. When both were married to the same spouse,
their two family records become one too. Everything removed or changed is kept in the edit history.

TNG's tables are MyISAM (no transactions): the kept person is written first, then the links are moved, and the
duplicate is deleted last, so a failure part-way leaves both people, never a missing one."""
from flask import abort, flash, redirect, render_template, request, url_for

from .db import execute, query, update
from .edit import (PERSON_EVENTS, audit, bp, editable, get_family, get_person, refresh_haskids, require, set_famc,
                   sync_family_flags)
from .privacy import people_filter
from .views import one, tree

# What can be chosen from either record. Each group is copied together (an event's date, sort date and place).
NAME_FIELDS = {"firstname": "First name", "lnprefix": "Surname prefix (van, de)", "lastname": "Last name",
               "nickname": "Nickname", "title": "Title", "prefix": "Prefix", "suffix": "Suffix"}
CHOICES = {**{k: (label, (k,)) for k, label in NAME_FIELDS.items()},
           "sex": ("Sex", ("sex",)), "living": ("Living", ("living",)), "private": ("Private", ("private",)),
           **{ev: (label, (f"{ev}date", f"{ev}datetr", f"{ev}place") + (("altbirthtype",) if ev == "altbirth" else ())
                   + (("burialtype",) if ev == "burial" else ()))
              for ev, label in PERSON_EVENTS.items()}}
DUPES_PER_PAGE = 50
LINKS = (("events", "persfamID"), ("notelinks", "persfamID"), ("citations", "persfamID"))
UNIQUE_LINKS = (("medialinks", "personID"), ("branchlinks", "persfamID"))  # the same link can't exist twice


def can_merge():
    require("allow_edit")
    require("allow_delete")


def duplicates(strong, page):
    """Pairs of people with the same first and last name: strong ones also share a birth year, the rest are
    missing a birth year on at least one side. ([(a, b)], total)"""
    cond_a, args_a = people_filter("a")
    cond_b, args_b = people_filter("b")
    year = ("YEAR(a.birthdatetr) > 0 AND YEAR(a.birthdatetr) = YEAR(b.birthdatetr)" if strong
            else "(YEAR(a.birthdatetr) = 0 OR YEAR(b.birthdatetr) = 0)")
    sql = (" FROM people a JOIN people b ON b.gedcom = a.gedcom AND b.lastname = a.lastname"
           f" AND b.firstname = a.firstname AND b.ID > a.ID WHERE a.gedcom = %s AND a.firstname <> '' AND {year}"
           f" AND {cond_a} AND {cond_b}")
    args = [tree(), *args_a, *args_b]
    total = one(f"SELECT COUNT(*) AS n {sql}", args)["n"]
    rows = query(f"SELECT a.personID AS a, b.personID AS b {sql} ORDER BY a.lastname, a.firstname, a.ID"
                 " LIMIT %s OFFSET %s", [*args, DUPES_PER_PAGE, (page - 1) * DUPES_PER_PAGE])
    return [(get_person(r["a"]), get_person(r["b"])) for r in rows], total


def has(p, fields):
    """Whether a person has a value for this choice. Sort dates are skipped: an empty one reads 0000-00-00."""
    return any(p[f] for f in fields if not f.endswith("tr"))


def summary(pid):
    """What hangs off a person, in words, for the comparison page."""
    where = {"Parent families": ("children", "personID = %s"),
             "Spouse families": ("families", "(husband = %s OR wife = %s)"),
             "Events": ("events", "persfamID = %s"), "Notes": ("notelinks", "persfamID = %s"),
             "Sources": ("citations", "persfamID = %s"), "Photos and documents": ("medialinks", "personID = %s")}
    return {label: one(f"SELECT COUNT(*) AS n FROM {table} WHERE gedcom = %s AND {cond}",
                       (tree(), *[pid] * cond.count("%s")))["n"] for label, (table, cond) in where.items()}


def problem(a, b):
    """Why these two can't be merged, or None."""
    if a["personID"] == b["personID"]:
        return "Those are the same person record."
    if one("SELECT 1 AS x FROM families WHERE gedcom = %s AND ((husband = %s AND wife = %s)"
           " OR (husband = %s AND wife = %s))", (tree(), a["personID"], b["personID"], b["personID"], a["personID"])):
        return "They're married to each other in the tree, so they can't be the same person."
    return None


@bp.get("/merge")
def merge_page():
    can_merge()
    keep, dup = request.args.get("keep", "").strip().upper(), request.args.get("merge", "").strip().upper()
    if keep and dup:
        a, b = editable(get_person(keep)), editable(get_person(dup))
        defaults = {k: "b" if has(b, fields) and not has(a, fields) else "a" for k, (_, fields) in CHOICES.items()}
        return render_template("merge_compare.html", a=a, b=b, choices=CHOICES, defaults=defaults,
                               problem=problem(a, b), sizes={"a": summary(keep), "b": summary(dup)})
    strong = request.args.get("weak") != "1"
    page = max(request.args.get("page", 1, type=int), 1)
    pairs, total = duplicates(strong, page)
    return render_template("merge.html", pairs=pairs, total=total, strong=strong, page=page,
                           pages=max((total + DUPES_PER_PAGE - 1) // DUPES_PER_PAGE, 1))


def move_links(old, new):
    """Point every link of one person or family at another (TNG keeps links by ID, so an UPDATE moves them)."""
    t = tree()
    for table, col in LINKS:
        execute(f"UPDATE {table} SET {col} = %s WHERE gedcom = %s AND {col} = %s", (new, t, old))
    for table, col in UNIQUE_LINKS:
        execute(f"UPDATE IGNORE {table} SET {col} = %s WHERE gedcom = %s AND {col} = %s", (new, t, old))
        execute(f"DELETE FROM {table} WHERE gedcom = %s AND {col} = %s", (t, old))  # those already on the new one


def merge_families(keep, dup):
    """Fold family dup into keep (same couple): its children, links and any facts keep lacks, then delete it."""
    t = tree()
    k, d = get_family(keep), get_family(dup)
    fill = {f: d[f] for f in ("marrdate", "marrdatetr", "marrplace", "marrtype", "divdate", "divdatetr", "divplace")
            if not k[f] and d[f]}
    if fill:
        update("families", fill, {"gedcom": t, "familyID": keep})
    for c in query("SELECT * FROM children WHERE gedcom = %s AND familyID = %s", (t, dup)):
        if one("SELECT 1 AS x FROM children WHERE gedcom = %s AND familyID = %s AND personID = %s",
               (t, keep, c["personID"])):
            execute("DELETE FROM children WHERE ID = %s", (c["ID"],))
        else:
            execute("UPDATE children SET familyID = %s WHERE ID = %s", (keep, c["ID"]))
        set_famc(c["personID"])
    move_links(dup, keep)
    execute("DELETE FROM families WHERE gedcom = %s AND familyID = %s", (t, dup))
    return d


def renumber_spouse_order(pid):
    """1, 2, 3... over a person's families, in their existing order (two merged lists can clash)."""
    for n, f in enumerate(query("SELECT * FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)"
                                " ORDER BY IF(husband = %s, husborder, wifeorder), familyID",
                                (tree(), pid, pid, pid)), 1):
        execute(f"UPDATE families SET {'husborder' if f['husband'] == pid else 'wifeorder'} = %s"
                " WHERE gedcom = %s AND familyID = %s", (n, tree(), f["familyID"]))


@bp.post("/merge")
def merge():
    can_merge()
    a, b = editable(get_person(request.form.get("keep", ""))), editable(get_person(request.form.get("merge", "")))
    if problem(a, b):
        abort(400)
    keep, dup, t = a["personID"], b["personID"], tree()
    own = {f["familyID"] for f in query("SELECT familyID FROM families WHERE gedcom = %s AND (husband = %s"
                                         " OR wife = %s)", (t, keep, keep))}
    before = {"kept": a, "merged": b,
              **{f"{table}.{col}": query(f"SELECT * FROM {table} WHERE gedcom = %s AND {col} = %s", (t, dup))
                 for table, col in (*LINKS, *UNIQUE_LINKS, ("children", "personID"), ("app_face_tags", "personID"))},
              "spouse families": query("SELECT * FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)",
                                       (t, dup, dup))}
    chosen = {f: b[f] for k, (_, fields) in CHOICES.items() if request.form.get(k) == "b" for f in fields}
    if chosen:
        update("people", chosen, {"gedcom": t, "personID": keep})
    for c in before["children.personID"]:  # the duplicate as someone's child
        if one("SELECT 1 AS x FROM children WHERE gedcom = %s AND familyID = %s AND personID = %s",
               (t, c["familyID"], keep)):
            execute("DELETE FROM children WHERE ID = %s", (c["ID"],))
        else:
            execute("UPDATE children SET personID = %s WHERE ID = %s", (keep, c["ID"]))
    execute("UPDATE families SET husband = %s WHERE gedcom = %s AND husband = %s", (keep, t, dup))
    execute("UPDATE families SET wife = %s WHERE gedcom = %s AND wife = %s", (keep, t, dup))
    move_links(dup, keep)
    execute("UPDATE users SET personID = %s WHERE personID = %s", (keep, dup))
    execute("UPDATE app_link_requests SET personID = %s WHERE personID = %s", (keep, dup))
    execute("UPDATE app_face_tags SET personID = %s WHERE gedcom = %s AND personID = %s", (keep, t, dup))
    before["comments"] = query("SELECT * FROM app_comments WHERE gedcom = %s AND kind = 'person' AND target = %s",
                               (t, dup))
    execute("UPDATE app_comments SET target = %s WHERE gedcom = %s AND kind = 'person' AND target = %s", (keep, t, dup))
    execute("UPDATE IGNORE app_watches SET personID = %s WHERE gedcom = %s AND personID = %s", (keep, t, dup))
    execute("DELETE FROM app_watches WHERE gedcom = %s AND personID = %s", (t, dup))
    merged_families = []
    couples = {}
    families = query("SELECT * FROM families WHERE gedcom = %s AND (husband = %s OR wife = %s)"
                     " ORDER BY IF(husband = %s, husborder, wifeorder), familyID", (t, keep, keep, keep))
    for f in sorted(families, key=lambda f: f["familyID"] not in own):  # the kept person's own families win
        other = f["wife"] if f["husband"] == keep else f["husband"]
        if other and other in couples:  # married to the same spouse twice over: one family
            merged_families.append((couples[other], merge_families(couples[other], f["familyID"])))
            renumber_spouse_order(other)
        elif other:
            couples[other] = f["familyID"]
    execute("DELETE FROM people WHERE gedcom = %s AND personID = %s", (t, dup))
    renumber_spouse_order(keep)
    set_famc(keep)
    refresh_haskids(keep)
    sync_family_flags(keep)
    before["families folded together"] = merged_families
    audit("merge person", "people", keep, before, get_person(keep))
    flash(f"Merged {dup} into {keep}." + (f" Also combined {len(merged_families)} duplicate famil"
                                           f"{'y' if len(merged_families) == 1 else 'ies'} with the same spouse."
                                           if merged_families else ""))
    return redirect(url_for("main.person", pid=keep))

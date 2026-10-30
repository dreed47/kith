"""Surname spellings: an editor records that two spellings are one family name (Schmidt and Schmitt). Each person
keeps the spelling they used; search and the Surnames page treat the linked spellings as one surname."""
from flask import flash, redirect, render_template, request, url_for

from .db import execute, query
from .edit import audit, bp, require
from .search import aliases, distance, surnames
from .views import tree


def groups():
    """[(main spelling, [other spellings])], by main spelling, as stored on people where possible."""
    out = {}
    for name, main in aliases().items():
        out.setdefault(main, []).append(surnames().get(name, name))
    return sorted(((surnames().get(m, m), sorted(names)) for m, names in out.items()), key=lambda g: g[0].upper())


SOUNDEX = {c: str(d) for d, letters in enumerate(("", "BFPV", "CGJKQSXZ", "DT", "L", "MN", "R")) for c in letters}
SUGGESTED = 50


def soundex(name):
    """The American Soundex code of a surname ("Reed", "Reid" and "Read" are all R300)."""
    letters = [c for c in name.upper() if c.isalpha()]
    if not letters:
        return ""
    code, last = letters[0], SOUNDEX.get(letters[0], "")
    for c in letters[1:]:
        d = SOUNDEX.get(c, "")
        if d and d != last:
            code += d
        if c not in "HW":  # H and W don't separate two letters with the same code; vowels do
            last = d
    return (code + "000")[:4]


def suggestions(counts):
    """Spelling pairs that may be one surname: they sound alike (Soundex) and are a letter apart (Reed and Reid), or
    two if both are six letters or more (Stuart and Stewart; two changes make other names of short ones: Hook, Hawk). Only guesses: an editor links the ones that are. Pairs already linked are
    left out; the most used first, at most SUGGESTED. [(less used, more used)]."""
    by_sound = {}
    for name in surnames():
        by_sound.setdefault(soundex(name), []).append(name)
    pairs = []
    for names in by_sound.values():
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                limit = 2 if min(len(a.strip()), len(b.strip())) >= 6 else 1
                if aliases().get(a, a) != aliases().get(b, b) and distance(a, b, limit) <= limit:
                    rare, common = sorted((a, b), key=lambda x: (counts.get(x, 0), x))
                    pairs.append((counts.get(a, 0) + counts.get(b, 0), surnames()[rare], surnames()[common]))
    return [(rare, common) for _, rare, common in sorted(pairs, key=lambda x: (-x[0], x[1]))[:SUGGESTED]]


@bp.route("/surnames", methods=["GET", "POST"])
def surname_aliases():
    require("allow_edit")
    if request.method == "POST":
        if remove := request.form.get("remove", "").strip():
            main = aliases().get(remove.upper())
            if main:
                execute("DELETE FROM app_surname_aliases WHERE gedcom = %s AND name = %s", (tree(), remove))
                audit("unlink surname", "app_surname_aliases", remove, {"name": remove, "same as": main}, None)
                flash(f"{remove} is its own surname again.")
            return redirect(url_for("edit.surname_aliases"))
        name, main = (request.form.get(k, "").strip().upper() for k in ("name", "main"))
        missing = [x for x in (name, main) if x not in surnames()]
        if missing:
            flash(f"No one has the surname {missing[0] or '(blank)'}; check the spelling.")
        elif aliases().get(name, name) == aliases().get(main, main):
            flash(f"{surnames()[name]} and {surnames()[main]} are already one surname.")
        else:
            # Joining two spellings joins their whole groups, under main's group's spelling.
            old, new = aliases().get(name, name), aliases().get(main, main)
            execute("UPDATE app_surname_aliases SET main = %s WHERE gedcom = %s AND main = %s",
                    (surnames().get(new, new), tree(), surnames().get(old, old)))
            execute("REPLACE INTO app_surname_aliases (gedcom, name, main) VALUES (%s, %s, %s)",
                    (tree(), surnames().get(old, old), surnames().get(new, new)))
            audit("link surname", "app_surname_aliases", surnames()[name], None,
                  {"name": surnames()[name], "same as": surnames().get(new, new)})
            flash(f"{surnames()[name]} is now listed with {surnames().get(new, new)}.")
        return redirect(url_for("edit.surname_aliases"))
    counts = {r["lastname"].upper(): r["n"] for r in query(
        "SELECT lastname, COUNT(*) AS n FROM people WHERE gedcom = %s GROUP BY lastname", (tree(),))}
    return render_template("surname_aliases.html", groups=groups(), counts=counts, suggested=suggestions(counts))

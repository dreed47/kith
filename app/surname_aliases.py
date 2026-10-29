"""Surname spellings: an editor records that two spellings are one family name (Schmidt and Schmitt). Each person
keeps the spelling they used; search and the Surnames page treat the linked spellings as one surname."""
from flask import flash, redirect, render_template, request, url_for

from .db import execute, query
from .edit import audit, bp, require
from .search import aliases, surnames
from .views import tree


def groups():
    """[(main spelling, [other spellings])], by main spelling, as stored on people where possible."""
    out = {}
    for name, main in aliases().items():
        out.setdefault(main, []).append(surnames().get(name, name))
    return sorted(((surnames().get(m, m), sorted(names)) for m, names in out.items()), key=lambda g: g[0].upper())


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
    return render_template("surname_aliases.html", groups=groups(), counts=counts)

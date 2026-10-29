"""Data checks (TNG's data validation report): dates in an impossible order, ages that can't be right, and links
that don't make sense. Only dates that can be read count (unreadable ones are on "Dates to fix"); "0000" years
are skipped, and an event is only compared with another when both have a year."""
from flask import render_template

from .db import query
from .edit import bp, require
from .privacy import rights
from .views import people_by_id, tree

SHOWN = 200  # findings listed per check
KNOWN = "YEAR({}) > 0"


def parents(sql):
    """The same check once for fathers and once for mothers ({role}, {parent} in the SQL), split so each join can
    use an index (a join on "husband OR wife" took half a minute)."""
    return [sql.format(role=role, parent=col) for role, col in (("father", "f.husband"), ("mother", "f.wife"))]


CHILDREN = ("FROM children c JOIN families f ON f.gedcom = c.gedcom AND f.familyID = c.familyID"
            " JOIN people k ON k.gedcom = c.gedcom AND k.personID = c.personID"
            " JOIN people p ON p.gedcom = f.gedcom AND p.personID = {parent}")
SPOUSES = "FROM families f JOIN people p ON p.gedcom = f.gedcom AND p.personID = {parent}"
CHILD_ROW = "SELECT k.personID AS pid, f.familyID AS fid, CONCAT('born ', k.birthdate, '; {role} ', p.personID, "

# (key, what's wrong, [SELECT ... AS pid, ... AS fid, ... AS detail  with the tree as the only parameter])
CHECKS = [
    ("born_after_death", "Born after they died",
     [("SELECT personID AS pid, '' AS fid, CONCAT('born ', birthdate, ', died ', deathdate) AS detail FROM people"
      f" WHERE gedcom = %s AND {KNOWN.format('birthdatetr')} AND {KNOWN.format('deathdatetr')}"
      " AND birthdatetr > deathdatetr")]),
    ("buried_before_death", "Buried before they died",
     [("SELECT personID, '', CONCAT('died ', deathdate, ', buried ', burialdate) FROM people WHERE gedcom = %s"
      f" AND {KNOWN.format('deathdatetr')} AND {KNOWN.format('burialdatetr')} AND burialdatetr < deathdatetr")]),
    ("christened_before_birth", "Christened or baptized before they were born",
     [("SELECT personID, '', CONCAT('born ', birthdate, ', christened ', altbirthdate) FROM people"
      f" WHERE gedcom = %s AND {KNOWN.format('birthdatetr')} AND {KNOWN.format('altbirthdatetr')}"
      " AND altbirthdatetr < birthdatetr")]),
    ("too_old", "Lived more than 110 years",
     [("SELECT personID, '', CONCAT('born ', birthdate, ', died ', deathdate) FROM people WHERE gedcom = %s"
      f" AND {KNOWN.format('birthdatetr')} AND {KNOWN.format('deathdatetr')}"
      " AND YEAR(deathdatetr) - YEAR(birthdatetr) > 110")]),
    ("young_parent", "Parent younger than 13 when the child was born",
     parents(CHILD_ROW + "' born ', p.birthdate) " + CHILDREN + " WHERE c.gedcom = %s"
             " AND YEAR(k.birthdatetr) > 0 AND YEAR(p.birthdatetr) > 0"
             " AND YEAR(k.birthdatetr) - YEAR(p.birthdatetr) < 13")),
    ("old_mother", "Mother older than 55 when the child was born",
     [CHILD_ROW.format(role="mother", parent="") + "' born ', p.birthdate) " + CHILDREN.format(parent="f.wife")
      + " WHERE c.gedcom = %s AND YEAR(k.birthdatetr) > 0 AND YEAR(p.birthdatetr) > 0"
        " AND YEAR(k.birthdatetr) - YEAR(p.birthdatetr) > 55"]),
    ("old_father", "Father older than 80 when the child was born",
     [CHILD_ROW.format(role="father", parent="") + "' born ', p.birthdate) " + CHILDREN.format(parent="f.husband")
      + " WHERE c.gedcom = %s AND YEAR(k.birthdatetr) > 0 AND YEAR(p.birthdatetr) > 0"
        " AND YEAR(k.birthdatetr) - YEAR(p.birthdatetr) > 80"]),
    ("after_mother_died", "Born after their mother died",
     [CHILD_ROW.format(role="mother", parent="") + "' died ', p.deathdate) " + CHILDREN.format(parent="f.wife")
      + " WHERE c.gedcom = %s AND YEAR(k.birthdatetr) > 0 AND YEAR(p.deathdatetr) > 0"
        " AND k.birthdatetr > p.deathdatetr"]),
    ("after_father_died", "Born more than a year after their father died",
     [CHILD_ROW.format(role="father", parent="") + "' died ', p.deathdate) " + CHILDREN.format(parent="f.husband")
      + " WHERE c.gedcom = %s AND YEAR(k.birthdatetr) > 0 AND YEAR(p.deathdatetr) > 0"
        " AND YEAR(k.birthdatetr) > YEAR(p.deathdatetr) + 1"]),
    ("married_young", "Married before age 13",
     parents("SELECT p.personID, f.familyID, CONCAT('born ', p.birthdate, ', married ', f.marrdate) " + SPOUSES
             + " WHERE f.gedcom = %s AND YEAR(f.marrdatetr) > 0 AND YEAR(p.birthdatetr) > 0"
               " AND YEAR(f.marrdatetr) - YEAR(p.birthdatetr) < 13")),
    ("married_after_death", "Married after they died",
     parents("SELECT p.personID, f.familyID, CONCAT('died ', p.deathdate, ', married ', f.marrdate) " + SPOUSES
             + " WHERE f.gedcom = %s AND YEAR(f.marrdatetr) > 0 AND YEAR(p.deathdatetr) > 0"
               " AND f.marrdatetr > p.deathdatetr")),
    ("no_name", "No first or last name",
     [("SELECT personID, '', 'no name' FROM people WHERE gedcom = %s AND TRIM(firstname) = ''"
      " AND TRIM(lastname) = ''")]),
    ("empty_family", "Family with nobody in it",
     [("SELECT '', f.familyID, 'no husband, wife or children' FROM families f WHERE f.gedcom = %s"
      " AND f.husband = '' AND f.wife = '' AND NOT EXISTS (SELECT 1 FROM children c"
      " WHERE c.gedcom = f.gedcom AND c.familyID = f.familyID)")]),
    ("own_child", "A child of their own family",
     parents("SELECT c.personID, f.familyID, 'is the {role} and a child' FROM children c JOIN families f"
             " ON f.gedcom = c.gedcom AND f.familyID = c.familyID AND c.personID = {parent} WHERE c.gedcom = %s")),
]


def run(selects):
    """A check's findings the user may see: [{pid, fid, detail, person, family}]."""
    rows = [r for sql in selects for r in query(sql, (tree(),))]
    rows = [dict(zip(("pid", "fid", "detail"), r.values())) for r in rows]
    folks = people_by_id([r["pid"] for r in rows])
    fids = list({r["fid"] for r in rows if r["fid"]})
    fams = {f["familyID"]: f for f in query(
        f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(fids))})",
        [tree(), *fids])} if fids else {}
    out = []
    for r in rows:
        r["person"], r["family"] = folks.get(r["pid"]), fams.get(r["fid"])
        if (not r["person"] or rights(r["person"])["both"]) and (not r["family"] or rights(r["family"])["both"]):
            out.append(r)
    return out


@bp.get("/checks")
def checks():
    require("allow_edit")
    results = [(key, label, run(selects)) for key, label, selects in CHECKS]
    return render_template("checks.html", results=results, shown=SHOWN,
                           total=sum(len(found) for _, _, found in results))

"""Data checks (TNG's data validation report): dates in an impossible order, ages that can't be right, and links
that don't make sense. Only dates that can be read count (unreadable ones are on "Dates to fix"); "0000" years
are skipped, and an event is only compared with another when both have a year."""
import re

from flask import g, render_template

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


MONTHS = "january february march april may june july august september october november december".split()
_MONTH = r"(" + "|".join(MONTHS) + r")\.?"
# "died March 11, 1923", "passed away on 11 March 1923": the first full date after the words about dying
OBIT_DATE = re.compile(r"(?:died|passed away|death)\b[^.]{0,80}?(?:" + _MONTH + r"\s+(\d{1,2}),?\s+(\d{4})|"
                       r"(\d{1,2})\s+" + _MONTH + r",?\s+(\d{4}))", re.I)


def obituary_rows():
    """People whose obituary (a note on their death) gives a death date the death field doesn't match: only the
    parts the field has (a year alone is compared as a year). The obituary may be about someone else's death,
    so these are for an editor to look at, never fixed by themselves."""
    out = []
    for r in query("SELECT p.personID, p.deathdate, p.deathdatetr, x.note FROM notelinks n JOIN xnotes x"
                   " ON x.gedcom = n.gedcom AND x.ID = n.xnoteID JOIN people p ON p.gedcom = n.gedcom"
                   " AND p.personID = n.persfamID WHERE n.gedcom = %s AND n.eventID = 'DEAT' AND (n.secret = 0 OR %s)",
                   (tree(), int(g.user["allow_private_notes"] == 1))):
        m = OBIT_DATE.search(r["note"] or "")
        if not m:
            continue
        month = MONTHS.index((m[1] or m[5]).lower()) + 1
        said = (int(m[3] or m[6]), month, int(m[2] or m[4]))
        field = [int(x) for x in str(r["deathdatetr"] or "0000-00-00").split("-")]
        if any(f and f != s for f, s in zip(field, said)):
            # a few words before it too: "her husband died ..." means someone else
            words = " ".join(r["note"][max(m.start() - 40, 0):m.end()].split())
            out.append({"pid": r["personID"], "fid": "", "detail": f"death {r['deathdate'] or 'not given'};"
                        f" obituary: \u201c{words}\u201d"})
    return out


def run(selects):
    """A check's findings the user may see: [{pid, fid, detail, person, family}]."""
    rows = [r for sql in selects for r in query(sql, (tree(),))]
    return visible([dict(zip(("pid", "fid", "detail"), r.values())) for r in rows])


def visible(rows):
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
    results.append(("obituary_death", "Death date differs from the obituary", visible(obituary_rows())))
    return render_template("checks.html", results=results, shown=SHOWN,
                           total=sum(len(found) for _, _, found in results))

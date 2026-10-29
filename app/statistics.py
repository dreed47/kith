"""Statistics (TNG's statistics.php): how big the tree is, lifespans, families, and the most common names and
places. Counts include everyone; anything that names people leaves out those the viewer can't see. Ages only use
dates with a year, and lifespans over 110 years (almost always a typo) are left out of averages."""
from flask import render_template

from .db import query
from .privacy import people_filter
from .views import bp, one, tree

TOP = 10
SANE_AGE = "YEAR(deathdatetr) >= YEAR(birthdatetr) AND YEAR(deathdatetr) - YEAR(birthdatetr) <= 110"
DATED = "YEAR(birthdatetr) > 0 AND YEAR(deathdatetr) > 0"


@bp.get("/statistics")
def statistics():
    t = tree()
    cond, params = people_filter("p")

    def count(sql, *args):
        return one(sql, (t, *args))["n"]

    by_sex = {r["sex"]: r for r in query(
        f"SELECT sex, COUNT(*) AS n, SUM(living) AS living, AVG(CASE WHEN {DATED} AND {SANE_AGE}"
        f" THEN YEAR(deathdatetr) - YEAR(birthdatetr) END) AS age FROM people WHERE gedcom = %s GROUP BY sex",
        (t,))}
    people = sum(r["n"] for r in by_sex.values())
    totals = {
        "People": people, "Living": sum(r["living"] for r in by_sex.values()),
        "Men": by_sex.get("M", {}).get("n", 0), "Women": by_sex.get("F", {}).get("n", 0),
        "Families": count("SELECT COUNT(*) AS n FROM families WHERE gedcom = %s"),
        "Surnames": count("SELECT COUNT(DISTINCT lastname) AS n FROM people WHERE gedcom = %s AND lastname <> ''"),
        "Places": count("SELECT COUNT(*) AS n FROM places WHERE gedcom = %s"),
        "Sources": count("SELECT COUNT(*) AS n FROM sources WHERE gedcom = %s"),
        "Notes": count("SELECT COUNT(*) AS n FROM notelinks WHERE gedcom = %s"),
        "Photos and documents": one("SELECT COUNT(*) AS n FROM media")["n"],
        "With a birth date": count("SELECT COUNT(*) AS n FROM people WHERE gedcom = %s AND YEAR(birthdatetr) > 0"),
        "With a death date": count("SELECT COUNT(*) AS n FROM people WHERE gedcom = %s AND YEAR(deathdatetr) > 0"),
    }
    overall = one(f"SELECT AVG(YEAR(deathdatetr) - YEAR(birthdatetr)) AS age FROM people WHERE gedcom = %s"
                  f" AND {DATED} AND {SANE_AGE}", (t,))["age"]
    lifespans = {"Everyone": overall, "Men": by_sex.get("M", {}).get("age"), "Women": by_sex.get("F", {}).get("age")}
    kids = one("SELECT AVG(n) AS avg, MAX(n) AS most FROM (SELECT COUNT(*) AS n FROM children WHERE gedcom = %s"
               " GROUP BY familyID) t", (t,))
    biggest = query("SELECT familyID, COUNT(*) AS n FROM children WHERE gedcom = %s GROUP BY familyID"
                    " ORDER BY n DESC LIMIT 1", (t,))
    married_at = {label: one(f"SELECT AVG(YEAR(f.marrdatetr) - YEAR(p.birthdatetr)) AS age FROM families f"
                             f" JOIN people p ON p.gedcom = f.gedcom AND p.personID = f.{col} WHERE f.gedcom = %s"
                             " AND YEAR(f.marrdatetr) > 0 AND YEAR(p.birthdatetr) > 0"
                             " AND YEAR(f.marrdatetr) - YEAR(p.birthdatetr) BETWEEN 13 AND 90", (t,))["age"]
                  for label, col in (("Men", "husband"), ("Women", "wife"))}
    oldest = query(f"SELECT p.*, YEAR(deathdatetr) - YEAR(birthdatetr) AS age FROM people p WHERE p.gedcom = %s"
                   f" AND {DATED} AND {SANE_AGE} AND {cond} ORDER BY age DESC, p.lastname LIMIT {TOP}", [t, *params])
    ranked = {
        "Surnames": query(f"SELECT lastname AS name, COUNT(*) AS n FROM people p WHERE p.gedcom = %s"
                          f" AND lastname <> '' AND {cond} GROUP BY lastname ORDER BY n DESC LIMIT {TOP}", [t, *params]),
        "First names": query(f"SELECT SUBSTRING_INDEX(TRIM(firstname), ' ', 1) AS name, COUNT(*) AS n FROM people p"
                             f" WHERE p.gedcom = %s AND firstname <> '' AND {cond} GROUP BY name ORDER BY n DESC"
                             f" LIMIT {TOP}", [t, *params]),
        "Birthplaces": query(f"SELECT birthplace AS name, COUNT(*) AS n FROM people p WHERE p.gedcom = %s"
                             f" AND birthplace <> '' AND {cond} GROUP BY birthplace ORDER BY n DESC LIMIT {TOP}",
                             [t, *params]),
    }
    earliest = query(f"SELECT p.* FROM people p WHERE p.gedcom = %s AND YEAR(birthdatetr) >= 1000 AND {cond}"
                     " ORDER BY birthdatetr LIMIT 5", [t, *params])
    return render_template("statistics.html", totals=totals, lifespans=lifespans, kids=kids,
                           biggest=biggest[0] if biggest else None, married_at=married_at, oldest=oldest,
                           ranked=ranked, earliest=earliest)

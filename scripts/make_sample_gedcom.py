"""Writes app/sample.ged: a made-up family, about 800 people from 1790 to today, for tests, screenshots and trying
a new site (`flask --app app setup sample`). Every name, date and story is invented; the places are real towns so
the map has something to show. Seeded, so the file only changes when this script does.

    python3 scripts/make_sample_gedcom.py"""
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "sample.ged"
TARGET = 800
SURNAMES = ["Hollis", "Marsh", "Pruett", "Abernathy"]  # the four founding lines; Hollis is the big one
SPOUSE_SURNAMES = ["Drummond", "Fenwick", "Gaskill", "Kellerman", "Lindqvist", "Mercer", "Ostrander", "Pell",
                   "Quayle", "Rourke", "Satterfield", "Thorne", "Underhill", "Vance", "Whitcomb", "Yoder"]
MEN = ["John", "William", "James", "George", "Samuel", "Henry", "Thomas", "Charles", "Joseph", "Daniel", "Robert",
       "Walter", "Frank", "Harold", "Paul", "Mark"]
WOMEN = ["Mary", "Sarah", "Elizabeth", "Anna", "Margaret", "Catherine", "Martha", "Emma", "Clara", "Ruth", "Helen",
         "Dorothy", "Alice", "Grace", "Susan", "Linda"]
PLACES = ["Brookville, Jefferson Cnty, PA", "Clarion, Clarion Cnty, PA", "Kittanning, Armstrong Cnty, PA",
          "Indiana, Indiana Cnty, PA", "Punxsutawney, Jefferson Cnty, PA", "Franklin, Venango Cnty, PA",
          "Ravenna, Portage Cnty, OH", "Canton, Stark Cnty, OH", "Wooster, Wayne Cnty, OH",
          "Fort Wayne, Allen Cnty, IN", "Peoria, Peoria Cnty, IL", "Denver, Denver Cnty, CO"]
JOBS = ["Farmer", "Blacksmith", "Coal miner", "Schoolteacher", "Carpenter", "Merchant", "Railroad brakeman",
        "Nurse", "Machinist", "Bookkeeper"]
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def date(r, year):
    return f"{r.randint(1, 28)} {r.choice(MONTHS)} {year}"


def main():
    r = random.Random(1790)
    people, families = [], []

    def person(sex, surname, born, place):
        p = {"id": f"@I{len(people) + 1}@", "sex": sex, "given": r.choice(MEN if sex == "M" else WOMEN),
             "surname": surname, "born": born, "place": place, "fams": [], "famc": None}
        if born < 1935 or born < 1950 and r.random() < 0.5:
            p["died"] = min(born + r.randint(30, 92), 2024)
        people.append(p)
        return p

    # Each founding couple marries around 1815; children follow the father's surname, generation by generation.
    queue = []
    for i, surname in enumerate(SURNAMES):
        place = PLACES[i]
        queue.append((person("M", surname, 1788 + i, place), 3 if surname == "Hollis" else 0))
    while queue and len(people) < TARGET:
        head, extra = queue.pop(0)
        wife = person("F", r.choice(SPOUSE_SURNAMES), head["born"] + r.randint(-4, 2), head["place"])
        fam = {"id": f"@F{len(families) + 1}@", "husb": head, "wife": wife, "kids": [],
               "married": head["born"] + r.randint(21, 28), "place": head["place"]}
        families.append(fam)
        head["fams"].append(fam["id"])
        wife["fams"].append(fam["id"])
        for n in range(r.randint(2, 6) + extra):
            if (born := fam["married"] + 1 + 2 * n) > 2015:
                break
            kid = person(r.choice("MF"), head["surname"], born,
                         head["place"] if r.random() < 0.8 else r.choice(PLACES))
            kid["famc"] = fam["id"]
            fam["kids"].append(kid)
            if kid["sex"] == "M" and born < 1990 and r.random() < 0.85:
                queue.append((kid, 2 if head["surname"] == "Hollis" and born < 1900 else 0))

    lines = ["0 HEAD", "1 SOUR make_sample_gedcom", "1 GEDC", "2 VERS 5.5.1", "1 CHAR UTF-8",
             "0 @S1@ SOUR", "1 TITL County Courthouse Records (sample)", "1 AUTH Sample County Clerk"]
    for p in people:
        lines += [f"0 {p['id']} INDI", f"1 NAME {p['given']} /{p['surname']}/", f"1 SEX {p['sex']}", "1 BIRT"]
        if r.random() > 0.1:  # some births were never found, as in a real tree
            lines.append(f"2 DATE {date(r, p['born'])}")
        lines.append(f"2 PLAC {p['place']}")
        if p["born"] < 1900:
            lines += ["2 SOUR @S1@", f"3 PAGE Birth register, vol. {r.randint(1, 9)}"]
        if "died" in p:
            lines += ["1 DEAT", f"2 DATE {date(r, p['died'])}", f"2 PLAC {p['place']}",
                      "1 BURI", f"2 PLAC {p['place']}"]
        if r.random() < 0.4:
            lines += [f"1 OCCU {r.choice(JOBS)}", f"2 DATE ABT {p['born'] + 30}"]
        if r.random() < 0.1:
            lines += [f"1 NOTE {p['given']} was remembered for a fine singing voice."]
        lines += [f"1 FAMS {f}" for f in p["fams"]]
        if p["famc"]:
            lines.append(f"1 FAMC {p['famc']}")
    for f in families:
        lines += [f"0 {f['id']} FAM", f"1 HUSB {f['husb']['id']}", f"1 WIFE {f['wife']['id']}",
                  "1 MARR", f"2 DATE {date(r, f['married'])}", f"2 PLAC {f['place']}"]
        if (census := (f["married"] // 10 + 1) * 10) < 1950:
            lines += ["1 CENS", f"2 DATE {census}", f"2 PLAC {f['place']}"]
        lines += [f"1 CHIL {k['id']}" for k in f["kids"]]
    lines.append("0 TRLR")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"{OUT}: {len(people)} people, {len(families)} families")


if __name__ == "__main__":
    main()

"""More chart styles (TNG's verticalchart, fan, ahnentafel, descendtables and familychart): the same ancestors and
descendants as the pedigree and descendants pages, drawn other ways."""
import math

from flask import abort, render_template

from .db import query
from .privacy import display_name, rights
from .views import (DESCEND_GENS, ancestors, bp, chart_photos, children_by_family, families_of, gens_arg, one,
                    people_by_id, tree)

VERTICAL_GENS = range(3, 6)  # each generation doubles the width: 16 boxes across is the most that fits a screen
FAN_GENS = range(3, 7)
LIST_GENS = range(3, 11)
HOURGLASS_GENS = range(1, 5)  # each way from the person
FAN_RING = 70  # pixels per generation
FAN_RING_PHOTOS = 95  # wider with photos, so names keep their room
FAN_CENTER = 60  # radius of the middle (the person themselves)


def get_person(pid):
    p = one("SELECT * FROM people WHERE gedcom = %s AND personID = %s", (tree(), pid))
    if not p:
        abort(404)
    return p


@bp.get("/pedigree/<pid>/vertical")
def vertical_pedigree(pid):
    """Ancestors upward: the person at the bottom, each generation a row above."""
    gens = gens_arg(VERTICAL_GENS, 4)
    root = get_person(pid)
    slots, _ = ancestors(root, gens)
    rows = [[(n, slots.get(n)) for n in range(2 ** g, 2 ** (g + 1))] for g in reversed(range(gens))]
    return render_template("pedigree_vertical.html", root=root, rows=rows, gens=gens, choices=VERTICAL_GENS,
                           photos=chart_photos(p for row in rows for _, p in row))


def descendant_rows(root, gens):
    """Each generation below root as a row, one entry per parent with their children by marriage:
    [[(parent, [(family, [child, ...]), ...]), ...], ...]."""
    rows, seen, current = [], {root["personID"]}, [root]
    for _ in range(gens):
        fams = families_of([p["personID"] for p in current])
        kids = children_by_family([f["familyID"] for fs in fams.values() for f in fs])
        row = []
        for p in current:
            groups = []
            for f in fams.get(p["personID"], []):
                mine = [c for c in kids.get(f["familyID"], [])
                        if c["personID"] not in seen]  # a loop in bad data stops instead of repeating
                seen.update(c["personID"] for c in mine)
                if mine:
                    groups.append((f, mine))
            if groups:
                row.append((p, groups))
        if not row:
            break
        rows.append(row)
        current = [c for _, groups in row for _, cs in groups for c in cs]
    return rows


@bp.get("/hourglass/<pid>")
def hourglass(pid):
    """Ancestors above, descendants below as a tree (each person centred over their own children)."""
    gens = gens_arg(HOURGLASS_GENS, 2)
    root = get_person(pid)
    slots, _ = ancestors(root, gens + 1)
    above = [[(n, slots.get(n)) for n in range(2 ** g, 2 ** (g + 1))] for g in reversed(range(1, gens + 1))]
    above = [row for row in above if any(p for _, p in row)]  # no empty rows past the furthest known generation
    below = descendant_rows(root, gens)
    everyone = [root, *(p for row in above for _, p in row),
                *(c for row in below for _, groups in row for _, cs in groups for c in cs)]
    kids_of = {parent["personID"]: groups for row in below for parent, groups in row}
    # Someone with children from more than one marriage gets each marriage's children under the other parent's name
    others = people_by_id([f["wife"] if f["husband"] == pid else f["husband"] for pid, groups in kids_of.items()
                           if len(groups) > 1 for f, _ in groups])
    folks = people_by_id([f["wife"] if f["husband"] == pid else f["husband"] for f in families_of([pid]).get(pid, [])])
    return render_template("hourglass.html", root=root, above=above, kids_of=kids_of, others=others, gens=gens,
                           choices=HOURGLASS_GENS, photos=chart_photos(everyone), spouses=list(folks.values()))


def point(r, degrees):
    a = math.radians(degrees)
    return round(r * math.cos(a), 1), round(-r * math.sin(a), 1)


def fan_sectors(slots, gens, photos=False):
    """SVG pieces for a half-circle fan: father's side on the left, mother's on the right. Each generation is a
    ring; each ancestor gets an equal slice of it, with their name written along the slice (outer rings, where
    slices are narrow, write it outward from the middle). With photos, each slice keeps a circle for one at its
    inner edge and the name moves outward."""
    out, ring = [], FAN_RING_PHOTOS if photos else FAN_RING
    for g in range(1, gens):
        span = 180 / 2 ** g
        inner, outer = FAN_CENTER + (g - 1) * ring, FAN_CENTER + g * ring
        radial = g >= 3  # thin slices: read along the radius
        for i, n in enumerate(range(2 ** g, 2 ** (g + 1))):
            start, end = 180 - i * span, 180 - (i + 1) * span
            (x1, y1), (x2, y2), (x3, y3), (x4, y4) = (point(inner, start), point(outer, start),
                                                      point(outer, end), point(inner, end))
            path = (f"M{x1},{y1} L{x2},{y2} A{outer},{outer} 0 0 1 {x3},{y3} L{x4},{y4}"
                    f" A{inner},{inner} 0 0 0 {x1},{y1} Z")
            mid, radius = (start + end) / 2, (inner + outer) / 2
            # A photo circle sits at the inner edge of the slice, as big as the slice allows; the name moves out
            pr = round(min(12 if radial else 16, math.radians(span) * inner * 0.4), 1) if photos else 0
            px, py = point(inner + pr + 4, mid)
            x, y = point(radius + pr, mid)
            # characters that fit
            room = ((ring - 2 * pr - (4 if pr else 0)) if radial else math.radians(span) * radius) / 6.5
            angle = -mid if mid <= 90 else 180 - mid  # outward text, never upside down
            out.append({"n": n, "path": path, "x": x, "y": y, "rotate": round(angle if radial else 90 - mid, 1),
                        "person": slots.get(n), "room": int(room), "photo": (px, py, pr)})
    return out


def short(text, room):
    return text if len(text) <= room else text[:max(room - 1, 1)].rstrip() + "…"


@bp.get("/pedigree/<pid>/fan")
def fan_chart(pid):
    gens = gens_arg(FAN_GENS, 5)
    root = get_person(pid)
    slots, _ = ancestors(root, gens)
    photos = chart_photos([root, *slots.values()])
    sectors = fan_sectors(slots, gens, photos is not None)
    for s in sectors:
        s["label"] = short(display_name(s["person"]), s["room"]) if s["person"] else ""
    size = FAN_CENTER + (gens - 1) * (FAN_RING_PHOTOS if photos is not None else FAN_RING)
    return render_template("fan.html", root=root, sectors=sectors, gens=gens, choices=FAN_GENS, size=size,
                           photos=photos)


@bp.get("/ahnentafel/<pid>")
def ahnentafel(pid):
    """The numbered list of ancestors: 1 is the person, 2n the father of n, 2n+1 the mother."""
    gens = gens_arg(LIST_GENS, 5)
    root = get_person(pid)
    slots, parents = ancestors(root, gens)
    generations = [[(n, slots[n]) for n in range(2 ** g, 2 ** (g + 1)) if slots.get(n)] for g in range(gens)]
    return render_template("ahnentafel.html", root=root, generations=[x for x in generations if x], gens=gens,
                           choices=LIST_GENS, parents=parents)


GENERATION_NAMES = ("", "Parents", "Grandparents", "Great-grandparents")


@bp.app_template_filter("generation_name")
def generation_name(g):
    """0 -> "", 1 -> "Parents", 4 -> "2nd great-grandparents"."""
    if g < len(GENERATION_NAMES):
        return GENERATION_NAMES[g]
    n = g - 2
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')} great-grandparents"


@bp.get("/descendants/<pid>/table")
def descendant_table(pid):
    """Every descendant on one row: generation, name, dates, spouses and which parent they come through."""
    gens = gens_arg(DESCEND_GENS, 4)
    root = get_person(pid)
    rows, seen, current = [], {pid}, [(root, None)]
    for g in range(gens):
        fams = families_of([p["personID"] for p, _ in current])
        kids = children_by_family([f["familyID"] for fs in fams.values() for f in fs])
        spouses = people_by_id([x for fs in fams.values() for f in fs for x in (f["husband"], f["wife"])])
        following = []
        for p, parent in current:
            mine = fams.get(p["personID"], [])
            rows.append({"generation": g + 1, "person": p, "parent": parent,
                         "spouses": [spouses.get(f["wife"] if f["husband"] == p["personID"] else f["husband"])
                                     for f in mine]})
            for f in mine:
                for c in kids.get(f["familyID"], []):
                    if c["personID"] not in seen:  # a loop in bad data stops instead of repeating
                        seen.add(c["personID"])
                        following.append((c, p))
        current = following
    return render_template("descendant_table.html", root=root, rows=rows, gens=gens, choices=DESCEND_GENS)


@bp.get("/family/<fid>/chart")
def family_chart(fid):
    """The couple in the middle, each one's parents above, their children below."""
    f = one("SELECT * FROM families WHERE gedcom = %s AND familyID = %s", (tree(), fid))
    if not f:
        abort(404)
    folks = people_by_id([f["husband"], f["wife"]])
    couple = [folks.get(f["husband"]), folks.get(f["wife"])]
    famcs = [p["famc"] for p in couple if p and p["famc"]]
    parent_fams = {x["familyID"]: x for x in query(
        f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(famcs))})",
        [tree(), *famcs])} if famcs else {}
    elders = people_by_id([x for pf in parent_fams.values() for x in (pf["husband"], pf["wife"])])
    above = [[elders.get(parent_fams[p["famc"]][k]) for k in ("husband", "wife")]
             if p and p["famc"] in parent_fams else [None, None] for p in couple]
    children = children_by_family([fid]).get(fid, [])
    return render_template("family_chart.html", f=f, couple=couple, above=above, children=children,
                           show_marriage=rights(f)["both"])


"""How two people are related: blood relationships via their closest common ancestors, otherwise one
marriage away (in-laws, step-relations, "your cousin's husband")."""
import time

from flask import current_app

from .db import query

GRAPH_TTL = 300  # seconds; edits in this process refresh it at once via invalidate()
MAX_GENERATIONS = 40
_cache = {"at": 0.0, "graph": None}


def invalidate():
    _cache["at"] = 0.0


def graph():
    """parents: person -> [(familyID, husband, wife)], spouses: person -> [(familyID, spouse)].
    shortcut: per-process cache; with several workers another worker's edit shows up within GRAPH_TTL."""
    if _cache["graph"] is None or time.time() - _cache["at"] > GRAPH_TTL:
        tree = current_app.config["TREE"]
        fams = {f["familyID"]: f for f in query(
            "SELECT familyID, husband, wife FROM families WHERE gedcom = %s", (tree,))}
        parents, spouses = {}, {}
        for c in query("SELECT familyID, personID FROM children WHERE gedcom = %s ORDER BY parentorder", (tree,)):
            if f := fams.get(c["familyID"]):
                parents.setdefault(c["personID"], []).append((f["familyID"], f["husband"], f["wife"]))
        for f in fams.values():
            if f["husband"] and f["wife"]:
                spouses.setdefault(f["husband"], []).append((f["familyID"], f["wife"]))
                spouses.setdefault(f["wife"], []).append((f["familyID"], f["husband"]))
        _cache.update(at=time.time(), graph={"parents": parents, "spouses": spouses})
    return _cache["graph"]


def line_starts(g):
    """{person with no parents in the tree: how many descendants they have}, for everyone with any. Worked out
    once per family graph (it's cached; see graph())."""
    if _cache.get("starts_for") is not g:
        kids = {}
        for child, fams in g["parents"].items():
            for _, husband, wife in fams:
                for x in (husband, wife):
                    if x:
                        kids.setdefault(x, set()).add(child)
        counts = {}
        for root in kids.keys() - g["parents"].keys():
            seen, todo = set(), list(kids[root])
            while todo:
                x = todo.pop()
                if x not in seen:
                    seen.add(x)
                    todo.extend(kids.get(x, ()))
            counts[root] = len(seen)
        _cache.update(starts_for=g, starts=counts)
    return _cache["starts"]


def ancestors(pid, g):
    """Every ancestor of pid (and pid itself): {id: (generations up, child on the way, that child's family)}."""
    found = {pid: (0, None, None)}
    frontier = [pid]
    for depth in range(1, MAX_GENERATIONS + 1):
        nxt = []
        for person in frontier:
            for fid, husband, wife in g["parents"].get(person, ()):
                for parent in (husband, wife):
                    if parent and parent not in found:
                        found[parent] = (depth, person, fid)
                        nxt.append(parent)
        if not nxt:
            break
        frontier = nxt
    return found


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def greats(n):
    return "" if n <= 0 else "great-" * n if n <= 2 else f"{ordinal(n)} great-"


def word(sex, male, female, neutral):
    return {"M": male, "F": female}.get(sex, neutral)


def times(n):
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def blood_name(up_a, up_b, sex_b, half):
    """What B is to A, given generations from each up to their closest common ancestor."""
    h = "half-" if half else ""
    if up_a == 0:  # B descends from A
        base = word(sex_b, "son", "daughter", "child")
        return base if up_b == 1 else greats(up_b - 2) + "grand" + base
    if up_b == 0:  # B is A's ancestor
        base = word(sex_b, "father", "mother", "parent")
        return base if up_a == 1 else greats(up_a - 2) + "grand" + base
    if up_a == up_b == 1:
        return h + word(sex_b, "brother", "sister", "sibling")
    if up_a == 1:  # B descends from A's sibling
        pre = "" if up_b == 2 else greats(up_b - 3) + "grand"
        return h + word(sex_b, pre + "nephew", pre + "niece", f"{pre}niece or {pre}nephew")
    if up_b == 1:  # B is a sibling of A's ancestor
        pre = greats(up_a - 2)
        return h + word(sex_b, pre + "uncle", pre + "aunt", f"{pre}aunt or {pre}uncle")
    degree, removed = min(up_a, up_b) - 1, abs(up_a - up_b)
    return f"{'half ' if half else ''}{ordinal(degree)} cousin" + (f", {times(removed)} removed" if removed else "")


def blood(a, b, g):
    """Closest blood relationship of b to a, or None."""
    anc_a, anc_b = ancestors(a, g), ancestors(b, g)
    common = set(anc_a) & set(anc_b)
    if not common:
        return None
    best = min(common, key=lambda c: (anc_a[c][0] + anc_b[c][0], anc_a[c][0], c))
    up_a, up_b = anc_a[best][0], anc_b[best][0]
    # Half relations: the two lines reach the common ancestor through different families of theirs.
    half = up_a > 0 and up_b > 0 and anc_a[best][2] != anc_b[best][2]
    # The other parent of that same family is a common ancestor too (not for half relations).
    shared = [best] + sorted(c for c in common if c != best and not half
                             and anc_a[c] == (up_a, *anc_a[best][1:]) and anc_b[c] == (up_b, *anc_b[best][1:]))
    return {"up_a": up_a, "up_b": up_b, "half": half, "common": shared,
            "path_a": climb(anc_a, best), "path_b": climb(anc_b, best)}


def climb(anc, top):
    """IDs from the starting person up to top."""
    path, x = [], top
    while x is not None:
        path.append(x)
        x = anc[x][1]
    return path[::-1]


IN_LAW_VIA_SPOUSE = {(1, 0): "{p}-in-law", (1, 1): "{s}-in-law", (0, 1): "step{c}"}  # b is my spouse's ...
IN_LAW_VIA_THEIR_SPOUSE = {(0, 1): "{c}-in-law", (1, 1): "{s}-in-law", (1, 0): "step{p}"}  # b married my ...


def relationship(a, b, sex):
    """{"label", "kind", ...} describing what b is to a, or None if no link was found. sex: person -> M/F/U."""
    if a == b:
        return {"label": "self", "kind": "self"}
    g = graph()
    if r := blood(a, b, g):
        return {**r, "kind": "blood", "label": blood_name(r["up_a"], r["up_b"], sex(b), r["half"])}
    candidates = []
    for _, spouse in g["spouses"].get(a, ()):
        if spouse == b:
            return {"kind": "spouse", "label": word(sex(b), "husband", "wife", "spouse")}
        if r := blood(spouse, b, g):  # b is a blood relative of a's spouse
            candidates.append((r["up_a"] + r["up_b"], "theirs", spouse, r))
    for _, spouse in g["spouses"].get(b, ()):
        if r := blood(a, spouse, g):  # b married a blood relative of a
            candidates.append((r["up_a"] + r["up_b"], "mine", spouse, r))
    if not candidates:
        return None
    _, side, via, r = min(candidates, key=lambda c: (c[0], c[1], c[2]))
    words = {"p": word(sex(b), "father", "mother", "parent"), "s": word(sex(b), "brother", "sister", "sibling"),
             "c": word(sex(b), "son", "daughter", "child")}
    if side == "theirs":
        special = IN_LAW_VIA_SPOUSE.get((r["up_a"], r["up_b"]))
        label = special.format(**words) if special and not r["half"] else \
            f"{word(sex(via), 'husband', 'wife', 'spouse')}'s {blood_name(r['up_a'], r['up_b'], sex(b), r['half'])}"
    else:
        special = IN_LAW_VIA_THEIR_SPOUSE.get((r["up_a"], r["up_b"]))
        label = special.format(**words) if special and not r["half"] else \
            f"{blood_name(r['up_a'], r['up_b'], sex(via), r['half'])}'s {word(sex(b), 'husband', 'wife', 'spouse')}"
    return {**r, "kind": "marriage", "label": label, "via": via, "side": side}


def phrase(rel, whose):
    """The relationship as words after "is": phrase(rel, "your") -> "your grandfather". A long label ending in
    a possessive reads badly ("your 2nd cousin, once removed's wife"), so that becomes "the wife of your 2nd
    cousin, once removed"."""
    label = rel["label"]
    if "," in label and "'s " in label:
        relative, spouse = label.rsplit("'s ", 1)
        return f"the {spouse} of {whose} {relative}"
    return f"{whose} {label}"

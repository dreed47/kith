from collections import Counter

from flask import Blueprint, abort, render_template, request, url_for

from .db import query
from .privacy import families_filter, media_filter, people_filter
from .views import people_by_id, tree

bp = Blueprint("places", __name__)

PERSON_PLACES = {"birthplace": "Birth", "altbirthplace": "Christening", "deathplace": "Death", "burialplace": "Burial"}
FAMILY_PLACES = {"marrplace": "Marriage", "divplace": "Divorce"}


def segments(place):
    return [s.strip() for s in place.split(",") if s.strip()]


@bp.get("/places")
def places():
    """Drill down one level at a time from the last comma segment, like TNG ("PA" -> "Clarion, PA" -> ...)."""
    within = request.args.get("within", "")
    depth = len(segments(within))
    groups, exact = Counter(), None
    for r in query("SELECT place FROM places WHERE gedcom = %s AND place <> ''", (tree(),)):
        segs = segments(r["place"])
        if segs[len(segs) - depth:] != segments(within) or len(segs) < depth:
            continue
        if len(segs) == depth:
            exact = r["place"]
        else:
            groups[", ".join(segs[len(segs) - depth - 1:])] += 1
    if within and not groups and not exact:
        abort(404)
    return render_template("places.html", within=within, exact=exact,
                           groups=sorted(groups.items(), key=lambda g: g[0].lower()))


@bp.get("/place")
def place():
    name = request.args.get("name", "")
    if not name.strip():  # a blank name would list every record with no place: slow, and no use to anyone
        abort(404)
    info = query("SELECT * FROM places WHERE gedcom = %s AND place = %s", (tree(), name))
    pcond, pparams = people_filter("p")
    fcond, fparams = families_filter("f")
    hits = []
    for col, label in PERSON_PLACES.items():
        date = col.replace("place", "date")
        for p in query(f"SELECT p.*, p.{date} AS evdate FROM people p WHERE p.gedcom = %s AND p.{col} = %s"
                       f" AND {pcond}", [tree(), name, *pparams]):
            hits.append({"label": label, "date": p["evdate"], "person": p})
    for col, label in FAMILY_PLACES.items():
        date = col.replace("place", "date")
        for f in query(f"SELECT f.*, f.{date} AS evdate FROM families f WHERE f.gedcom = %s AND f.{col} = %s"
                       f" AND {fcond}", [tree(), name, *fparams]):
            hits.append({"label": label, "date": f["evdate"], "family": f})
    for e in query(
            "SELECT e.eventdate, et.display, p.personID AS pid, f.familyID AS fid FROM events e"
            " JOIN eventtypes et ON et.eventtypeID = e.eventtypeID"
            " LEFT JOIN people p ON p.gedcom = e.gedcom AND p.personID = e.persfamID"
            " LEFT JOIN families f ON f.gedcom = e.gedcom AND f.familyID = e.persfamID"
            f" WHERE e.gedcom = %s AND e.eventplace = %s AND et.ldsevent = 0"
            f" AND (p.personID IS NULL OR {pcond}) AND (f.familyID IS NULL OR {fcond})",
            [tree(), name, *pparams, *fparams]):
        hits.append({"label": e["display"], "date": e["eventdate"], "pid": e["pid"], "fid": e["fid"]})
    # Event rows only carry IDs; load the people/families they point at in one go each.
    folks = people_by_id([h["pid"] for h in hits if h.get("pid")])
    fids = [h["fid"] for h in hits if h.get("fid")]
    fams = {f["familyID"]: f for f in query(
        f"SELECT * FROM families WHERE gedcom = %s AND familyID IN ({','.join(['%s'] * len(fids))})",
        [tree(), *fids])} if fids else {}
    for h in hits:
        if h.get("pid"):
            h["person"] = folks.get(h["pid"])
        if h.get("fid"):
            h["family"] = fams.get(h["fid"])
    hits = [h for h in hits if h.get("person") or h.get("family")]
    spouses = people_by_id([x for h in hits if h.get("family") for x in (h["family"]["husband"], h["family"]["wife"])])
    if not hits and not info:
        abort(404)
    return render_template("place.html", name=name, info=info[0] if info else None, hits=hits, spouses=spouses,
                           parents=segments(name))


def buried_in(cids):
    """People this user may see, by cemetery: linked to one of its headstones (most are), or buried at its place.
    Whether someone shows depends on them, not the stone: a couple's stone hidden for one still places the other."""
    if not cids:
        return {}
    marks = ",".join(["%s"] * len(cids))
    pcond, pparams = people_filter("p")
    out = {}
    for r in query(
            "SELECT m.cemeteryID, p.* FROM media m JOIN medialinks ml ON ml.mediaID = m.mediaID"
            " AND ml.linktype = 'I' JOIN people p ON p.gedcom = ml.gedcom AND p.personID = ml.personID"
            f" WHERE m.cemeteryID IN ({marks}) AND {pcond}"
            " UNION SELECT c.cemeteryID, p.* FROM cemeteries c JOIN people p ON c.place <> ''"
            f" AND p.burialplace = c.place WHERE p.gedcom = %s AND c.cemeteryID IN ({marks}) AND {pcond}"
            " ORDER BY lastname, firstname", [*cids, *pparams, tree(), *cids, *pparams]):
        out.setdefault(r.pop("cemeteryID"), []).append(r)
    return out


def map_pin(c, people, stones):
    """A cemetery's pin for the map, or None when it has no usable coordinates (blank, or typed in wrong in TNG)."""
    try:
        lat, lon = float(c["latitude"]), float(c["longitude"])
    except (TypeError, ValueError):
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return {"lat": round(lat, 5), "lon": round(lon, 5), "name": c["cemname"], "people": people, "stones": stones,
            "where": ", ".join(x for x in (c["city"], c["county"], c["state"]) if x),
            "url": url_for("places.cemetery", cid=c["cemeteryID"])}


@bp.get("/cemeteries")
def cemeteries():
    rows = query("SELECT c.*, (SELECT COUNT(*) FROM media m WHERE m.cemeteryID = c.cemeteryID) AS n"
                 " FROM cemeteries c ORDER BY c.country, c.state, c.county, c.city, c.cemname")
    buried = buried_in([c["cemeteryID"] for c in rows])
    for c in rows:
        c["people"] = len(buried.get(c["cemeteryID"], []))
    pins = [p for c in rows if (p := map_pin(c, c["people"], c["n"]))]
    return render_template("cemeteries.html", cemeteries=rows, pins=pins)


@bp.get("/cemeteries/<int:cid>")
def cemetery(cid):
    rows = query("SELECT * FROM cemeteries WHERE cemeteryID = %s", (cid,))
    if not rows:
        abort(404)
    c = rows[0]
    mcond, mparams = media_filter("m")
    media = query(f"SELECT * FROM media m WHERE m.cemeteryID = %s AND {mcond} ORDER BY m.description",
                  [cid, *mparams])
    buried = buried_in([cid]).get(cid, [])
    pin = map_pin(c, len(buried), len(media))
    return render_template("cemetery.html", c=c, media=media, buried=buried, pins=[pin] if pin else [])

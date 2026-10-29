"""What's new: people and photos added, faces tagged and comments made, newest first, for everyone. Each viewer
sees only news about people and photos they may see in full. Additions come from our edit history (app_audit), so
the feed starts at the cutover; TNG kept no record of when things were added."""
from flask import render_template, request

from .comments import recent as recent_comments
from .db import query
from .home import ago
from .privacy import media_filter, people_filter
from .views import bp, people_by_id, tree

PER_PAGE = 30
HOME_COUNT = 6


def latest(n):
    """Up to n of the newest items of each kind, as {type, at, who, person, media, comment}."""
    pcond, pparams = people_filter("np")
    mcond, mparams = media_filter("nm")
    people = query(f"SELECT a.at, a.username, np.personID FROM app_audit a JOIN people np"
                   f" ON np.gedcom = %s AND np.personID = a.record WHERE a.action = 'add person' AND {pcond}"
                   " ORDER BY a.id DESC LIMIT %s", [tree(), *pparams, n])
    photos = query(f"SELECT a.at, a.username, nm.mediaID FROM app_audit a JOIN media nm ON nm.mediaID = a.record"
                   f" WHERE a.action = 'add media' AND {mcond} ORDER BY a.id DESC LIMIT %s", [*mparams, n])
    tags = query(f"SELECT t.* FROM app_face_tags t JOIN people np ON np.gedcom = t.gedcom AND np.personID = t.personID"
                 f" JOIN media nm ON nm.mediaID = t.mediaID WHERE t.gedcom = %s AND {pcond} AND {mcond}"
                 " ORDER BY t.id DESC LIMIT %s", [tree(), *pparams, *mparams, n])
    folks = people_by_id([r["personID"] for r in people + tags])
    mids = [r["mediaID"] for r in photos + tags] or [0]
    media = {m["mediaID"]: m for m in query(f"SELECT * FROM media WHERE mediaID IN ({','.join(['%s'] * len(mids))})",
                                           mids)}
    return [
        *({"type": "person", "at": r["at"], "who": r["username"], "person": folks[r["personID"]]} for r in people),
        *({"type": "photo", "at": r["at"], "who": r["username"], "media": media[r["mediaID"]]} for r in photos),
        *({"type": "tag", "at": t["created_at"], "who": t["created_by"], "person": folks[t["personID"]],
           "media": media[t["mediaID"]], "tag_id": t["id"]} for t in tags),
        *({"type": "comment", "at": c["created_at"], "who": c["username"], "comment": c,
           "person": c.get("person"), "media": c.get("media")} for c in recent_comments(n)),
    ]


def grouped(items):
    """Runs of the same kind of addition by the same person on the same day become one entry with a list
    (an editor adding forty people is one line, not forty). Comments always stand alone."""
    out = []
    for x in items:
        last = out[-1] if out else None
        if (last and x["type"] != "comment" and last["type"] == x["type"] and last["who"] == x["who"]
                and last["at"].date() == x["at"].date()):
            last["group"].append(x)
        else:
            out.append({**x, "group": [x], "ago": ago(x["at"])})
    return out


def whats_new(limit, offset=0):
    """(entries for this page, whether there are older ones)."""
    items = sorted(latest(offset + limit + 1), key=lambda x: x["at"], reverse=True)
    return grouped(items[offset:offset + limit]), len(items) > offset + limit


@bp.get("/whats-new")
def whats_new_page():
    page = max(request.args.get("page", 1, type=int), 1)
    entries, more = whats_new(PER_PAGE, (page - 1) * PER_PAGE)
    return render_template("whats_new.html", entries=entries, page=page, more=more)

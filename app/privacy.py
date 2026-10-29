"""Living/private rules, matching our TNG config: nonames=1 (hidden living people show as "Living"),
nnpriv=0 (private people keep their name, details hidden), livedefault=0, LDS data never shown.

shortcut: single tree and no user branches, so TNG's tree/branch checks are dropped; revisit if either appears.
"""
from flask import g


def _me(user):
    return (user or {}).get("personID") or None


def rights(row, user=None):
    """What the current user may see of a people or families row."""
    user = user if user is not None else g.user
    me = _me(user)
    is_me = bool(me) and me in (row.get("personID"), row.get("husband"), row.get("wife"))
    living = not row.get("living") or user["allow_living"] == 1 or is_me
    private = not row.get("private") or user["allow_private"] == 1 or is_me
    return {"living": living, "private": private, "both": living and private,
            "private_notes": user["allow_private_notes"] == 1 or is_me}


def display_name(row, user=None):
    if row is None:
        return ""
    if not rights(row, user)["living"]:
        return "Living"
    title = row.get("title") or row.get("prefix") or ""
    last = f"{row.get('lnprefix') or ''} {row.get('lastname') or ''}"
    name = " ".join(f"{title} {row.get('firstname') or ''} {last}".split())
    return f"{name}, {row['suffix']}" if row.get("suffix") else name


def _hidden(alias, user, family=False):
    """SQL (and params) true for a people/families row this user can't see; None if they see everything."""
    me = _me(user)
    not_me = ""
    if me:
        not_me = f" AND %s NOT IN ({alias}.husband, {alias}.wife)" if family else f" AND {alias}.personID != %s"
    parts, params = [], []
    for flag, allowed in (("living", user["allow_living"] == 1), ("private", user["allow_private"] == 1)):
        if not allowed:
            parts.append(f"({alias}.{flag} = 1{not_me})")
            params += [me] if me else []
    return (f"({' OR '.join(parts)})", params) if parts else (None, [])


def people_filter(alias, user=None):
    """SQL condition (and params) excluding people this user can't see, for name searches and lists.
    Mirrors TNG's getLivingPrivateRestrictions: you can't find hidden people by searching."""
    hidden, params = _hidden(alias, user if user is not None else g.user)
    return (f"NOT {hidden}", params) if hidden else ("1=1", [])


def families_filter(alias, user=None):
    hidden, params = _hidden(alias, user if user is not None else g.user, family=True)
    return (f"NOT {hidden}", params) if hidden else ("1=1", [])


def media_filter(alias, user=None):
    """SQL condition for media this user may see. Like TNG: 'alwayson' media always shows;
    otherwise hidden when flagged private or linked to any person/family the user can't see."""
    user = user if user is not None else g.user
    conds, params = [], []
    if user["allow_private"] != 1:
        conds.append(f"{alias}.private != 1")
    for table, a, key, family in (("people", "p", "personID", False), ("families", "f", "familyID", True)):
        hidden, hp = _hidden(a, user, family)
        if hidden:
            conds.append(
                f"NOT EXISTS (SELECT 1 FROM medialinks ml JOIN {table} {a}"
                f" ON {a}.{key} = ml.personID AND {a}.gedcom = ml.gedcom"
                f" WHERE ml.mediaID = {alias}.mediaID AND {hidden})")
            params += hp
    return (f"({alias}.alwayson = 1 OR ({' AND '.join(conds)}))", params) if conds else ("1=1", [])

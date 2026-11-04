"""Moving from TNG: `flask --app app setup import-tng dump.sql` loads a dump of a TNG database (mysqldump,
MariaDB's dump or phpMyAdmin's SQL export) into a new, empty Kith database. Kith's tree tables are TNG's without
the tng_ prefix, so rows copy across column by column: a column the TNG version lacks gets a blank, and a column
Kith doesn't use is left behind. So are TNG's other tables (albums, DNA tests, its templates and settings). User
accounts come too, with their TNG passwords. Photos and documents are copied by hand; the report says which
folders."""
import re

import click
from flask import current_app

from .db import columns, query

HOLD = "tngimport_"  # the dump's tables wait under this prefix, so they can't clash with Kith's own
CHARSETS = {"latin1": "cp1252", "utf8": "utf-8", "utf8mb3": "utf-8", "utf8mb4": "utf-8"}
# Statements that would change the connection's character set (Python already decoded the file) or its database.
SKIP = re.compile(r"(/\*!\d+\s+)?(SET\s+NAMES|SET\s+character_set_client|USE\s|CREATE\s+DATABASE)", re.I)


def statements(path):
    """The dump's statements, one at a time. Dumps end each one with ';' at the end of a line, and write newlines
    inside text as \\n, so that's a safe place to split."""
    raw = path.read_bytes()
    m = re.search(rb"SET NAMES (\w+)", raw[:20000])
    encoding = CHARSETS.get(m[1].decode().lower(), "utf-8") if m else "utf-8"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        raise click.ClickException("The dump isn't UTF-8 text. Make it again with --default-character-set=utf8mb4.")
    buf = []
    for line in text.splitlines():
        if not buf and (not line.strip() or line.startswith(("--", "/*M!999999"))):  # comments; MariaDB's sandbox line
            continue
        if line.startswith("DELIMITER"):
            raise click.ClickException("The dump has triggers or procedures, which TNG doesn't use. Make it again "
                                       "with only the tables (mysqldump --skip-triggers).")
        buf.append(line)
        if line.rstrip().endswith(";"):
            yield "\n".join(buf)
            buf = []


def load(path):
    """Run the dump into this (empty) database, then move its tables under HOLD. Returns their old names."""
    for statement in statements(path):
        if not SKIP.match(statement.lstrip()):
            query(statement)
    loaded = [next(iter(r.values())) for r in query("SHOW TABLES")]
    for t in loaded:
        query(f"RENAME TABLE `{t}` TO `{HOLD}{t}`")
    return loaded


def prefix_of(loaded):
    """TNG's table prefix (tng_ unless changed when TNG was installed), found from its people table."""
    found = [t[:-len("people")] for t in loaded if t.endswith("people")
             and query(f"SHOW COLUMNS FROM `{HOLD}{t}` LIKE 'personID'")]
    if len(found) != 1:
        raise click.ClickException("No TNG people table in the dump (tng_people)." if not found else
                                   "The dump holds more than one TNG site: " + ", ".join(f"{p}people" for p in found))
    return found[0]


def pick_tree(prefix, tree):
    """The TNG tree to import: the only one, or the one asked for."""
    trees = {r["gedcom"]: r["n"] for r in
             query(f"SELECT gedcom, COUNT(*) n FROM `{HOLD}{prefix}people` GROUP BY gedcom ORDER BY gedcom")}
    if tree in trees or (tree is None and len(trees) == 1):
        return tree or next(iter(trees))
    listed = ", ".join(f"{t} ({n} people)" for t, n in trees.items())
    if not trees:
        raise click.ClickException("The dump's people table is empty.")
    raise click.ClickException(f"Pick a tree with --tree. The dump has: {listed}." if tree is None else
                               f"No tree {tree!r} in the dump. It has: {listed}.")


def kith_tables():
    names = (next(iter(r.values())) for r in query("SHOW TABLES"))
    return [t for t in names if not t.startswith(("app_", HOLD))]


def copy(prefix, tree):
    """Copy the tree's rows into Kith's tables; returns {table: rows}, with None for a table the dump lacks."""
    mine = current_app.config["TREE"]
    counts = {}
    for table in kith_tables():
        src = f"{HOLD}{prefix}{table}"
        if not query("SHOW TABLES LIKE %s", (src,)):
            counts[table] = None
            continue
        have = {r["Field"] for r in query(f"SHOW COLUMNS FROM `{src}`")}
        blanks = columns(table)
        names, picks, args = [], [], []
        for r in query(f"SHOW COLUMNS FROM `{table}`"):
            c = r["Field"]
            if c == "gedcom" and c in have and table == "users":  # a user of no tree, or of all, stays so
                picks.append("IF(gedcom = %s, %s, gedcom)")
                args += [tree, mine]
            elif c == "gedcom" and c in have:  # the tree's name becomes this site's
                picks.append("%s")
                args.append(mine)
            elif c in have:
                picks.append(f"`{c}`")
            elif c in blanks:  # an older TNG without this column
                picks.append("%s")
                args.append(blanks[c])
            else:  # an id the database numbers itself
                continue
            names.append(f"`{c}`")
        where = ""
        if "gedcom" in have:  # and rows TNG keeps for all trees (no tree named), such as shared photos
            where = " WHERE gedcom = %s OR gedcom = '' OR gedcom IS NULL"
            args.append(tree)
        query(f"DELETE FROM `{table}`")  # the default event and media types, replaced by the TNG site's own
        query(f"INSERT INTO `{table}` ({', '.join(names)}) SELECT {', '.join(picks)} FROM `{src}`{where}", args)
        counts[table] = query(f"SELECT COUNT(*) n FROM `{table}`")[0]["n"]
    return counts


def left_behind(prefix, loaded):
    """TNG's tables Kith has no place for, with rows in them: {name: rows}."""
    kept = {f"{prefix}{t}" for t in kith_tables()}
    out = {}
    for t in loaded:
        if t not in kept and (n := query(f"SELECT COUNT(*) n FROM `{HOLD}{t}`")[0]["n"]):
            out[t] = n
    return out


def drop_held():
    for r in query("SHOW TABLES"):
        if (t := next(iter(r.values()))).startswith(HOLD):
            query(f"DROP TABLE `{t}`")


def folders():
    """The media folders the imported files are expected in, with how many files each."""
    return query("SELECT IF(usecollfolder, mediatypeID, 'media') folder, COUNT(*) n FROM media"
                 " WHERE path <> '' AND NOT COALESCE(abspath, 0) GROUP BY folder ORDER BY folder")


def empty_database():
    """Undo a failed import: the database was empty when it started."""
    query("SET FOREIGN_KEY_CHECKS = 0")
    for r in query("SHOW TABLES"):
        query(f"DROP TABLE `{next(iter(r.values()))}`")

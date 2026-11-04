"""Starting a new site: `flask --app app setup db` fills an empty database with the tables and the first admin;
`flask --app app setup sample` adds a made-up family (app/sample.ged) to try the site with;
`flask --app app setup import-tng dump.sql` starts it from a TNG site instead (app/tng_import.py).

Database upgrades: each change to the tables is a numbered file in app/migrations, run once, in order, by
`flask --app app setup upgrade` (update.sh runs it on each deploy) and recorded in app_migrations. MariaDB can't
undo a half-run file, so each must be safe to run again (IF NOT EXISTS, MODIFY to the same type)."""
from pathlib import Path

import click
from flask import Blueprint, current_app, g
from pymysql import MySQLError

from .admin import RIGHTS
from .auth import password_fields
from .db import insert, query
from .edit import now
from . import tng_import
from .gedcom_import import parse, write

bp = Blueprint("setup", __name__)
SCHEMA = Path(__file__).with_name("schema.sql")
SAMPLE = Path(__file__).with_name("sample.ged")
MIGRATIONS = Path(__file__).with_name("migrations")


def run_sql(path):
    for statement in path.read_text().split(";\n"):
        if statement.strip():
            query(statement)


def pending():
    query("CREATE TABLE IF NOT EXISTS app_migrations (name VARCHAR(100) PRIMARY KEY, at DATETIME NOT NULL)"
          " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    done = {r["name"] for r in query("SELECT name FROM app_migrations")}
    return [p for p in sorted(MIGRATIONS.glob("*.sql")) if p.name not in done]


def upgrade():
    """Run the migrations not yet run; returns their names."""
    names = []
    for path in pending():
        run_sql(path)
        query("INSERT INTO app_migrations (name, at) VALUES (%s, NOW())", (path.name,))
        names.append(path.name)
    return names


def load_schema():
    run_sql(SCHEMA)
    upgrade()


@bp.cli.command("db")
@click.option("--admin", "username", prompt="Admin username", help="The first admin's username.")
@click.password_option(help="The first admin's password.")
def db_command(username, password):
    """Create the tables in an empty database and the first admin."""
    if query("SHOW TABLES"):  # upgrades recorded on an empty database would never rename the tables loaded here
        raise click.ClickException("This database isn't empty. Setup only runs on an empty database.")
    if len(password) < 8:
        raise click.ClickException("The password needs at least 8 characters.")
    load_schema()
    current_app.config["STRONG_PASSWORDS"] = True  # a new site has no TNG that must still read the hashes
    insert("users", {"username": username, "realname": username, "description": username, "role": "admin",
                     "gedcom": current_app.config["TREE"], **{k: 1 for k in RIGHTS},
                     "dt_registered": now(), "dt_activated": now(), **password_fields(password)})
    click.echo(f"Database ready. Log in as {username}.")


@bp.cli.command("upgrade")
@click.option("--check", is_flag=True, help="Only list the upgrades waiting; exit status 1 if there are any.")
def upgrade_command(check):
    """Bring the database up to date with this version of the code."""
    if not query("SHOW TABLES"):  # a new install starting before `setup db`: nothing to upgrade, nothing to record
        return click.echo("Empty database: run `flask --app app setup db` first.")
    if check:
        waiting = [p.name for p in pending()]
        click.echo("\n".join(waiting) or "Up to date.")
        raise SystemExit(int(bool(waiting)))
    names = upgrade()
    click.echo("Ran " + ", ".join(names) + "." if names else "Up to date.")


@bp.cli.command("sample")
def sample_command():
    """Add the made-up sample family, to try the site with."""
    g.user = {"username": "setup"}
    made = write(parse(SAMPLE.read_text()))
    click.echo(f"Added {len(made['people'])} people and {len(made['families'])} families.")


@bp.cli.command("import-tng")
@click.argument("dump", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--tree", help="The TNG tree to import, when the dump holds more than one.")
def import_tng_command(dump, tree):
    """Start this site from a dump of a TNG database: the tree, its sources and media records, and the users."""
    tables = {next(iter(r.values())) for r in query("SHOW TABLES")}
    if tables and ("people" not in tables or query("SELECT 1 FROM people LIMIT 1")):
        raise click.ClickException("This database already has a tree, or isn't a Kith site."
                                   " The import only runs on a new site.")
    if tables:  # a new site with no one in it yet, as the installer leaves it: the TNG site replaces it
        tng_import.empty_database()
        click.echo("Replaced the new, empty site (its admin account too: log in with a TNG account).")
    try:
        loaded = tng_import.load(dump)
        prefix = tng_import.prefix_of(loaded)
        tree = tng_import.pick_tree(prefix, tree)
        load_schema()
        counts = tng_import.copy(prefix, tree)
        behind = tng_import.left_behind(prefix, loaded)
        tng_import.drop_held()
    except (click.ClickException, MySQLError) as e:
        tng_import.empty_database()
        message = e.message if isinstance(e, click.ClickException) else f"The database said: {e.args[-1]}"
        raise click.ClickException(f"{message}\nNothing was imported; the database is empty again.")
    click.echo(f"Imported tree {tree!r} from TNG:")
    for table, n in counts.items():
        click.echo(f"  {table}: {'not in the dump' if n is None else n}")
    if behind:
        click.echo("Left behind (Kith has no place for these): "
                   + ", ".join(f"{t} ({n} row{'s' * (n != 1)})" for t, n in behind.items()))
    if found := tng_import.folders():
        click.echo(f"Now copy these folders from your TNG site into {current_app.config['MEDIA_ROOT']}: "
                   + ", ".join(f"{r['folder']} ({r['n']} file{'s' * (r['n'] != 1)})" for r in found))
    click.echo("TNG users log in with their TNG usernames and passwords.")

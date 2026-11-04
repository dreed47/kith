import pymysql
import pytest

from app import create_app
from app.auth import check_password
from app.db import query

SETUP_DB = "kith_setup_test"


@pytest.fixture
def fresh(app):  # app: the shared test DB is up, so the server is running
    root = pymysql.connect(host="127.0.0.1", port=3307, user="root", password="dev", autocommit=True)
    with root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {SETUP_DB}")
        cur.execute(f"CREATE DATABASE {SETUP_DB}")
    new = create_app({"TESTING": True, "DB_NAME": SETUP_DB, "DB_USER": "root", "DB_PASSWORD": "dev"})
    yield new
    with root.cursor() as cur:
        cur.execute(f"DROP DATABASE {SETUP_DB}")
    root.close()


def test_new_site_from_empty_database(fresh):
    run = fresh.test_cli_runner().invoke
    assert "run `flask --app app setup db` first" in run(args=["setup", "upgrade"]).output  # records nothing
    assert "at least 8" in run(args=["setup", "db", "--admin", "boss", "--password", "short"]).output
    assert "Log in as boss" in run(args=["setup", "db", "--admin", "boss", "--password", "long enough"]).output
    with fresh.app_context():
        boss = query("SELECT * FROM users")[0]
        assert boss["role"] == "admin" and boss["allow_edit"] and boss["password_type"] == "pbkdf2"
        assert check_password("long enough", boss)
    assert "isn't empty" in run(args=["setup", "db", "--admin", "x", "--password", "long enough"]).output
    assert "Added 805 people" in run(args=["setup", "sample"]).output


def test_upgrade_runs_each_migration_once(fresh):
    run = fresh.test_cli_runner().invoke
    run(args=["setup", "db", "--admin", "boss", "--password", "long enough"])
    assert run(args=["setup", "upgrade", "--check"]).exit_code == 0
    assert "Up to date" in run(args=["setup", "upgrade"]).output
    with fresh.app_context():  # a site from before migrations: its app_ tables exist, but nothing is recorded
        query("DELETE FROM app_migrations")
        query("INSERT INTO app_audit (at, username, action, tbl, record) VALUES (NOW(), 'x', 'edit', 'tng_people', 'I1')")
    check = run(args=["setup", "upgrade", "--check"])
    assert check.exit_code == 1 and "0001_app_tables.sql" in check.output
    assert "Ran 0001_app_tables.sql, 0002_kith_table_names.sql" in run(args=["setup", "upgrade"]).output
    assert run(args=["setup", "upgrade", "--check"]).exit_code == 0
    with fresh.app_context():
        assert query("SELECT tbl FROM app_audit")[0]["tbl"] == "people"  # the edit history follows the rename
        assert not query("SHOW TABLES LIKE 'tng\\_%'")


def tng_dump(tmp_path, body="", prefix="tng_", charset="utf8mb4", encoding="utf-8"):
    """A small TNG dump the way mysqldump writes one: Kith's tables under TNG's prefix, then `body`."""
    from app.setup import SCHEMA
    tables = SCHEMA.read_text().replace("CREATE TABLE `", f"CREATE TABLE `{prefix}").replace(
        "INSERT INTO `", f"INSERT INTO `{prefix}")
    text = (f"/*M!999999\\- enable the sandbox mode */ \n-- MariaDB dump\n\n/*!40101 SET NAMES {charset} */;\n"
            "/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='' */;\nCREATE DATABASE tng;\nUSE tng;\n"
            f"/*!40101 SET character_set_client = {charset} */;\n{tables}\n{body}\n"
            "/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;\n")
    path = tmp_path / "tng.sql"
    path.write_bytes(text.encode(encoding))
    return path


TNG_ROWS = """ALTER TABLE tng_people ADD COLUMN tngonly INT NOT NULL DEFAULT 0;
ALTER TABLE tng_people DROP COLUMN lnprefix;
DROP TABLE tng_timelineevents;
ALTER TABLE tng_xnotes DROP COLUMN ID;
INSERT INTO tng_xnotes (noteID, gedcom, note) VALUES ('N1', 'fam', 'A note');
CREATE TABLE tng_albums (albumID INT);
INSERT INTO tng_albums VALUES (1), (2);
INSERT INTO tng_people (gedcom, personID, lastname, firstname, living) VALUES ('fam', 'I1', 'Brontë', 'Ann', 0),
('fam', 'I2', 'Reed', 'Bo', 0), ('other', 'I1', 'Else', 'Where', 0);
INSERT INTO tng_eventtypes (eventtypeID, tag, display, keep, type) VALUES (99, 'MINE', 'Mine', 1, 'I');
INSERT INTO tng_media (mediatypeID, mediakey, gedcom, path, usecollfolder, abspath) VALUES
('photos', 'a', 'fam', 'a.jpg', 1, 0), ('photos', 'b', 'fam', 'b.jpg', 1, NULL),
('photos', 'c', 'fam', 'http://x/c.jpg', 1, 1), ('photos', 'd', 'other', 'd.jpg', 1, 0),
('headstones', 'e', '', 'e.jpg', 1, 0);
INSERT INTO tng_users (username, password, password_type, gedcom, role, allow_living) VALUES
('old', MD5('old secret'), 'md5', '', 'admin', 1), ('famed', MD5('x'), 'md5', 'fam', 'guest', 1),
('elsewhere', MD5('x'), 'md5', 'other', 'guest', 1);"""


def test_import_tng(fresh, tmp_path):
    from test_auth import login
    run = fresh.test_cli_runner().invoke
    path = tng_dump(tmp_path, TNG_ROWS, charset="latin1", encoding="cp1252")
    run(args=["setup", "db", "--admin", "boss", "--password", "long enough"])  # as the installer leaves it
    picked = run(args=["setup", "import-tng", str(path)])
    assert "Pick a tree with --tree. The dump has: fam (2 people), other (1 people)." in picked.output
    assert "the database is empty again" in picked.output
    assert "No tree 'nope'" in run(args=["setup", "import-tng", str(path), "--tree", "nope"]).output
    out = run(args=["setup", "import-tng", str(path), "--tree", "fam"]).output
    assert "Replaced the new, empty site" not in out  # the failed tries above left it empty
    assert "people: 2" in out and "timelineevents: not in the dump" in out and "eventtypes: 35" in out
    assert "Left behind (Kith has no place for these): tng_albums (2 rows)" in out
    assert "headstones (1 file), photos (2 files)" in out and "users: 2" in out and "xnotes: 1" in out  # its IDs numbered anew
    with fresh.app_context():
        people = query("SELECT gedcom, personID, lastname, lnprefix FROM people ORDER BY personID")
        assert people == [{"gedcom": "tree1", "personID": "I1", "lastname": "Brontë", "lnprefix": ""},
                          {"gedcom": "tree1", "personID": "I2", "lastname": "Reed", "lnprefix": ""}]
        assert query("SELECT gedcom FROM media GROUP BY gedcom") == [{"gedcom": "tree1"}]
        assert {r["username"]: r["gedcom"] for r in query("SELECT username, gedcom FROM users")} == {
            "old": "", "famed": "tree1"}
        assert not query("SHOW TABLES LIKE 'tng%'") and len(query("SELECT * FROM app_migrations")) >= 4
    assert login(fresh.test_client(), "old", "old secret").status_code == 302  # the TNG password still works
    assert "already has a tree" in run(args=["setup", "import-tng", str(path)]).output


def test_import_tng_other_prefix_and_one_tree(fresh, tmp_path):
    path = tng_dump(tmp_path, "INSERT INTO my_people (gedcom, personID, lastname) VALUES ('t', 'I1', 'A');",
                    prefix="my_")
    run = fresh.test_cli_runner().invoke
    run(args=["setup", "db", "--admin", "boss", "--password", "long enough"])
    out = run(args=["setup", "import-tng", str(path)]).output
    assert "Replaced the new, empty site" in out and "Imported tree 't'" in out and "people: 1" in out and "Now copy" not in out and "Left behind" not in out


@pytest.mark.parametrize("body, prefix, message", [
    ("DROP TABLE tng_people;", "tng_", "No TNG people table in the dump"),
    ("CREATE TABLE b_people (personID INT);", "tng_", "more than one TNG site: b_people, tng_people"),
    ("", "tng_", "people table is empty"),
    ("DELIMITER ;;", "tng_", "triggers or procedures"),
    ("INSERT INTO tng_nothing VALUES (1);", "tng_", "The database said: Table 'kith_setup_test.tng_nothing'"),
])
def test_import_tng_refuses(fresh, tmp_path, body, prefix, message):
    out = fresh.test_cli_runner().invoke(args=["setup", "import-tng", str(tng_dump(tmp_path, body, prefix))]).output
    assert message in out and "the database is empty again" in out
    with fresh.app_context():
        assert not query("SHOW TABLES")


def test_import_tng_other_tables(fresh, tmp_path):
    with fresh.app_context():
        query("CREATE TABLE something (x INT)")
    out = fresh.test_cli_runner().invoke(args=["setup", "import-tng", str(tng_dump(tmp_path))]).output
    assert "already has a tree" in out


def test_import_tng_not_text(fresh, tmp_path):
    path = tmp_path / "tng.sql"
    path.write_bytes(b"/*!40101 SET NAMES utf8mb4 */;\n\xff\xfe;\n")
    assert "isn't UTF-8" in fresh.test_cli_runner().invoke(args=["setup", "import-tng", str(path)]).output

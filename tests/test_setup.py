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

"""Shared fixtures. Database tests run on kith_test: an empty database (app/schema.sql) holding the made-up sample
family (app/sample.ged), rebuilt once per test run and topped up with records for edge cases the sample doesn't have."""
import itertools

import pymysql
import pytest
from flask import g

from app import create_app
from app.db import insert, query
from app.gedcom_import import parse, write
from app.setup import SAMPLE, load_schema

TEST_DB = "kith_test"
ALL = {"allow_living": 1, "allow_private": 1, "allow_private_notes": 1}
USERS = {  # fixed test accounts, one per permission combination the code checks
    "fx_admin": {**ALL, "allow_edit": 1, "allow_add": 1, "allow_delete": 1},
    "fx_guest": {},
    "fx_nonotes": {"allow_living": 1, "allow_private": 1},
    "fx_adder": {**ALL, "allow_add": 1},
    "fx_editor": {**ALL, "allow_edit": 1},
    "fx_exporter": {"allow_ged": 1},  # may download GEDCOM, but sees no living/private details
    "fx_limited_editor": {"allow_edit": 1},  # edits, but sees no living/private people or private notes
    "fx_ged_admin": {**ALL, "allow_ged": 1, "allow_edit": 1},
    "fx_siteadmin": {**ALL, "allow_edit": 1, "allow_add": 1, "role": "admin"},  # the only one allowed into /admin
}
CSRF = "t"


@pytest.fixture(autouse=True)
def no_dev_db(monkeypatch):
    """Apps built without a DB_NAME would otherwise open the dev database (kith) and pick up whatever is in it,
    like site settings saved while trying the app. Tests that need a database use the app fixture's kith_test."""
    monkeypatch.setenv("KITH_DB_NAME", "no_such_db_in_tests")
    # The fixture accounts have TNG-style md5 passwords; strong hashing has its own test (test_account).
    monkeypatch.setenv("KITH_STRONG_PASSWORDS", "0")


@pytest.fixture(scope="session")
def media_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("media")
    from PIL import Image
    for rel in ("media/plain.jpg", "media/thumb_plain.jpg", "documents/letter.pdf", "photos/nothumb.jpg"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".jpg"):  # real images, so thumbnails can be made from them
            Image.new("RGB", (900, 600), "teal").save(root / rel, "JPEG")
        else:
            (root / rel).write_bytes(b"test file " + rel.encode())
    return root


@pytest.fixture(scope="session")
def app(media_root, tmp_path_factory):
    try:
        root = pymysql.connect(host="127.0.0.1", port=3307, user="root", password="dev", autocommit=True)
    except pymysql.err.OperationalError:
        pytest.skip("local DB not running")
    with root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        cur.execute(f"CREATE DATABASE {TEST_DB}")
    root.close()
    app = create_app({"TESTING": True, "DB_NAME": TEST_DB, "DB_USER": "root", "DB_PASSWORD": "dev",
                      "MEDIA_ROOT": str(media_root), "STRONG_PASSWORDS": False})
    app.instance_path = str(tmp_path_factory.mktemp("instance"))  # avatars, uploads: never the dev site's own
    with app.app_context():
        load_schema()
        g.user = {"username": "fixtures"}
        write(parse(SAMPLE.read_text()))
        add_fixtures()
    return app


def add_fixtures():
    """Made-up records, all IDs prefixed X so they can't collide with real ones."""
    t = "tree1"
    insert("people", {"gedcom": t, "personID": "XORPHAN", "firstname": "Orphan", "lastname": "Fixture",
                          "sex": "U", "famc": "XNOFAMILY"})  # famc points at a family that doesn't exist
    insert("people", {"gedcom": t, "personID": "XHUSB", "firstname": "Hal", "lastname": "Fixture", "sex": "M",
                          "birthplace": "Synthville, Testshire", "famc": "XFAM2"})
    # Four generations: XKID -> parents XHUSB + XWIFE -> XHUSB's parents XGPA + XGMA -> XGPA's father XGGPA
    insert("people", {"gedcom": t, "personID": "XKID", "firstname": "Kit", "lastname": "Fixture", "famc": "XFAM"})
    insert("children", {"gedcom": t, "familyID": "XFAM", "personID": "XKID", "ordernum": 1})
    for pid, first, sex, famc in (("XGPA", "Gus", "M", "XFAM3"), ("XGMA", "Gina", "F", ""), ("XGGPA", "Abe", "M", "")):
        insert("people", {"gedcom": t, "personID": pid, "firstname": first, "lastname": "Fixture", "sex": sex,
                              "famc": famc})
    insert("families", {"gedcom": t, "familyID": "XFAM2", "husband": "XGPA", "wife": "XGMA"})
    insert("families", {"gedcom": t, "familyID": "XFAM3", "husband": "XGGPA"})
    insert("children", {"gedcom": t, "familyID": "XFAM2", "personID": "XHUSB", "ordernum": 1})
    insert("children", {"gedcom": t, "familyID": "XFAM3", "personID": "XGPA", "ordernum": 1})
    insert("people", {"gedcom": t, "personID": "XWIFE", "firstname": "Wanda", "lastname": "Fixture", "sex": "F"})
    insert("families", {"gedcom": t, "familyID": "XFAM", "husband": "XHUSB", "wife": "XWIFE",
                            "marrdate": "1 JAN 1900", "marrplace": "Synthville, Testshire"})
    census = query("SELECT eventtypeID FROM eventtypes WHERE tag = 'CENS' AND type = 'F'")[0]["eventtypeID"]
    occu = query("SELECT eventtypeID FROM eventtypes WHERE type = 'I' AND ldsevent = 0 LIMIT 1")[0]["eventtypeID"]
    insert("events", {"gedcom": t, "persfamID": "XFAM", "eventtypeID": census, "eventplace": "Synthville, Testshire"})
    insert("events", {"gedcom": t, "persfamID": "XHUSB", "eventtypeID": occu, "eventplace": "Synthville, Testshire"})
    insert("places", {"gedcom": t, "place": "Synthville, Testshire"})
    insert("places", {"gedcom": t, "place": "Emptyplace, Testshire"})  # a place nobody used
    insert("cemeteries", {"cemname": "Placeless Fixture Cemetery"})
    _, cem = insert("cemeteries", {"cemname": "Fixture Hill Cemetery", "place": "Synthville, Testshire",
                                       "latitude": "41.16", "longitude": "-79.08"})

    def media(**v):  # mediakey is unique per tree; TNG fills it with the path
        _, mid = insert("media", {"gedcom": t, "alwayson": 1, "usecollfolder": 1,
                                      "mediakey": "X" + v["description"], **v})
        return mid
    ids = {
        "plain": media(mediatypeID="photos", usecollfolder=0, path="plain.jpg", thumbpath="thumb_plain.jpg",
                       description="Plain fixture"),
        "pdf": media(mediatypeID="documents", path="letter.pdf", description="PDF fixture"),
        "url": media(mediatypeID="photos", path="http://example.com/x.jpg", description="URL fixture"),
        "body": media(mediatypeID="histories", path="", bodytext="<p>Story fixture</p>", description="Body fixture"),
        "nothumb": media(mediatypeID="photos", path="nothumb.jpg", thumbpath="missing_thumb.jpg",
                         description="Missing thumb fixture"),
    }
    media(mediatypeID="headstones", path="plain.jpg", description="Headstone fixture", cemeteryID=cem)
    for n in range(50):  # enough photos for a second page
        media(mediatypeID="photos", usecollfolder=0, path="plain.jpg", description=f"Album fixture {n}")
    insert("medialinks", {"gedcom": t, "linktype": "F", "personID": "XFAM", "mediaID": ids["plain"]})
    insert("medialinks", {"gedcom": t, "linktype": "I", "personID": "XHUSB", "mediaID": ids["plain"]})
    insert("medialinks", {"gedcom": t, "linktype": "I", "personID": "XGGPA", "mediaID": ids["body"]})  # no file
    _, xid = insert("xnotes", {"gedcom": t, "note": "Secret fixture note"})
    insert("notelinks", {"gedcom": t, "persfamID": "XHUSB", "xnoteID": xid, "secret": 1})
    _, xid = insert("xnotes", {"gedcom": t, "note": "Open fixture note"})
    insert("notelinks", {"gedcom": t, "persfamID": "XHUSB", "xnoteID": xid, "secret": 0})
    for name, rights in USERS.items():
        insert("users", {"username": name, "password_type": "md5", "role": "custom", "gedcom": t, **rights})
    query("CREATE TABLE IF NOT EXISTS fixture_ids (name VARCHAR(20) PRIMARY KEY, id INT)")
    for name, mid in ids.items():
        query("INSERT INTO fixture_ids VALUES (%s, %s)", (name, mid))


@pytest.fixture(scope="session")
def media_ids(app):
    return {r["name"]: r["id"] for r in sql(app, "SELECT * FROM fixture_ids")}


def sql(app, q, args=None):
    with app.app_context():
        return query(q, args)


def client_for(app, username):
    uid = sql(app, "SELECT userID FROM users WHERE username = %s", (username,))[0]["userID"]
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"], s["csrf"] = uid, CSRF
    return c


@pytest.fixture
def admin(app):
    return client_for(app, "fx_admin")


@pytest.fixture
def guest(app):
    return client_for(app, "fx_guest")


@pytest.fixture
def plain_app():
    """An app with no database, for tests that don't need one."""
    return create_app({"TESTING": True})


_n = itertools.count(1)


def make(app, *people, family=None, kids=()):
    """Fresh made-up people (and optionally a family with children) for one test. Returns their IDs."""
    tag = f"T{next(_n)}"
    ids = {}
    with app.app_context():
        for name, sex, living in people:
            pid = f"X{tag}{name.upper()}"
            insert("people", {"gedcom": "tree1", "personID": pid, "firstname": name, "lastname": tag,
                                  "sex": sex, "living": living})
            ids[name] = pid
        if family:
            husband, wife = family
            fid = f"X{tag}F"
            insert("families", {"gedcom": "tree1", "familyID": fid, "husband": ids.get(husband, ""),
                                    "wife": ids.get(wife, ""), "husborder": 1, "wifeorder": 1})
            for i, kid in enumerate(kids, 1):
                insert("children", {"gedcom": "tree1", "familyID": fid, "personID": ids[kid], "ordernum": i})
                sql(app, "UPDATE people SET famc = %s WHERE personID = %s", (fid, ids[kid]))
            ids["family"] = fid
    return ids

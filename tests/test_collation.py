"""Our columns that hold TNG IDs and places must share TNG's collation. Otherwise MariaDB can't use TNG's indexes
when joining on them (MariaDB 11.8 defaults new tables to utf8mb4_uca1400_ai_ci; each join then scanned all people)."""
from conftest import sql

JOINED = {"app_audit": ["record"], "app_comments": ["gedcom", "target"], "app_face_tags": ["gedcom", "personID"],
          "app_geocode": ["gedcom", "place"], "app_link_requests": ["personID"], "app_watches": ["gedcom", "personID"]}


def test_joined_columns_match_tng(app):
    tng = sql(app, "SELECT COLLATION_NAME c FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
                   " AND TABLE_NAME = 'people' AND COLUMN_NAME = 'personID'")[0]["c"]
    for table, cols in JOINED.items():
        for col in cols:
            got = sql(app, "SELECT COLLATION_NAME c FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
                           " AND TABLE_NAME = %s AND COLUMN_NAME = %s", (table, col))[0]["c"]
            assert got == tng, (table, col, got)

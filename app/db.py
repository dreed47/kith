import pymysql
from flask import current_app, g


def get_db():
    if "db" not in g:
        c = current_app.config
        g.db = pymysql.connect(
            host=c["DB_HOST"], port=c["DB_PORT"], user=c["DB_USER"],
            password=c["DB_PASSWORD"], database=c["DB_NAME"],
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        )
    return g.db


def query(sql, args=None):
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def execute(sql, args=None):
    """Run a write; returns (rows affected, last insert id)."""
    with get_db().cursor() as cur:
        n = cur.execute(sql, args)
        return n, cur.lastrowid


_columns = {}


def columns(table):
    """Column name -> blank value for its type. TNG's columns are NOT NULL with no defaults,
    so every INSERT must supply all of them."""
    if table not in _columns:
        rows = query("SELECT COLUMN_NAME c, DATA_TYPE t, EXTRA x FROM information_schema.COLUMNS"
                     " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
        _columns[table] = {r["c"]: (0 if "int" in r["t"] or r["t"] in ("float", "double", "decimal")
                                    else "0000-00-00" if r["t"] == "date"
                                    else "0000-00-00 00:00:00" if r["t"] == "datetime" else "")
                           for r in rows if "auto_increment" not in r["x"]}
    return _columns[table]


def insert(table, values, ignore=False):
    row = {**columns(table), **values}
    names = ", ".join(f"`{k}`" for k in row)
    return execute(f"INSERT {'IGNORE ' if ignore else ''}INTO {table} ({names})"
                   f" VALUES ({', '.join(['%s'] * len(row))})", list(row.values()))


def update(table, values, where):
    sets = ", ".join(f"`{k}` = %s" for k in values)
    conds = " AND ".join(f"`{k}` = %s" for k in where)
    return execute(f"UPDATE {table} SET {sets} WHERE {conds}", [*values.values(), *where.values()])[0]


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)

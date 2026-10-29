from conftest import client_for, make, sql


def text(r):
    return r.get_data(as_text=True)


def test_statistics(app, admin):
    ids = make(app, ("Statold", "F", 0), ("Statliving", "M", 1), ("Typo", "M", 0))
    sql(app, "UPDATE people SET lastname = 'Zzstat', birthplace = 'Statville', birthdatetr = '1001-01-01',"
             " deathdatetr = '1110-01-01' WHERE personID = %s", (ids["Statold"],))
    sql(app, "UPDATE people SET lastname = 'Zzstat', birthdatetr = '1002-01-01', deathdatetr = '1111-01-01'"
             " WHERE personID = %s", (ids["Statliving"],))
    sql(app, "UPDATE people SET birthdatetr = '1003-01-01', deathdatetr = '1300-01-01' WHERE personID = %s",
        (ids["Typo"],))  # 297 years: a typo, left out
    html = text(admin.get("/statistics"))
    assert "People</span>" in html and "Average age at death: everyone" in html and "Children per family" in html
    longest = html[html.index("Longest lives"):html.index("Most common")]
    assert ids["Statold"] in longest and ids["Typo"] not in longest
    assert html.index(ids["Statold"]) < html.index("Most common")
    earliest = html[html.index("Earliest recorded births"):]
    assert ids["Statold"] in earliest
    guest = text(client_for(app, "fx_guest").get("/statistics"))
    assert ids["Statliving"] not in guest  # hidden people aren't named
    assert "/statistics" in text(admin.get("/"))


def test_statistics_with_little_data(app, monkeypatch):
    from app import statistics
    real = statistics.query
    monkeypatch.setattr(statistics, "query", lambda sql, args=None: [] if "GROUP BY familyID" in sql
                        and "LIMIT 1" in sql else real(sql, args))
    assert "the most is" not in text(client_for(app, "fx_admin").get("/statistics"))

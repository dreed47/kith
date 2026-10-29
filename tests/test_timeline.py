"""Timeline of lifetimes, and the historical events admins enter for it."""
from conftest import CSRF, client_for, make, sql


def text(r):
    return r.get_data(as_text=True)


def test_timeline_around_a_person(app, admin):
    ids = make(app, ("Tlkid", "F", 0), ("Tldad", "M", 0), ("Tlmom", "F", 0), family=("Tldad", "Tlmom"), kids=("Tlkid",))
    other = make(app, ("Tlspouse", "M", 0), ("Tlgrandkid", "M", 0), ("Tllive", "F", 1), family=("Tlspouse", None),
                 kids=("Tlgrandkid",))
    sql(app, "UPDATE families SET wife = %s, marrdatetr = '1905-06-01' WHERE familyID = %s",
        (ids["Tlkid"], other["family"]))
    for pid, born, died in ((ids["Tldad"], 1850, 1910), (ids["Tlkid"], 1880, None), (other["Tlspouse"], 1878, 1940)):
        sql(app, "UPDATE people SET birthdatetr = %s, deathdatetr = %s WHERE personID = %s",
            (f"{born}-01-01", f"{died}-01-01" if died else "0000-00-00", pid))
    sql(app, "UPDATE people SET birthdatetr = '1990-01-01' WHERE personID = %s", (other["Tllive"],))
    with app.app_context():
        from app.db import insert
        insert("timelineevents", {"evyear": "1861", "endyear": "1865", "evtitle": "Civil War"})
        insert("timelineevents", {"evyear": "1066", "evtitle": "Too early to show"})
    html = text(admin.get(f"/timeline?around={ids['Tlkid']}&p={other['Tllive']}&p="))
    for pid in (ids["Tldad"], ids["Tlkid"], other["Tlspouse"], other["Tllive"]):  # those with a birth year
        assert f"/person/{pid}" in html
    assert "1850 – 1910" in html and 'class="tl-bar open"' in html and 'class="tl-mark"' in html
    assert "1861 Civil War" in html and "Too early" not in html
    shown_not = html[html.index("Not shown"):]
    assert "Tlmom" in shown_not and "Tlgrandkid" in shown_not  # no birth year: listed, not drawn
    guest = text(client_for(app, "fx_guest").get(f"/timeline?p={other['Tllive']}"))
    assert "Not shown" in guest and "Living" in guest  # living people stay hidden from guests
    assert "Add people to compare" in text(admin.get("/timeline"))
    assert "/timeline?around=" in text(admin.get(f"/person/{ids['Tlkid']}"))


def test_timeline_unknown_people_and_lonely(app, admin):
    pid = make(app, ("Tlsolo", "M", 0))["Tlsolo"]
    sql(app, "UPDATE people SET birthdatetr = '1700-00-00' WHERE personID = %s", (pid,))
    html = text(admin.get(f"/timeline?p={pid}&p=NOPE&around=NOPE"))
    assert "1700" in html and 'class="tl-bar open"' in html


def test_history_events(app):
    c = client_for(app, "fx_siteadmin")
    for form, message in (({"evyear": "x", "evtitle": "A"}, "Enter the year"),
                          ({"evyear": "1900", "endyear": "1800", "evtitle": "A"}, "end year should be"),
                          ({"evyear": "1900", "evtitle": ""}, "Enter what happened"),
                          ({"evyear": "1900", "evtitle": "x" * 129}, "longer than 128")):
        assert message in text(c.post("/admin/history", data={"csrf": CSRF, **form}, follow_redirects=True)), message
    html = text(c.post("/admin/history", data={"csrf": CSRF, "evyear": "1918", "endyear": "1919",
                                               "evtitle": "Influenza", "evdetail": "Spanish flu"},
                       follow_redirects=True))
    assert "1918–1919" in html and "Spanish flu" in html
    tid = sql(app, "SELECT tleventID FROM timelineevents WHERE evtitle = 'Influenza'")[0]["tleventID"]
    c.post(f"/admin/history/{tid}/delete", data={"csrf": CSRF})
    assert not sql(app, "SELECT * FROM timelineevents WHERE tleventID = %s", (tid,))
    assert c.post(f"/admin/history/{tid}/delete", data={"csrf": CSRF}).status_code == 404
    assert client_for(app, "fx_admin").get("/admin/history").status_code == 403

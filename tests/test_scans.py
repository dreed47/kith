"""Scans as evidence: media attached to a source citation, or linked by TNG to one event, shown on that fact."""
import html as htmllib
import io

from conftest import client_for, make, sql
from test_gedcom import export, record

from app.db import insert


def text(r):
    return htmllib.unescape(r.get_data(as_text=True))


def media(app, description, path="scan.jpg", **v):
    with app.app_context():
        return insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "usecollfolder": 1, "path": path,
                                    "mediakey": "XSCAN " + description, "description": description, **v})[1]


def cite(app, owner, about="", **v):
    with app.app_context():
        return insert("citations", {"gedcom": "tree1", "persfamID": owner, "sourceID": "XS90", "eventID": about,
                                        **v})[1]


def event(app, owner, tag, kind):
    et = sql(app, "SELECT eventtypeID FROM eventtypes WHERE tag = %s AND type = %s", (tag, kind))[0]["eventtypeID"]
    with app.app_context():
        return insert("events", {"gedcom": "tree1", "persfamID": owner, "eventtypeID": et, "eventdate": "1900"})[1]


def test_upload_a_scan_from_a_citation(app, admin):
    ids = make(app, ("Scanned", "M", 0))
    sql(app, "INSERT IGNORE INTO sources (gedcom, sourceID, title) VALUES ('tree1', 'XS90', 'Birth register')")
    sql(app, "UPDATE people SET birthdate = '1 JAN 1880' WHERE personID = %s", (ids["Scanned"],))
    cid = cite(app, ids["Scanned"], "BIRT", page="entry 12")
    form = text(admin.get(f"/edit/citation/{cid}"))
    assert 'id="scans"' in form and f"/edit/media/new?for={ids['Scanned']}&citation={cid}" in form
    page = text(admin.get(f"/edit/media/new?for={ids['Scanned']}&citation={cid}"))
    assert "as a scan of a source" in page and 'value="documents" selected' in page
    r = admin.post("/edit/media/new", content_type="multipart/form-data", data={
        "csrf": "t", "for": ids["Scanned"], "citation": cid, "collection": "documents", "description": "Birth entry",
        "file": (io.BytesIO(b"%PDF-1.4 scan"), "birth.pdf")})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/edit/citation/{cid}#scans")
    mid = sql(app, "SELECT mediaID FROM media WHERE description = 'Birth entry'")[0]["mediaID"]
    assert sql(app, "SELECT * FROM app_citation_media WHERE citationID = %s AND mediaID = %s", (cid, mid))
    assert sql(app, "SELECT * FROM medialinks WHERE personID = %s AND mediaID = %s", (ids["Scanned"], mid))
    assert "Birth entry" in text(admin.get(f"/edit/citation/{cid}"))
    person = text(admin.get(f"/person/{ids['Scanned']}"))
    birth = person[person.index("<strong>Birth</strong>"):person.index("</li>", person.index("<strong>Birth</strong>"))]
    assert f"/media/{mid}" in birth  # on the fact itself
    sources = person[person.index("<h2>Sources</h2>"):]
    assert f"/media/{mid}" in sources and f"/edit/citation/{cid}#scans" in sources
    shown = text(admin.get(f"/media/{mid}"))
    assert "Evidence for" in shown and "Birth register" in shown and "entry 12" in shown and ", birth" in shown
    # a citation that isn't this person's can't be the target
    other = make(app, ("Otherone", "F", 0))
    assert admin.get(f"/edit/media/new?for={other['Otherone']}&citation={cid}").status_code == 400


def test_attach_and_detach_existing(app, admin):
    ids = make(app, ("Attacher", "F", 0), family=("Attacher", None))
    sql(app, "UPDATE families SET marrdate = '1901' WHERE familyID = %s", (ids["family"],))
    cid = cite(app, ids["family"], "MARR")
    mid = media(app, "Marriage license scan")
    url = f"/edit/citation/{cid}/scans"
    r = admin.post(url, data={"csrf": "t", "action": "add", "mediaID": mid})
    assert r.headers["Location"].endswith(f"/edit/citation/{cid}#scans")
    links = sql(app, "SELECT * FROM medialinks WHERE mediaID = %s", (mid,))
    assert [(x["personID"], x["linktype"]) for x in links] == [(ids["family"], "F")]  # linked to the family too
    admin.post(url, data={"csrf": "t", "action": "add", "mediaID": mid})
    assert "already attached" in text(admin.get(f"/edit/citation/{cid}"))
    admin.post(url, data={"csrf": "t", "action": "add", "mediaID": 99999999})
    assert "No photo or document number 99999999" in text(admin.get(f"/edit/citation/{cid}"))
    # attaching one already linked to the family adds no second link
    cid2 = cite(app, ids["family"])
    admin.post(f"/edit/citation/{cid2}/scans", data={"csrf": "t", "action": "add", "mediaID": mid})
    assert len(sql(app, "SELECT * FROM medialinks WHERE mediaID = %s", (mid,))) == 1
    fam = text(admin.get(f"/family/{ids['family']}"))
    marriage = fam[fam.index("<th>Marriage</th>"):fam.index("</tr>", fam.index("<th>Marriage</th>"))]
    assert f"/media/{mid}" in marriage
    # on a family citation, the media page names the family
    assert f"/family/{ids['family']}" in text(admin.get(f"/media/{mid}"))
    admin.post(url, data={"csrf": "t", "action": "remove", "mediaID": mid})
    assert not sql(app, "SELECT * FROM app_citation_media WHERE citationID = %s", (cid,))
    assert sql(app, "SELECT * FROM media WHERE mediaID = %s", (mid,))  # the media itself stays
    assert admin.post(url, data={"csrf": "t", "action": "remove", "mediaID": mid}).status_code == 400  # not attached
    assert admin.post(url, data={"csrf": "t", "action": "other", "mediaID": mid}).status_code == 400
    assert client_for(app, "fx_guest").post(url, data={"csrf": "t", "action": "add", "mediaID": mid}).status_code == 403


def test_event_links_and_household_census(app, admin):
    ids = make(app, ("Dad", "M", 0), ("Kid", "M", 0), family=("Dad", None), kids=("Kid",))
    sql(app, "UPDATE people SET burialplace = 'Somewhere' WHERE personID = %s", (ids["Dad"],))
    sql(app, "UPDATE families SET marrdate = '1890' WHERE familyID = %s", (ids["family"],))
    stone = media(app, "Dad headstone", mediatypeID="headstones")
    lic = media(app, "Dad license")
    census = media(app, "1900 census page")
    with app.app_context():
        insert("medialinks", {"gedcom": "tree1", "linktype": "I", "personID": ids["Dad"], "eventID": "BURI",
                                  "mediaID": stone})
        insert("medialinks", {"gedcom": "tree1", "linktype": "F", "personID": ids["family"], "eventID": "MARR",
                                  "mediaID": lic})
    eid = event(app, ids["family"], "CENS", "F")
    cid = cite(app, ids["family"], str(eid))
    admin.post(f"/edit/citation/{cid}/scans", data={"csrf": "t", "action": "add", "mediaID": census})
    # the same scan linked by TNG to the event as well is shown once
    with app.app_context():
        insert("medialinks", {"gedcom": "tree1", "linktype": "F", "personID": ids["family"], "eventID": str(eid),
                                  "mediaID": census, "ordernum": 5})
    dad = text(admin.get(f"/person/{ids['Dad']}"))
    burial = dad[dad.index("<strong>Burial</strong>"):dad.index("</li>", dad.index("<strong>Burial</strong>"))]
    assert f"/media/{stone}" in burial
    marriage = dad[dad.index("<strong>Marriage</strong>"):dad.index("</li>", dad.index("<strong>Marriage</strong>"))]
    assert f"/media/{lic}" in marriage
    kid = text(admin.get(f"/person/{ids['Kid']}"))
    cens = kid[kid.index("with <a"):kid.index("</li>", kid.index("with <a"))]
    assert cens.count(f"/media/{census}") == 1  # the 1900 census, straight from the census event
    fam = text(admin.get(f"/family/{ids['family']}"))
    row = fam[fam.index("<th>Census</th>"):fam.index("</tr>", fam.index("<th>Census</th>"))]
    assert row.count(f'/media/{census}"') == 1


def test_evidence_hidden_with_its_person(app):
    ids = make(app, ("Livingone", "F", 1))
    cid = cite(app, ids["Livingone"], "BIRT", page="secret page")
    mid = media(app, "Always shown scan", alwayson=1)
    with app.app_context():
        insert("app_citation_media", {"citationID": cid, "mediaID": mid, "ordernum": 1})
    page = text(client_for(app, "fx_guest").get(f"/media/{mid}"))
    assert "Always shown scan" in page and "Evidence for" not in page and "secret page" not in page


def test_export_media(app):
    ids = make(app, ("Exported", "M", 0))
    sql(app, "INSERT IGNORE INTO sources (gedcom, sourceID, title) VALUES ('tree1', 'XS90', 'Birth register')")
    sql(app, "UPDATE people SET deathdate = '1950' WHERE personID = %s", (ids["Exported"],))
    cid = cite(app, ids["Exported"], "DEAT", page="no. 7")
    bare = cite(app, ids["Exported"], "BIRT", sourceID="", description="Family Bible")
    empty = cite(app, ids["Exported"], "BIRT", sourceID="")  # nothing to write, so its scan isn't either
    cert = media(app, "Death certificate", path="cert.jpg")
    bible = media(app, "Bible page", path="bible", mediatypeID="photos", usecollfolder=0)
    stone = media(app, "Stone", path="stone.jpg", mediatypeID="headstones")
    web = media(app, "Web copy", path="https://example.com/c")
    lost = media(app, "Unwritten", path="lost.jpg")
    fileless = media(app, "No file", path="")  # no file to name, so left out
    with app.app_context():
        for c, m in ((cid, cert), (bare, bible), (empty, lost), (cid, fileless)):
            insert("app_citation_media", {"citationID": c, "mediaID": m, "ordernum": 1})
        insert("medialinks", {"gedcom": "tree1", "linktype": "I", "personID": ids["Exported"], "eventID": "DEAT",
                                  "mediaID": web})
        insert("medialinks", {"gedcom": "tree1", "linktype": "I", "personID": ids["Exported"], "mediaID": stone})
    body = export(app, "fx_ged_admin")
    indi = record(body, ids["Exported"])
    assert f"1 DEAT\n2 DATE 1950\n2 SOUR @XS90@\n3 PAGE no. 7\n3 OBJE @M{cert}@\n2 OBJE @M{web}@" in indi
    assert f"2 SOUR Family Bible\n3 OBJE @M{bible}@" in indi and f"1 OBJE @M{stone}@" in indi
    assert f"@M{lost}@" not in body and f"@M{fileless}@" not in body
    assert record(body, f"M{cert}") == f"0 @M{cert}@ OBJE\n1 FILE documents/cert.jpg\n2 FORM jpg\n2 TITL Death certificate\n"
    assert record(body, f"M{stone}") == (f"0 @M{stone}@ OBJE\n1 FILE headstones/stone.jpg\n2 FORM jpg\n3 TYPE tombstone\n"
                                         "2 TITL Stone\n")
    assert record(body, f"M{bible}") == f"0 @M{bible}@ OBJE\n1 FILE media/bible\n2 TITL Bible page\n"
    assert "1 FILE https://example.com/c\n" in record(body, f"M{web}")

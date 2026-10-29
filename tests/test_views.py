from conftest import sql


def test_search_one_word_matches_first_or_last_name(app, admin):
    html = admin.get("/search?q=Wanda").get_data(as_text=True)
    assert "/person/XWIFE" in html
    html = admin.get("/search?q=Fixture").get_data(as_text=True)
    assert "/person/XWIFE" in html and "/person/XHUSB" in html


def test_search_first_and_last(admin):
    html = admin.get("/search?q=Hal+Fixture").get_data(as_text=True)
    assert "/person/XHUSB" in html and "/person/XWIFE" not in html


def test_search_empty_and_no_results(admin):
    assert "No one found" not in admin.get("/search").get_data(as_text=True)
    assert "No one found" in admin.get("/search?q=Zzqxjv").get_data(as_text=True)


def test_surnames(admin):
    assert "/surnames/Fixture" in admin.get("/surnames").get_data(as_text=True)
    html = admin.get("/surnames/Fixture").get_data(as_text=True)
    assert "<h1>Fixture</h1>" in html and "/person/XORPHAN" in html


def test_person_page(admin):
    html = admin.get("/person/XHUSB").get_data(as_text=True)
    assert "Hal Fixture" in html and "Synthville, Testshire" in html  # birth place, linked
    assert "/family/XFAM" in html and "Wanda Fixture" in html          # family with spouse
    assert "Plain fixture" in html                                       # linked media
    assert "Open fixture note" in html


def test_person_without_families(admin):
    html = admin.get("/person/XORPHAN").get_data(as_text=True)
    assert "Orphan Fixture" in html and "<h2>Parents</h2>" not in html and "family-glance" not in html


def test_person_with_parents(admin):
    html = admin.get("/person/XKID").get_data(as_text=True)
    assert "Parents" in html and "/person/XHUSB" in html and "/person/XWIFE" in html


def test_person_events_and_sources(app, admin):
    e = sql(app, "SELECT e.persfamID FROM events e JOIN citations c ON c.persfamID = e.persfamID"
                 " JOIN people p ON p.personID = e.persfamID WHERE e.info <> '' LIMIT 1")[0]
    html = admin.get(f"/person/{e['persfamID']}").get_data(as_text=True)
    assert "Sources" in html and 'class="pre"' in html


def test_family_page(admin):
    html = admin.get("/family/XFAM").get_data(as_text=True)
    assert "Hal Fixture" in html and "Wanda Fixture" in html and "1 Jan 1900" in html and "Census" in html


def test_missing_records_404(admin):
    for url in ("/person/NOPE", "/family/NOPE", "/pedigree/NOPE"):
        assert admin.get(url).status_code == 404, url


def test_pedigree_four_generations(admin):
    html = admin.get("/pedigree/XKID").get_data(as_text=True)
    assert html.count('class="ped-node"') == 6  # kid, 2 parents, 2 grandparents, 1 great-grandparent
    assert html.count('class="ped-node empty"') == 9  # unknown slots still drawn so pairs stay aligned
    assert 'class="ped-parents none"' in html  # Wanda's parents: no bracket
    for pid in ("XHUSB", "XWIFE", "XGPA", "XGMA", "XGGPA"):
        assert f"/pedigree/{pid}?gens=4" in html


def test_pedigree_stops_at_missing_parent_family(admin):
    # XORPHAN's parent family ID points at nothing; the chart still renders with only them.
    html = admin.get("/pedigree/XORPHAN").get_data(as_text=True)
    assert html.count('class="ped-node"') == 1


def test_pedigree_without_parents(admin):
    html = admin.get("/pedigree/XWIFE").get_data(as_text=True)
    assert html.count('class="ped-node"') == 1 and "ped-parents" not in html

from conftest import client_for, make, sql


def text(r):
    return r.get_data(as_text=True)


def test_first_name_list_and_people(app, admin):
    ids = make(app, ("Zebulon", "M", 0), ("Zebulon2", "M", 0), ("Hiddenzeb", "M", 1))
    sql(app, "UPDATE people SET firstname = 'Zebulon Pike' WHERE personID = %s", (ids["Zebulon2"],))
    sql(app, "UPDATE people SET firstname = 'Zebulonx' WHERE personID = %s", (ids["Hiddenzeb"],))
    html = text(admin.get("/firstnames"))
    assert 'href="/firstnames/Zebulon">Zebulon</a> <span class="muted">2</span>' in html  # "Zebulon Pike" counts
    assert 'id="letter-Z"' in html and "Most common" in html and "/surnames" in html
    people = text(admin.get("/firstnames/Zebulon"))
    assert "Zebulon (first name)" in people and ids["Zebulon2"] in people and ids["Hiddenzeb"] not in people
    assert "Zebulonx" not in text(client_for(app, "fx_guest").get("/firstnames"))  # living, hidden from guests
    assert "/firstnames" in text(admin.get("/surnames"))


def test_odd_first_names_grouped(app, admin):
    pid = make(app, ("Oddname", "F", 0))["Oddname"]
    sql(app, "UPDATE people SET firstname = '??' WHERE personID = %s", (pid,))
    assert 'id="letter-#"' in text(admin.get("/firstnames"))


def test_no_first_names(app, monkeypatch):
    from app import views
    monkeypatch.setattr(views, "query", lambda *a: [])
    assert "No first names to show" in text(client_for(app, "fx_guest").get("/firstnames"))

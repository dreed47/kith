"""What's new: people and photos added, faces tagged and comments, filtered by what each viewer may see."""
import datetime
import html as htmllib
import re

from conftest import CSRF, client_for, make, sql
from test_comments import say
from test_edit_media import newest_media, upload
from test_faces import tag

from app.news import grouped


def text(r):
    """The page with markup between words dropped and spaces collapsed, plus its links' hrefs kept."""
    html = htmllib.unescape(r.get_data(as_text=True))
    return " ".join(re.sub(r"<(?!a |img )[^>]*>", " ", html).split())


def at(day, hour):
    return datetime.datetime(2026, 10, day, hour)


def test_grouping():
    items = [{"type": "person", "who": "ann", "at": at(28, 12), "n": 1}, {"type": "person", "who": "ann", "at": at(28, 9), "n": 2},
             {"type": "person", "who": "ann", "at": at(27, 23), "n": 3},  # another day
             {"type": "person", "who": "beth", "at": at(27, 22), "n": 4},  # someone else
             {"type": "photo", "who": "beth", "at": at(27, 21), "n": 5},  # another kind
             {"type": "comment", "who": "beth", "at": at(27, 20), "n": 6},
             {"type": "comment", "who": "beth", "at": at(27, 19), "n": 7}]  # comments never group
    assert [[x["n"] for x in e["group"]] for e in grouped(items)] == [[1, 2], [3], [4], [5], [6], [7]]


def new_person(client, first):
    r = client.post("/edit/person/new", data={"csrf": CSRF, "firstname": first, "lastname": "Newsworthy", "sex": "F"})
    return r.location.rsplit("/", 1)[-1]


def test_whats_new(app, admin, monkeypatch):
    from app import news
    alive = make(app, ("Alive", "M", 1))["Alive"]
    first, second = new_person(admin, "Nora"), new_person(admin, "Nell")
    upload(admin, first)
    photo = newest_media(app)["mediaID"]
    upload(admin, alive)  # linked to a living person: hidden from accounts that can't see the living
    hidden_photo = newest_media(app)["mediaID"]
    tag(admin, photo, second)
    say(admin, "person", first, "About Nora " + "x" * 400)
    say(admin, "person", alive, "About Alive")
    # Everything above happened within a second; spread it out, oldest first, as it would be in real use, and on a
    # day of its own, so other tests' additions as fx_admin today don't join its groups.
    for minutes, (table, col, cond, args) in enumerate((
            ("app_audit", "at", "action = 'add person' AND record IN (%s, %s)", (first, second)),
            ("app_audit", "at", "action = 'add media' AND record IN (%s, %s)", (str(photo), str(hidden_photo))),
            ("app_face_tags", "created_at", "mediaID = %s", (photo,)),
            ("app_comments", "created_at", "target = %s", (first,)),
            ("app_comments", "created_at", "target = %s", (alive,)))):
        sql(app, f"UPDATE {table} SET {col} = NOW() + INTERVAL 1 DAY - INTERVAL %s MINUTE WHERE {cond}",
            (10 - minutes, *args))
    page = text(admin.get("/whats-new"))
    for part in ("fx_admin commented on", "About Alive", "fx_admin tagged", f"/media/{photo}/face/",
                 "fx_admin added 2 photos", f"/media/{hidden_photo}\"", "added 2 people: ", f"/person/{first}"):
        assert part in page, part
    order = [page.index(x) for x in ("About Alive", "About Nora", "fx_admin tagged", "added 2 photos", "added 2 people")]
    assert order == sorted(order)  # newest first
    assert "x" * 400 not in page  # long comments are cut short
    viewer = client_for(app, "fx_exporter")
    page = text(viewer.get("/whats-new"))
    assert "About Nora" in page and "fx_admin tagged" in page and f"/media/{photo}\"" in page and "added a photo" in page
    assert "About Alive" not in page and f"/media/{hidden_photo}\"" not in page
    home = text(viewer.get("/"))
    assert "What's new" in home and "About Nora" in home and "/whats-new" in home and "About Alive" not in home
    monkeypatch.setattr(news, "PER_PAGE", 1)
    one, two = text(admin.get("/whats-new")), text(admin.get("/whats-new?page=2"))
    assert "About Alive" in one and "Older" in one and "Newer" not in one
    assert "About Nora" in two and "Newer" in two and "About Alive" not in two


def test_tags_of_one_person_group_with_names_once(app, admin):
    who = make(app, ("Tagged", "F", 0))["Tagged"]
    for _ in range(2):
        upload(admin, who)
        tag(admin, newest_media(app)["mediaID"], who)
    many = [new_person(admin, f"Many{n}") for n in range(6)]
    later = "NOW() + INTERVAL 2 DAY"  # a day of its own (see test_whats_new)
    sql(app, f"UPDATE app_face_tags SET created_at = {later} WHERE personID = %s", (who,))
    sql(app, f"UPDATE app_audit SET at = {later} - INTERVAL 1 MINUTE WHERE action = 'add person'"
             f" AND record IN ({', '.join(['%s'] * 6)})", many)
    page = text(admin.get("/whats-new"))
    assert "tagged 2 faces: " in page and page.count(f'href="/person/{who}"') == 1
    assert "added 6 people: " in page and "and 1 more" in page

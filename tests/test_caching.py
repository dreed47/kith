"""Browser caching: CSS/JS/icons carry their file's version and are kept a year; photos stay out of shared caches."""
import re

from flask import url_for


def test_versioned_files_cached_a_year(app, admin):
    html = admin.get("/").get_data(as_text=True)
    css = re.search(r'href="(/static/app\.css\?v=\d+)"', html).group(1)
    theme = re.search(r'href="(/theme/theme\.css\?v=\d+)"', html).group(1)
    for u in (css, theme, "/themes/heritage.css?v=1"):
        cc = admin.get(u).headers["Cache-Control"]
        assert "max-age=31536000" in cc and "immutable" in cc and "no-cache" not in cc, (u, cc)
    assert "no-cache" in admin.get("/static/app.css").headers["Cache-Control"]  # no version: check every time
    with app.test_request_context():
        assert url_for("static", filename="app.css", v="x").endswith("v=x")  # a given version is kept
        assert "v=" not in url_for("static", filename="no-such-file.js")
        assert "v=" in url_for("user_theme", name="heritage")


def test_photos_private(admin, media_ids):
    cc = admin.get(f"/media/{media_ids['plain']}/thumb").headers["Cache-Control"]
    assert "private" in cc and "public" not in cc

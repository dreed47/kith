def test_browse_and_paging(admin):
    html = admin.get("/media/").get_data(as_text=True)
    assert "Page 1 of" in html and "Next" in html
    html = admin.get("/media/?page=2").get_data(as_text=True)
    assert "Previous" in html
    pages = [admin.get(f"/media/?type=documents&page={n}").get_data(as_text=True) for n in range(1, 6)]
    assert any("PDF fixture" in h for h in pages) and not any("Plain fixture" in h for h in pages)
    assert "URL fixture" not in admin.get("/media/?type=photos&page=999").get_data(as_text=True)
    assert admin.get("/media/?page=0").status_code == 200


def test_show_image_with_people_and_family_links(admin, media_ids):
    html = admin.get(f"/media/{media_ids['plain']}").get_data(as_text=True)
    assert f"/media/{media_ids['plain']}/file" in html and "media-full" in html
    assert "/person/XHUSB" in html and "/family/XFAM" in html


def test_show_url_pdf_and_story(admin, media_ids):
    assert 'href="http://example.com/x.jpg"' in admin.get(f"/media/{media_ids['url']}").get_data(as_text=True)
    assert "Open letter.pdf" in admin.get(f"/media/{media_ids['pdf']}").get_data(as_text=True)
    assert "<p>Story fixture</p>" in admin.get(f"/media/{media_ids['body']}").get_data(as_text=True)


def test_files(admin, media_ids, media_root):
    # usecollfolder = 0 serves from the shared "media" folder, otherwise from the type's folder.
    assert admin.get(f"/media/{media_ids['plain']}/file").data == (media_root / "media/plain.jpg").read_bytes()
    assert admin.get(f"/media/{media_ids['pdf']}/file").data == b"test file documents/letter.pdf"
    assert admin.get(f"/media/{media_ids['url']}/file").status_code == 404
    assert admin.get(f"/media/{media_ids['body']}/file").status_code == 404
    assert admin.get("/media/999999/file").status_code == 404


def image_size(data):
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def test_thumbs_are_made_at_display_sizes(admin, media_ids, media_root):
    r = admin.get(f"/media/{media_ids['plain']}/thumb")
    assert r.mimetype == "image/jpeg" and image_size(r.data) == (300, 200)  # from the 900x600 original, not TNG's
    assert image_size(admin.get(f"/media/{media_ids['plain']}/thumb?s=600").data) == (600, 400)
    assert image_size(admin.get(f"/media/{media_ids['plain']}/thumb?s=999").data) == (300, 200)  # unknown size
    assert (media_root / ".thumbs" / f"{media_ids['plain']}-300.jpg").is_file()
    # TNG's thumbnail is missing for this one; ours is made from the image itself
    assert admin.get(f"/media/{media_ids['nothumb']}/thumb").mimetype == "image/jpeg"


def test_thumb_cache_is_reused_and_refreshed(app, admin, media_ids, media_root):
    import os
    from PIL import Image
    cached = media_root / ".thumbs" / f"{media_ids['nothumb']}-300.jpg"
    admin.get(f"/media/{media_ids['nothumb']}/thumb")
    first = cached.stat().st_mtime_ns
    admin.get(f"/media/{media_ids['nothumb']}/thumb")
    assert cached.stat().st_mtime_ns == first  # reused
    source = media_root / "photos/nothumb.jpg"
    Image.new("RGB", (300, 900), "red").save(source, "JPEG")
    os.utime(source, (cached.stat().st_mtime + 10,) * 2)
    assert image_size(admin.get(f"/media/{media_ids['nothumb']}/thumb").data) == (100, 300)  # remade from new file


def test_icons_instead_of_broken_images(app, admin, media_ids, media_root):
    from app.db import insert
    svg = lambda mid: admin.get(f"/media/{mid}/thumb")
    assert svg(media_ids["pdf"]).mimetype == "image/svg+xml" and b">PDF<" in svg(media_ids["pdf"]).data
    assert b">LINK<" in svg(media_ids["url"]).data
    assert b">MISSING<" in svg(media_ids["body"]).data  # no file at all
    (media_root / "documents/broken.jpg").write_bytes(b"not really a jpeg")
    (media_root / "documents/notes.zip").write_bytes(b"zip")
    with app.app_context():
        _, bad = insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "broken.jpg",
                                      "alwayson": 1, "usecollfolder": 1, "mediakey": "Xbroken"})
        _, other = insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "notes.zip",
                                        "alwayson": 1, "usecollfolder": 1, "mediakey": "Xzip"})
        _, gone = insert("media", {"gedcom": "tree1", "mediatypeID": "photos", "path": "vanished.jpg",
                                       "alwayson": 1, "usecollfolder": 1, "mediakey": "Xvanished"})
    assert b">IMAGE<" in svg(bad).data and b">FILE<" in svg(other).data and b">MISSING<" in svg(gone).data
    from PIL import Image
    Image.new("RGB", (400, 400), "green").save(media_root / "documents/Smithjpg", "JPEG")  # lost its dot
    with app.app_context():
        _, nodot = insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "Smithjpg",
                                        "alwayson": 1, "usecollfolder": 1, "mediakey": "Xnodot"})
    assert svg(nodot).mimetype == "image/jpeg"
    assert "max-age=86400" in svg(other).headers["Cache-Control"]


def test_thumbs_command(app, media_root, media_ids):
    from PIL import Image
    Image.new("RGB", (50, 50), "blue").save(media_root / "photos/fresh.jpg", "JPEG")
    (media_root / "photos/bad.png").write_bytes(b"nope")
    from app.db import insert
    with app.app_context():
        _, fresh = insert("media", {"gedcom": "tree1", "mediatypeID": "photos", "path": "fresh.jpg",
                                        "usecollfolder": 1, "mediakey": "Xfresh"})
        insert("media", {"gedcom": "tree1", "mediatypeID": "photos", "path": "bad.png",
                             "usecollfolder": 1, "mediakey": "Xbadpng"})
    result = app.test_cli_runner().invoke(args=["media", "thumbs"])
    assert result.exit_code == 0 and "thumbnails ready" in result.output and "couldn't be read" in result.output
    assert (media_root / ".thumbs" / f"{fresh}-300.jpg").is_file() and (media_root / ".thumbs" / f"{fresh}-600.jpg").is_file()


def test_pdf_thumbnail_is_its_first_page(app, admin, media_root):
    from PIL import Image
    from app.db import insert
    Image.new("RGB", (600, 800), "teal").save(media_root / "documents/real.pdf")
    with app.app_context():
        _, mid = insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "real.pdf",
                                      "alwayson": 1, "usecollfolder": 1, "mediakey": "Xrealpdf"})
    r = admin.get(f"/media/{mid}/thumb?s=600")
    assert r.mimetype == "image/jpeg" and image_size(r.data) == (450, 600)
    assert f"/media/{mid}/thumb?s=1200" in admin.get(f"/media/{mid}").get_data(as_text=True)  # preview on its page
    (media_root / "documents/letter.docx").write_bytes(b"doc")
    with app.app_context():
        _, doc = insert("media", {"gedcom": "tree1", "mediatypeID": "documents", "path": "letter.docx",
                                      "alwayson": 1, "usecollfolder": 1, "mediakey": "Xdocx"})
    assert b">DOC<" in admin.get(f"/media/{doc}/thumb").data  # other documents keep their icon


def test_body_text_is_cleaned():
    from app.files import clean_html
    assert clean_html("Born here<br>moved<div>later</div>") == "Born here<br>moved<div>later</div>"  # TNG's own use
    dirty = ('<script>alert(1)</script><b onclick="x()">bold</b><img src=x onerror="alert(1)">'
             '<a href="javascript:alert(1)">bad</a><a href="//evil.example">far</a><a href="/person/I1">ok</a>'
             '<style>p {}</style><iframe src="https://evil.example"></iframe>')
    assert clean_html(dirty) == '<b>bold</b><a>bad</a><a>far</a><a href="/person/I1" rel="noopener">ok</a>'
    assert clean_html("1 < 2 & <unknown>kept text</unknown>") == "1 &lt; 2 &amp; kept text"
    assert clean_html(None) == ""

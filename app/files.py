import os
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp"}
RASTER_EXTS = IMAGE_EXTS | {"tif", "tiff", "bmp"}  # what Pillow can shrink into a thumbnail


def is_url(m):
    # TNG has some web links stored as plain paths with abspath = 0.
    return bool(m["abspath"]) or (m["path"] or "").startswith(("http://", "https://"))


def is_image(path):
    # Pictures are always shown through a JPEG thumbnail, so TIFF and BMP count even though browsers can't show them.
    return Path(path or "").suffix.lstrip(".").lower() in RASTER_EXTS


def shrink(source, target, box, quality=82):
    """JPEG copy of an image fitting within box (w, h), turned upright per the camera's orientation tag.
    Written to a temp name first, so a half-written file is never served."""
    tmp = Path(target).with_name(f"{Path(target).name}.{os.getpid()}.tmp")  # per process: two workers can race
    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail(box)
        im.convert("RGB").save(tmp, "JPEG", quality=quality)
    tmp.replace(target)


SAFE_TAGS = {"p", "br", "div", "span", "b", "strong", "i", "em", "u", "ul", "ol", "li", "blockquote", "h2", "h3", "h4"}
DROPPED = {"script", "style", "iframe", "object", "embed", "template"}  # their contents go too


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROPPED:
            self.skip += 1
        elif not self.skip and tag in SAFE_TAGS:
            self.out.append(f"<{tag}>")
        elif not self.skip and tag == "a":
            href = dict(attrs).get("href") or ""
            safe = href.startswith(("https://", "http://", "/")) and not href.startswith("//")
            self.out.append(f'<a href="{escape(href)}" rel="noopener">' if safe else "<a>")

    def handle_endtag(self, tag):
        if tag in DROPPED:
            self.skip = max(self.skip - 1, 0)
        elif not self.skip and (tag in SAFE_TAGS or tag == "a") and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data))


def clean_html(text):
    """Text written as HTML (TNG's media "body text"), kept to simple formatting and plain links: every other tag,
    every attribute and any script are dropped, so the page can show it as HTML safely."""
    cleaner = _Cleaner()
    cleaner.feed(text or "")
    cleaner.close()
    return "".join(cleaner.out)

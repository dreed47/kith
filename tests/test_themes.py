import re
from pathlib import Path

import pytest

from app import THEMES_DIR, create_app

ROOT = Path(__file__).resolve().parent.parent
THEMES = sorted(p.name for p in THEMES_DIR.iterdir() if (p / "theme.css").is_file())
LITERAL_STYLE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|font-family:(?!\s*var\()")


def test_app_css_and_templates_use_only_tokens():
    files = [ROOT / "app/static/app.css", *(ROOT / "app/templates").rglob("*.html")]
    for f in files:
        assert not LITERAL_STYLE.search(f.read_text()), f"literal color/font in {f}"


def test_every_theme_defines_every_token_used():
    used = set(re.findall(r"var\((--[\w-]+)\)", (ROOT / "app/static/app.css").read_text()))
    for name in THEMES:
        defined = set(re.findall(r"(--[\w-]+)\s*:", (THEMES_DIR / name / "theme.css").read_text()))
        assert used <= defined, f"{name} missing {sorted(used - defined)}"


def test_every_theme_has_light_and_dark():
    """Each theme sets its colors twice: light in :root, dark under data-scheme="dark", so any theme works in either.
    Sizes, fonts and "none" are the same in both."""
    same = ("--font", "--space", "--radius", "--link-underline")
    for name in THEMES:
        css = (THEMES_DIR / name / "theme.css").read_text()
        light, dark = css.split(':root[data-scheme="dark"] {')
        colors = {t for t, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", light) if not t.startswith(same) and v != "none"}
        missing = colors - set(re.findall(r"(--[\w-]+)\s*:", dark.split("}")[0]))
        assert not missing, f"{name} has no dark {sorted(missing)}"
        assert "prefers-color-scheme" not in css, name  # base.html decides, from the user's choice or the device


@pytest.mark.parametrize("name", THEMES)
def test_theme_renders(name):
    # The login page needs no DB, so this test runs anywhere.
    client = create_app({"THEME": name, "TESTING": True}).test_client()
    html = client.get("/login").get_data(as_text=True)
    assert "theme.css" in html and "app.css" in html
    assert client.get("/theme/theme.css").status_code == 200
    assert client.get("/theme/theme.toml").status_code == 404


def test_heritage_override_extends_default():
    html = create_app({"THEME": "heritage"}).test_client().get("/login").get_data(as_text=True)
    assert "Our family history" in html and 'name="password"' in html


def test_unknown_theme_fails_fast():
    with pytest.raises(RuntimeError):
        create_app({"THEME": "nope"})


def test_theme_without_toml_and_with_static(tmp_path, monkeypatch):
    import app as app_module
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "theme.css").write_text(":root {}")
    (tmp_path / "bare" / "static").mkdir()
    (tmp_path / "bare" / "static" / "logo.svg").write_text("<svg/>")
    monkeypatch.setattr(app_module, "THEMES_DIR", tmp_path)
    a = create_app({"THEME": "bare", "SECRET_KEY": "x"})
    c = a.test_client()
    assert "Log in · Family Tree</title>" in c.get("/login").get_data(as_text=True)
    assert c.get("/theme/static/logo.svg").data == b"<svg/>"


def test_thumb_and_trash_folders_configurable(tmp_path, monkeypatch):
    assert create_app({"MEDIA_ROOT": str(tmp_path)}).config["THUMB_ROOT"] == str(tmp_path / ".thumbs")
    monkeypatch.setenv("KITH_THUMB_ROOT", "/srv/thumbs")
    monkeypatch.setenv("KITH_TRASH_ROOT", "/srv/trash")
    a = create_app()
    assert (a.config["THUMB_ROOT"], a.config["TRASH_ROOT"]) == ("/srv/thumbs", "/srv/trash")

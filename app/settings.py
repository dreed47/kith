"""Site text an admin may change (Admin > Site settings), stored in app_settings and laid over theme.toml."""
import pymysql
from flask import current_app

from .db import query

# Site text an admin may change: name -> (label, max length, multi-line). Blank means "use the theme's".
SITE_TEXT = {"site_title": ("Site title (top of every page)", 60, False),
             "tagline": ("Tagline", 120, False),
             "welcome_title": ("Welcome page heading (before login)", 100, False),
             "welcome": ("Welcome page text (before login)", 2000, True),
             "contact": ("How to ask for an account (welcome page)", 200, False)}


# Line art behind the home page banner and the welcome page (Site settings): file in static/ -> name in the list.
HERO_ART = {"fan.svg": "Fan chart", "pedigree.svg": "Pedigree chart", "descendants.svg": "Descendant chart",
            "tree.svg": "Branching tree"}


def stored(name):
    """One admin choice from app_settings, or None (also before the table exists, or without a database)."""
    try:
        rows = query("SELECT value FROM app_settings WHERE name = %s", (name,))
    except pymysql.err.MySQLError:
        rows = []
    return rows[0]["value"] if rows else None


def default_theme():
    """The theme for everyone who hasn't picked their own: the admin's choice (Site settings), else
    KITH_THEME. A choice whose theme has since been removed counts as no choice."""
    name = stored("theme")
    return name if name in current_app.extensions["themes"] else current_app.config["THEME"]


def hero_art():
    name = stored("hero_art")
    return name if name in HERO_ART else "fan.svg"


def site_settings():
    """Admin-set site text. Read on every page; before the table exists (or without a database) it's empty."""
    try:
        return {r["name"]: r["value"] for r in query("SELECT name, value FROM app_settings")
                if r["name"] in SITE_TEXT}
    except pymysql.err.MySQLError:
        return {}

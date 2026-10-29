"""Old TNG addresses (getperson.php?personID=I12 and the rest) redirect permanently to the matching new page, so
bookmarks, links people have shared and search-engine results keep working after the switch."""
from flask import redirect, request, url_for

from .views import bp


def _id(*names):
    """The first of these query arguments that's set, upper-cased like the IDs (TNG accepts i12)."""
    for n in names:
        if v := request.args.get(n, "").strip():
            return v.upper()
    return None


def _gens():
    g = request.args.get("generations", type=int)  # TNG's name for the chart depth
    return {"gens": g} if g else {}


def target():
    """Where an old TNG page lives now, or None."""
    page = request.view_args["page"].lower()
    pid, fid = _id("personID", "primaryID"), _id("familyID")
    by_person = {"getperson": "main.person", "pedigree": "main.pedigree", "pedigreetext": "main.ahnentafel",
                 "verticalchart": "main.vertical_pedigree", "ahnentafel": "main.ahnentafel",
                 "descend": "main.descendants", "descendtext": "main.descendants", "descendvert": "main.descendants",
                 "descendtables": "main.descendant_table", "register": "main.register", "fan": "main.fan_chart",
                 "extrastree": "main.person", "desctracker": "main.descendants"}
    if page in by_person and pid:
        return url_for(by_person[page], pid=pid, **({} if by_person[page] == "main.person" else _gens()))
    if page in ("familygroup", "familychart") and fid:
        return url_for("main.family_chart" if page == "familychart" else "main.family", fid=fid)
    if page in ("relationship", "relationship2", "connections", "connections2", "relateform"):
        return url_for("main.relationship_page", a=pid or None, b=_id("secondpersonID", "secondPersonID") or None)
    if page == "showmedia" and request.args.get("mediaID", "").isdigit():
        return url_for("media.show", media_id=int(request.args["mediaID"]))
    if page == "showsource" and _id("sourceID"):
        return url_for("main.source", sid=_id("sourceID"))
    if page == "showmap" and request.args.get("cemeteryID", "").isdigit():
        return url_for("places.cemetery", cid=int(request.args["cemeteryID"]))
    if page in ("placesearch", "findplace") and request.args.get("psearch"):
        return url_for("places.place", name=request.args["psearch"])
    if page == "search":
        last, first = request.args.get("mylastname", "").strip(), request.args.get("myfirstname", "").strip()
        if last and not first and request.args.get("lnqualify") == "equals":  # TNG's surname-page links
            return url_for("main.surname", name=last)
        return url_for("main.search", q=" ".join(x for x in (first, last) if x) or None)
    if page in ("famsearch", "famsearchform"):  # TNG's family search: f = husband ("father"), m = wife
        name = lambda who: " ".join(x for x in (request.args.get(f"my{who}firstname", "").strip(),
                                                request.args.get(f"my{who}lastname", "").strip()) if x) or None
        return url_for("main.search_families", husband=name("f"), wife=name("m"),
                       place=request.args.get("mymarrplace", "").strip() or None)
    return url_for(PAGES[page]) if page in PAGES else None


PAGES = {"index": "main.home", "whatsnew": "main.home", "login": "auth.login", "searchform": "main.search",
         "surnames": "main.surnames", "surnames-all": "main.surnames", "surnames-oneletter": "main.surnames",
         "surnames100": "main.surnames", "firstnames": "main.firstnames", "firstnames-all": "main.firstnames",
         "firstnames-oneletter": "main.firstnames", "firstnames100": "main.firstnames", "places": "places.places",
         "places-all": "places.places", "places-oneletter": "places.places", "places100": "places.places",
         "cemeteries": "places.cemeteries", "headstones": "places.cemeteries", "browsemedia": "media.browse",
         "browsephotos": "media.browse", "browsedocs": "media.browse", "browseheadstones": "media.browse",
         "anniversaries": "main.anniversaries", "anniversaries2": "main.anniversaries",
         "calendar": "main.anniversary_calendar", "newacctform": "account.request_account", "browsesources": "main.sources",
         "timeline": "main.timeline", "timeline2": "main.timeline", "statistics": "main.statistics"}


@bp.get("/<page>.php")
def old_tng_page(page):
    return redirect(target() or url_for("main.home"), code=301)

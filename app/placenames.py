"""The house style for place names ("Madison Twp, Clarion Cnty, PA"): used when a place is saved and by Tidy places."""
import re

STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
          "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
          "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
          "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
          "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
          "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
          "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "penna": "PA",
          "penn": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
          "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
          "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC"}
CODES = set(STATES.values())
USA = re.compile(r"(usa|u\.s\.a?\.?|united states( of america)?)", re.I)
# Only a part's last word: "Armstrong County Memorial Hospital" keeps its name.
STYLE = ((re.compile(r"\b(county|cnty\.?|co\.?)$", re.I), "Cnty"), (re.compile(r"\b(township|twp\.?|twsp\.?)$", re.I), "Twp"))


def standard(name):
    """A place name in the house style: one space after each comma, no empty parts, no trailing USA, County and
    Township as Cnty and Twp, a US state as its two-letter code. A name with no commas is left alone (Georgia)."""
    parts = [" ".join(p.split()) for p in name.split(",")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return " ".join(parts)
    if len(parts) > 2 and USA.fullmatch(parts[-1]):
        parts.pop()
    state = parts[-1].replace(".", "")
    state = STATES.get(state.lower(), state.upper() if state.upper() in CODES else parts[-1])
    for rx, word in STYLE:
        parts = [rx.sub(word, p) for p in parts[:-1]] + [parts[-1]]
    return ", ".join([*parts[:-1], state])

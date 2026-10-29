"""Port of TNG's convertDate (datelib.php): free-text genealogy date -> sortable 'YYYY-MM-DD'.
TNG reads the *datetr columns for sorting and ages, so new writes must compute them the same way.
Validated against every existing date/datetr pair in tests/test_dates.py.

shortcut: Hebrew month names aren't converted (none in our data); they sort by year only.
"""
import calendar
import re

MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
LAST_DAY = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _numeric(s):
    # PHP is_numeric, close enough for date parts: optional sign, digits, optional decimals.
    try:
        float(s)
        return s.strip() == s and s != ""
    except ValueError:
        return False


def convert_date(old):
    year, month, day = parse(old)
    # TNG produces impossible dates ("5/31/1995" parses as month 31, "31 FEB 1900" as Feb 31). MySQL stores
    # some as zero and rejects others outright in strict mode, so they all become "no date" here.
    if not (0 <= year <= 9999 and 0 <= month <= 12 and 0 <= day <= 31):
        return "0000-00-00"
    if year and month and day > calendar.monthrange(year, month)[1]:
        return "0000-00-00"
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse(old):
    """TNG's reading of the text as (year, month, day), before checking the date can exist."""
    if not old or not old.strip():
        return 0, 0, 0
    parts = old.strip().upper().split(" ")
    year = month = day = 0
    bef = False

    found = parts.index("TO") if "TO" in parts else (parts.index("AND") if "AND" in parts else 0)
    ptr = found - 1 if found else len(parts) - 1

    pieces = parts[ptr].split("/")
    if len(pieces) == 3:  # MM/DD/YYYY
        parts[ptr:ptr + 1] = pieces
        ptr += 2

    token = parts[ptr]
    slash = token.find("/")
    if slash > 0:  # dual year like 1731/32
        whole1, whole2 = token.split("/")[:2]
        cut = len(whole1) - len(whole2)
        century, year1 = whole1[:cut], whole1[cut:]
        if _numeric(year1) and _numeric(whole2) and float(year1) > float(whole2):
            century = str(int(century or 0) + 1)
        tempyear = century + whole2
    else:
        tempyear = token[-4:].strip()
        if "-" in tempyear:
            tempyear = tempyear[tempyear.index("-") + 1:]

    if _numeric(tempyear):
        year = int(float(tempyear))
        ptr -= 1
        tempmonth = parts[ptr][:3].strip() if ptr >= 0 else ""
        foundit = False
        tempday = ""
        if tempmonth in MONTHS:
            month, foundit = MONTHS[tempmonth], True
        elif _numeric(tempmonth) and len(tempmonth) <= 2:
            month, foundit = int(float(tempmonth)), True
        if foundit:
            ptr -= 1
            tempday = parts[ptr] if ptr >= 0 else ""
            if _numeric(tempday) and len(tempday) <= 2:
                day = int(float(tempday))
                ptr -= 1
                word = parts[ptr][:3] if ptr >= 0 else ""
                if word == "AFT":
                    day += 1
                    if day > LAST_DAY[month - 1]:
                        day = 0
                        if month == 12:
                            year += 1
                        month = month + 1 if month < 12 else 1
                elif word == "BEF":
                    day -= 1
                    bef = True
            else:
                day = 0
                word = tempday[:3]
                if word == "AFT":
                    if month == 12:
                        year += 1
                    month = month + 1 if month < 12 else 1
                elif word == "BEF":
                    bef = True
        else:
            month = day = 0
            if tempmonth == "AFT":
                year += 1
            elif tempmonth == "BEF":
                bef = True
        if bef and day == 0:
            month -= 1
            if month <= 0:
                month, year = 12, year - 1
            day = calendar.monthrange(year, month)[1] if year > 0 else 31
    return year, month, day


MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
QUALIFIERS = {"ABT": "about", "ABOUT": "about", "C.": "about", "CA": "about", "CA.": "about", "CIRCA": "about",
              "EST": "estimated", "CAL": "calculated", "BEF": "before", "BEFORE": "before",
              "AFT": "after", "AFTER": "after", "BET": "between", "FROM": "from"}


def describe(text):
    """How a typed date is understood, for the hint under date boxes: (message, ok)."""
    text = (text or "").strip()
    if not text:
        return "", True
    if found := readings(text):
        other = f" (read as month/day/year; for day/month type {found[1][0]})" if len(found) > 1 else ""
        return f"Will be saved as {found[0][0]}{other}.", True
    sort = convert_date(text)
    if sort == "0000-00-00":
        y, m, d = parse(text)
        if 1 <= m <= 12 and d > 0 and y > 0:
            return f"There is no {d} {MONTH_NAMES[m - 1]} {y}; check the day. It won't sort by date as written.", False
        if m > 12 and "/" in text:
            return "Not recognised: check the day and month numbers, or write it as 23 JAN 1733.", False
        return "Not recognised as a date, so it won't sort by date. Try e.g. 3 DEC 1920, abt 1895, bef 1850.", False
    y, m, d = (int(x) for x in sort.split("-"))
    when = " ".join(str(x) for x in (d or "", MONTH_NAMES[m - 1] if m else "", y) if x)
    qualifier = QUALIFIERS.get(text.split()[0].upper())
    return (f"{qualifier.capitalize()}: sorts as {when}" if qualifier else f"Sorts as {when}"), True


GEDCOM_QUALIFIER = {"ABT": "ABT", "ABOUT": "ABT", "C.": "ABT", "CA": "ABT", "CA.": "ABT", "CIRCA": "ABT",
                    "EST": "EST", "CAL": "CAL", "BEF": "BEF", "BEFORE": "BEF", "AFT": "AFT", "AFTER": "AFT"}
_QUAL = r"(?:(?P<q>[A-Za-z.]+)\s+)?"
NUMERIC = re.compile(_QUAL + r"(?P<a>\d{1,2})(?P<sep>[/.-])(?P<b>\d{1,2})(?P=sep)(?P<y>\d{3,4})$")
ISO = re.compile(_QUAL + r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$")
MONTH_FIRST = re.compile(_QUAL + r"(?P<m>[A-Za-z]{3,})\.?\s+(?P<d>\d{1,2}),?\s+(?P<y>\d{3,4})$")
YEAR_FIRST = re.compile(_QUAL + r"(?P<y>\d{3,4}),?\s+(?P<m>[A-Za-z]{3,})\.?(?:\s+(?P<d>\d{1,2}))?$")
PLAIN = re.compile(r"(?:\d{1,2}\s+)?(?:(?P<m>[A-Za-z]{3,})\s+)?\d{3,4}$")  # 15 APR 1933, APR 1933, 1933
QUALIFIER_LAST = re.compile(r"(?P<rest>.+?),?\s+(?P<q>[A-Za-z.]+)$")


FULL_MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
               "NOVEMBER", "DECEMBER")


def month_number(word):
    """1-12 for a month name or its abbreviation (Sep, Sept, September), else 0 ("maybe" isn't May)."""
    w = (word or "").upper()
    return next((i for i, name in enumerate(FULL_MONTHS, 1) if len(w) >= 3 and name.startswith(w)), 0)


def readings(text):
    """Ways a date typed as numbers ("1/3/1964", "1964-01-03"), month or year first ("Feb 19 1993", "1751 Sep"),
    or with "after" etc. at the end ("15 APR 1933 after") could be meant,
    written the standard way: [("3 JAN 1964", "month/day/year"), ("1 MAR 1964", "day/month/year")]. US
    month/day order comes first. Only dates that exist; empty when the text isn't one of these shapes."""
    text = (text or "").strip()
    out = []
    if m := NUMERIC.match(text):
        a, b = int(m["a"]), int(m["b"])
        tries = [(b, a, "month/day/year"), (a, b, "day/month/year")]
    elif m := ISO.match(text):
        tries = [(int(m["d"]), int(m["m"]), "")]
    elif (m := MONTH_FIRST.match(text)) and month_number(m["m"]):
        tries = [(int(m["d"]), month_number(m["m"]), "")]
    elif (m := YEAR_FIRST.match(text)) and month_number(m["m"]):
        tries = [(int(m["d"] or 0), month_number(m["m"]), "")]  # day 0: month and year only
    elif (m := QUALIFIER_LAST.match(text)) and (q := GEDCOM_QUALIFIER.get(m["q"].upper())) \
            and not GEDCOM_QUALIFIER.get(m["rest"].split()[0].upper()):
        if found := readings(m["rest"]):
            return [(f"{q} {v}", how) for v, how in found]
        rest = m["rest"].upper()
        plain = PLAIN.match(rest)
        ok = plain and (not plain["m"] or month_number(plain["m"])) and convert_date(rest) != "0000-00-00"
        return [(f"{q} {rest}", "")] if ok else []
    else:
        return []
    q = m["q"] and GEDCOM_QUALIFIER.get(m["q"].upper())
    if m["q"] and not q:
        return []
    year = int(m["y"])
    for day, month, how in tries:
        if 1 <= month <= 12 and (day == 0 and year or 1 <= day <= calendar.monthrange(year, month)[1]):
            value = f"{q + ' ' if q else ''}{str(day) + ' ' if day else ''}{MONTH_NAMES[month - 1].upper()} {year}"
            if value not in (v for v, _ in out):
                out.append((value, how))
    return out if len(out) > 1 else [(v, "") for v, _ in out]


def standard(text):
    """A typed date as saved: numbers or month-first become "23 JAN 1733" (month/day/year when it could be
    either), anything else is kept as typed."""
    found = readings(text)
    return found[0][0] if found else (text or "").strip()


DISPLAY_WORDS = {"ABT", "ABOUT", "BEF", "BEFORE", "AFT", "AFTER", "BET", "BETWEEN", "AND", "FROM", "TO", "EST",
                 "CAL", "CA", "CA.", "CIRCA", "INT"}


def display(text):
    """One consistent look for dates shown on pages ("02 JUL 1921" -> "2 Jul 1921", "ABT 1895" -> "abt 1895").
    Only the display changes; the stored text is left exactly as entered."""
    words = (text or "").split()
    out = []
    for i, w in enumerate(words):
        up = w.upper()
        if up[:3] in MONTHS and w.isalpha() and len(w) >= 3:
            out.append(w.capitalize())
        elif up in DISPLAY_WORDS:
            out.append(w.lower())
        elif w.isdigit() and len(w) <= 2 and i + 1 < len(words) and words[i + 1][:3].upper() in MONTHS:
            out.append(str(int(w)))  # day before a month name: drop the leading zero
        else:
            out.append(w)
    return " ".join(out)

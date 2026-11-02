import pytest

from app.dates import convert_date

# Expected values are TNG 15's own convertDate output (run with PHP on the live server), except where
# TNG produces an impossible date that MySQL stores as zero, or a year under 100 (TNG reads "1900-10" as the
# year 10 and "May 2" as May of the year 2); those expect "0000-00-00".
CASES = """
1900	1900-00-00
abt 1895	1895-00-00
12 JUL 1850	1850-07-12
12 jul 1850	1850-07-12
BEF 3 MAR 1900	1900-03-02
BEF 1 MAR 1900	1900-02-28
BEF 1 JAN 1900	1899-12-31
AFT 31 DEC 1900	1901-01-00
AFT 30 JUN 1900	1900-07-00
AFT 15 JUN 1900	1900-06-16
AFT DEC 1900	1901-01-00
AFT JUN 1900	1900-07-00
BEF JAN 1900	1899-12-31
BEF JUN 1900	1900-05-31
AFT 1900	1901-00-00
BEF 1900	1899-12-31
BET 1900 AND 1910	1900-00-00
FROM 1900 TO 1910	1900-00-00
BET 3 MAR 1900 AND 1910	1900-03-03
1731/32	1732-00-00
1799/00	1800-00-00
1/23/1733	0000-00-00
5/31/1995	0000-00-00
12/25/1990	0000-00-00
Feb 19 1993	0000-00-00
1900-1910	1910-00-00
unknown	0000-00-00
abt	0000-00-00
JUN 1900	1900-06-00
6 1900	1900-06-00
c. 1850	1850-00-00
1900s	0000-00-00
WFT Est. 1850-1900	1900-00-00
15 6 1900	1900-06-15
x 15 JUN 1900	1900-06-15
BEF JUN 0001	0000-00-00
1800/1	1801-00-00
1x/32	0000-00-00
ab/cd	0000-00-00
1900-10	0000-00-00
abt 1900-5	0000-00-00
"""


@pytest.mark.parametrize("text,expected", [l.split("\t") for l in CASES.strip().splitlines()])
def test_matches_tng(text, expected):
    assert convert_date(text) == expected


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank(blank):
    assert convert_date(blank) == "0000-00-00"


@pytest.mark.parametrize("text", ["31 FEB 1900", "29 FEB 1900", "31 APR 1900", "30 FEB 1900"])
def test_impossible_days_become_no_date(text):
    # TNG 15 returns e.g. 1900-02-31, which strict MySQL refuses to store; we store "no date" instead.
    assert convert_date(text) == "0000-00-00"


def test_real_leap_day_and_zero_day():
    assert convert_date("29 FEB 2000") == "2000-02-29"
    assert convert_date("0 JAN 1900") == "1900-01-00"


@pytest.mark.parametrize("text,message,ok", [
    ("", "", True),
    ("3 DEC 1920", "Sorts as 3 Dec 1920", True),
    ("Mar 1910", "Sorts as Mar 1910", True),
    ("1885", "Sorts as 1885", True),
    ("abt 1895", "About: sorts as 1895", True),
    ("bef 1850", "Before: sorts as 31 Dec 1849", True),
    ("BET 1900 AND 1910", "Between: sorts as 1900", True),
    ("est 1700", "Estimated: sorts as 1700", True),
    ("1/23/1733", "Will be saved as 23 JAN 1733.", True),
    ("1/3/1964", "Will be saved as 3 JAN 1964 (read as month/day/year; for day/month type 1 MAR 1964).", True),
    ("13/13/1900", "Not recognised: check the day and month numbers", False),
    ("31 feb 1900", "There is no 31 Feb 1900; check the day", False),
    ("29 FEB 1900", "There is no 29 Feb 1900", False),
    ("unknown", "Not recognised as a date", False),
])
def test_describe(text, message, ok):
    from app.dates import describe
    got, got_ok = describe(text)
    assert got.startswith(message) and got_ok == ok


@pytest.mark.parametrize("text,found", [
    ("1/23/1733", [("23 JAN 1733", "")]),
    ("23/1/1733", [("23 JAN 1733", "")]),
    ("1/3/1964", [("3 JAN 1964", "month/day/year"), ("1 MAR 1964", "day/month/year")]),
    ("1.3.1964", [("3 JAN 1964", "month/day/year"), ("1 MAR 1964", "day/month/year")]),
    ("1/1/1900", [("1 JAN 1900", "")]),
    ("abt 2/14/1988", [("ABT 14 FEB 1988", "")]),
    ("Circa 2/14/1988", [("ABT 14 FEB 1988", "")]),
    ("Feb 19 1993", [("19 FEB 1993", "")]),
    ("1964-01-03", [("3 JAN 1964", "")]),
    ("5/1890", [("MAY 1890", "")]),          # month and year only
    ("12-1913", [("DEC 1913", "")]),
    ("aft 7/1860", [("AFT JUL 1860", "")]),
    ("13/1890", []),                         # no 13th month
    ("5/90", []),                            # a two-digit year could be anything
    ("2/30/1900", []),        # no such day either way
    ("13/13/1900", []),
    ("1/2-1900", []),         # mixed separators
    ("bet 1/2/1900", []),     # needs a second date
    ("Foo 19 1993", []),      # not a month
    ("1850-1860", []),
    ("15 APR 1933 after", [("AFT 15 APR 1933", "")]),
    ("1933 after", [("AFT 1933", "")]),
    ("1/3/1964 about", [("ABT 3 JAN 1964", "month/day/year"), ("ABT 1 MAR 1964", "day/month/year")]),
    ("1751 Sep", [("SEP 1751", "")]),
    ("1751, September 3", [("3 SEP 1751", "")]),
    ("abt 1751 Sep", [("ABT SEP 1751", "")]),
    ("Madison Twp 1830 after", []),   # not a plain date before the word
    ("Foo 1830 after", []),
    ("31 FEB 1900 after", []),        # no such day
    ("bef 1900 after", []),           # two qualifiers
    ("1900 maybe", []),
    ("1751 Foo", []),
    ("Sept 3 1751", [("3 SEP 1751", "")]),
    ("Septx 3 1751", []),
    ("12 JAN 1900", []),
    ("", []),
    (None, []),
])
def test_readings(text, found):
    from app.dates import readings, standard
    assert readings(text) == found
    assert standard(text) == (found[0][0] if found else (text or "").strip())


@pytest.mark.parametrize("raw,shown", [
    ("31 MAR 1898", "31 Mar 1898"),
    ("02 JUL 1921", "2 Jul 1921"),
    ("4 Oct 1806", "4 Oct 1806"),
    ("ABT 1895", "abt 1895"),
    ("BET 1900 AND 1910", "bet 1900 and 1910"),
    ("BEF 3 MAR 1900", "bef 3 Mar 1900"),
    ("15 AUGUST 1900", "15 August 1900"),
    ("1731/32", "1731/32"),
    ("1/23/1733", "1/23/1733"),
    ("16 SEP 19??", "16 Sep 19??"),
    ("12 MAYBE", "12 Maybe"),
    ("", ""),
    (None, ""),
])
def test_display(raw, shown):
    from app.dates import display
    assert display(raw) == shown


def test_day_and_month_only():
    from app.dates import convert_date, describe
    for text in ("May 2", "Sept 3", "Jan 15", "2 May", "02 MAY"):
        assert convert_date(text) == "0000-00-00", text  # not the year 2, 3 or 15
        message, ok = describe(text)
        assert message.startswith("Day and month only") and not ok, text
    assert not describe("Foo 3")[0].startswith("Day and month")  # not a month
    assert convert_date("15 APR 1933") == "1933-04-15" and convert_date("abt 1885") == "1885-00-00"

"""Spoken dates -> ISO dates, for date slots ("move it to the third of October").

Plain parsing, no LLM call, for the same reason the ID slot is a regex: it keeps
the turn free and the result checkable. What STT actually writes for a date:

    "2026 10 01" / "2026-10-01"            numeric, year first
    "October 3rd" / "the third of October"  month name, ordinal digits or words
    "tomorrow" / "next Monday"              relative to today
    "in three days"                         relative to today
    "postpone it for three days"            relative to the CURRENT delivery date

The last one is why parse_date takes a `base`: "push it back three days" means
three days after the delivery that is planned, not three days after today.
A date without a year is the first such date on or after that base.

Business rules (is that date bookable? is it too late?) are the tool's job, not
the parser's: it only refuses what is not a date at all, like September 31st.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}

_UNITS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
          "sixteen", "seventeen", "eighteen", "nineteen"]
_ORD_UNITS = ["", "first", "second", "third", "fourth", "fifth", "sixth",
              "seventh", "eighth", "ninth"]
_ORD_TEENS = ["tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
              "sixteenth", "seventeenth", "eighteenth", "nineteenth"]


def _number_words() -> tuple[dict[str, int], dict[str, int]]:
    """Cardinal and ordinal words 1-31, both 'twenty one' and 'twenty-one'."""
    card = {w: i for i, w in enumerate(_UNITS) if w}
    card.update({w: 10 + i for i, w in enumerate(_TEENS)})
    ordn = {w: i for i, w in enumerate(_ORD_UNITS) if w}
    ordn.update({w: 10 + i for i, w in enumerate(_ORD_TEENS)})
    card.update({"twenty": 20, "thirty": 30})
    ordn.update({"twentieth": 20, "thirtieth": 30})
    for tens, word in ((20, "twenty"), (30, "thirty")):
        for u in range(1, 10 if tens == 20 else 2):
            for sep in (" ", "-"):
                card[f"{word}{sep}{_UNITS[u]}"] = tens + u
                ordn[f"{word}{sep}{_ORD_UNITS[u]}"] = tens + u
    card.update({"a": 1, "an": 1, "a couple of": 2, "couple of": 2})
    return card, ordn


CARDINALS, ORDINALS = _number_words()

# Longest first, so "twenty one" wins over "twenty" and "one".
_ORD_RX = "|".join(sorted(map(re.escape, ORDINALS), key=len, reverse=True))
_CARD_RX = "|".join(sorted(map(re.escape, CARDINALS), key=len, reverse=True))
_MONTH_RX = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY_RX = rf"(\d{{1,2}})(?:st|nd|rd|th)?|{_ORD_RX}"
_COUNT_RX = rf"\d{{1,3}}|{_CARD_RX}"

_NUMERIC = re.compile(r"\b(20\d\d)[\s\-/.]+(\d{1,2})[\s\-/.]+(\d{1,2})\b")
_DAY_MONTH = re.compile(rf"\b(?P<day>{_DAY_RX})\s+(?:of\s+)?(?P<month>{_MONTH_RX})\b"
                        r"(?:,?\s+(?P<year>20\d\d))?")
_MONTH_DAY = re.compile(rf"\b(?P<month>{_MONTH_RX})\s+(?:the\s+)?(?P<day>{_DAY_RX})\b"
                        r"(?:,?\s+(?P<year>20\d\d))?")
_SHIFT = re.compile(rf"\b(?P<n>{_COUNT_RX})\s+(?P<unit>days?|weeks?)\b")
_POSTPONE = re.compile(r"\b(postpone|delay|push|move|later|back|after|extra|more)\b")
_WEEKDAY = re.compile(rf"\b(?:(next|this|on)\s+)?({'|'.join(WEEKDAYS)})\b")


def _day(text: str) -> int | None:
    digits = re.match(r"\d{1,2}", text)
    if digits:
        return int(digits.group(0))
    return ORDINALS.get(text)


def _count(text: str) -> int | None:
    return int(text) if text.isdigit() else CARDINALS.get(text)


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:          # September 31st, February 30th: not a date
        return None


def _on_or_after(month: int, day: int, ref: date) -> date | None:
    """The first month/day on or after ref: 'October 3rd' with no year."""
    for year in (ref.year, ref.year + 1):
        d = _build(year, month, day)
        if d is None:
            if not (month == 2 and day == 29):
                return None     # invalid every year
            continue            # Feb 29: try the next year
        if d >= ref:
            return d
    return None


def parse_date(text: str, today: date, base: date | None = None) -> date | None:
    """The date the caller means, or None if they did not say one.

    today: the call's date, for "tomorrow", "next Monday", "in three days".
    base:  the date being changed (the current delivery estimate), for
           "postpone it three days" and for dates said without a year.
    """
    t = " ".join(text.lower().replace(",", " ").split())
    ref = base or today

    m = _NUMERIC.search(t)
    if m:
        return _build(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    for rx in (_DAY_MONTH, _MONTH_DAY):
        m = rx.search(t)
        if m:
            day = _day(m.group("day"))
            month = MONTHS[m.group("month")]
            if day is None:
                return None
            if m.group("year"):
                return _build(int(m.group("year")), month, day)
            return _on_or_after(month, day, ref)

    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2)
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)

    m = _SHIFT.search(t)
    if m:
        n = _count(m.group("n"))
        if n is None or n == 0:
            return None
        days = n * 7 if m.group("unit").startswith("week") else n
        # "in three days" counts from today; "push it back three days" from the
        # delivery that is currently planned.
        start = ref if _POSTPONE.search(t) else today
        return start + timedelta(days=days)

    m = _WEEKDAY.search(t)
    if m:
        ahead = (WEEKDAYS[m.group(2)] - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)

    return None

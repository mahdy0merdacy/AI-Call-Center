"""Spoken dates, as STT writes them, to ISO dates."""

from __future__ import annotations

from datetime import date

import pytest

from app.dates import parse_date, say_dates

TODAY = date(2026, 9, 25)          # a Friday
ESTIMATE = date(2026, 9, 30)       # the delivery being moved


@pytest.mark.parametrize("said, expected", [
    # numeric, as STT writes a date read out digit by digit
    ("2026 10 01", date(2026, 10, 1)),
    ("make the new date. 2026-10-03.", date(2026, 10, 3)),
    # month names, ordinals as digits or words, either order
    ("October 3rd", date(2026, 10, 3)),
    ("the third of October", date(2026, 10, 3)),
    ("on the twenty-first of October please", date(2026, 10, 21)),
    ("twenty first october", date(2026, 10, 21)),
    ("3 Oct", date(2026, 10, 3)),
    ("October 3, 2027", date(2027, 10, 3)),
    # relative to today
    ("tomorrow", date(2026, 9, 26)),
    ("the day after tomorrow", date(2026, 9, 27)),
    ("in three days", date(2026, 9, 28)),
    ("next Monday", date(2026, 9, 28)),
    ("on friday", date(2026, 10, 2)),      # never today: that is not a reschedule
    # relative to the planned delivery
    ("just postpone it for three days", date(2026, 10, 3)),
    ("push it back a week", date(2026, 10, 7)),
    ("two days later", date(2026, 10, 2)),
])
def test_spoken_dates(said, expected):
    assert parse_date(said, today=TODAY, base=ESTIMATE) == expected


def test_a_date_without_a_year_is_never_before_the_planned_delivery():
    # "the 2nd of September" is already past the estimate this year: next year's.
    assert parse_date("2nd of September", TODAY, ESTIMATE) == date(2027, 9, 2)


@pytest.mark.parametrize("said", [
    "2026 09 31",                 # from a live call: September has 30 days
    "the thirty-first of September",
    "February 30th",
])
def test_impossible_dates_are_refused(said):
    assert parse_date(said, TODAY, ESTIMATE) is None


@pytest.mark.parametrize("said", [
    "may locate the parcel",      # "may" the verb, not the month
    "please confirm that",
    "I don't know",
    "AC4471X209",
    "",
])
def test_no_date_said(said):
    assert parse_date(said, TODAY, ESTIMATE) is None


# ── ISO dates -> words, for what the agent says ─────────────────────────────


@pytest.mark.parametrize("iso, en, fr", [
    ("2026-10-03", "Saturday the third of October", "samedi 3 octobre"),
    ("2026-10-21", "Wednesday the twenty-first of October", "mercredi 21 octobre"),
    ("2027-01-01", "Friday the first of January 2027", "vendredi 1er janvier 2027"),
    ("2026-09-26T14:12:00", "Saturday the twenty-sixth of September at 2:12 pm",
     "samedi 26 septembre à 14 h 12"),
    ("2026-10-02T12:00:00Z", "Friday the second of October at noon",
     "vendredi 2 octobre à 12 h"),
])
def test_iso_dates_are_said_in_words(iso, en, fr):
    assert say_dates(iso, TODAY) == en
    assert say_dates(iso, TODAY, "fr") == fr


def test_dates_inside_text_and_json_are_rewritten_in_place():
    data = '{"new_date": "2026-10-03", "previous_estimate": "2026-09-30"}'
    assert say_dates(data, TODAY) == (
        '{"new_date": "Saturday the third of October", '
        '"previous_estimate": "Wednesday the thirtieth of September"}')
    assert (say_dates("Done, it now arrives 2026-10-03.", TODAY)
            == "Done, it now arrives Saturday the third of October.")


@pytest.mark.parametrize("text", ["2026-09-31", "AC4471X209", "ref 2026-10-03-7", "no date"])
def test_what_is_not_a_date_is_left_alone(text):
    assert say_dates(text, TODAY) == text

"""Spoken dates, as STT writes them, to ISO dates."""

from __future__ import annotations

from datetime import date

import pytest

from app.dates import parse_date

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

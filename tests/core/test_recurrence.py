"""Recurrence maths. Off-by-one here silently skips a whole week of booking."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule

# 2026-09-01 is a Tuesday. Tuesday is weekday 1.
SEASON_START = date(2026, 9, 1)


def weekly(**kwargs: object) -> RecurrenceRule:
    return RecurrenceRule(
        frequency=Frequency.WEEKLY,
        weekday=1,
        season_start=SEASON_START,
        **kwargs,  # type: ignore[arg-type]
    )


def biweekly(**kwargs: object) -> RecurrenceRule:
    return RecurrenceRule(
        frequency=Frequency.BIWEEKLY,
        weekday=1,
        season_start=SEASON_START,
        **kwargs,  # type: ignore[arg-type]
    )


def test_weekly_occurrences_are_seven_days_apart_starting_at_the_season() -> None:
    assert weekly().occurrences(after=SEASON_START, limit=3) == [
        date(2026, 9, 1),
        date(2026, 9, 8),
        date(2026, 9, 15),
    ]


def test_first_occurrence_rolls_forward_when_the_season_starts_off_weekday() -> None:
    # 2026-09-02 is a Wednesday; the first Tuesday on or after it is the 8th.
    rule = RecurrenceRule(frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 2))
    assert rule.occurrences(after=date(2026, 9, 2), limit=1) == [date(2026, 9, 8)]


def test_biweekly_skips_every_other_week_anchored_on_the_season_start() -> None:
    assert biweekly().occurrences(after=SEASON_START, limit=3) == [
        date(2026, 9, 1),
        date(2026, 9, 15),
        date(2026, 9, 29),
    ]


def test_biweekly_phase_survives_being_asked_mid_season() -> None:
    """Anchoring on the season, not on "today", is what keeps the phase.

    A restart of Home Assistant must not flip a biweekly job onto the wrong
    fortnight.
    """
    assert biweekly().occurrences(after=date(2026, 9, 20), limit=2) == [
        date(2026, 9, 29),
        date(2026, 10, 13),
    ]


def test_an_occurrence_falling_exactly_on_the_cursor_is_included() -> None:
    """ "After" means on or after; excluding it would skip today's booking."""
    assert biweekly().occurrences(after=date(2026, 9, 15), limit=1) == [date(2026, 9, 15)]


def test_season_end_truncates_the_series_and_is_inclusive() -> None:
    rule = weekly(season_end=date(2026, 9, 15))
    assert rule.occurrences(after=SEASON_START, limit=10) == [
        date(2026, 9, 1),
        date(2026, 9, 8),
        date(2026, 9, 15),
    ]


def test_next_occurrence_returns_none_after_the_season_ends() -> None:
    rule = weekly(season_end=date(2026, 9, 15))
    assert rule.next_occurrence(after=date(2026, 9, 16)) is None


def test_next_occurrence_returns_the_first_upcoming_date() -> None:
    assert weekly().next_occurrence(after=date(2026, 9, 2)) == date(2026, 9, 8)


def test_occurrences_before_the_season_start_are_never_returned() -> None:
    assert weekly().occurrences(after=date(2026, 8, 1), limit=1) == [date(2026, 9, 1)]


def test_a_season_of_one_day_yields_exactly_one_occurrence() -> None:
    rule = weekly(season_end=SEASON_START)
    assert rule.occurrences(after=SEASON_START, limit=5) == [SEASON_START]


def test_a_limit_of_zero_yields_nothing() -> None:
    assert weekly().occurrences(after=SEASON_START, limit=0) == []


def test_occurrences_are_strictly_ascending() -> None:
    dates = weekly().occurrences(after=SEASON_START, limit=20)
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)


def test_every_occurrence_falls_on_the_configured_weekday() -> None:
    """The one property that must hold for every frequency and offset."""
    for frequency in Frequency:
        for weekday in range(7):
            rule = RecurrenceRule(frequency=frequency, weekday=weekday, season_start=SEASON_START)
            dates = rule.occurrences(after=date(2026, 10, 7), limit=5)
            assert {d.weekday() for d in dates} == {weekday}


def test_occurrences_step_by_the_frequency_across_a_year_boundary() -> None:
    """Crossing into a new year is where naive month arithmetic breaks."""
    rule = RecurrenceRule(frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 12, 22))
    assert rule.occurrences(after=date(2026, 12, 22), limit=3) == [
        date(2026, 12, 22),
        date(2026, 12, 29),
        date(2027, 1, 5),
    ]


@pytest.mark.parametrize("weekday", [-1, 7])
def test_weekday_outside_zero_to_six_is_rejected(weekday: int) -> None:
    with pytest.raises(ValueError, match="weekday"):
        RecurrenceRule(frequency=Frequency.WEEKLY, weekday=weekday, season_start=SEASON_START)


def test_season_end_before_season_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="season_end"):
        RecurrenceRule(
            frequency=Frequency.WEEKLY,
            weekday=1,
            season_start=SEASON_START,
            season_end=date(2026, 8, 1),
        )


def test_rules_are_immutable() -> None:
    rule = weekly()
    with pytest.raises(AttributeError):
        rule.weekday = 3  # type: ignore[misc]

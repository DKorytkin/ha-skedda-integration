"""The booking job ties recurrence, slot and window together."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.core.job import BookingJob
from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule
from custom_components.skedda_scheduler.core.window import BookingWindow

KYIV = ZoneInfo("Europe/Kyiv")


def make_job(**overrides: Any) -> BookingJob:
    defaults: dict[str, Any] = {
        "job_id": "job-1",
        "name": "Tuesday 18:00",
        "space_ids": ("2000001",),
        "start_time": time(18, 0),
        "duration_minutes": 60,
        "recurrence": RecurrenceRule(
            frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 1)
        ),
        "window": BookingWindow(window_days=14),
        "venue_timezone": "Europe/Kyiv",
        "title": "Tennis (auto)",
    }
    return BookingJob(**{**defaults, **overrides})


def test_slot_for_builds_a_venue_local_interval_of_the_configured_duration() -> None:
    start, end = make_job().slot_for(date(2026, 9, 8))
    assert start == datetime(2026, 9, 8, 18, 0, tzinfo=KYIV)
    assert end == datetime(2026, 9, 8, 19, 0, tzinfo=KYIV)


def test_slot_datetimes_carry_a_named_zone_so_the_window_accepts_them() -> None:
    """BookingWindow rejects fixed offsets; the job must not hand it one."""
    start, _ = make_job().slot_for(date(2026, 9, 8))
    assert isinstance(start.tzinfo, ZoneInfo)
    make_job().window.opens_at(start)


def test_next_slot_picks_the_first_occurrence_after_now() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    slot = make_job().next_slot(now)
    assert slot is not None
    assert slot[0].date() == date(2026, 9, 8)


def test_next_slot_skips_an_occurrence_that_has_already_started_today() -> None:
    """18:00 Kyiv is 15:00 UTC; at 16:00 UTC today's slot is gone."""
    now = datetime(2026, 9, 8, 16, 0, tzinfo=UTC)
    slot = make_job().next_slot(now)
    assert slot is not None
    assert slot[0].date() == date(2026, 9, 15)


def test_next_slot_keeps_an_occurrence_that_has_not_started_yet_today() -> None:
    now = datetime(2026, 9, 8, 14, 0, tzinfo=UTC)
    slot = make_job().next_slot(now)
    assert slot is not None
    assert slot[0].date() == date(2026, 9, 8)


def test_next_window_open_tracks_the_slots_own_time_of_day() -> None:
    """Rolling horizon: 18:00 Kyiv minus 14 days is 18:00 Kyiv, i.e. 15:00 UTC."""
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    assert make_job().next_window_open(now) == datetime(
        2026, 8, 25, 15, 0, tzinfo=UTC
    )


def test_next_window_open_resolves_dst_on_the_opening_date() -> None:
    """A slot in summer time can open while the venue is still in winter time."""
    job = make_job(
        recurrence=RecurrenceRule(
            frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 3, 31)
        )
    )
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    # Slot 2026-03-31 18:00 (+03:00) opens 2026-03-17 18:00 (+02:00) = 16:00 UTC.
    assert job.next_window_open(now) == datetime(2026, 3, 17, 16, 0, tzinfo=UTC)


def test_next_slot_is_none_once_the_season_is_over() -> None:
    job = make_job(
        recurrence=RecurrenceRule(
            frequency=Frequency.WEEKLY,
            weekday=1,
            season_start=date(2026, 9, 1),
            season_end=date(2026, 9, 8),
        )
    )
    over = datetime(2026, 9, 9, tzinfo=UTC)
    assert job.next_slot(over) is None
    assert job.next_window_open(over) is None


def test_primary_space_id_is_the_first_configured_space() -> None:
    """The rest are reserves, tried only when the primary is taken."""
    assert make_job(space_ids=("7", "8", "9")).primary_space_id == "7"


def test_space_ids_stay_strings() -> None:
    assert isinstance(make_job().primary_space_id, str)


def test_empty_space_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="space_ids"):
        make_job(space_ids=())


def test_non_positive_duration_is_rejected() -> None:
    with pytest.raises(ValueError, match="duration_minutes"):
        make_job(duration_minutes=0)


def test_unknown_timezone_is_rejected() -> None:
    """Caught at construction, not at 03:00 when a window opens."""
    with pytest.raises(ValueError, match="venue_timezone"):
        make_job(venue_timezone="Mars/Olympus")


def test_jobs_are_immutable() -> None:
    with pytest.raises(AttributeError):
        make_job().enabled = False  # type: ignore[misc]

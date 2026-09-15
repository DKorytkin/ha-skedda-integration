"""Turning stored subentry config into a domain BookingJob."""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.core.recurrence import Frequency
from custom_components.skedda_scheduler.job_factory import (
    build_job,
    describe,
    venue_timezone_for,
)

DATA: dict[str, Any] = {
    "name": "Tuesday 18:00",
    "space_id": "10293",
    "weekday": "1",
    "start_time": "18:00:00",
    "duration_minutes": 60,
    "window_days": 14,
    "frequency": "weekly",
    "season_start": "2026-09-01",
    "season_end": "2026-12-20",
    "title": "Tennis (auto)",
    "strategy": "precise",
    "notify_targets": ["notify.telegram"],
    "enabled": True,
}


def test_build_job_maps_every_stored_field() -> None:
    job = build_job("sub-1", DATA, "Europe/Kyiv")

    assert job.job_id == "sub-1"
    assert job.name == "Tuesday 18:00"
    assert job.space_ids == ("10293",)
    assert job.start_time == time(18, 0)
    assert job.duration_minutes == 60
    assert job.window.window_days == 14
    assert job.recurrence.frequency is Frequency.WEEKLY
    assert job.recurrence.weekday == 1
    assert job.recurrence.season_start == date(2026, 9, 1)
    assert job.recurrence.season_end == date(2026, 12, 20)
    assert job.venue_timezone == "Europe/Kyiv"
    assert job.title == "Tennis (auto)"
    assert job.strategy == "precise"
    assert job.notify_targets == ("notify.telegram",)
    assert job.enabled is True


def test_the_space_id_stays_a_string() -> None:
    """Skedda's ids are strings on the wire; an int would not match a space."""
    job = build_job("sub-1", {**DATA, "space_id": 10293}, "Europe/Kyiv")
    assert job.space_ids == ("10293",)


def test_selector_output_types_are_accepted() -> None:
    """A number selector hands back a float, a select selector a string."""
    job = build_job(
        "sub-1",
        {**DATA, "duration_minutes": 60.0, "window_days": 14.0, "weekday": "1"},
        "Europe/Kyiv",
    )
    assert job.duration_minutes == 60
    assert job.window.window_days == 14


def test_season_end_is_optional() -> None:
    job = build_job("sub-1", {**DATA, "season_end": None}, "Europe/Kyiv")
    assert job.recurrence.season_end is None


def test_missing_season_end_key_is_also_accepted() -> None:
    data = {key: value for key, value in DATA.items() if key != "season_end"}
    assert build_job("sub-1", data, "Europe/Kyiv").recurrence.season_end is None


def test_optional_fields_fall_back_to_the_documented_defaults() -> None:
    data = {
        key: value
        for key, value in DATA.items()
        if key not in {"strategy", "notify_targets", "enabled"}
    }
    job = build_job("sub-1", data, "Europe/Kyiv")
    assert job.strategy == "precise"
    assert job.notify_targets == ()
    assert job.enabled is True


def test_invalid_stored_config_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_job("sub-1", {**DATA, "duration_minutes": 0}, "Europe/Kyiv")


def test_an_unknown_venue_timezone_is_rejected_here_not_at_booking_time() -> None:
    with pytest.raises(ValueError, match="IANA"):
        build_job("sub-1", DATA, "Mars/Olympus")


def test_the_venue_timezone_falls_back_when_the_account_never_loaded(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Entry data holds what setup discovered; HA's own zone is the last resort."""
    assert venue_timezone_for(hass, mock_entry) == "Europe/Kyiv"

    blank = MockConfigEntry(domain=mock_entry.domain, data={})
    assert venue_timezone_for(hass, blank) == hass.config.time_zone


DATED: dict[str, Any] = {
    "name": "Tennis",
    "space_id": "2000001",
    "start_date": "2026-09-29",
    "start_time": "20:00:00",
    "duration_minutes": 60,
    "window_days": 14,
    "frequency": "once",
    "title": "Tennis",
}


def test_a_dated_job_keeps_the_date_it_was_given() -> None:
    """The form asks for a date, the way a calendar does."""
    job = build_job("sub-1", DATED, "Europe/Kyiv")

    assert job.recurrence.frequency is Frequency.ONCE
    assert job.recurrence.season_start == date(2026, 9, 29)
    assert job.recurrence.season_end == date(2026, 9, 29)


def test_a_repeating_job_takes_its_weekday_from_the_date() -> None:
    """29 September 2026 is a Tuesday, so weekly means every Tuesday.

    Deriving it removes a field that could contradict the date the user chose.
    """
    job = build_job("sub-1", {**DATED, "frequency": "weekly"}, "Europe/Kyiv")

    assert job.recurrence.weekday == 1
    assert job.recurrence.season_start == date(2026, 9, 29)
    assert job.recurrence.season_end is None


def test_a_repeating_job_keeps_an_explicit_season() -> None:
    """A court paid for until the end of autumn stops there."""
    data = {**DATED, "frequency": "weekly", "season_end": "2026-11-30"}

    job = build_job("sub-1", data, "Europe/Kyiv")

    assert job.recurrence.season_end == date(2026, 11, 30)


def test_a_job_stored_by_an_earlier_version_still_builds() -> None:
    """v0.0.1 stored a weekday and a season start instead of a date.

    Anyone who installed that release keeps their jobs across the upgrade.
    """
    job = build_job("sub-1", DATA, "Europe/Kyiv")

    assert job.recurrence.weekday == 1
    assert job.recurrence.season_start == date(2026, 9, 1)


def test_a_job_is_named_in_the_language_of_whoever_will_read_it() -> None:
    """A stored name cannot be translated afterwards, so it must start right."""
    tuesday = date(2026, 9, 29)

    assert describe("Теніс", tuesday, time(20, 0), Frequency.WEEKLY, "uk") == (
        "Теніс · щовівторка 20:00"
    )
    assert describe("Court 1", tuesday, time(20, 0), Frequency.WEEKLY, "en") == (
        "Court 1 · Tuesdays 20:00"
    )


def test_an_unfamiliar_language_falls_back_rather_than_failing() -> None:
    assert describe("Court 1", date(2026, 9, 29), time(20, 0), Frequency.WEEKLY, "fr") == (
        "Court 1 · Tuesdays 20:00"
    )


def test_a_one_off_needs_no_weekday_and_so_no_language() -> None:
    """A date reads the same in every language this ships in."""
    assert describe("Теніс", date(2026, 9, 29), time(20, 0), Frequency.ONCE, "uk") == (
        "Теніс · 29.09 20:00"
    )

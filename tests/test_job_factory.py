"""Turning stored subentry config into a domain BookingJob."""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest

from custom_components.skedda_scheduler.core.recurrence import Frequency
from custom_components.skedda_scheduler.job_factory import build_job

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

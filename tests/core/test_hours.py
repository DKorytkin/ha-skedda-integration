"""When a venue takes bookings at all. No Home Assistant, no HTTP."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.skedda_scheduler.core.provider import OpenHours, VenueRules

KYIV = ZoneInfo("Europe/Kyiv")
EIGHT_TO_TEN = OpenHours(weekdays=frozenset(range(7)), start_minute=480, end_minute=1320)


def hour(at: int, day: int = 15) -> tuple[datetime, datetime]:
    start = datetime(2026, 10, day, at, tzinfo=KYIV)
    return start, start + timedelta(hours=1)


def rules(*hours: OpenHours) -> VenueRules:
    return VenueRules(
        timezone="Europe/Kyiv",
        slot_minutes=60,
        max_days_ahead=14,
        weekly_quota_minutes=60,
        hours=hours,
    )


def test_an_hour_wholly_inside_is_open() -> None:
    assert EIGHT_TO_TEN.admits("court-1", *hour(21))


def test_an_hour_running_past_closing_is_not() -> None:
    """22:00-23:00 at a court shut at 22:00: refused by the venue every time."""
    assert not EIGHT_TO_TEN.admits("court-1", *hour(22))


def test_an_hour_before_opening_is_not() -> None:
    assert not EIGHT_TO_TEN.admits("court-1", *hour(7))


def test_hours_to_midnight_take_the_last_hour_of_the_day() -> None:
    late = OpenHours(weekdays=frozenset(range(7)), start_minute=0, end_minute=1440)

    assert late.admits("court-1", *hour(23))


def test_a_day_the_venue_is_shut_takes_nothing() -> None:
    weekdays_only = OpenHours(weekdays=frozenset(range(5)), start_minute=480, end_minute=1320)

    assert weekdays_only.admits("court-1", *hour(19, day=16))  # Friday
    assert not weekdays_only.admits("court-1", *hour(19, day=17))  # Saturday


def test_hours_for_one_court_say_nothing_about_another() -> None:
    court_one = OpenHours(
        weekdays=frozenset(range(7)), start_minute=480, end_minute=1320, space_ids=("court-1",)
    )

    assert court_one.admits("court-1", *hour(19))
    assert not court_one.admits("court-2", *hour(19))


def test_a_venue_that_published_no_hours_is_always_open() -> None:
    assert rules().is_open("court-1", *hour(3))


def test_any_one_set_of_hours_is_enough() -> None:
    court_two_late = OpenHours(
        weekdays=frozenset(range(7)), start_minute=480, end_minute=1440, space_ids=("court-2",)
    )
    venue = rules(EIGHT_TO_TEN, court_two_late)

    assert venue.is_open("court-2", *hour(22))
    assert not venue.is_open("court-1", *hour(22))

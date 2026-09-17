"""Rules, quota and candidate slots. No Home Assistant, no HTTP."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.core.provider import Booking
from custom_components.skedda_scheduler.core.watch import (
    WatchMode,
    WatchRule,
    accounts_with_quota,
    week_of,
)

KYIV = ZoneInfo("Europe/Kyiv")


def booking(day: int, hour: int, *, space: str = "court-1", minutes: int = 60) -> Booking:
    start = datetime(2026, 10, day, hour, tzinfo=KYIV)
    return Booking(
        id=f"b{day}{hour}{space}",
        space_ids=(space,),
        start=start,
        end=start + timedelta(minutes=minutes),
        title="",
        is_mine=True,
    )


def rule(**overrides: Any) -> WatchRule:
    defaults: dict[str, Any] = {
        "rule_id": "r1",
        "name": "Our evening",
        "weekdays": frozenset({1, 3, 6}),
        "not_before": time(19, 0),
        "not_after": time(21, 0),
        "space_ids": ("court-1",),
        "duration_minutes": 60,
        "venue_timezone": "Europe/Kyiv",
    }
    return WatchRule(**{**defaults, **overrides})


def test_a_week_is_the_iso_week_of_the_venue_local_start() -> None:
    """Sunday evening and the Monday after it are different weeks."""
    assert week_of(datetime(2026, 10, 4, 20, tzinfo=KYIV)) == (2026, 40)
    assert week_of(datetime(2026, 10, 5, 20, tzinfo=KYIV)) == (2026, 41)


def test_an_account_that_has_spent_the_hour_has_no_quota_left() -> None:
    spent = {"acc-a": [booking(1, 20)], "acc-b": []}

    assert accounts_with_quota(spent, 60, week_of(booking(1, 20).start)) == ("acc-b",)


def test_an_unlimited_venue_leaves_every_account_available() -> None:
    """None is not zero: None means the venue caps nothing at all."""
    spent = {"acc-a": [booking(1, 20)]}

    assert accounts_with_quota(spent, None, (2026, 40)) == ("acc-a",)


def test_a_venue_that_forbids_booking_leaves_nobody() -> None:
    assert accounts_with_quota({"acc-a": []}, 0, (2026, 40)) == ()


def test_quota_is_counted_per_week_not_in_total() -> None:
    spent = {"acc-a": [booking(1, 20)]}

    assert accounts_with_quota(spent, 60, (2026, 41)) == ("acc-a",)


def test_a_rule_refuses_an_impossible_duration() -> None:
    with pytest.raises(ValueError, match="duration"):
        rule(duration_minutes=0)


def test_a_rule_with_no_weekdays_can_never_match_and_says_so() -> None:
    with pytest.raises(ValueError, match="weekdays"):
        rule(weekdays=frozenset())


def test_a_rule_whose_hours_run_backwards_is_refused() -> None:
    with pytest.raises(ValueError, match="not_after"):
        rule(not_before=time(21, 0), not_after=time(19, 0))


def test_a_rule_refuses_a_timezone_nobody_can_resolve() -> None:
    with pytest.raises(ValueError, match="IANA"):
        rule(venue_timezone="Mars/Olympus")


def test_a_rule_can_describe_itself_to_a_sink() -> None:
    """A catch is reported through the same sinks a booking job uses."""
    watching = rule()

    assert watching.subject_id == "r1"
    assert watching.tz == KYIV
    assert watching.mode is WatchMode.BOTH


def test_an_end_date_is_kept_as_written() -> None:
    assert rule(active_until=date(2026, 12, 31)).active_until == date(2026, 12, 31)

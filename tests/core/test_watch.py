"""Rules, quota and candidate slots. No Home Assistant, no HTTP."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.core.provider import Booking
from custom_components.skedda_scheduler.core.watch import (
    Candidate,
    WatchMode,
    WatchRule,
    accounts_with_quota,
    candidates,
    is_free,
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


NOW = datetime(2026, 10, 1, 9, tzinfo=KYIV)  # Thursday morning
HORIZON = datetime(2026, 10, 15, 23, 59, tzinfo=KYIV)


def test_candidates_cover_only_the_rules_weekdays_and_hours() -> None:
    found = candidates(rule(), ["court-1"], NOW, HORIZON, slot_minutes=60)

    assert {c.start.weekday() for c in found} == {1, 3, 6}
    assert {c.start.hour for c in found} == {19, 20}


def test_a_candidate_never_runs_past_the_rules_closing_time() -> None:
    found = candidates(rule(duration_minutes=90), ["court-1"], NOW, HORIZON, slot_minutes=30)

    assert all(c.end.time() <= time(21, 0) for c in found)


def test_candidates_stop_at_the_horizon() -> None:
    found = candidates(rule(), ["court-1"], NOW, HORIZON, slot_minutes=60)

    assert max(c.end for c in found) <= HORIZON


def test_a_slot_starting_too_soon_is_not_a_candidate() -> None:
    """Three hours' notice: a court nobody can be gathered for is no prize."""
    afternoon = datetime(2026, 10, 1, 16, 30, tzinfo=KYIV)

    found = candidates(rule(min_lead_minutes=180), ["court-1"], afternoon, HORIZON, 60)
    today = {c.start for c in found if c.start.date() == afternoon.date()}

    assert datetime(2026, 10, 1, 19, tzinfo=KYIV) not in today
    assert datetime(2026, 10, 1, 20, tzinfo=KYIV) in today


def test_an_empty_space_list_means_every_space() -> None:
    found = candidates(rule(space_ids=()), ["court-1", "court-2"], NOW, HORIZON, 60)

    assert {c.space_id for c in found} == {"court-1", "court-2"}


def test_candidates_follow_the_venue_grid_not_the_hour() -> None:
    """A venue on half-hour steps offers 19:00, 19:30 and 20:00."""
    found = candidates(rule(), ["court-1"], NOW, HORIZON, slot_minutes=30)

    assert {c.start.minute for c in found} == {0, 30}


def test_a_rule_past_its_end_date_offers_nothing() -> None:
    found = candidates(rule(active_until=date(2026, 9, 30)), ["court-1"], NOW, HORIZON, 60)

    assert found == []


def test_a_slot_overlapping_any_booking_is_not_free() -> None:
    candidate = Candidate(
        "r1",
        "court-1",
        datetime(2026, 10, 1, 20, tzinfo=KYIV),
        datetime(2026, 10, 1, 21, tzinfo=KYIV),
        neighbour=False,
    )

    assert not is_free(candidate, [booking(1, 20)])


def test_a_booking_on_another_court_does_not_block_the_slot() -> None:
    candidate = Candidate(
        "r1",
        "court-2",
        datetime(2026, 10, 1, 20, tzinfo=KYIV),
        datetime(2026, 10, 1, 21, tzinfo=KYIV),
        neighbour=False,
    )

    assert is_free(candidate, [booking(1, 20, space="court-1")])


def test_a_booking_that_ends_exactly_when_the_slot_starts_does_not_block_it() -> None:
    candidate = Candidate(
        "r1",
        "court-1",
        datetime(2026, 10, 1, 20, tzinfo=KYIV),
        datetime(2026, 10, 1, 21, tzinfo=KYIV),
        neighbour=False,
    )

    assert is_free(candidate, [booking(1, 19)])

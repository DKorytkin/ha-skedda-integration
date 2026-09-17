"""Rules, quota and candidate slots. No Home Assistant, no HTTP."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.core.provider import Booking
from custom_components.skedda_scheduler.core.watch import (
    Candidate,
    Catch,
    WatchMode,
    WatchRule,
    WatchSpeed,
    accounts_with_quota,
    candidates,
    evaluate,
    has_capacity,
    interval_for,
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


ALL_SPACES = ["court-1", "court-2"]


def every_day(**overrides: Any) -> WatchRule:
    """A rule that does not filter by weekday, so a case can pick its own day."""
    return rule(weekdays=frozenset(range(7)), **overrides)


def test_the_gate_is_shut_when_every_account_has_spent_every_week() -> None:
    """Both weeks booked means no looking at all - the whole economy."""
    ours = {"acc-a": [booking(1, 20), booking(8, 20), booking(15, 20)]}

    assert not has_capacity(ours, 60, NOW, datetime(2026, 10, 15, 23, tzinfo=KYIV))


def test_the_gate_is_open_while_one_week_is_still_free() -> None:
    ours = {"acc-a": [booking(1, 20)]}

    assert has_capacity(ours, 60, NOW, HORIZON)


def test_a_day_where_we_hold_nothing_is_taken_by_the_window_rule() -> None:
    caught = evaluate([every_day()], {"acc-a": []}, [], 60, ALL_SPACES, NOW, HORIZON, 60)

    assert caught is not None
    assert caught.account_id == "acc-a"
    assert not caught.neighbour


def test_the_hour_before_ours_is_a_neighbour() -> None:
    ours = {"acc-a": [booking(1, 20)], "acc-b": []}

    caught = evaluate([every_day()], ours, [booking(1, 20)], 60, ALL_SPACES, NOW, HORIZON, 60)

    assert caught == Catch(
        rule_id="r1",
        space_id="court-1",
        start=datetime(2026, 10, 1, 19, tzinfo=KYIV),
        end=datetime(2026, 10, 1, 20, tzinfo=KYIV),
        account_id="acc-b",
        neighbour=True,
    )


def test_the_hour_after_ours_is_a_neighbour_too() -> None:
    ours = {"acc-a": [booking(1, 19)], "acc-b": []}

    caught = evaluate([every_day()], ours, [booking(1, 19)], 60, ALL_SPACES, NOW, HORIZON, 60)

    assert caught is not None
    assert caught.neighbour
    assert caught.start == datetime(2026, 10, 1, 20, tzinfo=KYIV)


def test_a_block_already_at_the_cap_rejects_a_further_neighbour() -> None:
    """Three hours is the limit, so a fourth is not wanted at any price."""
    ours = {
        "acc-a": [booking(1, 19)],
        "acc-b": [booking(1, 20)],
        "acc-c": [booking(1, 21)],
        "acc-d": [],
    }
    everyone = [booking(1, 19), booking(1, 20), booking(1, 21)]

    caught = evaluate(
        [every_day(not_before=time(17, 0), not_after=time(23, 0), mode=WatchMode.NEIGHBOUR)],
        ours,
        everyone,
        60,
        ALL_SPACES,
        NOW,
        HORIZON,
        60,
    )

    assert caught is None


def test_a_block_below_the_cap_still_grows() -> None:
    ours = {"acc-a": [booking(1, 19)], "acc-b": [booking(1, 20)], "acc-c": []}
    everyone = [booking(1, 19), booking(1, 20)]

    caught = evaluate(
        [every_day(not_before=time(17, 0), not_after=time(23, 0), mode=WatchMode.NEIGHBOUR)],
        ours,
        everyone,
        60,
        ALL_SPACES,
        NOW,
        HORIZON,
        60,
    )

    assert caught is not None
    assert caught.start in {
        datetime(2026, 10, 1, 18, tzinfo=KYIV),
        datetime(2026, 10, 1, 21, tzinfo=KYIV),
    }


def test_neighbour_mode_ignores_a_day_where_we_hold_nothing() -> None:
    caught = evaluate(
        [every_day(mode=WatchMode.NEIGHBOUR)], {"acc-a": []}, [], 60, ALL_SPACES, NOW, HORIZON, 60
    )

    assert caught is None


def test_window_mode_ignores_a_day_where_we_already_hold_something() -> None:
    """Only the empty day is the window rule's business."""
    ours = {"acc-a": [booking(1, 20)], "acc-b": []}
    only_thursday = rule(weekdays=frozenset({3}), mode=WatchMode.WINDOW)

    caught = evaluate(
        [only_thursday],
        ours,
        [booking(1, 20)],
        60,
        ALL_SPACES,
        NOW,
        datetime(2026, 10, 2, tzinfo=KYIV),
        60,
    )

    assert caught is None


def test_a_neighbour_beats_a_lone_hour_on_another_day() -> None:
    """Growing the block is worth more than a court by itself."""
    ours = {"acc-a": [booking(1, 20)], "acc-b": []}

    caught = evaluate([every_day()], ours, [booking(1, 20)], 60, ALL_SPACES, NOW, HORIZON, 60)

    assert caught is not None and caught.neighbour


def test_a_neighbour_on_another_court_needs_permission() -> None:
    ours = {"acc-a": [booking(1, 20, space="court-1")], "acc-b": []}
    everyone = [booking(1, 20, space="court-1"), booking(1, 19, space="court-1")]
    strict = every_day(space_ids=(), mode=WatchMode.NEIGHBOUR)
    permissive = every_day(space_ids=(), mode=WatchMode.NEIGHBOUR, allow_other_court=True)

    one_day = datetime(2026, 10, 2, tzinfo=KYIV)

    assert evaluate([strict], ours, everyone, 60, ALL_SPACES, NOW, one_day, 60) is None
    caught = evaluate([permissive], ours, everyone, 60, ALL_SPACES, NOW, one_day, 60)
    assert caught is not None and caught.space_id == "court-2"


def test_the_account_holding_the_block_is_not_asked_to_pay_twice() -> None:
    """One player keeps one block, and their hour this week is already spent."""
    ours = {"acc-a": [booking(1, 20)], "acc-b": []}

    caught = evaluate([every_day()], ours, [booking(1, 20)], 60, ALL_SPACES, NOW, HORIZON, 60)

    assert caught is not None and caught.account_id == "acc-b"


def test_nothing_is_taken_when_that_week_has_no_quota_left() -> None:
    ours = {"acc-a": [booking(1, 20)]}
    only_thursday = rule(weekdays=frozenset({3}))

    caught = evaluate(
        [only_thursday],
        ours,
        [booking(1, 20)],
        60,
        ALL_SPACES,
        NOW,
        datetime(2026, 10, 2, tzinfo=KYIV),
        60,
    )

    assert caught is None


def test_a_disabled_rule_is_not_consulted() -> None:
    assert (
        evaluate([every_day(enabled=False)], {"acc-a": []}, [], 60, ALL_SPACES, NOW, HORIZON, 60)
        is None
    )


def test_the_slot_nearest_the_middle_of_the_window_wins() -> None:
    """The edges of a wide window are what nobody asked for.

    Two equally central slots tie, and the tie breaks on the earlier start, so
    a group that wants one of them exactly should narrow the hours.
    """
    caught = evaluate(
        [every_day(not_before=time(18, 0), not_after=time(22, 0))],
        {"acc-a": []},
        [],
        60,
        ALL_SPACES,
        NOW,
        HORIZON,
        60,
    )

    assert caught is not None and caught.start.hour in {19, 20}


def test_the_courts_are_tried_in_the_order_the_rule_lists_them() -> None:
    caught = evaluate(
        [every_day(space_ids=("court-2", "court-1"))],
        {"acc-a": []},
        [],
        60,
        ALL_SPACES,
        NOW,
        HORIZON,
        60,
    )

    assert caught is not None and caught.space_id == "court-2"


def test_a_taken_slot_is_never_chosen() -> None:
    theirs = Booking(
        id="theirs",
        space_ids=("court-1",),
        start=datetime(2026, 10, 1, 20, tzinfo=KYIV),
        end=datetime(2026, 10, 1, 21, tzinfo=KYIV),
        title="",
        is_mine=False,
    )

    caught = evaluate(
        [every_day(space_ids=("court-1",), not_before=time(20, 0), not_after=time(21, 0))],
        {"acc-a": []},
        [theirs],
        60,
        ALL_SPACES,
        NOW,
        datetime(2026, 10, 2, tzinfo=KYIV),
        60,
    )

    assert caught is None


def test_the_watch_looks_hardest_the_day_of_play() -> None:
    now = datetime(2026, 10, 1, 9, tzinfo=KYIV)

    assert interval_for(WatchSpeed.STEPPED, now + timedelta(days=5), now) == timedelta(minutes=15)
    assert interval_for(WatchSpeed.STEPPED, now + timedelta(days=1), now) == timedelta(minutes=5)
    assert interval_for(WatchSpeed.STEPPED, now + timedelta(hours=4), now) == timedelta(minutes=2)


def test_a_calm_rule_costs_half_of_a_stepped_one() -> None:
    now = datetime(2026, 10, 1, 9, tzinfo=KYIV)

    assert interval_for(WatchSpeed.CALM, now + timedelta(days=5), now) == timedelta(minutes=30)
    assert interval_for(WatchSpeed.FAST, now + timedelta(days=5), now) == timedelta(minutes=5)


def test_nothing_to_watch_asks_for_no_polling_at_all() -> None:
    assert interval_for(WatchSpeed.FAST, None, NOW) is None


def test_a_day_that_has_already_started_is_treated_as_imminent() -> None:
    """The rule's hours may still be ahead even when the date is today."""
    now = datetime(2026, 10, 1, 20, tzinfo=KYIV)

    assert interval_for(WatchSpeed.STEPPED, now - timedelta(hours=1), now) == timedelta(minutes=2)

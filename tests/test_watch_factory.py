"""Subentry data is what a form can store; a rule is typed."""

from __future__ import annotations

from datetime import date, time

import pytest

from custom_components.skedda_scheduler.core.watch import WatchMode, WatchSpeed
from custom_components.skedda_scheduler.watch_factory import build_rule

DATA = {
    "name": "Our evening",
    "weekdays": ["tue", "thu", "sun"],
    "not_before": "19:00:00",
    "not_after": "21:00:00",
    "space_ids": ["court-1"],
    "duration_minutes": 60,
}


def test_a_rule_is_built_from_what_the_form_stored() -> None:
    rule = build_rule("sub-1", DATA, "Europe/Kyiv")

    assert rule.rule_id == "sub-1"
    assert rule.weekdays == frozenset({1, 3, 6})
    assert rule.not_before == time(19, 0)
    assert rule.space_ids == ("court-1",)
    assert rule.mode is WatchMode.BOTH
    assert rule.speed is WatchSpeed.STEPPED
    assert rule.book is True
    assert rule.enabled is True


def test_an_end_date_is_read_as_a_date() -> None:
    rule = build_rule("sub-1", {**DATA, "season_end": "2026-12-31"}, "Europe/Kyiv")

    assert rule.active_until == date(2026, 12, 31)


def test_an_empty_end_date_means_no_end() -> None:
    """A cleared date field stores an empty string, not nothing."""
    rule = build_rule("sub-1", {**DATA, "season_end": ""}, "Europe/Kyiv")

    assert rule.active_until is None


def test_the_form_may_store_real_types_too() -> None:
    """Some selectors hand back a time, not a string; both must work."""
    rule = build_rule(
        "sub-1",
        {**DATA, "not_before": time(18, 0), "season_end": date(2026, 11, 1)},
        "Europe/Kyiv",
    )

    assert rule.not_before == time(18, 0)
    assert rule.active_until == date(2026, 11, 1)


def test_an_empty_court_list_is_kept_as_any_court() -> None:
    rule = build_rule("sub-1", {**DATA, "space_ids": []}, "Europe/Kyiv")

    assert rule.space_ids == ()


def test_a_rule_with_no_weekdays_is_refused_rather_than_silently_idle() -> None:
    with pytest.raises(ValueError, match="weekdays"):
        build_rule("sub-1", {**DATA, "weekdays": []}, "Europe/Kyiv")


def test_the_optional_fields_carry_their_defaults() -> None:
    rule = build_rule("sub-1", DATA, "Europe/Kyiv")

    assert rule.max_block_minutes == 180
    assert rule.min_lead_minutes == 180
    assert rule.allow_other_court is False
    assert rule.notify_targets == ()


def test_what_the_form_stored_overrides_every_default() -> None:
    rule = build_rule(
        "sub-1",
        {
            **DATA,
            "mode": "neighbour",
            "speed": "fast",
            "max_block_minutes": 120,
            "min_lead_minutes": 60,
            "allow_other_court": True,
            "book": False,
            "enabled": False,
            "notify_targets": ["notify.mobile"],
        },
        "Europe/Kyiv",
    )

    assert rule.mode is WatchMode.NEIGHBOUR
    assert rule.speed is WatchSpeed.FAST
    assert rule.max_block_minutes == 120
    assert rule.min_lead_minutes == 60
    assert rule.allow_other_court is True
    assert rule.book is False
    assert rule.enabled is False
    assert rule.notify_targets == ("notify.mobile",)

"""Attempt timing and retry policy, as pure logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.skedda_scheduler.core.result import AttemptStatus
from custom_components.skedda_scheduler.core.strategy import (
    MAX_ATTEMPTS,
    RETRYABLE,
    TERMINAL,
    ImmediateStrategy,
    PreciseStrategy,
    build_strategy,
    should_retry,
)

# A rolling window opens at the slot's own time of day, so this is a realistic
# instant rather than a tidy midnight.
OPENS_AT = datetime(2026, 9, 15, 15, 0, 0, tzinfo=UTC)


def test_precise_arms_two_minutes_before_the_window_opens() -> None:
    """The lead time exists to warm the session and sync the clock."""
    assert PreciseStrategy().plan(OPENS_AT).arm_at == OPENS_AT - timedelta(seconds=120)


def test_precise_first_shot_lands_just_before_the_window_opens() -> None:
    """Deliberately early: the request must arrive as the window flips open."""
    plan = PreciseStrategy().plan(OPENS_AT)
    assert plan.first_fire_at == OPENS_AT - timedelta(milliseconds=150)


def test_precise_fires_a_burst_at_the_configured_spacing() -> None:
    plan = PreciseStrategy(burst_count=3, burst_spacing_ms=250, lead_ms=150).plan(OPENS_AT)
    assert plan.fire_times == (
        OPENS_AT - timedelta(milliseconds=150),
        OPENS_AT + timedelta(milliseconds=100),
        OPENS_AT + timedelta(milliseconds=350),
    )


def test_fire_times_are_strictly_increasing() -> None:
    times = PreciseStrategy().plan(OPENS_AT).fire_times
    assert list(times) == sorted(times)
    assert len(set(times)) == len(times)


def test_precise_burst_stays_within_the_per_run_attempt_ceiling() -> None:
    assert len(PreciseStrategy().plan(OPENS_AT).fire_times) <= MAX_ATTEMPTS


def test_immediate_fires_at_the_open_instant_then_backs_off() -> None:
    plan = ImmediateStrategy().plan(OPENS_AT)
    assert plan.fire_times == (
        OPENS_AT,
        OPENS_AT + timedelta(seconds=1),
        OPENS_AT + timedelta(seconds=3),
    )


def test_last_fire_at_reports_the_end_of_the_burst() -> None:
    plan = PreciseStrategy(burst_count=2, burst_spacing_ms=250, lead_ms=150).plan(OPENS_AT)
    assert plan.last_fire_at == OPENS_AT + timedelta(milliseconds=100)


def test_build_strategy_resolves_the_configured_names() -> None:
    assert isinstance(build_strategy("precise"), PreciseStrategy)
    assert isinstance(build_strategy("immediate"), ImmediateStrategy)


def test_build_strategy_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("shotgun")


@pytest.mark.parametrize("burst_count", [0, MAX_ATTEMPTS + 1, 99])
def test_burst_count_outside_the_ceiling_is_rejected(burst_count: int) -> None:
    with pytest.raises(ValueError, match="burst_count"):
        PreciseStrategy(burst_count=burst_count)


@pytest.mark.parametrize(
    "kwargs",
    [{"prewarm_seconds": -1}, {"lead_ms": -1}, {"burst_spacing_ms": -1}],
    ids=["prewarm", "lead", "spacing"],
)
def test_negative_timings_are_rejected(kwargs: dict[str, int]) -> None:
    """A negative spacing would order the burst backwards."""
    with pytest.raises(ValueError):
        PreciseStrategy(**kwargs)  # type: ignore[arg-type]


def test_immediate_rejects_an_empty_retry_schedule() -> None:
    with pytest.raises(ValueError, match="retry_delays_ms"):
        ImmediateStrategy(retry_delays_ms=())


def test_immediate_rejects_more_retries_than_the_ceiling() -> None:
    with pytest.raises(ValueError, match="retry_delays_ms"):
        ImmediateStrategy(retry_delays_ms=tuple(range(MAX_ATTEMPTS + 1)))


@pytest.mark.parametrize(
    ("status", "retry"),
    [
        (AttemptStatus.SUCCESS, False),
        (AttemptStatus.TOO_EARLY, True),
        (AttemptStatus.CONNECTION_ERROR, True),
        (AttemptStatus.RATE_LIMITED, True),
        (AttemptStatus.SLOT_TAKEN, False),
        (AttemptStatus.QUOTA_EXCEEDED, False),
        (AttemptStatus.WINDOW_CLOSED, False),
        (AttemptStatus.AUTH_FAILED, False),
        (AttemptStatus.CONTRACT_ERROR, False),
    ],
)
def test_retry_policy_per_status(status: AttemptStatus, retry: bool) -> None:
    """Each decision is a separate judgement, not a default.

    QUOTA_EXCEEDED and WINDOW_CLOSED are venue rules: no amount of retrying
    changes them, and hammering the server would be rude as well as useless.
    SLOT_TAKEN is lost to someone else - only a different space could help,
    which is a v0.2 concern.
    """
    assert should_retry(status) is retry


def test_every_attempt_status_is_explicitly_classified() -> None:
    """Adding a status must force a decision, not inherit a silent default.

    `should_retry` alone cannot catch an omission - an unclassified status just
    falls out as "do not retry", quietly abandoning bookings a single retry
    would have won. Comparing the two sets against the enum is what fails.
    """
    assert set(AttemptStatus) == RETRYABLE | TERMINAL
    assert not RETRYABLE & TERMINAL


def test_immediate_rejects_a_negative_prewarm() -> None:
    with pytest.raises(ValueError, match="prewarm_seconds"):
        ImmediateStrategy(prewarm_seconds=-1)


def test_immediate_rejects_a_negative_delay() -> None:
    """A negative delay would fire before the window and always be TOO_EARLY."""
    with pytest.raises(ValueError, match="negative delays"):
        ImmediateStrategy(retry_delays_ms=(0, -500))

"""Booking outcome value objects."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from custom_components.skedda_scheduler.core.result import (
    AttemptStatus,
    BookingAttempt,
    BookingOutcome,
)

SLOT_START = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
SLOT_END = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


def make_outcome(succeeded: bool, *statuses: AttemptStatus) -> BookingOutcome:
    attempts = tuple(
        BookingAttempt(i + 1, SLOT_START, status, 12.5) for i, status in enumerate(statuses)
    )
    return BookingOutcome(
        job_id="job-1",
        succeeded=succeeded,
        booking_id="300000001" if succeeded else None,
        space_id="2000001" if succeeded else None,
        slot_start=SLOT_START,
        slot_end=SLOT_END,
        attempts=attempts,
        finished_at=SLOT_START,
    )


def test_failure_reason_is_none_when_the_booking_succeeded() -> None:
    outcome = make_outcome(True, AttemptStatus.TOO_EARLY, AttemptStatus.SUCCESS)
    assert outcome.failure_reason is None


def test_failure_reason_reports_the_last_attempt_status() -> None:
    outcome = make_outcome(False, AttemptStatus.TOO_EARLY, AttemptStatus.SLOT_TAKEN)
    assert outcome.failure_reason == "slot_taken"


def test_failure_reason_is_unknown_when_no_attempt_was_made() -> None:
    """A job disabled or aborted before firing still needs a reportable state."""
    assert make_outcome(False).failure_reason == "unknown"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AttemptStatus.QUOTA_EXCEEDED, "quota_exceeded"),
        (AttemptStatus.WINDOW_CLOSED, "window_closed"),
    ],
)
def test_venue_rule_failures_are_their_own_reasons(status: AttemptStatus, expected: str) -> None:
    """Reporting these as contract_error would blame Skedda for a venue rule.

    Both are settings the venue owner chose, and the user's fix differs for
    each: shorten the booking, or wait for the window.
    """
    assert make_outcome(False, status).failure_reason == expected


def test_as_dict_is_json_serialisable_and_keeps_the_attempt_count() -> None:
    payload = make_outcome(True, AttemptStatus.SUCCESS).as_dict()
    assert json.loads(json.dumps(payload))
    assert payload["attempts"] == 1
    assert payload["booking_id"] == "300000001"
    assert payload["failure_reason"] is None


def test_as_dict_renders_times_as_iso_strings() -> None:
    """The payload goes into an HA event and a persisted store; both need JSON."""
    payload = make_outcome(False, AttemptStatus.SLOT_TAKEN).as_dict()
    assert payload["slot_start"] == "2026-09-28T08:00:00+00:00"
    assert payload["slot_end"] == "2026-09-28T09:00:00+00:00"


def test_space_ids_stay_strings_end_to_end() -> None:
    """Skedda's ids are strings; coercing loses the round trip to the API."""
    assert isinstance(make_outcome(True, AttemptStatus.SUCCESS).space_id, str)


def test_an_attempt_can_carry_a_human_readable_detail() -> None:
    """The server's own message is the most useful thing in a failure report."""
    attempt = BookingAttempt(
        1, SLOT_START, AttemptStatus.SLOT_TAKEN, 250.0, detail="conflicts with..."
    )
    assert attempt.detail == "conflicts with..."


def test_outcomes_are_immutable() -> None:
    """History is persisted and shared between entities; nothing may mutate it."""
    outcome = make_outcome(True, AttemptStatus.SUCCESS)
    with pytest.raises(AttributeError):
        outcome.succeeded = False  # type: ignore[misc]

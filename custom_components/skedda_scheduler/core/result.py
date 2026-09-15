"""What happened when we tried to book. Pure data, no Home Assistant.

These objects are the integration's record of a booking run: they feed the
sensors, the persisted history, the diagnostics dump and the
`skedda_scheduler_booking_succeeded` / `_failed` events. Everything here must
stay JSON-serialisable and immutable - the same outcome is read by several
entities at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class AttemptStatus(StrEnum):
    """Why one attempt ended.

    The distinctions are not cosmetic: each value implies a different recovery,
    and the value is what a user sees in `failure_reason`. The retry policy
    that consumes them lives in core/strategy.py.

    SLOT_TAKEN is the only failure where trying a different space can help.
    QUOTA_EXCEEDED and WINDOW_CLOSED are venue rules - permanent for the run,
    and not Skedda's fault, so they must not be reported as CONTRACT_ERROR.
    """

    SUCCESS = "success"
    SLOT_TAKEN = "slot_taken"
    TOO_EARLY = "too_early"
    QUOTA_EXCEEDED = "quota_exceeded"
    WINDOW_CLOSED = "window_closed"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    CONTRACT_ERROR = "contract_error"
    CONNECTION_ERROR = "connection_error"


@dataclass(frozen=True, slots=True)
class BookingAttempt:
    """One request fired at Skedda.

    `detail` carries the server's own message where there is one; it is by far
    the most useful thing in a failure report, so it is kept verbatim rather
    than reduced to a code.
    """

    attempt_no: int
    fired_at: datetime
    status: AttemptStatus
    latency_ms: float
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class BookingOutcome:
    """The result of one run of one booking job."""

    job_id: str
    succeeded: bool
    booking_id: str | None
    # Skedda's ids are strings on the wire; keeping them strings preserves the
    # round trip back to the API.
    space_id: str | None
    slot_start: datetime
    slot_end: datetime
    attempts: tuple[BookingAttempt, ...]
    finished_at: datetime
    #: Why the run fired nothing at all, when it fired nothing. A run can end
    #: without an attempt: the season is over, or the slot is already ours.
    no_attempt_reason: str | None = None

    @property
    def failure_reason(self) -> str | None:
        """A stable machine-readable reason, or None when the booking landed."""
        if self.succeeded:
            return None
        if not self.attempts:
            # A job can finish without firing: the season is over, or the slot
            # is already booked. Still needs a reportable state.
            return self.no_attempt_reason or "unknown"
        return str(self.attempts[-1].status)

    def as_record(self) -> dict[str, Any]:
        """Render for the persisted history, keeping every attempt.

        The event payload collapses attempts to a count. The store is where a
        lost race has to be explainable weeks later, and only the individual
        timings and the server's own words can answer that.
        """
        return {
            **self.as_dict(),
            "attempt_log": [
                {
                    "attempt_no": attempt.attempt_no,
                    "fired_at": attempt.fired_at.isoformat(),
                    "status": str(attempt.status),
                    "latency_ms": attempt.latency_ms,
                    "detail": attempt.detail,
                }
                for attempt in self.attempts
            ],
        }

    def as_dict(self) -> dict[str, Any]:
        """Render for an HA event payload, the persisted store and diagnostics.

        Attempts collapse to a count: the full list is retained in the store,
        but an event payload carrying every attempt would be unwieldy in
        automations and in the logbook.
        """
        return {
            "job_id": self.job_id,
            "succeeded": self.succeeded,
            "booking_id": self.booking_id,
            "space_id": self.space_id,
            "slot_start": self.slot_start.isoformat(),
            "slot_end": self.slot_end.isoformat(),
            "attempts": len(self.attempts),
            "failure_reason": self.failure_reason,
            "finished_at": self.finished_at.isoformat(),
        }

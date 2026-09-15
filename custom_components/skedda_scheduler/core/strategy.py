"""How hard do we try, when exactly, and when do we stop?

Two decisions live here, both pure:

* **Timing** - a strategy turns "the window opens at T" into a schedule of
  instants. Keeping it free of I/O is what makes the millisecond behaviour
  testable; the sleeping belongs to scheduler.py.
* **Retry policy** - whether a given attempt status is worth another shot.
  This is the rule that decides whether a booking is lost, so it is kept in
  core where it can be tested without Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .result import AttemptStatus

# Mirrors MAX_ATTEMPTS_PER_RUN in const.py. Duplicated rather than imported
# because core/ must stay free of the Home Assistant package layout.
MAX_ATTEMPTS = 8

# Statuses worth firing again at the same space.
#
# TOO_EARLY: our clock was slightly ahead of the venue's - the next shot in the
#   burst is exactly the remedy.
# CONNECTION_ERROR: nothing was learned about the slot; the request never landed.
# RATE_LIMITED: Skedda is throttling, but the slot may still be free. The
#   scheduler backs off before the next shot.
RETRYABLE: frozenset[AttemptStatus] = frozenset(
    {
        AttemptStatus.TOO_EARLY,
        AttemptStatus.CONNECTION_ERROR,
        AttemptStatus.RATE_LIMITED,
    }
)

# Statuses where another shot cannot help. Listed explicitly rather than left
# to fall through, so that adding a status to AttemptStatus forces a decision
# here instead of silently inheriting "give up".
#
# SLOT_TAKEN: someone else holds it. Only a *different space* could succeed,
#   which v0.1 does not attempt - see the roadmap.
# QUOTA_EXCEEDED / WINDOW_CLOSED: venue rules. Retrying changes nothing and
#   hammering the server for a rule violation would be rude as well as futile.
# AUTH_FAILED: needs the user to fix credentials.
# CONTRACT_ERROR: Skedda changed something; retrying a request we no longer
#   understand risks doing the wrong thing repeatedly.
TERMINAL: frozenset[AttemptStatus] = frozenset(
    {
        AttemptStatus.SUCCESS,
        AttemptStatus.SLOT_TAKEN,
        AttemptStatus.QUOTA_EXCEEDED,
        AttemptStatus.WINDOW_CLOSED,
        AttemptStatus.AUTH_FAILED,
        AttemptStatus.CONTRACT_ERROR,
    }
)


def should_retry(status: AttemptStatus) -> bool:
    """Whether firing again at the same space could still win the slot."""
    return status in RETRYABLE


@dataclass(frozen=True, slots=True)
class AttemptPlan:
    """When to wake up, and the instants at which to fire."""

    arm_at: datetime
    fire_times: tuple[datetime, ...]

    @property
    def first_fire_at(self) -> datetime:
        return self.fire_times[0]

    @property
    def last_fire_at(self) -> datetime:
        return self.fire_times[-1]


class BookingStrategy(Protocol):
    def plan(self, opens_at: datetime) -> AttemptPlan: ...


@dataclass(frozen=True, slots=True)
class PreciseStrategy:
    """Fire a tight burst straddling the instant the window opens.

    The first shot goes out slightly *early* on purpose: what matters is when
    the request arrives at Skedda, not when it leaves us. `lead_ms` covers the
    network leg, and the remaining shots cover the residual clock error.
    """

    prewarm_seconds: int = 120
    lead_ms: int = 150
    burst_count: int = 5
    burst_spacing_ms: int = 250

    def __post_init__(self) -> None:
        if not 1 <= self.burst_count <= MAX_ATTEMPTS:
            raise ValueError(f"burst_count must be 1..{MAX_ATTEMPTS}, got {self.burst_count}")
        for name in ("prewarm_seconds", "lead_ms", "burst_spacing_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")

    def plan(self, opens_at: datetime) -> AttemptPlan:
        first = opens_at - timedelta(milliseconds=self.lead_ms)
        fire_times = tuple(
            first + timedelta(milliseconds=self.burst_spacing_ms * index)
            for index in range(self.burst_count)
        )
        return AttemptPlan(
            arm_at=opens_at - timedelta(seconds=self.prewarm_seconds),
            fire_times=fire_times,
        )


@dataclass(frozen=True, slots=True)
class ImmediateStrategy:
    """Fire on the open instant and back off. For venues with no real race."""

    prewarm_seconds: int = 30
    retry_delays_ms: tuple[int, ...] = (0, 1000, 3000)

    def __post_init__(self) -> None:
        if not 1 <= len(self.retry_delays_ms) <= MAX_ATTEMPTS:
            raise ValueError(
                f"retry_delays_ms must hold 1..{MAX_ATTEMPTS} entries, "
                f"got {len(self.retry_delays_ms)}"
            )
        if self.prewarm_seconds < 0:
            raise ValueError("prewarm_seconds must not be negative")
        if any(delay < 0 for delay in self.retry_delays_ms):
            raise ValueError("retry_delays_ms must not contain negative delays")

    def plan(self, opens_at: datetime) -> AttemptPlan:
        return AttemptPlan(
            arm_at=opens_at - timedelta(seconds=self.prewarm_seconds),
            fire_times=tuple(
                opens_at + timedelta(milliseconds=delay) for delay in self.retry_delays_ms
            ),
        )


_STRATEGIES: dict[str, type[PreciseStrategy] | type[ImmediateStrategy]] = {
    "precise": PreciseStrategy,
    "immediate": ImmediateStrategy,
}


def build_strategy(name: str) -> BookingStrategy:
    try:
        return _STRATEGIES[name]()
    except KeyError as err:
        raise ValueError(f"unknown strategy {name!r}") from err

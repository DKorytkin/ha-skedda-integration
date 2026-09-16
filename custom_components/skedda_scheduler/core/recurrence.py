"""When does this job recur?

Occurrences are anchored on the season start, never on "today". That is the
whole point: a biweekly job asked for its next date must land on the same
fortnight whether Home Assistant has been running for a month or restarted
thirty seconds ago.

Dates only, no times and no timezones. Which wall-clock instant a date maps to
is the venue's business and is applied in core/job.py, which knows the venue
timezone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class Frequency(StrEnum):
    #: A single date. The common case: most people want one court, once.
    ONCE = "once"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"


# Keyed by the enum member rather than its value: renaming a member should be a
# type error, not a KeyError at the moment a booking window opens.
_STEP_DAYS: dict[Frequency, int] = {
    # A one-off never steps; the value is unused but must exist so that
    # adding a frequency without deciding its step is a type error here
    # rather than a KeyError the night a window opens.
    Frequency.ONCE: 0,
    Frequency.WEEKLY: 7,
    Frequency.BIWEEKLY: 14,
}


@dataclass(frozen=True, slots=True)
class RecurrenceRule:
    """A repeating slot: "every other Tuesday from September until May"."""

    frequency: Frequency
    #: 0 = Monday .. 6 = Sunday, matching `date.weekday()`.
    weekday: int
    season_start: date
    #: Inclusive. None means the series runs indefinitely.
    season_end: date | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError(f"weekday must be 0..6 (Monday..Sunday), got {self.weekday}")
        if self.season_end is not None and self.season_end < self.season_start:
            raise ValueError(
                f"season_end {self.season_end} must not precede season_start {self.season_start}"
            )

    @property
    def repeats(self) -> bool:
        return self.frequency is not Frequency.ONCE

    @property
    def step(self) -> timedelta:
        return timedelta(days=_STEP_DAYS[self.frequency])

    @property
    def first_occurrence(self) -> date:
        """The first matching date on or after the season start.

        A one-off is its season start exactly: the date was chosen from a
        calendar, so shifting it to the nearest weekday would move the very
        thing the user picked.
        """
        if not self.repeats:
            return self.season_start
        shift = (self.weekday - self.season_start.weekday()) % 7
        return self.season_start + timedelta(days=shift)

    def occurrences(self, after: date, limit: int) -> list[date]:
        """Up to `limit` occurrence dates on or after `after`, ascending.

        Jumping straight to the right step keeps the phase exact: walking
        forward one step at a time from the season start would give the same
        answer but costs a loop proportional to the age of the season.
        """
        if not self.repeats:
            first = self.first_occurrence
            return [first] if first >= after else []
        step_days = _STEP_DAYS[self.frequency]
        current = self.first_occurrence
        if current < after:
            missed = (after - current).days
            # Ceiling division: land on the first step at or beyond `after`.
            whole_steps = -(-missed // step_days)
            current += timedelta(days=whole_steps * step_days)
        found: list[date] = []
        while len(found) < limit:
            if self.season_end is not None and current > self.season_end:
                break
            found.append(current)
            current += self.step
        return found

    def next_occurrence(self, after: date) -> date | None:
        """The next occurrence on or after `after`, or None past the season."""
        found = self.occurrences(after=after, limit=1)
        return found[0] if found else None

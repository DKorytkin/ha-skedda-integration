"""When does the booking window for a given slot open?

Skedda enforces a **rolling horizon**: a slot starting at T becomes bookable at
T minus the venue's window, at the slot's own wall-clock time. It is not a daily
unlock at midnight, and there is no venue-wide "opening time" to configure.
Confirmed 2026-09-15 against the live venue; see the contract doc.

That makes the timezone handling load-bearing rather than incidental. A slot and
its opening moment are two weeks apart and can sit on opposite sides of a
daylight-saving switch, so the offset must be resolved for the *opening* date,
not the slot's. Getting it wrong fires an hour late, which loses the slot.

All arithmetic happens in the venue's timezone, carried on `slot_start_local`.
The result is UTC because everything downstream schedules in UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class BookingWindow:
    """How far ahead a venue lets its members book."""

    window_days: int

    def __post_init__(self) -> None:
        if self.window_days < 0:
            raise ValueError(f"window_days must not be negative, got {self.window_days}")

    def opens_at(self, slot_start_local: datetime) -> datetime:
        """The UTC instant at which `slot_start_local` becomes bookable."""
        venue_tz = self._require_named_zone(slot_start_local)
        # Subtract calendar days from the wall clock, then resolve the offset.
        # Doing it the other way round - subtracting from the UTC instant - would
        # shift the local time by an hour whenever a DST switch falls in between.
        naive = slot_start_local.replace(tzinfo=None) - timedelta(days=self.window_days)
        return naive.replace(tzinfo=venue_tz).astimezone(UTC)

    def is_open(self, slot_start_local: datetime, now: datetime) -> bool:
        """Whether the window for this slot has opened by `now`."""
        return now >= self.opens_at(slot_start_local)

    @staticmethod
    def _require_named_zone(value: datetime) -> ZoneInfo:
        if value.tzinfo is None:
            raise ValueError(
                "slot_start_local must be timezone-aware; a naive datetime means "
                "the venue timezone was lost upstream"
            )
        if not isinstance(value.tzinfo, ZoneInfo):
            raise ValueError(
                "slot_start_local must carry a named timezone (ZoneInfo). A fixed "
                "offset cannot express daylight saving, so the opening instant "
                "would be an hour out for part of the year."
            )
        return value.tzinfo

"""One configured booking job: what to book, when, and how often.

This is where the venue timezone is applied. The recurrence rule deals in bare
dates and the window in instants; the job is what turns "every Tuesday at 18:00"
into a concrete pair of venue-local datetimes, and so it is the only place that
needs to know which zone the venue sits in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .recurrence import RecurrenceRule
from .window import BookingWindow

# How many occurrences next_slot will walk before giving up. Only the first
# future one is ever wanted; the allowance exists so that a job whose recent
# occurrences have already started still finds the one after them.
_SEARCH_LIMIT = 8


@dataclass(frozen=True, slots=True)
class BookingJob:
    """A standing instruction: book this space, at this time, on this cadence."""

    job_id: str
    name: str
    #: Primary space first, then reserves to try when the primary is taken.
    #: Strings, matching Skedda's wire format.
    space_ids: tuple[str, ...]
    #: Venue-local wall clock.
    start_time: time
    duration_minutes: int
    recurrence: RecurrenceRule
    window: BookingWindow
    #: IANA name, e.g. "Europe/Kyiv". Read from the venue's own settings.
    venue_timezone: str
    title: str
    strategy: str = "precise"
    notify_targets: tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.space_ids:
            raise ValueError("space_ids must contain at least one space")
        if self.duration_minutes <= 0:
            raise ValueError(f"duration_minutes must be positive, got {self.duration_minutes}")
        try:
            ZoneInfo(self.venue_timezone)
        except (ZoneInfoNotFoundError, ValueError) as err:
            # Fail here rather than in the middle of the night when a window
            # opens and there is no one to read the traceback.
            raise ValueError(
                f"venue_timezone {self.venue_timezone!r} is not a known IANA zone"
            ) from err

    @property
    def subject_id(self) -> str:
        return self.job_id

    @property
    def tz(self) -> ZoneInfo:
        # ZoneInfo caches instances internally, so this is cheap to recompute
        # and keeps the dataclass frozen and hashable.
        return ZoneInfo(self.venue_timezone)

    @property
    def primary_space_id(self) -> str:
        return self.space_ids[0]

    def slot_for(self, occurrence: date) -> tuple[datetime, datetime]:
        """The venue-local interval this job would book on `occurrence`."""
        start = datetime.combine(occurrence, self.start_time).replace(tzinfo=self.tz)
        return start, start + timedelta(minutes=self.duration_minutes)

    def next_slot(self, now_utc: datetime) -> tuple[datetime, datetime] | None:
        """The next slot that has not started yet, or None past the season."""
        today_local = now_utc.astimezone(self.tz).date()
        for occurrence in self.recurrence.occurrences(today_local, _SEARCH_LIMIT):
            start, end = self.slot_for(occurrence)
            if start > now_utc:
                return start, end
        return None

    def next_window_open(self, now_utc: datetime) -> datetime | None:
        """The UTC instant at which the next slot becomes bookable."""
        slot = self.next_slot(now_utc)
        if slot is None:
            return None
        return self.window.opens_at(slot[0])

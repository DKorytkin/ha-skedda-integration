"""Rules for taking a slot somebody else gave up.

Everything here is a pure function of the rules, the bookings and the clock.
The Home Assistant layer supplies snapshots and carries out what this decides;
it makes no decisions of its own, which is what keeps the interesting part
testable as a table of cases.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .provider import Booking


class WatchMode(StrEnum):
    """What counts as a catch worth taking."""

    #: Only a slot that touches one of ours, growing a block.
    NEIGHBOUR = "neighbour"
    #: Only a day where we hold nothing at all.
    WINDOW = "window"
    BOTH = "both"


class WatchSpeed(StrEnum):
    """How hard to look. The venue pays for this in requests."""

    CALM = "calm"
    STEPPED = "stepped"
    FAST = "fast"


@dataclass(frozen=True, slots=True)
class WatchRule:
    """One standing instruction to take a freed slot."""

    rule_id: str
    name: str
    #: Monday is 0, matching datetime.weekday().
    weekdays: frozenset[int]
    #: Venue-local wall clock. A candidate starts at or after not_before and
    #: ends at or before not_after.
    not_before: time
    not_after: time
    #: Empty means any space. Order is preference order.
    space_ids: tuple[str, ...]
    duration_minutes: int
    venue_timezone: str
    mode: WatchMode = WatchMode.BOTH
    #: The most we may hold in one contiguous block on one day.
    max_block_minutes: int = 180
    allow_other_court: bool = False
    #: A slot starting sooner than this is no use: nobody can be gathered.
    min_lead_minutes: int = 180
    speed: WatchSpeed = WatchSpeed.STEPPED
    #: False means notify and leave the slot alone.
    book: bool = True
    active_until: date | None = None
    enabled: bool = True
    notify_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.duration_minutes <= 0:
            raise ValueError(f"duration must be positive, got {self.duration_minutes}")
        if not self.weekdays:
            raise ValueError("a rule with no weekdays can never match")
        if self.not_after <= self.not_before:
            raise ValueError("not_after must be later than not_before")
        try:
            ZoneInfo(self.venue_timezone)
        except (ZoneInfoNotFoundError, ValueError) as err:
            # Caught here rather than at the moment a slot frees up, where
            # there would be nobody to read the traceback.
            raise ValueError(f"{self.venue_timezone!r} is not a known IANA zone") from err

    @property
    def subject_id(self) -> str:
        return self.rule_id

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.venue_timezone)


def week_of(moment: datetime) -> tuple[int, int]:
    """The ISO year and week a slot's start falls in.

    The quota is weekly and the venue means its own calendar week, so this
    comes from the venue-local start rather than from UTC.
    """
    iso = moment.isocalendar()
    return (iso.year, iso.week)


def accounts_with_quota(
    used: Mapping[str, Sequence[Booking]],
    quota_minutes: int | None,
    week: tuple[int, int],
) -> tuple[str, ...]:
    """Which accounts may still book something in this week.

    None means the venue caps nothing; 0 means nobody may book at all. Reading
    one as the other is the difference between doing nothing for ever and
    ignoring the venue's rule.
    """
    if quota_minutes == 0:
        return ()
    if quota_minutes is None:
        return tuple(used)
    free: list[str] = []
    for account, bookings in used.items():
        spent = sum(
            int((booking.end - booking.start).total_seconds() // 60)
            for booking in bookings
            if week_of(booking.start) == week
        )
        if spent < quota_minutes:
            free.append(account)
    return tuple(free)

"""Rules for taking a slot somebody else gave up.

Everything here is a pure function of the rules, the bookings and the clock.
The Home Assistant layer supplies snapshots and carries out what this decides;
it makes no decisions of its own, which is what keeps the interesting part
testable as a table of cases.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
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
    reserved: Mapping[str, Collection[tuple[int, int]]] | None = None,
) -> tuple[str, ...]:
    """Which accounts may still book something in this week.

    `reserved` names the weeks an account has a booking job aiming at. That
    hour is already spoken for: spending it on a freed slot would leave the job
    to fail on quota, which is losing the court we planned for to one we merely
    stumbled on.

    None quota means the venue caps nothing; 0 means nobody may book at all.
    Reading one as the other is the difference between doing nothing for ever
    and ignoring the venue's rule.
    """
    if quota_minutes == 0:
        return ()
    reserved = reserved or {}
    free: list[str] = []
    for account, bookings in used.items():
        if week in reserved.get(account, ()):
            continue
        if quota_minutes is None:
            free.append(account)
            continue
        spent = sum(
            int((booking.end - booking.start).total_seconds() // 60)
            for booking in bookings
            if week_of(booking.start) == week
        )
        if spent < quota_minutes:
            free.append(account)
    return tuple(free)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A slot a rule would accept if it turns out to be free."""

    rule_id: str
    space_id: str
    start: datetime
    end: datetime
    neighbour: bool


def candidates(
    rule: WatchRule,
    spaces: Sequence[str],
    now: datetime,
    horizon_end: datetime,
    slot_minutes: int,
) -> list[Candidate]:
    """Every slot this rule would take, before asking whether it is free.

    The venue's grid decides the starts: offering 19:10 at a venue that books
    on the hour spends a request to learn what was already knowable.
    """
    duration = timedelta(minutes=rule.duration_minutes)
    earliest = now + timedelta(minutes=rule.min_lead_minutes)
    wanted = rule.space_ids or tuple(spaces)
    found: list[Candidate] = []
    day = now.astimezone(rule.tz).date()
    last = horizon_end.astimezone(rule.tz).date()
    while day <= last:
        if day.weekday() in rule.weekdays and (
            rule.active_until is None or day <= rule.active_until
        ):
            closes = datetime.combine(day, rule.not_after, tzinfo=rule.tz)
            start = datetime.combine(day, rule.not_before, tzinfo=rule.tz)
            while start + duration <= closes:
                if start >= earliest and start + duration <= horizon_end:
                    found.extend(
                        Candidate(rule.rule_id, space, start, start + duration, neighbour=False)
                        for space in wanted
                    )
                start += timedelta(minutes=slot_minutes)
        day += timedelta(days=1)
    return found


def is_free(candidate: Candidate, bookings: Sequence[Booking]) -> bool:
    """Whether nothing in the snapshot occupies this space at this time."""
    return not any(
        candidate.space_id in booking.space_ids
        and booking.start < candidate.end
        and candidate.start < booking.end
        for booking in bookings
    )


@dataclass(frozen=True, slots=True)
class Catch:
    """One slot to take, and the account to take it with."""

    rule_id: str
    space_id: str
    start: datetime
    end: datetime
    account_id: str
    neighbour: bool


def has_capacity(
    ours: Mapping[str, Sequence[Booking]],
    quota_minutes: int | None,
    now: datetime,
    horizon_end: datetime,
    reserved: Mapping[str, Collection[tuple[int, int]]] | None = None,
) -> bool:
    """Whether any account may still book anything inside the horizon.

    False means the watcher does nothing at all - no polling, no candidates -
    until the horizon rolls forward or something is cancelled. That is the
    economy that makes watching affordable.
    """
    return any(
        accounts_with_quota(ours, quota_minutes, week) for week in _weeks_between(now, horizon_end)
    )


def _weeks_between(start: datetime, end: datetime) -> set[tuple[int, int]]:
    weeks: set[tuple[int, int]] = set()
    day = start
    while day <= end:
        weeks.add(week_of(day))
        day += timedelta(days=1)
    weeks.add(week_of(end))
    return weeks


def _ours_on(day: date, ours: Mapping[str, Sequence[Booking]]) -> list[Booking]:
    return [
        booking for bookings in ours.values() for booking in bookings if booking.start.date() == day
    ]


def _block_minutes(candidate: Candidate, mine: Sequence[Booking]) -> int:
    """How long the contiguous run containing this candidate would be.

    Walks outward through bookings that touch it, so two separate blocks on
    one day are measured separately rather than added together.
    """
    total = int((candidate.end - candidate.start).total_seconds() // 60)
    start, end = candidate.start, candidate.end
    counted: set[str] = set()
    growing = True
    while growing:
        growing = False
        for booking in mine:
            if booking.id in counted:
                continue
            if booking.end == start or booking.start == end:
                total += int((booking.end - booking.start).total_seconds() // 60)
                start = min(start, booking.start)
                end = max(end, booking.end)
                counted.add(booking.id)
                growing = True
    return total


def _is_neighbour(candidate: Candidate, rule: WatchRule, mine: Sequence[Booking]) -> bool:
    return any(
        (booking.end == candidate.start or booking.start == candidate.end)
        and (rule.allow_other_court or candidate.space_id in booking.space_ids)
        for booking in mine
    )


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _distance_from_middle(candidate: Candidate, rule: WatchRule) -> int:
    """Minutes from the centre of the rule's hours.

    The centre is what the group actually wants: a rule reading 19:00-21:00
    was written by somebody who plays at 20:00.
    """
    middle = (_minutes(rule.not_before) + _minutes(rule.not_after) - rule.duration_minutes) // 2
    return abs(_minutes(candidate.start.time()) - middle)


def _space_rank(space_id: str, rule: WatchRule, spaces: Sequence[str]) -> int:
    order = rule.space_ids or tuple(spaces)
    return order.index(space_id) if space_id in order else len(order)


def evaluate(
    rules: Sequence[WatchRule],
    ours: Mapping[str, Sequence[Booking]],
    everyone: Sequence[Booking],
    quota_minutes: int | None,
    spaces: Sequence[str],
    now: datetime,
    horizon_end: datetime,
    slot_minutes: int,
    reserved: Mapping[str, Collection[tuple[int, int]]] | None = None,
    released: Collection[tuple[str, str]] | None = None,
) -> Catch | None:
    """The one slot worth taking now, or None.

    One catch per pass: booking spends an account's hour and changes the
    block, so the next decision has to be made against the world as it then
    is rather than against this snapshot.

    `released` names slots we gave up ourselves. They are free, they match the
    rules, and taking them back is the last thing anybody wants - somebody
    cancelled that court on purpose.
    """
    released = released or ()
    best: tuple[tuple[int, int, int, datetime], Catch] | None = None
    for rule in rules:
        if not rule.enabled:
            continue
        for candidate in candidates(rule, spaces, now, horizon_end, slot_minutes):
            if (candidate.space_id, candidate.start.isoformat()) in released:
                continue
            if not is_free(candidate, everyone):
                continue
            mine_today = _ours_on(candidate.start.date(), ours)
            neighbour = _is_neighbour(candidate, rule, mine_today)
            if mine_today:
                if rule.mode is WatchMode.WINDOW or not neighbour:
                    continue
                if _block_minutes(candidate, mine_today) > rule.max_block_minutes:
                    continue
            elif rule.mode is WatchMode.NEIGHBOUR:
                continue
            free = accounts_with_quota(ours, quota_minutes, week_of(candidate.start), reserved)
            if not free:
                continue
            order = (
                0 if neighbour else 1,
                _distance_from_middle(candidate, rule),
                _space_rank(candidate.space_id, rule, spaces),
                candidate.start,
            )
            if best is None or order < best[0]:
                best = (
                    order,
                    Catch(
                        rule_id=rule.rule_id,
                        space_id=candidate.space_id,
                        start=candidate.start,
                        end=candidate.end,
                        account_id=free[0],
                        neighbour=neighbour,
                    ),
                )
    return best[1] if best else None


#: Minutes between looks, by how near the nearest candidate day is. The middle
#: row is roughly 700-1000 requests a week while the gate is open, which is
#: what a member with the venue's page open all day already produces.
_STEPS: dict[WatchSpeed, tuple[int, int, int]] = {
    WatchSpeed.CALM: (30, 15, 5),
    WatchSpeed.STEPPED: (15, 5, 2),
    WatchSpeed.FAST: (5, 2, 1),
}

#: Cancellations cluster as the day approaches, so the rate follows them.
_NEAR = timedelta(days=2)
_IMMINENT = timedelta(hours=6)


def interval_for(speed: WatchSpeed, nearest: datetime | None, now: datetime) -> timedelta | None:
    """How often to look, or None when there is nothing to look for."""
    if nearest is None:
        return None
    far, near, imminent = _STEPS[speed]
    remaining = nearest - now
    if remaining <= _IMMINENT:
        return timedelta(minutes=imminent)
    if remaining <= _NEAR:
        return timedelta(minutes=near)
    return timedelta(minutes=far)

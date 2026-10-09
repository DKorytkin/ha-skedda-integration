"""Free slots beside ours, and what can be done about each.

A day where we hold one hour is a day one more hour would make a game of. The
panel shows the free hour either side of it: to take, when an account still
has an hour that week, or to move ours onto, when none has.

Pure, like core/watch.py: the bookings, the quota and the clock in, the offers
out.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from .provider import Booking
from .watch import IsOpen, accounts_with_quota, week_of


class OfferKind(StrEnum):
    """What the panel offers to do with a free neighbour."""

    #: Book it, paid for by an account with an hour left that week.
    TAKE = "take"
    #: Move our booking that day onto it; nobody can pay for another hour.
    MOVE = "move"


@dataclass(frozen=True, slots=True)
class Offer:
    kind: OfferKind
    space_id: str
    start: datetime
    end: datetime
    #: Who books it (TAKE) or whose booking moves (MOVE).
    account_id: str
    #: Our booking this slot sits beside - the one a MOVE moves.
    booking_id: str


def neighbour_offers(
    ours: Mapping[str, Sequence[Booking]],
    everyone: Sequence[Booking],
    *,
    quota_minutes: int | None,
    now: datetime,
    reserved: Mapping[str, Collection[tuple[int, int]]] | None = None,
    is_open: IsOpen | None = None,
    min_lead_minutes: int = 0,
) -> list[Offer]:
    """Every free neighbour of a lone booking of ours, in time order.

    Only days holding exactly one booking across all our accounts: a day with
    two already has its game, and a day with none is the watch's business.
    """
    earliest = now + timedelta(minutes=min_lead_minutes)
    owners = {booking.id: account for account, bookings in ours.items() for booking in bookings}
    found: list[Offer] = []
    for booking in _lone_bookings(ours, now):
        if not booking.space_ids:
            continue
        space = booking.space_ids[0]
        length = booking.end - booking.start
        for start in (booking.start - length, booking.end):
            end = start + length
            if start < earliest or not _is_free(space, start, end, everyone):
                continue
            if is_open is not None and not is_open(space, start, end):
                continue
            payers = accounts_with_quota(ours, quota_minutes, week_of(start), reserved)
            if payers:
                kind, account = OfferKind.TAKE, payers[0]
            else:
                kind, account = OfferKind.MOVE, owners[booking.id]
            found.append(Offer(kind, space, start, end, account, booking.id))
    return sorted(found, key=lambda offer: offer.start)


def _lone_bookings(ours: Mapping[str, Sequence[Booking]], now: datetime) -> list[Booking]:
    by_day: dict[date, list[Booking]] = {}
    for bookings in ours.values():
        for booking in bookings:
            if booking.start > now:
                by_day.setdefault(booking.start.date(), []).append(booking)
    return [day[0] for day in by_day.values() if len(day) == 1]


def _is_free(space: str, start: datetime, end: datetime, everyone: Sequence[Booking]) -> bool:
    return not any(
        space in booking.space_ids and booking.start < end and start < booking.end
        for booking in everyone
    )

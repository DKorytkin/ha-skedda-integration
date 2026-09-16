"""The seam that lets a second booking platform be added later.

Nothing here may mention Skedda, HTTP or aiohttp. That is the whole point: the
scheduler talks to this protocol, so swapping in another venue system means
writing one adapter and changing nothing else.

Implementations raise the transport exception taxonomy from api/errors.py.
core/ deliberately does not import those types - the exception contract is
documented on `book` below and honoured by adapters; the burst loop in
scheduler.py catches them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Space:
    """Somewhere bookable. Ids are opaque strings, never parsed."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Booking:
    id: str
    space_ids: tuple[str, ...]
    start: datetime
    end: datetime
    title: str
    #: Whether this booking belongs to the account that fetched it. A venue
    #: hands back everybody's bookings, and a calendar of everybody's is noise.
    is_mine: bool = False


@dataclass(frozen=True, slots=True)
class BookingRequest:
    """One space, one interval. Who is booking is the adapter's business."""

    space_id: str
    start: datetime
    end: datetime
    title: str


@dataclass(frozen=True, slots=True)
class DateRange:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class VenueRules:
    """The venue settings that decide whether a job can ever succeed.

    `max_days_ahead` and `weekly_quota_minutes` are None when the venue sets no
    such rule. None means unlimited and must never be read as 0, which is a
    real value meaning "may not book at all".
    """

    timezone: str
    slot_minutes: int
    max_days_ahead: int | None
    weekly_quota_minutes: int | None


@runtime_checkable
class BookingProvider(Protocol):
    """A booking platform.

    `book` raises SlotTakenError, TooEarlyError, QuotaExceededError,
    BookingWindowClosedError, AuthExpiredError, RateLimitedError,
    ApiContractError or SkeddaConnectionError.
    """

    @property
    def is_authenticated(self) -> bool: ...

    async def authenticate(self) -> None: ...

    async def list_spaces(self) -> list[Space]: ...

    async def venue_settings(self) -> VenueRules: ...

    async def book(self, request: BookingRequest) -> Booking: ...

    async def list_bookings(self, window: DateRange) -> list[Booking]: ...

    async def cancel(self, booking_id: str) -> None: ...

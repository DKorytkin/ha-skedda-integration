"""The protocol must stay structural so a second provider can be dropped in."""

from __future__ import annotations

from datetime import datetime

from custom_components.skedda_scheduler.core.provider import (
    Booking,
    BookingProvider,
    BookingRequest,
    DateRange,
    Space,
    VenueRules,
)
from custom_components.skedda_scheduler.skedda_provider import SkeddaProvider


def test_skedda_provider_satisfies_the_protocol() -> None:
    for member in BookingProvider.__protocol_attrs__:  # type: ignore[attr-defined]
        assert hasattr(SkeddaProvider, member), member


class FakeVenueSystem:
    """A second platform, written against nothing but core/.

    That this compiles and satisfies the protocol is the seam's only real
    guarantee: adding another venue system means one adapter and no changes
    anywhere else. (core/ is kept free of transport imports by
    tests/test_layering.py.)
    """

    is_authenticated = True

    async def authenticate(self) -> None:
        return None

    async def list_spaces(self) -> list[Space]:
        return [Space(id="1", name="Hall")]

    async def venue_settings(self) -> VenueRules:
        return VenueRules(
            timezone="UTC",
            slot_minutes=30,
            max_days_ahead=None,
            weekly_quota_minutes=None,
        )

    async def book(self, request: BookingRequest) -> Booking:
        return Booking(
            id="1",
            space_ids=(request.space_id,),
            start=request.start,
            end=request.end,
            title=request.title,
        )

    async def list_bookings(self, window: DateRange) -> list[Booking]:
        return []

    async def cancel(self, booking_id: str) -> None:
        return None


def test_an_unrelated_implementation_also_satisfies_the_protocol() -> None:
    assert isinstance(FakeVenueSystem(), BookingProvider)


async def test_the_scheduler_could_drive_that_implementation() -> None:
    """Proves the protocol is usable, not merely satisfiable."""
    provider: BookingProvider = FakeVenueSystem()
    booking = await provider.book(
        BookingRequest(
            space_id="1",
            start=datetime(2026, 9, 28, 8, 0),
            end=datetime(2026, 9, 28, 9, 0),
            title="Tennis",
        )
    )
    assert booking.space_ids == ("1",)

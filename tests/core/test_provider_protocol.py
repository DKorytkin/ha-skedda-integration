"""The protocol must stay structural so a second provider can be dropped in."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
            start=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
            end=datetime(2026, 9, 28, 9, 0, tzinfo=UTC),
            title="Tennis",
        )
    )
    assert booking.space_ids == ("1",)


def test_a_booking_without_a_timezone_is_refused_at_the_boundary() -> None:
    """Skedda writes a bare wall clock, so this is the mistake to make.

    Observed live 2026-09-16: it surfaced three layers away as a calendar that
    would not load and a duplicate-booking guard that never matched.
    """
    with pytest.raises(ValueError, match="timezone"):
        Booking(
            id="1",
            space_ids=("1",),
            start=datetime(2026, 9, 28, 8, 0),
            end=datetime(2026, 9, 28, 9, 0, tzinfo=UTC),
            title="Tennis",
        )


def test_a_request_without_a_timezone_is_refused_too() -> None:
    """The transport strips the offset when it serialises; it cannot invent one."""
    with pytest.raises(ValueError, match="timezone"):
        BookingRequest(
            space_id="1",
            start=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
            end=datetime(2026, 9, 28, 9, 0),
            title="Tennis",
        )

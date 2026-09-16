"""Adapter: Skedda's transport layer expressed as a BookingProvider.

Lives outside api/ because api/ must not import core/, and outside core/
because it knows Skedda exists. Mapping is the entire job - any logic that
appears here belongs in core/ instead.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from .api.client import SkeddaClient
from .api.models import SkeddaBooking, SkeddaBookingRequest
from .core.provider import Booking, BookingRequest, DateRange, Space, VenueRules


class SkeddaProvider:
    """Implements core.provider.BookingProvider over a SkeddaClient."""

    def __init__(self, client: SkeddaClient) -> None:
        self._client = client

    @property
    def is_authenticated(self) -> bool:
        return self._client.is_authenticated

    @property
    def client(self) -> SkeddaClient:
        """Exposed for diagnostics and the clock estimate, not for booking."""
        return self._client

    async def authenticate(self) -> None:
        await self._client.authenticate()

    async def list_spaces(self) -> list[Space]:
        return [Space(id=item.id, name=item.name) for item in await self._client.list_spaces()]

    async def venue_settings(self) -> VenueRules:
        venue = await self._client.venue_settings()
        return VenueRules(
            timezone=venue.timezone,
            slot_minutes=venue.slot_minutes,
            max_days_ahead=venue.max_days_ahead,
            weekly_quota_minutes=venue.weekly_quota_minutes,
        )

    async def book(self, request: BookingRequest) -> Booking:
        venue = await self._client.venue_settings()
        created = await self._client.create_booking(
            SkeddaBookingRequest(
                space_ids=(request.space_id,),
                start=request.start,
                end=request.end,
                title=request.title,
            )
        )
        return self._to_booking(created, venue.timezone)

    async def list_bookings(self, window: DateRange) -> list[Booking]:
        """Every booking the venue reports, each marked as ours or not.

        Skedda returns the whole venue's diary, which is what makes a taken
        slot detectable - and what makes "my bookings" a question the adapter
        has to answer, since only it knows our membership id.
        """
        identity = await self._client.identity()
        venue = await self._client.venue_settings()
        found = await self._client.list_bookings(window.start, window.end)
        return [self._to_booking(item, venue.timezone, identity.venueuser_id) for item in found]

    async def cancel(self, booking_id: str) -> None:
        await self._client.cancel_booking(booking_id)

    @staticmethod
    def _to_booking(item: SkeddaBooking, timezone: str, venueuser_id: str | None = None) -> Booking:
        """Give the venue's wall clock its zone.

        Skedda writes booking times with no offset and no Z: they are the
        venue's local time and nothing else. Passing them along unchanged
        leaves every later comparison against a real instant quietly false.
        """
        venue_tz = ZoneInfo(timezone)
        return Booking(
            id=item.id,
            space_ids=item.space_ids,
            start=item.start.replace(tzinfo=venue_tz),
            end=item.end.replace(tzinfo=venue_tz),
            title=item.title,
            is_mine=venueuser_id is not None and item.venueuser_id == venueuser_id,
        )

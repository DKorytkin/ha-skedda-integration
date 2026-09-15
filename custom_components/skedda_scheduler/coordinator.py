"""Periodic refresh of account health, venue rules and upcoming bookings.

This is also where the credentials are first proven: setting the entry up only
builds objects, so a wrong password surfaces here, as a reauth flow, rather
than as a failed startup with nothing the user can act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta, tzinfo
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.errors import SkeddaAuthError, SkeddaError
from .const import DOMAIN
from .core.provider import Booking, DateRange, Space, VenueRules

if TYPE_CHECKING:
    from .skedda_provider import SkeddaProvider

_LOGGER = logging.getLogger(__name__)

#: Often enough to notice someone else's cancellation freeing a slot, rare
#: enough to stay a well-behaved client of a service that never invited us.
UPDATE_INTERVAL = timedelta(minutes=15)
#: Comfortably past any venue's booking horizon (14 days at the venue this was
#: built against), so a job's next slot is always inside the polled range.
LOOKAHEAD = timedelta(days=30)


@dataclass(slots=True)
class SkeddaData:
    """One poll's worth of the account's view of Skedda."""

    spaces: list[Space]
    bookings: list[Booking]
    rules: VenueRules


class SkeddaCoordinator(DataUpdateCoordinator[SkeddaData]):
    """Keeps one account's view of Skedda fresh."""

    #: The base class allows None; this one is always built with an entry, and
    #: the entities read it without checking.
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, provider: SkeddaProvider) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.provider = provider
        self.authenticated = False

    async def _async_update_data(self) -> SkeddaData:
        try:
            if not self.provider.is_authenticated:
                await self.provider.authenticate()
            rules = await self.provider.venue_settings()
            spaces = await self.provider.list_spaces()
            bookings = await self.provider.list_bookings(self._window(rules))
        except SkeddaAuthError as err:
            # Not retryable: nothing improves until the user types a password.
            self.authenticated = False
            raise ConfigEntryAuthFailed(str(err)) from err
        except SkeddaError as err:
            self.authenticated = False
            raise UpdateFailed(str(err)) from err

        self.authenticated = True
        return SkeddaData(spaces=spaces, bookings=bookings, rules=rules)

    def _window(self, rules: VenueRules) -> DateRange:
        """The range to list, in the venue's own wall clock.

        Skedda's list endpoint takes naive venue-local times, so a UTC instant
        would be sent verbatim and silently ask about the wrong hours - or, for
        a venue far enough east late enough in the day, the wrong day.
        """
        try:
            venue_tz: tzinfo = ZoneInfo(rules.timezone)
        except ZoneInfoNotFoundError, ValueError:
            # Home Assistant's own zone is a poor stand-in, but it beats
            # refusing to poll at all over a zone name we cannot resolve.
            _LOGGER.warning(
                "Venue timezone %r is not a known IANA zone; using %s instead",
                rules.timezone,
                dt_util.get_default_time_zone(),
            )
            venue_tz = dt_util.get_default_time_zone()
        now = dt_util.utcnow().astimezone(venue_tz)
        return DateRange(start=now, end=now + LOOKAHEAD)

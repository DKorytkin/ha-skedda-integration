"""Periodic refresh of account health, venue rules and upcoming bookings.

This is also where the credentials are first proven: setting the entry up only
builds objects, so a wrong password surfaces here, as a reauth flow, rather
than as a failed startup with nothing the user can act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
#: Used only when a booking is close: see async_note_next_arming.
UPDATE_INTERVAL = timedelta(minutes=15)
#: A booking is coming, but not today.
DISTANT_INTERVAL = timedelta(hours=1)
#: Nothing is due at all - out of season, or every job disabled. Not "never":
#: a password that stopped working is better discovered in February than on
#: the morning the season opens.
IDLE_INTERVAL = timedelta(hours=12)
#: How close an arming has to be before the frequent poll is worth it.
IMMINENT = timedelta(hours=1)
#: Beyond this, twice a day is plenty. The scheduler does not need the poll to
#: fire - it has its own alarm - so this only keeps the panel and the calendars
#: current. A day of hourly polling ahead of every booking was the largest
#: share of everything this integration asked the venue, for no benefit.
DISTANT = timedelta(hours=6)
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
        # Nothing is known to be due until a scheduler says so, and polling a
        # venue every quarter hour on the chance is thousands of requests a
        # month that nobody asked for.
        self._arming_interval = IDLE_INTERVAL
        #: What a slot watch wants, when one is watching this venue.
        self._watch_interval: timedelta | None = None
        self.update_interval = IDLE_INTERVAL

    @callback
    def async_note_next_arming(self, when: datetime | None) -> None:
        """Set the poll rate from how soon the next booking attempt is.

        Called by the scheduler whenever it arms or disarms a job: the
        scheduler is the only thing that knows when this account next has
        something to do.
        """
        self._arming_interval = self._interval_for(when)
        self._apply_interval()
        _LOGGER.debug("Next arming %s; polling every %s", when, self.update_interval)

    @callback
    def async_note_watch_interval(self, interval: timedelta | None) -> None:
        """How often a slot watch wants this account polled.

        Two callers share one dial, so the shorter demand wins: the scheduler
        needs a poll before a window opens, the watch needs one between them.
        """
        self._watch_interval = interval
        self._apply_interval()

    @callback
    def _apply_interval(self) -> None:
        demands = [self._arming_interval]
        if self._watch_interval is not None:
            demands.append(self._watch_interval)
        self.update_interval = min(demands)

    @staticmethod
    def _interval_for(when: datetime | None) -> timedelta:
        if when is None:
            return IDLE_INTERVAL
        remaining = when - dt_util.utcnow()
        if remaining <= IMMINENT:
            return UPDATE_INTERVAL
        if remaining <= DISTANT:
            return DISTANT_INTERVAL
        return IDLE_INTERVAL

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

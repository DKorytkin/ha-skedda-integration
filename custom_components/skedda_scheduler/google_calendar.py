"""Wiring between Home Assistant's OAuth session and the Google transport."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.errors import SkeddaError
from .api.google import GoogleCalendarClient
from .const import (
    CONF_ATTENDEES,
    CONF_CALENDAR_ID,
    CONF_EVENT_TITLE,
    CONF_LOCATION,
    DEFAULT_EVENT_TITLE,
    DOMAIN,
    ENTRY_KIND_CALENDAR,
)
from .entry_kinds import entry_kind
from .sinks.google_calendar import GoogleCalendarSink

_LOGGER = logging.getLogger(__name__)


def is_calendar_entry(entry: ConfigEntry) -> bool:
    return entry_kind(entry) == ENTRY_KIND_CALENDAR


def async_find_calendar_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The linked calendar, if one has been linked.

    Deliberately not gated on the entry being loaded: linking a calendar
    reloads the accounts already running, and that reload starts while the
    calendar's own setup is still in progress.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        if is_calendar_entry(entry) and CONF_CALENDAR_ID in entry.data:
            return entry
    return None


async def async_build_client(hass: HomeAssistant, entry: ConfigEntry) -> GoogleCalendarClient:
    """A transport that refreshes its own token.

    Home Assistant owns the refreshing; this only asks it for a live token
    each time, so an hour-old link keeps working.
    """
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    async def token() -> str:
        await session.async_ensure_token_valid()
        return str(session.token["access_token"])

    return GoogleCalendarClient(async_get_clientsession(hass), token)


async def async_build_sink(hass: HomeAssistant) -> GoogleCalendarSink | None:
    """The calendar sink, or None when no calendar is linked."""
    entry = async_find_calendar_entry(hass)
    if entry is None:
        return None
    try:
        client = await async_build_client(hass, entry)
    except (SkeddaError, ValueError) as err:
        # A calendar that cannot be reached is not a reason for an account to
        # fail to set up. The court matters; the diary entry does not.
        _LOGGER.warning("Not writing bookings to the calendar: %s", err)
        return None
    return GoogleCalendarSink(
        hass,
        client,
        calendar_id=str(entry.data[CONF_CALENDAR_ID]),
        title=str(entry.data.get(CONF_EVENT_TITLE) or DEFAULT_EVENT_TITLE),
        location=entry.data.get(CONF_LOCATION) or None,
        attendees=tuple(entry.data.get(CONF_ATTENDEES) or ()),
    )

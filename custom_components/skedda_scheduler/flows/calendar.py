"""Choosing a calendar, and who gets invited to it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..api.errors import SkeddaError
from ..api.google import GoogleCalendarClient
from ..const import (
    CONF_ATTENDEES,
    CONF_CALENDAR_ID,
    CONF_ENTRY_KIND,
    CONF_EVENT_TITLE,
    CONF_LOCATION,
    DEFAULT_EVENT_TITLE,
    ENTRY_KIND_CALENDAR,
)

if TYPE_CHECKING:
    from ..config_flow import SkeddaConfigFlow


def settings_schema(calendars: list[tuple[str, str]], defaults: dict[str, Any]) -> vol.Schema:
    """What to write, where, and who to tell."""
    return vol.Schema(
        {
            vol.Required(
                CONF_CALENDAR_ID, default=defaults.get(CONF_CALENDAR_ID, vol.UNDEFINED)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=identifier, label=name)
                        for identifier, name in calendars
                    ]
                )
            ),
            vol.Required(
                CONF_EVENT_TITLE, default=defaults.get(CONF_EVENT_TITLE, DEFAULT_EVENT_TITLE)
            ): selector.TextSelector(),
            vol.Optional(
                CONF_LOCATION, default=defaults.get(CONF_LOCATION, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_ATTENDEES, default=defaults.get(CONF_ATTENDEES, [])
            ): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
        }
    )


async def async_calendar_step(
    flow: SkeddaConfigFlow,
    token_data: dict[str, Any],
    user_input: dict[str, Any] | None,
    defaults: dict[str, Any] | None = None,
    client: GoogleCalendarClient | None = None,
) -> ConfigFlowResult:
    """Ask which calendar to write to, listing the ones Google allows.

    Straight after linking, the token in hand is minutes old and is used as it
    is. Editing an entry set up days ago is the opposite case: that token
    expired within the hour, so the caller passes a client that refreshes.
    """
    if user_input is not None:
        return async_create_calendar_entry(flow, token_data, user_input)

    defaults = defaults or {
        key: token_data[key]
        for key in (CONF_CALENDAR_ID, CONF_EVENT_TITLE, CONF_LOCATION, CONF_ATTENDEES)
        if key in token_data
    }

    # Home Assistant's own session: it owns the lifetime, and closing it here
    # would take every other integration's requests down with it. Google needs
    # no cookie jar of its own, unlike the venue.
    if client is None:
        session = async_get_clientsession(flow.hass)

        async def token() -> str:
            return str(token_data["token"]["access_token"])

        client = GoogleCalendarClient(session, token)

    try:
        calendars = await client.list_calendars()
    except SkeddaError:
        # Nothing to choose from means nothing to save; saying so beats an
        # empty dropdown the user cannot get past.
        return flow.async_abort(reason="calendar_list_failed")

    if not calendars:
        return flow.async_abort(reason="no_writable_calendar")

    return flow.async_show_form(
        step_id="calendar_settings",
        data_schema=settings_schema(
            [(calendar.id, calendar.name) for calendar in calendars], defaults
        ),
    )


def async_create_calendar_entry(
    flow: SkeddaConfigFlow, token_data: dict[str, Any], user_input: dict[str, Any]
) -> ConfigFlowResult:
    data = {**token_data, **user_input, CONF_ENTRY_KIND: ENTRY_KIND_CALENDAR}
    if flow.source == SOURCE_RECONFIGURE:
        # Editing keeps the link Google already granted; only the settings
        # around it change.
        return flow.async_update_reload_and_abort(flow._get_reconfigure_entry(), data=data)
    return flow.async_create_entry(title="Google Calendar", data=data)


__all__ = ["async_calendar_step", "async_create_calendar_entry", "settings_schema"]

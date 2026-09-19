"""Config flow for Skedda Scheduler.

Two kinds of entry share this flow, so it opens with a menu:

* a **Skedda account** - venue, login, and a name to tell several apart;
* the **Google calendar** link, so a booking can become an event with the
  people who are coming invited to it.

They are separate entries rather than one because the calendar belongs to no
particular account, and because OAuth in Home Assistant is a config-entry flow.
Booking jobs remain subentries of an account.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow

from . import google_calendar
from .api.errors import (
    SignInBlockedError,
    SkeddaAuthError,
    SkeddaConnectionError,
    SkeddaError,
)
from .api.google import SCOPES, GoogleCalendarClient
from .const import (
    CONF_ALIAS,
    CONF_ENTRY_KIND,
    CONF_VENUE,
    CONF_VENUE_TIMEZONE,
    DOMAIN,
    ENTRY_KIND_ACCOUNT,
    ENTRY_KIND_CALENDAR,
    ENTRY_KIND_WATCH,
    SUBENTRY_TYPE_JOB,
    SUBENTRY_TYPE_WATCH_RULE,
)
from .core.provider import VenueRules
from .entry_kinds import entry_kind

# Imported as a module, not by name: validate_credentials is the seam the
# flow's tests patch, and a from-import would bind it here at import time.
from .flows import account
from .flows.account import STEP_REAUTH_SCHEMA, STEP_USER_SCHEMA, normalise, unique_id_for
from .flows.calendar import async_calendar_step
from .flows.job import JobSubentryFlowHandler
from .flows.watch import WatchRuleSubentryFlowHandler, async_watch_step

_LOGGER = logging.getLogger(__name__)


class SkeddaConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Collects a Skedda account, or the Google calendar to write bookings to."""

    #: The OAuth handler reads this attribute; the class keyword above only
    #: registers the flow.
    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._token_data: dict[str, Any] = {}
        #: Set only when editing a calendar that was linked some time ago.
        self._client: GoogleCalendarClient | None = None

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """What to ask Google for.

        offline access and a forced consent screen, because without a refresh
        token the link works until the first hour is up and then stops.
        """
        return {"scope": SCOPES, "access_type": "offline", "prompt": "consent"}

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Booking jobs are subentries: many per account, added after setup."""
        if entry_kind(config_entry) == ENTRY_KIND_WATCH:
            return {SUBENTRY_TYPE_WATCH_RULE: WatchRuleSubentryFlowHandler}
        return {SUBENTRY_TYPE_JOB: JobSubentryFlowHandler}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask which of the three things is being added."""
        return self.async_show_menu(step_id="user", menu_options=["account", "calendar", "watch"])

    async def async_step_watch(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start watching this venue for slots other people give up."""
        return async_watch_step(self)

    async def async_step_calendar(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Hand over to Home Assistant's OAuth flow."""
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Google has answered; now ask which calendar, and who is coming."""
        self._token_data = data
        return await self.async_step_calendar_settings()

    async def async_step_calendar_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await async_calendar_step(self, self._token_data, user_input, client=self._client)

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add an account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = normalise(user_input)
            await self.async_set_unique_id(unique_id_for(data[CONF_VENUE], data[CONF_EMAIL]))
            # Before the network call: re-adding an account is a common
            # mis-step, and there is no point signing in to discover it.
            self._abort_if_unique_id_configured()
            rules, errors = await self._validate(data)
            if rules is not None:
                return self.async_create_entry(
                    title=data[CONF_ALIAS] or data[CONF_VENUE],
                    data={
                        **data,
                        CONF_VENUE_TIMEZONE: rules.timezone,
                        CONF_ENTRY_KIND: ENTRY_KIND_ACCOUNT,
                    },
                )
        return self.async_show_form(step_id="account", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Skedda rejected the stored password; ask for the new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            # Only the password is asked for: changing the venue or the login
            # would make this a different account, which is a reconfigure.
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            rules, errors = await self._validate(data)
            if rules is not None:
                return self.async_update_reload_and_abort(
                    entry, data={**data, CONF_VENUE_TIMEZONE: rules.timezone}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"account": entry.title},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit whichever kind of entry this is."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_CALENDAR:
            # Who comes to tennis changes more often than the Google account
            # does, so this edits the settings without asking Google again.
            self._token_data = dict(entry.data)
            # The stored access token expired within an hour of being issued;
            # only the entry's own session knows how to renew it.
            self._client = await google_calendar.async_build_client(self.hass, entry)
            return await self.async_step_calendar_settings(user_input)
        return await self.async_step_account_reconfigure(user_input)

    async def async_step_account_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Correct any of the account's details in place."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            data = normalise(user_input)
            await self.async_set_unique_id(unique_id_for(data[CONF_VENUE], data[CONF_EMAIL]))
            # Pointing this entry at an account that is already set up would
            # leave two entries sharing one quota, both surprised by the other.
            self._abort_if_unique_id_mismatch(reason="account_mismatch")
            rules, errors = await self._validate(data)
            if rules is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    title=data[CONF_ALIAS] or data[CONF_VENUE],
                    data={**data, CONF_VENUE_TIMEZONE: rules.timezone},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, {**entry.data, CONF_PASSWORD: ""}
            ),
            errors=errors,
        )

    async def _validate(self, data: Mapping[str, Any]) -> tuple[VenueRules | None, dict[str, str]]:
        """Check the credentials, translating failures into form errors."""
        try:
            rules = await account.validate_credentials(
                self.hass, data[CONF_VENUE], data[CONF_EMAIL], data[CONF_PASSWORD]
            )
        except SignInBlockedError:
            # Skedda declined the attempt, not the password. Checked before
            # SkeddaAuthError would be, though it is not one - the two must
            # never be confused in either direction.
            return None, {"base": "sign_in_blocked"}
        except SkeddaAuthError:
            return None, {"base": "invalid_auth"}
        except SkeddaConnectionError:
            return None, {"base": "cannot_connect"}
        except SkeddaError as err:
            # A venue that answers, but not in the shape we know. Worth the log
            # line: it is the signal that Skedda's API has moved.
            _LOGGER.warning("Skedda rejected the setup check: %s", err)
            return None, {"base": "unknown"}
        return rules, {}

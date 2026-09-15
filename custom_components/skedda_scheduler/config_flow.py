"""Config flow for Skedda Scheduler.

Collects one Skedda account per config entry: the venue subdomain, the login,
and a name to tell several accounts apart. Booking jobs are added afterwards as
subentries, so this flow is only ever about credentials.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback

from .api.errors import (
    SignInBlockedError,
    SkeddaAuthError,
    SkeddaConnectionError,
    SkeddaError,
)
from .const import (
    CONF_ALIAS,
    CONF_VENUE,
    CONF_VENUE_TIMEZONE,
    DOMAIN,
    SUBENTRY_TYPE_JOB,
)
from .core.provider import VenueRules

# Imported as a module, not by name: validate_credentials is the seam the
# flow's tests patch, and a from-import would bind it here at import time.
from .flows import account
from .flows.account import STEP_REAUTH_SCHEMA, STEP_USER_SCHEMA, normalise, unique_id_for
from .flows.job import JobSubentryFlowHandler

_LOGGER = logging.getLogger(__name__)


class SkeddaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collects one Skedda account per config entry."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Booking jobs are subentries: many per account, added after setup."""
        return {SUBENTRY_TYPE_JOB: JobSubentryFlowHandler}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
                    data={**data, CONF_VENUE_TIMEZONE: rules.timezone},
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

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

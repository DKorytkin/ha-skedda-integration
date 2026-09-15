"""Subentry flow: one booking job per subentry."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import selector

from ..api.errors import SkeddaError
from ..const import (
    CONF_DURATION,
    CONF_FREQUENCY,
    CONF_NAME,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SEASON_START,
    CONF_SPACE_ID,
    CONF_START_TIME,
    CONF_STRATEGY,
    CONF_TITLE,
    CONF_WEEKDAY,
    CONF_WINDOW_DAYS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_WINDOW_DAYS,
)
from ..core.provider import Space, VenueRules

#: Values match datetime.date.weekday(): Monday is 0.
WEEKDAYS = [
    selector.SelectOptionDict(value="0", label="Monday"),
    selector.SelectOptionDict(value="1", label="Tuesday"),
    selector.SelectOptionDict(value="2", label="Wednesday"),
    selector.SelectOptionDict(value="3", label="Thursday"),
    selector.SelectOptionDict(value="4", label="Friday"),
    selector.SelectOptionDict(value="5", label="Saturday"),
    selector.SelectOptionDict(value="6", label="Sunday"),
]

#: Used only when the venue publishes no granularity of its own.
_FALLBACK_SLOT_MINUTES = 15
_MAX_DURATION_MINUTES = 480
_MAX_WINDOW_DAYS = 60


def job_schema(spaces: list[Space], rules: VenueRules | None) -> vol.Schema:
    """Build the job form, shaped by what the venue itself allows.

    Every default here that can be read from the venue is read from the venue.
    A window that does not match the venue's own horizon, or a duration that is
    not a whole number of its slots, produces a job that fails every week
    without ever saying why.
    """
    space_selector: Any
    if spaces:
        space_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=space.id, label=space.name) for space in spaces
                ]
            )
        )
    else:
        # The venue could not be asked. A free-text id keeps the user moving
        # rather than blocking them on a transient failure.
        space_selector = selector.TextSelector()

    slot = rules.slot_minutes if rules and rules.slot_minutes else _FALLBACK_SLOT_MINUTES
    window_default = rules.max_days_ahead if rules and rules.max_days_ahead else DEFAULT_WINDOW_DAYS
    duration_default = max(DEFAULT_DURATION_MINUTES, slot)

    return vol.Schema(
        {
            vol.Required(CONF_NAME): selector.TextSelector(),
            vol.Required(CONF_SPACE_ID): space_selector,
            vol.Required(CONF_WEEKDAY): selector.SelectSelector(
                selector.SelectSelectorConfig(options=WEEKDAYS)
            ),
            vol.Required(CONF_START_TIME): selector.TimeSelector(),
            vol.Required(CONF_DURATION, default=duration_default): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    # The venue books in whole slots; offering a finer step
                    # offers a booking the server will refuse.
                    min=slot,
                    max=_MAX_DURATION_MINUTES,
                    step=slot,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_WINDOW_DAYS, default=window_default): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=_MAX_WINDOW_DAYS, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(CONF_FREQUENCY, default="weekly"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="weekly", label="Every week"),
                        selector.SelectOptionDict(value="biweekly", label="Every other week"),
                    ]
                )
            ),
            vol.Required(CONF_SEASON_START): selector.DateSelector(),
            vol.Optional(CONF_SEASON_END): selector.DateSelector(),
            vol.Required(CONF_TITLE): selector.TextSelector(),
            vol.Required(CONF_STRATEGY, default="precise"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value="precise", label="Precise (fire as the window opens)"
                        ),
                        selector.SelectOptionDict(
                            value="immediate", label="Immediate (fire and retry)"
                        ),
                    ]
                )
            ),
            vol.Optional(CONF_NOTIFY_TARGETS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", multiple=True)
            ),
        }
    )


def validate_against_venue(user_input: dict[str, Any], rules: VenueRules | None) -> dict[str, str]:
    """Reject jobs the venue's own rules make impossible.

    Both checks describe jobs that would never book anything: one asks for more
    than the weekly allowance, the other fires before the venue will accept a
    booking at all. Saying so now beats a weekly failure notification.
    """
    if rules is None:
        return {}
    errors: dict[str, str] = {}
    quota = rules.weekly_quota_minutes
    if quota is not None and int(user_input[CONF_DURATION]) > quota:
        errors[CONF_DURATION] = "over_quota"
    horizon = rules.max_days_ahead
    if horizon is not None and int(user_input[CONF_WINDOW_DAYS]) > horizon:
        errors[CONF_WINDOW_DAYS] = "beyond_horizon"
    return errors


class JobSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit one booking job."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        spaces, rules = await self._venue_context()
        if user_input is not None:
            errors = validate_against_venue(user_input, rules)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
            return self._form("user", job_schema(spaces, rules), rules, errors)
        return self._form("user", job_schema(spaces, rules), rules, {})

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        spaces, rules = await self._venue_context()
        subentry = self._get_reconfigure_subentry()
        schema = job_schema(spaces, rules)
        if user_input is not None:
            errors = validate_against_venue(user_input, rules)
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=user_input[CONF_NAME],
                    data=user_input,
                )
            return self._form("reconfigure", schema, rules, errors)
        return self._form(
            "reconfigure",
            self.add_suggested_values_to_schema(schema, subentry.data),
            rules,
            {},
        )

    def _form(
        self,
        step_id: str,
        schema: vol.Schema,
        rules: VenueRules | None,
        errors: dict[str, str],
    ) -> SubentryFlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            # The venue's own numbers, so the message can name them rather than
            # leaving the user to guess what "too long" means.
            description_placeholders={
                "quota": _describe(rules.weekly_quota_minutes if rules else None),
                "horizon": _describe(rules.max_days_ahead if rules else None),
            },
        )

    async def _venue_context(self) -> tuple[list[Space], VenueRules | None]:
        """Ask the venue what it offers and what it allows.

        Failures are swallowed on purpose: this only shapes the form, and being
        unable to reach Skedda for a moment is no reason to block configuration.
        """
        entry: ConfigEntry = self._get_entry()
        try:
            provider = entry.runtime_data.provider
        except AttributeError:
            return [], None
        try:
            if not provider.is_authenticated:
                # Setting the entry up deliberately does not sign in, so for a
                # freshly started Home Assistant this form is the first to need it.
                await provider.authenticate()
            return await provider.list_spaces(), await provider.venue_settings()
        except SkeddaError:
            return [], None


def _describe(value: int | None) -> str:
    return "unlimited" if value is None else str(value)

"""The slot watch entry, and the form for one rule.

A rule belongs to the venue rather than to an account: accounts are one hour a
week each, and a rule does not care which of them pays. So the watch is its own
entry, and each rule is a subentry of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.helpers import selector

from ..const import (
    CONF_ALLOW_OTHER_COURT,
    CONF_BOOK,
    CONF_DURATION,
    CONF_ENTRY_KIND,
    CONF_MAX_BLOCK_MINUTES,
    CONF_MIN_LEAD_MINUTES,
    CONF_MODE,
    CONF_NAME,
    CONF_NOT_AFTER,
    CONF_NOT_BEFORE,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SPACE_IDS,
    CONF_SPEED,
    CONF_VENUE,
    CONF_WEEKDAYS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_MAX_BLOCK_MINUTES,
    DEFAULT_MIN_LEAD_MINUTES,
    DOMAIN,
    ENTRY_KIND_WATCH,
)
from ..core.provider import Space
from ..core.watch import WatchMode, WatchSpeed
from ..entry_kinds import entry_kind, is_account_entry
from ..watch_factory import WEEKDAYS

if TYPE_CHECKING:
    from ..config_flow import SkeddaConfigFlow

MODE_OPTIONS = [
    selector.SelectOptionDict(value=WatchMode.BOTH, label="Next to ours, or any free slot"),
    selector.SelectOptionDict(value=WatchMode.NEIGHBOUR, label="Only next to one of ours"),
    selector.SelectOptionDict(value=WatchMode.WINDOW, label="Only when we have nothing that day"),
]

SPEED_OPTIONS = [
    selector.SelectOptionDict(value=WatchSpeed.CALM, label="Calm (30/15/5 min)"),
    selector.SelectOptionDict(value=WatchSpeed.STEPPED, label="Stepped (15/5/2 min)"),
    selector.SelectOptionDict(value=WatchSpeed.FAST, label="Fast (5/2/1 min)"),
]

WEEKDAY_OPTIONS = [
    selector.SelectOptionDict(value=key, label=label)
    for key, label in zip(
        WEEKDAYS,
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        strict=True,
    )
]


def rule_schema(spaces: list[Space], defaults: dict[str, Any]) -> vol.Schema:
    """What to watch for, and what to do about it."""
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)
            ): selector.TextSelector(),
            vol.Required(
                CONF_WEEKDAYS, default=defaults.get(CONF_WEEKDAYS, vol.UNDEFINED)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=WEEKDAY_OPTIONS, multiple=True)
            ),
            vol.Required(
                CONF_NOT_BEFORE, default=defaults.get(CONF_NOT_BEFORE, "19:00:00")
            ): selector.TimeSelector(),
            vol.Required(
                CONF_NOT_AFTER, default=defaults.get(CONF_NOT_AFTER, "21:00:00")
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_SPACE_IDS, default=defaults.get(CONF_SPACE_IDS, [])
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=space.id, label=space.name)
                        for space in spaces
                    ],
                    multiple=True,
                )
            ),
            vol.Required(
                CONF_DURATION, default=defaults.get(CONF_DURATION, DEFAULT_DURATION_MINUTES)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15,
                    max=480,
                    step=15,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MODE, default=defaults.get(CONF_MODE, WatchMode.BOTH)
            ): selector.SelectSelector(selector.SelectSelectorConfig(options=MODE_OPTIONS)),
            vol.Required(
                CONF_MAX_BLOCK_MINUTES,
                default=defaults.get(CONF_MAX_BLOCK_MINUTES, DEFAULT_MAX_BLOCK_MINUTES),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60,
                    max=480,
                    step=30,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_ALLOW_OTHER_COURT, default=defaults.get(CONF_ALLOW_OTHER_COURT, False)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_MIN_LEAD_MINUTES,
                default=defaults.get(CONF_MIN_LEAD_MINUTES, DEFAULT_MIN_LEAD_MINUTES),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=1440,
                    step=30,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SPEED, default=defaults.get(CONF_SPEED, WatchSpeed.STEPPED)
            ): selector.SelectSelector(selector.SelectSelectorConfig(options=SPEED_OPTIONS)),
            vol.Required(
                CONF_BOOK, default=defaults.get(CONF_BOOK, True)
            ): selector.BooleanSelector(),
            # No default: an empty date field is how "no end" is written, and
            # a DateSelector refuses the empty string a default would supply.
            vol.Optional(CONF_SEASON_END): selector.DateSelector(),
            vol.Optional(
                CONF_NOTIFY_TARGETS, default=defaults.get(CONF_NOTIFY_TARGETS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", multiple=True)
            ),
        }
    )


def async_watch_step(flow: SkeddaConfigFlow) -> ConfigFlowResult:
    """Create the watch for the venue the accounts already point at.

    Nothing to ask: the venue comes from the accounts, and everything else is
    a property of the rules that will live under it.
    """
    accounts = [
        entry for entry in flow.hass.config_entries.async_entries(DOMAIN) if is_account_entry(entry)
    ]
    if not accounts:
        # Without an account there is no venue to watch, no court list to
        # choose from, and nothing to pay with.
        return flow.async_abort(reason="no_account")
    venue = str(accounts[0].data.get(CONF_VENUE, ""))
    if any(
        entry_kind(entry) == ENTRY_KIND_WATCH and entry.data.get(CONF_VENUE) == venue
        for entry in flow.hass.config_entries.async_entries(DOMAIN)
    ):
        # Rules are subentries of it; a second entry would split them in two.
        return flow.async_abort(reason="already_configured")
    return flow.async_create_entry(
        title="Slot watch",
        data={CONF_ENTRY_KIND: ENTRY_KIND_WATCH, CONF_VENUE: venue},
    )


class WatchRuleSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit one watch rule."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=rule_schema(self._spaces(), {}))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=rule_schema(self._spaces(), dict(subentry.data)),
        )

    def _spaces(self) -> list[Space]:
        """The venue's courts, as whichever account has read them sees them."""
        watch = self._get_entry()
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            if not is_account_entry(entry) or entry.data.get(CONF_VENUE) != watch.data.get(
                CONF_VENUE
            ):
                continue
            data = _coordinator_data(entry)
            if data is not None:
                return list(data.spaces)
        return []


def _coordinator_data(entry: ConfigEntry) -> Any:
    runtime = getattr(entry, "runtime_data", None)
    return getattr(getattr(runtime, "coordinator", None), "data", None)


__all__ = ["WatchRuleSubentryFlowHandler", "async_watch_step", "rule_schema"]

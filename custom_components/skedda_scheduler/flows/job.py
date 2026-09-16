"""Subentry flow: one booking job per subentry.

The form asks the four things only the user can know - which court, which date,
what time, how long - and derives or defaults everything else. A second step
holds the rest for the people who need it.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_DURATION,
    CONF_FREQUENCY,
    CONF_NAME,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SPACE_ID,
    CONF_START_DATE,
    CONF_START_TIME,
    CONF_STRATEGY,
    CONF_TITLE,
    CONF_WINDOW_DAYS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_WINDOW_DAYS,
)
from ..core.provider import Booking, Space, VenueRules
from ..core.recurrence import Frequency
from ..job_factory import describe

#: Used only when the venue publishes no granularity of its own.
_FALLBACK_SLOT_MINUTES = 15
_MAX_DURATION_MINUTES = 480
_MAX_WINDOW_DAYS = 60

#: Fortnightly exists in the domain model and is tested there, but nobody has
#: asked for it and every extra option is one more thing to read past.
REPEAT_OPTIONS = [
    selector.SelectOptionDict(value=Frequency.ONCE, label="Once"),
    selector.SelectOptionDict(value=Frequency.WEEKLY, label="Every week"),
]

STRATEGY_OPTIONS = [
    selector.SelectOptionDict(value="precise", label="Precise (fire as the window opens)"),
    selector.SelectOptionDict(value="immediate", label="Immediate (fire and retry)"),
]


def default_duration(rules: VenueRules | None, bookings: list[Booking]) -> int:
    """How long to book, learned from the venue and from what you book there.

    Your own recent bookings are the best answer there is: if every court you
    take is an hour, an hour is what the form should offer. Failing that, one
    of the venue's own slots, never more than its weekly allowance.
    """
    lengths = [
        int((booking.end - booking.start).total_seconds() // 60)
        for booking in bookings
        if booking.end > booking.start
    ]
    slot = rules.slot_minutes if rules and rules.slot_minutes else _FALLBACK_SLOT_MINUTES
    chosen = (
        Counter(lengths).most_common(1)[0][0] if lengths else max(DEFAULT_DURATION_MINUTES, slot)
    )
    quota = rules.weekly_quota_minutes if rules else None
    if quota is not None:
        chosen = min(chosen, quota)
    # Still a whole number of the venue's slots, whatever the history says.
    return max(slot, chosen - chosen % slot)


def default_date(now: datetime, rules: VenueRules | None) -> date:
    """The last date the venue will currently accept.

    The horizon is the interesting edge: the slot furthest out is the one worth
    racing for, and the nearer ones are usually gone. It is a starting point,
    not a decision - the field is a calendar.
    """
    horizon = rules.max_days_ahead if rules and rules.max_days_ahead else DEFAULT_WINDOW_DAYS
    return (now + timedelta(days=horizon)).date()


def essentials_schema(
    spaces: list[Space], rules: VenueRules | None, bookings: list[Booking], now: datetime
) -> vol.Schema:
    """The four things only the user can know, plus whether to repeat."""
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
    return vol.Schema(
        {
            vol.Required(CONF_SPACE_ID): space_selector,
            vol.Required(CONF_START_DATE, default=default_date(now, rules).isoformat()): (
                selector.DateSelector()
            ),
            vol.Required(CONF_START_TIME): selector.TimeSelector(),
            vol.Required(
                CONF_DURATION, default=default_duration(rules, bookings)
            ): selector.NumberSelector(
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
            vol.Required(CONF_FREQUENCY, default=Frequency.ONCE): selector.SelectSelector(
                selector.SelectSelectorConfig(options=REPEAT_OPTIONS)
            ),
        }
    )


def advanced_schema(rules: VenueRules | None, suggested_name: str) -> vol.Schema:
    """Everything that has a sensible answer without asking."""
    window_default = rules.max_days_ahead if rules and rules.max_days_ahead else DEFAULT_WINDOW_DAYS
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=suggested_name): selector.TextSelector(),
            vol.Required(CONF_TITLE, default=suggested_name): selector.TextSelector(),
            vol.Optional(CONF_SEASON_END): selector.DateSelector(),
            vol.Required(CONF_WINDOW_DAYS, default=window_default): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=_MAX_WINDOW_DAYS, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(CONF_STRATEGY, default="precise"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=STRATEGY_OPTIONS)
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
    if quota is not None and CONF_DURATION in user_input and int(user_input[CONF_DURATION]) > quota:
        errors[CONF_DURATION] = "over_quota"
    horizon = rules.max_days_ahead
    if (
        horizon is not None
        and CONF_WINDOW_DAYS in user_input
        and int(user_input[CONF_WINDOW_DAYS]) > horizon
    ):
        errors[CONF_WINDOW_DAYS] = "beyond_horizon"
    return errors


class JobSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit one booking job."""

    def __init__(self) -> None:
        self._essentials: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        spaces, rules, bookings = self._venue_context()
        if user_input is not None:
            errors = validate_against_venue(user_input, rules)
            if not errors:
                self._essentials = dict(user_input)
                if Frequency(user_input[CONF_FREQUENCY]) is not Frequency.ONCE:
                    # Only a repeating job has anything left to decide: when
                    # the season ends, what to call it, who to tell. A single
                    # date needs none of that, and a toggle offering it was a
                    # control that did nothing for most people who saw it.
                    return await self.async_step_advanced()
                data = self._with_defaults(self._essentials, rules, spaces)
                return self.async_create_entry(title=data[CONF_NAME], data=data)
            return self._form(
                "user", essentials_schema(spaces, rules, bookings, dt_util.utcnow()), rules, errors
            )
        return self._form(
            "user", essentials_schema(spaces, rules, bookings, dt_util.utcnow()), rules, {}
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        spaces, rules, _ = self._venue_context()
        suggested = self._suggested_name(self._essentials, spaces)
        if user_input is not None:
            errors = validate_against_venue(user_input, rules)
            if not errors:
                data = {**self._essentials, **user_input}
                return self.async_create_entry(title=data[CONF_NAME], data=data)
            return self._form("advanced", advanced_schema(rules, suggested), rules, errors)
        return self._form("advanced", advanced_schema(rules, suggested), rules, {})

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Editing shows everything at once: the job already exists.

        Hiding half its settings behind a toggle would make a known job harder
        to change than an unknown one is to create.
        """
        spaces, rules, bookings = self._venue_context()
        subentry = self._get_reconfigure_subentry()
        schema = essentials_schema(spaces, rules, bookings, dt_util.utcnow()).extend(
            advanced_schema(rules, str(subentry.data.get(CONF_NAME, ""))).schema
        )
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

    def _with_defaults(
        self, essentials: dict[str, Any], rules: VenueRules | None, spaces: list[Space]
    ) -> dict[str, Any]:
        """Fill in everything the short form did not ask for."""
        name = self._suggested_name(essentials, spaces)
        window = rules.max_days_ahead if rules and rules.max_days_ahead else DEFAULT_WINDOW_DAYS
        return {
            **essentials,
            CONF_NAME: name,
            CONF_TITLE: name,
            CONF_WINDOW_DAYS: window,
            CONF_STRATEGY: "precise",
        }

    def _suggested_name(self, essentials: dict[str, Any], spaces: list[Space]) -> str:
        space_id = str(essentials.get(CONF_SPACE_ID, ""))
        space_name = next(
            (space.name for space in spaces if space.id == space_id), space_id or "Booking"
        )
        start = date.fromisoformat(str(essentials.get(CONF_START_DATE, dt_util.utcnow().date())))
        raw_time = str(essentials.get(CONF_START_TIME, "00:00:00"))
        frequency = Frequency(essentials.get(CONF_FREQUENCY, Frequency.ONCE))
        return describe(
            space_name,
            start,
            time.fromisoformat(raw_time),
            frequency,
            # The name is stored as written, so it has to be written in the
            # language of whoever is going to read it back.
            language=self.hass.config.language,
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

    def _venue_context(self) -> tuple[list[Space], VenueRules | None, list[Booking]]:
        """What the venue offers and what it allows, from the last poll.

        Read rather than fetched: the coordinator already holds this, and a
        form that made its own calls would sign in again and ask twice every
        time somebody opened it. An entry that never loaded has nothing to
        offer, which the form handles by falling back to free text.
        """
        try:
            data = self._get_entry().runtime_data.coordinator.data
        except AttributeError:
            return [], None, []
        return data.spaces, data.rules, data.bookings


def _describe(value: int | None) -> str:
    return "unlimited" if value is None else str(value)

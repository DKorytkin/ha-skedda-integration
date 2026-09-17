"""Watching for a slot somebody gave up.

Owns no loop and no timer: it reads the snapshots an account's coordinator
already produces. Every decision belongs to core/watch.py; this module supplies
the world and carries out the verdict.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api.errors import SkeddaError
from .const import CONF_VENUE, DEFAULT_WINDOW_DAYS, DOMAIN, SUBENTRY_TYPE_WATCH_RULE
from .coordinator import SkeddaData
from .core.provider import Booking, BookingRequest
from .core.result import AttemptStatus, BookingAttempt, BookingOutcome
from .core.watch import Catch, WatchRule, candidates, evaluate, has_capacity, interval_for
from .entry_kinds import is_account_entry
from .sinks import async_dispatch
from .watch_factory import build_rule

_LOGGER = logging.getLogger(__name__)


def _nearest_start(
    rule: WatchRule,
    spaces: list[str],
    now: datetime,
    horizon: datetime,
    data: SkeddaData,
) -> datetime | None:
    """When this rule's next candidate slot begins, if it has one at all."""
    found = candidates(rule, spaces, now, horizon, data.rules.slot_minutes)
    return min((candidate.start for candidate in found), default=None)


class WatchRunner:
    """One venue's watch: the rules, the gate, and the catch."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        #: False until a scan proves an account still has an hour to spend.
        self.gate_open = False
        self.last_catch: Catch | None = None
        #: How often this watch is asking the reader to poll, None when idle.
        self.interval: timedelta | None = None
        self._listeners: list[CALLBACK_TYPE] = []

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Entities read this runner rather than a coordinator of their own."""
        self._listeners.append(listener)

        @callback
        def unsubscribe() -> None:
            self._listeners.remove(listener)

        return unsubscribe

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    @property
    def venue(self) -> str:
        return str(self.entry.data.get(CONF_VENUE, ""))

    @property
    def rules(self) -> list[WatchRule]:
        """Every rule that parses. A broken one is skipped, not fatal."""
        timezone = self._timezone()
        found: list[WatchRule] = []
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_WATCH_RULE:
                continue
            try:
                found.append(build_rule(subentry_id, subentry.data, timezone))
            except ValueError, KeyError:
                # One unusable rule must not stop the others watching.
                _LOGGER.warning("Ignoring watch rule %s: it does not describe a slot", subentry_id)
        return found

    def accounts(self) -> list[ConfigEntry]:
        """Loaded accounts for this venue, in a stable order.

        The first is the reader: every account sees the same venue-wide list,
        so polling with more than one would multiply the traffic for nothing.
        """
        return sorted(
            (
                entry
                for entry in self.hass.config_entries.async_loaded_entries(DOMAIN)
                if is_account_entry(entry) and entry.data.get(CONF_VENUE) == self.venue
            ),
            key=lambda entry: entry.entry_id,
        )

    async def async_refresh_and_scan(self) -> Catch | None:
        """Re-read the venue first, because something outside says to look."""
        accounts = self.accounts()
        if accounts:
            await accounts[0].runtime_data.coordinator.async_refresh()
        return await self.async_scan()

    async def async_scan(self) -> Catch | None:
        """One pass: gate, decide, book, report."""
        accounts = self.accounts()
        rules = [rule for rule in self.rules if rule.enabled]
        if not accounts or not rules:
            self.gate_open = False
            self._note_interval(accounts, None)
            return None

        data = accounts[0].runtime_data.coordinator.data
        if data is None:
            return None

        now = dt_util.utcnow()
        horizon = now + timedelta(days=data.rules.max_days_ahead or DEFAULT_WINDOW_DAYS)
        ours = {entry.entry_id: self._mine(entry) for entry in accounts}
        self.gate_open = has_capacity(ours, data.rules.weekly_quota_minutes, now, horizon)
        self._apply_interval(rules, data, now, horizon)
        if not self.gate_open:
            return None

        catch = evaluate(
            rules,
            ours,
            data.bookings,
            data.rules.weekly_quota_minutes,
            [space.id for space in data.spaces],
            now,
            horizon,
            data.rules.slot_minutes,
        )
        if catch is None:
            return None

        rule = next(rule for rule in rules if rule.rule_id == catch.rule_id)
        if rule.book and not await self._async_book(catch, rule):
            return None
        self.last_catch = catch
        await self._async_report(catch, rule, booked=rule.book)
        self._notify()
        return catch

    @callback
    def _apply_interval(
        self, rules: list[WatchRule], data: SkeddaData, now: datetime, horizon: datetime
    ) -> None:
        """Ask the reader to poll at the rate the nearest candidate deserves.

        Shut gate, no rate: the watch stops costing anything the moment there
        is nothing left to spend.
        """
        if not self.gate_open:
            self._note_interval(self.accounts(), None)
            return
        spaces = [space.id for space in data.spaces]
        demands = [
            interval_for(rule.speed, _nearest_start(rule, spaces, now, horizon, data), now)
            for rule in rules
        ]
        wanted = [demand for demand in demands if demand is not None]
        self._note_interval(self.accounts(), min(wanted) if wanted else None)

    @callback
    def _note_interval(self, accounts: list[ConfigEntry], interval: timedelta | None) -> None:
        """One reader polls; the others are told to stop on our account."""
        self.interval = interval
        self._notify()
        for position, entry in enumerate(accounts):
            entry.runtime_data.coordinator.async_note_watch_interval(
                interval if position == 0 else None
            )

    async def _async_book(self, catch: Catch, rule: WatchRule) -> bool:
        entry = next(entry for entry in self.accounts() if entry.entry_id == catch.account_id)
        request = BookingRequest(
            space_id=catch.space_id, start=catch.start, end=catch.end, title=rule.name
        )
        async with entry.runtime_data.semaphore:
            try:
                await entry.runtime_data.provider.book(request)
            except SkeddaError as err:
                # Losing the race is the ordinary outcome, not a fault: the
                # slot was free a moment ago and now is not.
                _LOGGER.info("Watch rule %s did not get %s: %s", rule.name, catch.start, err)
                return False
        # The booking spent an hour and changed the block, so the next decision
        # must not be made against the snapshot this one came from.
        await entry.runtime_data.coordinator.async_request_refresh()
        return True

    async def _async_report(self, catch: Catch, rule: WatchRule, *, booked: bool) -> None:
        now = dt_util.utcnow()
        outcome = BookingOutcome(
            job_id=rule.rule_id,
            succeeded=booked,
            booking_id=None,
            space_id=catch.space_id,
            slot_start=catch.start,
            slot_end=catch.end,
            attempts=(
                BookingAttempt(
                    attempt_no=1,
                    fired_at=now,
                    status=AttemptStatus.SUCCESS if booked else AttemptStatus.SLOT_TAKEN,
                    latency_ms=0.0,
                ),
            ),
            finished_at=now,
        )
        await async_dispatch(self.entry.runtime_data.sinks, outcome, rule)

    def _mine(self, entry: ConfigEntry) -> list[Booking]:
        data = entry.runtime_data.coordinator.data
        return [booking for booking in data.bookings if booking.is_mine] if data else []

    def _timezone(self) -> str:
        """The venue's zone, from whichever account has already read it."""
        for entry in self.accounts():
            data = entry.runtime_data.coordinator.data
            if data is not None:
                return str(data.rules.timezone)
        return str(dt_util.get_default_time_zone())

    @callback
    def async_shutdown(self) -> None:
        """The watch holds no timer and no session; only its listeners."""
        self._listeners.clear()

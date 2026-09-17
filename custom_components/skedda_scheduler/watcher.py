"""Watching for a slot somebody gave up.

Owns no loop and no timer: it reads the snapshots an account's coordinator
already produces. Every decision belongs to core/watch.py; this module supplies
the world and carries out the verdict.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .api.errors import SkeddaError
from .const import CONF_VENUE, DEFAULT_WINDOW_DAYS, DOMAIN, SUBENTRY_TYPE_WATCH_RULE
from .coordinator import SkeddaData
from .core.provider import Booking, BookingRequest
from .core.result import AttemptStatus, BookingAttempt, BookingOutcome
from .core.watch import (
    Catch,
    WatchRule,
    candidates,
    evaluate,
    has_capacity,
    interval_for,
    week_of,
)
from .entry_kinds import is_account_entry
from .sinks import async_dispatch
from .watch_factory import build_rule

_LOGGER = logging.getLogger(__name__)

#: How many occurrences of one job to walk when reserving its weeks.
_OCCURRENCE_LIMIT = 8


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
        #: Unsubscribes from the account coordinators we follow.
        self._coordinators: list[CALLBACK_TYPE] = []
        #: Unsubscribes that live as long as the entry does.
        self._following: list[CALLBACK_TYPE] = []
        #: Which account does the reading this time round.
        self._reader = 0

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
        """Loaded accounts for this venue, in a stable order."""
        return sorted(
            (
                entry
                for entry in self.hass.config_entries.async_loaded_entries(DOMAIN)
                if is_account_entry(entry) and entry.data.get(CONF_VENUE) == self.venue
            ),
            key=lambda entry: entry.entry_id,
        )

    async def async_start(self) -> None:
        """Follow the accounts' polls, and look once now.

        The watch has no clock of its own: every scan happens because an
        account's coordinator brought back a fresh view of the venue. An
        account that loads or reloads later builds a new coordinator, so this
        also listens for that rather than assuming the accounts it can see now
        are the accounts it will have.
        """
        self._following.append(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._async_entries_changed
            )
        )
        self._follow_accounts()
        await self.async_scan()

    @callback
    def _async_entries_changed(self, change: ConfigEntryChange, entry: ConfigEntry) -> None:
        if entry.domain == DOMAIN and is_account_entry(entry):
            self._follow_accounts()

    @callback
    def _follow_accounts(self) -> None:
        """Subscribe to every account's coordinator, dropping stale ones.

        Re-run on each scan: an account that reloads builds a new coordinator,
        and a listener left on the old one would never fire again.
        """
        for unsub in self._coordinators:
            unsub()
        self._coordinators = [
            entry.runtime_data.coordinator.async_add_listener(self._async_scan_soon)
            for entry in self.accounts()
        ]

    @callback
    def _async_scan_soon(self) -> None:
        """A poll landed; look at what it brought back."""
        self.entry.async_create_background_task(
            self.hass, self._async_scan_quietly(), name="skedda watch scan"
        )

    async def _async_scan_quietly(self) -> None:
        try:
            await self.async_scan()
        except SkeddaError as err:
            # A scan is a background task: an exception here would be logged
            # as "Task exception was never retrieved" and nothing else.
            _LOGGER.warning("Watch scan failed: %s", err)

    def reader(self, accounts: list[ConfigEntry]) -> ConfigEntry:
        """Whose session asks the venue this time.

        Every account sees the same venue-wide list, so one reader is enough -
        but always the same one would be a single account polling all day while
        the others sit idle. Taking turns spreads the same traffic across the
        people it belongs to.
        """
        return accounts[self._reader % len(accounts)]

    async def async_refresh_and_scan(self) -> Catch | None:
        """Re-read the venue first, because something outside says to look."""
        accounts = self.accounts()
        if accounts:
            await self.reader(accounts).runtime_data.coordinator.async_refresh()
        return await self.async_scan()

    async def async_scan(self) -> Catch | None:
        """One pass: gate, decide, book, report."""
        accounts = self.accounts()
        rules = [rule for rule in self.rules if rule.enabled]
        if not accounts or not rules:
            self.gate_open = False
            self._note_interval(accounts, None)
            return None

        self._follow_accounts()
        # Any account's snapshot describes the same venue, so a reader that has
        # not polled yet is no reason to sit out this round.
        data = next(
            (
                entry.runtime_data.coordinator.data
                for entry in (self.reader(accounts), *accounts)
                if entry.runtime_data.coordinator.data is not None
            ),
            None,
        )
        if data is None:
            return None

        now = dt_util.utcnow()
        horizon = now + timedelta(days=data.rules.max_days_ahead or DEFAULT_WINDOW_DAYS)
        ours = {entry.entry_id: self._mine(entry) for entry in accounts}
        reserved = {entry.entry_id: self._aimed_weeks(entry, now, horizon) for entry in accounts}
        self.gate_open = has_capacity(ours, data.rules.weekly_quota_minutes, now, horizon, reserved)
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
            reserved,
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
        """One account polls for all of them, and not always the same one."""
        self.interval = interval
        self._notify()
        if not accounts:
            return
        reading = self.reader(accounts)
        for entry in accounts:
            entry.runtime_data.coordinator.async_note_watch_interval(
                interval if entry is reading else None
            )
        # Next round belongs to somebody else.
        self._reader = (self._reader + 1) % len(accounts)

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

    def _aimed_weeks(
        self, entry: ConfigEntry, now: datetime, horizon_end: datetime
    ) -> set[tuple[int, int]]:
        """Weeks this account's booking jobs plan to use.

        Every occurrence inside the horizon, not merely the armed one: a
        weekly job arms one week at a time, and a watch that spent the weeks
        it had not reached yet would leave it failing on quota for ever after.
        The court we planned for beats the one we stumbled on.
        """
        scheduler = entry.runtime_data.scheduler
        if scheduler is None:
            return set()
        weeks: set[tuple[int, int]] = set()
        for runner in scheduler.runners.values():
            if not runner.job.enabled:
                continue
            moment = now
            # A season is finite and the horizon is a fortnight; the bound is
            # only a guard against a rule that yields dates for ever.
            for _ in range(_OCCURRENCE_LIMIT):
                slot = runner.job.next_slot(moment)
                if slot is None or slot[0] > horizon_end:
                    break
                # A slot the job has already fired at and lost is no longer
                # spoken for - and is exactly the week a watch exists for.
                if runner.attempted_slot != slot[0]:
                    weeks.add(week_of(slot[0]))
                moment = slot[0]
        return weeks

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
        """The watch holds no timer and no session; only its subscriptions."""
        for unsub in (*self._coordinators, *self._following):
            unsub()
        self._coordinators.clear()
        self._following.clear()
        self._listeners.clear()

"""Watching for a slot somebody gave up.

Owns no loop and no timer: it reads the snapshots an account's coordinator
already produces. Every decision belongs to core/watch.py; this module supplies
the world and carries out the verdict.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from . import google_calendar
from .api.errors import (
    AuthExpiredError,
    QuotaExceededError,
    RateLimitedError,
    SkeddaConnectionError,
    SkeddaError,
    SlotTakenError,
)
from .const import CONF_VENUE, DEFAULT_WINDOW_DAYS, DOMAIN, SUBENTRY_TYPE_WATCH_RULE
from .coordinator import SkeddaData
from .core.provider import Booking, BookingRequest, VenueRules
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
    found = candidates(rule, spaces, now, horizon, data.rules.slot_minutes, data.rules.is_open)
    return min((candidate.start for candidate in found), default=None)


#: Refusals that say nothing about the slot itself: somebody was quicker, the
#: venue could not be asked, or this account's hour is spent and another may
#: pay (seen 2026-10-08). Anything else will be refused again.
_WORTH_ASKING_AGAIN = (
    SlotTakenError,
    SkeddaConnectionError,
    RateLimitedError,
    AuthExpiredError,
    QuotaExceededError,
)


def _within_venue(rule: WatchRule, venue: VenueRules) -> WatchRule:
    """The rule, asking for a slot only while the venue still takes it.

    Seen 2026-10-09: a slot three hours off at a venue wanting three hours'
    notice was asked for on every scan, and refused every time.
    """
    return replace(rule, min_lead_minutes=venue.min_minutes_ahead)


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
        #: What each account held at the previous scan, so a booking that
        #: vanishes can be recognised as one we gave up.
        self._held: dict[str, set[tuple[str, str, str]]] = {}
        #: Which account does the reading this time round.
        self._reader = 0
        #: Every account's poll starts a scan, and six landing together raced
        #: each other to the same slot. One at a time, each seeing what the
        #: one before it booked.
        self._scanning = asyncio.Lock()
        #: Slots the venue refused for a reason that will not change by asking
        #: again, as (space, start, end). The watch takes one slot per scan, so
        #: a refused best slot asked for again on every scan hid every other
        #: one. Seen 2026-10-09: 18:00 refused as "too soon" five times a scan
        #: while 19:00 came free.
        self._refused: set[tuple[str, datetime, datetime]] = set()

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
        async with self._scanning:
            return await self._async_scan()

    async def _async_scan(self) -> Catch | None:
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
        rules = [_within_venue(rule, data.rules) for rule in rules]

        now = dt_util.utcnow()
        self._refused = {slot for slot in self._refused if slot[2] > now}
        horizon = now + timedelta(days=data.rules.max_days_ahead or DEFAULT_WINDOW_DAYS)
        ours = {entry.entry_id: self._mine(entry) for entry in accounts}
        await self._async_note_releases(accounts, ours, now)
        reserved = {entry.entry_id: self._aimed_weeks(entry, now, horizon) for entry in accounts}
        self.gate_open = has_capacity(
            ours, data.rules.weekly_quota_minutes, now, horizon, reserved, rules[0].tz
        )
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
            self._released(accounts) | self._refused,
            data.rules.is_open,
        )
        if catch is None:
            return None

        rule = next(rule for rule in rules if rule.rule_id == catch.rule_id)
        booking_id: str | None = None
        if rule.book:
            booked = await self._async_book(catch, rule)
            if booked is None:
                return None
            booking_id = booked.id
        self.last_catch = catch
        await self._async_report(catch, rule, booked=rule.book, booking_id=booking_id)
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

    async def _async_book(self, catch: Catch, rule: WatchRule) -> Booking | None:
        entry = next(entry for entry in self.accounts() if entry.entry_id == catch.account_id)
        request = BookingRequest(
            space_id=catch.space_id, start=catch.start, end=catch.end, title=rule.name
        )
        async with entry.runtime_data.semaphore:
            try:
                booked: Booking = await entry.runtime_data.provider.book(request)
            except SkeddaError as err:
                # Losing the race is the ordinary outcome, not a fault: the
                # slot was free a moment ago and now is not.
                _LOGGER.info("Watch rule %s did not get %s: %s", rule.name, catch.start, err)
                if not isinstance(err, _WORTH_ASKING_AGAIN):
                    self._refused.add((catch.space_id, catch.start, catch.end))
                return None
        # The booking spent an hour and changed the block, so the next decision
        # must not be made against the snapshot this one came from.
        await entry.runtime_data.coordinator.async_request_refresh()
        return booked

    async def _async_report(
        self, catch: Catch, rule: WatchRule, *, booked: bool, booking_id: str | None
    ) -> None:
        now = dt_util.utcnow()
        paid_by = next(
            (entry.title for entry in self.accounts() if entry.entry_id == catch.account_id), None
        )
        outcome = BookingOutcome(
            job_id=rule.rule_id,
            succeeded=booked,
            booking_id=booking_id,
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
            account=paid_by,
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
                # Only a slot the job will still fire at is spoken for: the
                # one it is armed for, and those whose window has yet to open.
                # A slot it fired at and lost, or whose window opened without
                # it, is exactly the week a watch exists for. Keying this on
                # the last attempt alone kept such weeks reserved until the
                # next restart cleared it, or for good.
                if slot[0] == runner.armed_slot or runner.job.window.opens_at(slot[0]) > now:
                    weeks.add(week_of(slot[0]))
                moment = slot[0]
        return weeks

    async def _async_note_releases(
        self,
        accounts: list[ConfigEntry],
        ours: dict[str, list[Booking]],
        now: datetime,
    ) -> None:
        """Record bookings of ours that have disappeared since the last scan.

        Whoever cancelled it - the panel, the Skedda app, the venue - the
        answer is the same: we are not to take that court back on our own,
        and its calendar event goes too. Only slots still in the future count;
        the rest merely aged out of the window we ask about.

        The last scan's view is kept on disk, so a booking cancelled while
        Home Assistant was restarting is caught on the first scan after it.
        """
        for entry in accounts:
            store = entry.runtime_data.store
            held = {
                (
                    booking.space_ids[0] if booking.space_ids else "",
                    booking.start.isoformat(),
                    booking.end.isoformat(),
                )
                for booking in ours.get(entry.entry_id, ())
            }
            previous = self._held.get(entry.entry_id)
            if previous is None:
                previous = store.held()
            self._held[entry.entry_id] = held
            await store.async_note_held(held)
            if previous is None:
                # Never scanned before: nothing to compare against, and
                # treating everything as released would block the lot.
                continue
            # The panel notes its own cancellations, and removes their events.
            known = store.released_intervals()
            for space_id, start, end in previous - held:
                moment = dt_util.parse_datetime(start)
                until = dt_util.parse_datetime(end) or moment
                if moment is None or until is None or moment <= now:
                    continue
                if (space_id, moment, until) in known:
                    continue
                await store.async_note_released(space_id, moment, until)
                await google_calendar.async_release_event(self.hass, space_id, moment, until)

    def _released(self, accounts: list[ConfigEntry]) -> set[tuple[str, datetime, datetime]]:
        """Every slot any of our accounts gave up."""
        return {
            slot for entry in accounts for slot in entry.runtime_data.store.released_intervals()
        }

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

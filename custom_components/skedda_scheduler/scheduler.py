"""Arming, firing and retrying booking attempts.

The timing arithmetic lives in core/strategy.py and core/window.py. This module
is the part that touches the clock and the network: it wakes up early, warms the
session, sleeps to the millisecond on the server's clock, and classifies what
comes back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from . import google_calendar
from .api.errors import (
    ApiContractError,
    AuthExpiredError,
    BookingWindowClosedError,
    QuotaExceededError,
    RateLimitedError,
    SkeddaAuthError,
    SkeddaConnectionError,
    SkeddaError,
    SlotTakenError,
    TooEarlyError,
)
from .const import MAX_ATTEMPTS_PER_RUN, SUBENTRY_TYPE_JOB
from .core.job import BookingJob
from .core.provider import BookingProvider, BookingRequest
from .core.result import AttemptStatus, BookingAttempt, BookingOutcome
from .core.strategy import build_strategy, should_retry
from .job_factory import build_job, venue_timezone_for
from .repairs import async_clear_contract_issue, async_raise_contract_issue
from .sinks import ResultSink, async_dispatch, build_sinks
from .store import AttemptStore

_LOGGER = logging.getLogger(__name__)

# Order matters: AuthExpiredError is a SkeddaAuthError, and the quota and
# window errors are plain SkeddaErrors that must keep their own identity -
# reporting a venue rule as a contract error would send the user hunting for
# an API change that never happened.
_ERROR_STATUS: tuple[tuple[type[SkeddaError], AttemptStatus], ...] = (
    (TooEarlyError, AttemptStatus.TOO_EARLY),
    (SlotTakenError, AttemptStatus.SLOT_TAKEN),
    (QuotaExceededError, AttemptStatus.QUOTA_EXCEEDED),
    (BookingWindowClosedError, AttemptStatus.WINDOW_CLOSED),
    (RateLimitedError, AttemptStatus.RATE_LIMITED),
    (ApiContractError, AttemptStatus.CONTRACT_ERROR),
    (SkeddaConnectionError, AttemptStatus.CONNECTION_ERROR),
    (AuthExpiredError, AttemptStatus.AUTH_FAILED),
    (SkeddaAuthError, AttemptStatus.AUTH_FAILED),
)

#: How long to wait before firing again after a 429. The burst's own spacing is
#: a quarter of a second, and answering rate limiting at that cadence is exactly
#: what earns a longer ban. core/strategy.py keeps 429 retryable because it is
#: genuinely transient; this is the back-off that makes the retry defensible.
RATE_LIMIT_BACKOFF_SECONDS = 2.0

#: How many occurrences to walk past when looking for a window that has not
#: opened yet. A season is finite and the horizon is short; this is a guard
#: against a rule that somehow yields dates for ever.
_CATCH_UP_SEARCH_LIMIT = 12

#: How soon a job may fire when its window is already open. Not zero: Home
#: Assistant's time tracker runs a wake-up that is already due immediately, so
#: a zero delay turns "catch up on an open window" into a hot loop.
CATCH_UP_DELAY = timedelta(seconds=1)

#: Recorded when a run ends without firing: no slot left in the season, or the
#: slot is already ours.
REASON_ALREADY_BOOKED = "already_booked"


def _status_for(error: SkeddaError) -> AttemptStatus:
    for error_type, status in _ERROR_STATUS:
        if isinstance(error, error_type):
            return status
    return AttemptStatus.CONTRACT_ERROR


class JobRunner:
    """Owns one booking job's schedule and its attempt loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        provider: BookingProvider,
        job: BookingJob,
        sinks: Sequence[ResultSink],
        store: AttemptStore,
        semaphore: asyncio.Semaphore,
        on_schedule: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.provider = provider
        self.job = job
        self.sinks = list(sinks)
        self.store = store
        self.semaphore = semaphore
        self._on_schedule = on_schedule
        self.armed_for: datetime | None = None
        #: The slot this arming is aimed at. Not always the nearest one: after
        #: catching up, the runner moves to the first window that has yet to
        #: open, and anything describing the job has to say the same.
        self.armed_slot: datetime | None = None
        #: The slot the last run fired at, so the next arming moves past it.
        self.attempted_slot: datetime | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def next_run(self) -> datetime | None:
        """The instant this job's next slot becomes bookable."""
        return self.job.next_window_open(dt_util.utcnow())

    @callback
    def async_schedule(self) -> None:
        """Arm for the next slot this job has not already tried.

        A window that opened while Home Assistant was down is still open - the
        horizon is rolling, and a slot stays bookable right up until it starts.
        Waiting for the next window would concede exactly the slot this
        integration exists to win, so the runner arms for now instead.

        The slot just attempted is skipped, and that is not a refinement: an
        open window plus a re-arm for the same slot is a wake-up that is
        already due, which runs, re-arms, and runs again. Observed live on
        2026-09-15 firing several times a second.
        """
        self.async_cancel()
        if not self.job.enabled:
            return

        now = dt_util.utcnow()
        slot = self._next_untried_slot(now)
        if slot is None:
            _LOGGER.debug("Job %s has no slot left to try", self.job.job_id)
            return

        opens_at = self.job.window.opens_at(slot)
        if self.attempted_slot is not None and opens_at <= now:
            # One catch-up per run of Home Assistant, not one per open window.
            # A weekly job at a venue allowing an hour a week has several open
            # windows at once, and firing at all of them means one booking and
            # a run of refusals - each of which reaches the user's phone.
            slot_and_window = self._next_unopened_slot(now)
            if slot_and_window is None:
                _LOGGER.debug("Job %s has caught up; nothing opens later", self.job.job_id)
                return
            slot, opens_at = slot_and_window
        arm_at = build_strategy(self.job.strategy).plan(opens_at).arm_at
        # Never in the past: a due wake-up fires immediately, and a run that
        # arms one loops however it came about.
        self.armed_for = max(arm_at, now + CATCH_UP_DELAY)
        self.armed_slot = slot
        self._unsub = async_track_point_in_utc_time(self.hass, self._async_armed, self.armed_for)
        self._announce()
        _LOGGER.debug(
            "Job %s armed for %s (slot %s, window opens %s)",
            self.job.job_id,
            self.armed_for,
            slot,
            opens_at,
        )

    def _next_unopened_slot(self, now: datetime) -> tuple[datetime, datetime] | None:
        """The first slot whose window has not opened yet, with that instant."""
        probe = now
        for _ in range(_CATCH_UP_SEARCH_LIMIT):
            slot = self.job.next_slot(probe)
            if slot is None:
                return None
            opens_at = self.job.window.opens_at(slot[0])
            if opens_at > now:
                return slot[0], opens_at
            probe = slot[0]
        return None

    def _next_untried_slot(self, now: datetime) -> datetime | None:
        """The start of the next slot worth arming for."""
        slot = self.job.next_slot(now)
        if slot is not None and slot[0] == self.attempted_slot:
            slot = self.job.next_slot(slot[0])
        return slot[0] if slot is not None else None

    @callback
    def async_cancel(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self.armed_for = None
        self.armed_slot = None
        self._announce()

    @callback
    def _announce(self) -> None:
        """Tell whoever cares that this job's next attempt time changed."""
        if self._on_schedule is not None:
            self._on_schedule()

    async def async_run_now(self) -> BookingOutcome:
        """Run the job immediately, as the service and the button do."""
        await self._async_warm_up()
        return await self._async_execute(dt_util.utcnow())

    async def _async_armed(self, _now: datetime) -> None:
        self._unsub = None
        opens_at = self.job.next_window_open(dt_util.utcnow())
        if opens_at is None:
            return
        try:
            await self._async_warm_up()
            await self._async_execute(opens_at)
        finally:
            # Always re-arm, however the run ended: a failed week must not
            # silently retire the job.
            self.async_schedule()

    async def _async_warm_up(self) -> None:
        """Sign in and open a connection so the first shot pays no setup cost.

        Each call also feeds the clock estimator another Date header, which is
        what the burst's timing depends on.
        """
        try:
            if not self.provider.is_authenticated:
                await self.provider.authenticate()
            await self.provider.list_spaces()
        except SkeddaError as err:
            _LOGGER.warning("Warm-up for job %s failed: %s", self.job.job_id, err)

    async def _async_execute(self, opens_at: datetime) -> BookingOutcome:
        slot = self.job.next_slot(dt_util.utcnow())
        if slot is None:
            return await self._async_no_op(opens_at, opens_at, reason=None)
        slot_start, slot_end = slot
        # Remember it before anything can fail: a run that crashed half way
        # must still not be repeated in a tight loop.
        self.attempted_slot = slot_start
        if self._already_booked(slot_start):
            _LOGGER.debug("Job %s already holds %s", self.job.job_id, slot_start)
            return await self._async_no_op(slot_start, slot_end, reason=REASON_ALREADY_BOOKED)

        request = BookingRequest(
            space_id=self.job.primary_space_id,
            start=slot_start,
            end=slot_end,
            title=self.job.title,
        )
        plan = build_strategy(self.job.strategy).plan(opens_at)

        attempts: list[BookingAttempt] = []
        booking_id: str | None = None
        reauth_used = False

        # One booking in flight per account: two jobs racing each other would
        # spend the same weekly quota twice.
        async with self.semaphore:
            for number, fire_at in enumerate(plan.fire_times[:MAX_ATTEMPTS_PER_RUN], start=1):
                await self._async_sleep_until(fire_at)
                started = dt_util.utcnow()
                try:
                    booking = await self.provider.book(request)
                except SkeddaError as err:
                    status = _status_for(err)
                    detail: str | None = str(err)
                else:
                    status, detail = AttemptStatus.SUCCESS, None
                    booking_id = booking.id

                attempts.append(
                    BookingAttempt(
                        attempt_no=number,
                        fired_at=started,
                        status=status,
                        latency_ms=(dt_util.utcnow() - started).total_seconds() * 1000,
                        detail=detail,
                    )
                )

                if status is AttemptStatus.CONTRACT_ERROR:
                    # Nobody documented this API. The day it changes, the user
                    # needs a sentence they can act on, not a job that quietly
                    # stops booking.
                    async_raise_contract_issue(self.hass, self.entry.entry_id, detail or "")
                elif status is AttemptStatus.SUCCESS:
                    async_clear_contract_issue(self.hass, self.entry.entry_id)

                if status is AttemptStatus.SUCCESS:
                    break
                if status is AttemptStatus.RATE_LIMITED:
                    await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS)
                if status is AttemptStatus.AUTH_FAILED and not reauth_used:
                    # A cookie can expire between the warm-up and the burst.
                    # Worth exactly one re-login; a second rejection is a
                    # password problem, which no retry will fix.
                    reauth_used = True
                    try:
                        await self.provider.authenticate()
                    except SkeddaError:
                        break
                    continue
                if not should_retry(status):
                    break

        outcome = BookingOutcome(
            job_id=self.job.job_id,
            succeeded=booking_id is not None,
            booking_id=booking_id,
            space_id=self.job.primary_space_id if booking_id else None,
            slot_start=slot_start,
            slot_end=slot_end,
            attempts=tuple(attempts),
            finished_at=dt_util.utcnow(),
            account=self.entry.title,
        )
        await self._async_finish(outcome)
        return outcome

    def _already_booked(self, slot_start: datetime) -> bool:
        """Whether the last poll already saw this slot on one of our spaces.

        Guards against a restart re-firing a job that has already landed: the
        window stays open until the slot starts, so arming again is normal.
        """
        try:
            bookings = self.entry.runtime_data.coordinator.data.bookings
        except AttributeError:
            return False
        wanted = set(self.job.space_ids)
        return any(
            booking.start == slot_start and wanted.intersection(booking.space_ids)
            for booking in bookings
        )

    async def _async_no_op(
        self, slot_start: datetime, slot_end: datetime, *, reason: str | None
    ) -> BookingOutcome:
        """Record a run that deliberately fired nothing."""
        outcome = BookingOutcome(
            job_id=self.job.job_id,
            succeeded=False,
            booking_id=None,
            space_id=None,
            slot_start=slot_start,
            slot_end=slot_end,
            attempts=(),
            finished_at=dt_util.utcnow(),
            no_attempt_reason=reason,
            account=self.entry.title,
        )
        await self._async_finish(outcome)
        return outcome

    async def _async_finish(self, outcome: BookingOutcome) -> None:
        await self.store.async_record(outcome)
        self._async_notify_entities(refresh=outcome.succeeded)
        await async_dispatch(self.sinks, outcome, self.job)

    @callback
    def _async_notify_entities(self, *, refresh: bool) -> None:
        """Push the new outcome to the entities, and only then ask the venue.

        The history lives in the store, which nothing polls, so without the
        first part the job's sensor would report the previous run until the
        next poll. The second part is for the booking we just made - a run
        that booked nothing changed nothing at the venue, and asking anyway is
        two requests for no new information.
        """
        try:
            coordinator = self.entry.runtime_data.coordinator
        except AttributeError:
            return
        coordinator.async_update_listeners()
        if refresh:
            self.hass.async_create_task(coordinator.async_request_refresh())

    async def _async_sleep_until(self, server_instant: datetime) -> None:
        """Sleep until our clock reads the moment Skedda's clock reads this.

        asyncio.sleep schedules through loop.call_later, which keeps the
        sub-second resolution that async_track_point_in_time cannot give.
        """
        clock = getattr(getattr(self.provider, "client", None), "clock", None)
        target = clock.local_instant_for(server_instant) if clock else server_instant
        delay = (target - dt_util.utcnow()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)


class JobScheduler:
    """Keeps one runner per job subentry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._runners: dict[str, JobRunner] = {}

    def runner_for(self, job_id: str) -> JobRunner | None:
        return self._runners.get(job_id)

    @property
    def runners(self) -> dict[str, JobRunner]:
        return dict(self._runners)

    @callback
    def async_sync_jobs(self, sinks: Sequence[ResultSink] | None = None) -> None:
        """Rebuild every runner from the entry's subentries."""
        runtime = self.entry.runtime_data
        timezone = venue_timezone_for(self.hass, self.entry)
        sinks = list(sinks) if sinks is not None else build_sinks(self.hass)

        self.async_shutdown()
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_JOB:
                continue
            try:
                job = build_job(subentry_id, subentry.data, timezone)
            except ValueError, KeyError:
                # A stored job that cannot be built is the user's to fix; the
                # rest of their jobs must keep running regardless.
                _LOGGER.exception("Ignoring misconfigured job %s", subentry_id)
                continue
            runner = JobRunner(
                hass=self.hass,
                entry=self.entry,
                provider=runtime.provider,
                job=job,
                sinks=sinks,
                store=runtime.store,
                semaphore=runtime.semaphore,
                on_schedule=self._async_publish_next_arming,
            )
            self._runners[subentry_id] = runner
            runner.async_schedule()
        self._async_publish_next_arming()

    @callback
    def async_shutdown(self) -> None:
        for runner in self._runners.values():
            runner.async_cancel()
        self._runners.clear()
        self._async_publish_next_arming()

    @callback
    def _async_publish_next_arming(self) -> None:
        """Let the coordinator match its poll rate to the nearest booking.

        Nothing else knows when this account is busy: out of season there is
        no reason to ask the venue anything, and in the hour before a window
        there is every reason.
        """
        try:
            coordinator = self.entry.runtime_data.coordinator
        except AttributeError:
            return
        armed = [runner.armed_for for runner in self._runners.values() if runner.armed_for]
        coordinator.async_note_next_arming(min(armed) if armed else None)

    async def async_run_now(self, job_id: str) -> BookingOutcome | None:
        runner = self._runners.get(job_id)
        if runner is None:
            _LOGGER.warning("No runner for job %s", job_id)
            return None
        return await runner.async_run_now()


async def async_build_job_sinks(hass: HomeAssistant) -> list[ResultSink]:
    """Where a finished run reports to, including the calendar if linked."""
    return build_sinks(hass, await google_calendar.async_build_sink(hass))

"""Arming, firing and retrying booking attempts.

The timing arithmetic lives in core/strategy.py and core/window.py. This module
is the part that touches the clock and the network: it wakes up early, warms the
session, sleeps to the millisecond on the server's clock, and classifies what
comes back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

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
from .sinks import ResultSink, async_dispatch, build_default_sinks
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
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.provider = provider
        self.job = job
        self.sinks = list(sinks)
        self.store = store
        self.semaphore = semaphore
        self.armed_for: datetime | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def next_run(self) -> datetime | None:
        """The instant this job's next slot becomes bookable."""
        return self.job.next_window_open(dt_util.utcnow())

    @callback
    def async_schedule(self) -> None:
        """Arm for the next window.

        A window that opened while Home Assistant was down is still open - the
        horizon is rolling, and a slot stays bookable right up until it starts.
        Waiting for the next one would concede exactly the slot this integration
        exists to win, so the runner arms for now instead.
        """
        self.async_cancel()
        if not self.job.enabled:
            return

        now = dt_util.utcnow()
        opens_at = self.job.next_window_open(now)
        if opens_at is None:
            _LOGGER.debug("Job %s has no future slot; season is over", self.job.job_id)
            return

        arm_at = build_strategy(self.job.strategy).plan(opens_at).arm_at
        self.armed_for = max(arm_at, now)
        self._unsub = async_track_point_in_utc_time(self.hass, self._async_armed, self.armed_for)
        _LOGGER.debug(
            "Job %s armed for %s (window opens %s)", self.job.job_id, self.armed_for, opens_at
        )

    @callback
    def async_cancel(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self.armed_for = None

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
        )
        await self._async_finish(outcome)
        return outcome

    async def _async_finish(self, outcome: BookingOutcome) -> None:
        await self.store.async_record(outcome)
        self._async_notify_entities()
        await async_dispatch(self.sinks, outcome, self.job)

    @callback
    def _async_notify_entities(self) -> None:
        """Push the new outcome to the entities, then catch the data up.

        The history lives in the store, which nothing polls, so without this
        the job's sensor would keep reporting the previous run until the next
        quarter-hourly poll. The refresh is for the booking we just made.
        """
        try:
            coordinator = self.entry.runtime_data.coordinator
        except AttributeError:
            return
        coordinator.async_update_listeners()
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
    def async_sync_jobs(self) -> None:
        """Rebuild every runner from the entry's subentries."""
        runtime = self.entry.runtime_data
        timezone = venue_timezone_for(self.hass, self.entry)
        sinks = build_default_sinks(self.hass)

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
            )
            self._runners[subentry_id] = runner
            runner.async_schedule()

    @callback
    def async_shutdown(self) -> None:
        for runner in self._runners.values():
            runner.async_cancel()
        self._runners.clear()

    async def async_run_now(self, job_id: str) -> BookingOutcome | None:
        runner = self._runners.get(job_id)
        if runner is None:
            _LOGGER.warning("No runner for job %s", job_id)
            return None
        return await runner.async_run_now()

"""The burst loop: how hard we try, and when we stop."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api.errors import (
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
from custom_components.skedda_scheduler.const import (
    MAX_ATTEMPTS_PER_RUN,
    SUBENTRY_TYPE_JOB,
)
from custom_components.skedda_scheduler.core.job import BookingJob
from custom_components.skedda_scheduler.core.provider import Booking
from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule
from custom_components.skedda_scheduler.core.result import AttemptStatus
from custom_components.skedda_scheduler.core.window import BookingWindow
from custom_components.skedda_scheduler.scheduler import (
    RATE_LIMIT_BACKOFF_SECONDS,
    JobRunner,
)
from custom_components.skedda_scheduler.store import AttemptStore

JOB = BookingJob(
    job_id="job-1",
    name="Tuesday 18:00",
    space_ids=("2000001",),
    start_time=time(18, 0),
    duration_minutes=60,
    recurrence=RecurrenceRule(frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 1)),
    window=BookingWindow(window_days=14),
    venue_timezone="Europe/Kyiv",
    title="Tennis (auto)",
)

BOOKING = Booking(
    id="bk-1",
    space_ids=("2000001",),
    start=datetime(2026, 9, 8, 18, 0, tzinfo=JOB.tz),
    end=datetime(2026, 9, 8, 19, 0, tzinfo=JOB.tz),
    title="Tennis (auto)",
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

JOB_INPUT = {
    "name": "Tuesday 18:00",
    "space_id": "2000001",
    "weekday": "1",
    "start_time": "18:00:00",
    "duration_minutes": 60,
    "window_days": 14,
    "frequency": "weekly",
    "season_start": "2026-09-01",
    "title": "Tennis (auto)",
    "strategy": "precise",
}


@pytest.fixture
def no_sleep() -> Iterator[None]:
    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        yield


@pytest.fixture
async def runner(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> JobRunner:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    store = AttemptStore(hass, mock_entry)
    await store.async_load()
    return JobRunner(
        hass=hass,
        entry=mock_entry,
        provider=mock_provider,
        job=JOB,
        sinks=[],
        store=store,
        semaphore=asyncio.Semaphore(1),
    )


async def test_a_first_shot_that_lands_stops_the_burst(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    mock_provider.book.return_value = BOOKING

    outcome = await runner.async_run_now()

    assert outcome.succeeded
    assert outcome.booking_id == "bk-1"
    assert outcome.space_id == "2000001"
    assert len(outcome.attempts) == 1


async def test_too_early_keeps_firing_until_the_window_opens(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    """The burst straddles the opening instant, so early shots are expected."""
    mock_provider.book.side_effect = [TooEarlyError("nope"), TooEarlyError("nope"), BOOKING]

    outcome = await runner.async_run_now()

    assert outcome.succeeded
    assert [attempt.status for attempt in outcome.attempts] == [
        AttemptStatus.TOO_EARLY,
        AttemptStatus.TOO_EARLY,
        AttemptStatus.SUCCESS,
    ]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (SlotTakenError("gone"), "slot_taken"),
        (ApiContractError("shape changed"), "contract_error"),
        (QuotaExceededError("out of hours"), "quota_exceeded"),
        (BookingWindowClosedError("too far ahead"), "window_closed"),
    ],
)
async def test_a_verdict_that_cannot_change_ends_the_run_at_once(
    runner: JobRunner,
    mock_provider: AsyncMock,
    no_sleep: None,
    error: Exception,
    reason: str,
) -> None:
    """Hammering the server changes none of these, and rate limiting gets worse."""
    mock_provider.book.side_effect = error

    outcome = await runner.async_run_now()

    assert not outcome.succeeded
    assert outcome.failure_reason == reason
    assert len(outcome.attempts) == 1


async def test_rate_limiting_backs_off_before_trying_again(
    runner: JobRunner, mock_provider: AsyncMock
) -> None:
    """429 is transient, so the run continues - but not at burst cadence.

    Firing again a quarter of a second later is what earns a longer ban.
    """
    mock_provider.book.side_effect = [RateLimitedError("slow down"), BOOKING]

    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep") as sleep:
        outcome = await runner.async_run_now()

    assert outcome.succeeded
    assert RATE_LIMIT_BACKOFF_SECONDS in [call.args[0] for call in sleep.await_args_list]


async def test_a_dropped_connection_is_worth_another_shot(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    mock_provider.book.side_effect = [SkeddaConnectionError("reset"), BOOKING]

    outcome = await runner.async_run_now()

    assert outcome.succeeded
    assert len(outcome.attempts) == 2


async def test_an_expired_session_is_re_established_once_then_retried(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    mock_provider.book.side_effect = [AuthExpiredError("expired"), BOOKING]
    mock_provider.authenticate.reset_mock()

    outcome = await runner.async_run_now()

    assert outcome.succeeded
    mock_provider.authenticate.assert_awaited()


async def test_a_second_expiry_is_not_retried_again(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    """Two rejections in a row is a password problem, not a stale cookie."""
    mock_provider.book.side_effect = AuthExpiredError("expired")

    outcome = await runner.async_run_now()

    assert not outcome.succeeded
    assert len(outcome.attempts) == 2


async def test_attempts_never_exceed_the_per_run_ceiling(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    mock_provider.book.side_effect = TooEarlyError("nope")

    outcome = await runner.async_run_now()

    assert 1 < len(outcome.attempts) <= MAX_ATTEMPTS_PER_RUN


async def test_the_outcome_is_written_to_the_history_store(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    mock_provider.book.return_value = BOOKING

    await runner.async_run_now()

    last = runner.store.last_outcome("job-1")
    assert last is not None
    assert last["succeeded"] is True


async def test_a_job_past_its_season_fires_nothing(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    runner.job = replace(
        JOB,
        recurrence=replace(JOB.recurrence, season_end=date(2026, 9, 1)),
    )

    outcome = await runner.async_run_now()

    assert not outcome.succeeded
    assert outcome.attempts == ()
    assert mock_provider.book.await_count == 0


async def test_a_slot_already_on_the_books_is_not_booked_twice(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    """Home Assistant restarting must not re-fire a job that already landed.

    The window stays open right up to the slot, so an arming run after a
    restart would otherwise throw another request at a booking we hold.
    """
    slot_start, _ = JOB.next_slot(NOW)
    runner.entry.runtime_data.coordinator.data.bookings = [replace(BOOKING, start=slot_start)]

    with patch("custom_components.skedda_scheduler.scheduler.dt_util.utcnow", return_value=NOW):
        outcome = await runner.async_run_now()

    assert not outcome.succeeded
    assert outcome.failure_reason == "already_booked"
    assert mock_provider.book.await_count == 0


async def test_next_run_is_the_instant_the_window_opens(runner: JobRunner) -> None:
    with patch("custom_components.skedda_scheduler.scheduler.dt_util.utcnow", return_value=NOW):
        # Slot 2026-09-08 18:00 Kyiv, minus the venue's 14-day horizon.
        assert runner.next_run == datetime(2026, 8, 25, 18, 0, tzinfo=JOB.tz)


async def test_an_already_open_window_arms_immediately_rather_than_waiting(
    runner: JobRunner,
) -> None:
    """A window that opened while Home Assistant was down is still open.

    Waiting for the next one would concede exactly the slot this integration
    exists to win.
    """
    with patch("custom_components.skedda_scheduler.scheduler.dt_util.utcnow", return_value=NOW):
        runner.async_schedule()

    assert runner.armed_for == NOW
    runner.async_cancel()
    assert runner.armed_for is None


async def test_a_disabled_job_is_never_armed(runner: JobRunner) -> None:
    runner.job = replace(JOB, enabled=False)

    runner.async_schedule()

    assert runner.armed_for is None


async def test_a_finished_season_is_never_armed(runner: JobRunner) -> None:
    runner.job = replace(JOB, recurrence=replace(JOB.recurrence, season_end=date(2026, 9, 1)))

    runner.async_schedule()

    assert runner.armed_for is None
    assert runner.next_run is None


async def test_the_burst_sleeps_on_skedda_s_clock_not_ours(
    runner: JobRunner, mock_provider: AsyncMock
) -> None:
    """Our clock can sit seconds away from the server's; the server's decides."""
    mock_provider.book.return_value = BOOKING
    mock_provider.client.clock.local_instant_for.side_effect = lambda instant: (
        instant + timedelta(seconds=5)
    )

    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep") as sleep:
        await runner.async_run_now()

    mock_provider.client.clock.local_instant_for.assert_called()
    assert sleep.await_count >= 1


async def test_every_job_subentry_gets_an_armed_runner(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_JOB), context={"source": "user"}
    )
    await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    await hass.async_block_till_done()

    scheduler = mock_entry.runtime_data.scheduler
    job_id = next(iter(mock_entry.subentries))
    runner = scheduler.runner_for(job_id)
    assert runner is not None
    assert runner.armed_for is not None
    assert runner.job.venue_timezone == "Europe/Kyiv"


async def test_a_job_stored_in_a_shape_we_cannot_build_is_skipped_not_fatal(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """One broken job must not take the account's other jobs down with it.

    The form would never produce this, but a hand-edited .storage file or a
    future config version can.
    """
    mock_entry.add_to_hass(hass)
    for index, data in enumerate(({**JOB_INPUT, "season_start": "not-a-date"}, JOB_INPUT)):
        hass.config_entries.async_add_subentry(
            mock_entry,
            ConfigSubentry(
                data=data,
                subentry_id=f"sub-{index}",
                subentry_type=SUBENTRY_TYPE_JOB,
                title=str(data["name"]),
                unique_id=None,
            ),
        )
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert len(mock_entry.subentries) == 2
    assert list(mock_entry.runtime_data.scheduler.runners) == ["sub-1"]


async def test_unloading_the_account_disarms_every_job(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_JOB), context={"source": "user"}
    )
    await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    await hass.async_block_till_done()
    scheduler = mock_entry.runtime_data.scheduler

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert scheduler.runners == {}


async def test_running_an_unknown_job_now_is_reported_not_raised(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert await mock_entry.runtime_data.scheduler.async_run_now("nope") is None


async def test_running_a_known_job_now_goes_through_the_scheduler(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_add_subentry(
        mock_entry,
        ConfigSubentry(
            data=JOB_INPUT,
            subentry_id="sub-1",
            subentry_type=SUBENTRY_TYPE_JOB,
            title="Tuesday 18:00",
            unique_id=None,
        ),
    )
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    outcome = await mock_entry.runtime_data.scheduler.async_run_now("sub-1")

    assert outcome is not None
    assert outcome.job_id == "sub-1"


async def test_subentries_of_another_type_are_left_alone(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Nothing else uses subentries yet, but the scheduler must not assume so."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_add_subentry(
        mock_entry,
        ConfigSubentry(
            data={},
            subentry_id="sub-x",
            subentry_type="something_else",
            title="Not a job",
            unique_id=None,
        ),
    )
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.runtime_data.scheduler.runners == {}


async def test_an_unfamiliar_failure_is_recorded_as_a_contract_error(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    """An error the taxonomy does not name means Skedda changed something."""
    mock_provider.book.side_effect = SkeddaError("something new")

    outcome = await runner.async_run_now()

    assert outcome.failure_reason == "contract_error"


async def test_a_warm_up_failure_does_not_abort_the_run(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    """The burst is the point; a failed warm-up only costs us the head start."""
    mock_provider.is_authenticated = False
    mock_provider.authenticate.side_effect = SkeddaConnectionError("down")
    mock_provider.book.return_value = BOOKING

    outcome = await runner.async_run_now()

    assert outcome.succeeded


async def test_a_failed_re_login_mid_burst_ends_the_run(
    runner: JobRunner, mock_provider: AsyncMock, no_sleep: None
) -> None:
    mock_provider.book.side_effect = AuthExpiredError("expired")
    mock_provider.authenticate.side_effect = SkeddaAuthError("rejected")

    outcome = await runner.async_run_now()

    assert not outcome.succeeded
    assert len(outcome.attempts) == 1


async def test_an_account_that_never_loaded_cannot_check_for_a_double_booking(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, no_sleep: None
) -> None:
    """Without a poll to consult, firing is the safer of the two mistakes."""
    mock_entry.add_to_hass(hass)
    store = AttemptStore(hass, mock_entry)
    await store.async_load()
    runner = JobRunner(
        hass=hass,
        entry=mock_entry,
        provider=mock_provider,
        job=JOB,
        sinks=[],
        store=store,
        semaphore=asyncio.Semaphore(1),
    )
    mock_provider.book.return_value = BOOKING

    assert (await runner.async_run_now()).succeeded


async def test_arming_a_job_whose_season_ends_before_it_fires_does_nothing(
    runner: JobRunner, mock_provider: AsyncMock
) -> None:
    """The season can end between arming and the alarm going off."""
    runner.job = replace(JOB, recurrence=replace(JOB.recurrence, season_end=date(2026, 9, 1)))

    await runner._async_armed(NOW)

    assert mock_provider.book.await_count == 0

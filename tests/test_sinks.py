"""Fan-out of booking outcomes."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.skedda_scheduler.const import (
    EVENT_BOOKING_FAILED,
    EVENT_BOOKING_SUCCEEDED,
)
from custom_components.skedda_scheduler.core.job import BookingJob
from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule
from custom_components.skedda_scheduler.core.result import (
    AttemptStatus,
    BookingAttempt,
    BookingOutcome,
)
from custom_components.skedda_scheduler.core.window import BookingWindow
from custom_components.skedda_scheduler.sinks import async_dispatch, build_default_sinks
from custom_components.skedda_scheduler.sinks.ha_event import HaEventSink
from custom_components.skedda_scheduler.sinks.notify import NotifySink, build_message

KYIV = ZoneInfo("Europe/Kyiv")
SLOT_START = datetime(2026, 9, 29, 18, 0, tzinfo=KYIV)

JOB = BookingJob(
    job_id="job-1",
    name="Tuesday 18:00",
    space_ids=("2000001",),
    start_time=time(18, 0),
    duration_minutes=60,
    recurrence=RecurrenceRule(frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 1)),
    window=BookingWindow(window_days=14),
    venue_timezone="Europe/Kyiv",
    title="Tennis",
    notify_targets=("notify.telegram",),
)


def outcome(*, succeeded: bool) -> BookingOutcome:
    return BookingOutcome(
        job_id="job-1",
        succeeded=succeeded,
        booking_id="bk-1" if succeeded else None,
        space_id="2000001" if succeeded else None,
        slot_start=SLOT_START,
        slot_end=SLOT_START.replace(hour=19),
        attempts=(BookingAttempt(1, SLOT_START, AttemptStatus.SLOT_TAKEN, 9.0),),
        finished_at=SLOT_START,
    )


@pytest.fixture
def notify_calls(hass: HomeAssistant) -> list[ServiceCall]:
    calls: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("notify", "telegram", record)
    return calls


async def test_a_success_fires_the_success_event(hass: HomeAssistant) -> None:
    events = async_capture_events(hass, EVENT_BOOKING_SUCCEEDED)

    await HaEventSink(hass).async_handle(outcome(succeeded=True), JOB)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["job_name"] == "Tuesday 18:00"
    assert events[0].data["booking_id"] == "bk-1"
    assert events[0].data["job_id"] == "job-1"


async def test_a_failure_fires_the_failure_event(hass: HomeAssistant) -> None:
    events = async_capture_events(hass, EVENT_BOOKING_FAILED)

    await HaEventSink(hass).async_handle(outcome(succeeded=False), JOB)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["failure_reason"] == "slot_taken"


async def test_the_notify_sink_calls_each_configured_target(
    hass: HomeAssistant, notify_calls: list[ServiceCall]
) -> None:
    await NotifySink(hass).async_handle(outcome(succeeded=True), JOB)
    await hass.async_block_till_done()

    assert len(notify_calls) == 1
    assert "Tuesday 18:00" in notify_calls[0].data["message"]


async def test_the_message_shows_the_slot_in_the_venue_s_own_time(
    hass: HomeAssistant,
) -> None:
    """The court is booked for 18:00 there, whatever the clock says here.

    Home Assistant's zone is the user's, not the venue's; rendering the slot in
    it would report a time the booking was never for.
    """
    await hass.config.async_set_time_zone("America/New_York")

    message = build_message(outcome(succeeded=True), JOB)

    assert "18:00" in message
    assert "11:00" not in message


async def test_the_message_names_the_job_and_the_reason_it_failed(
    hass: HomeAssistant,
) -> None:
    message = build_message(outcome(succeeded=False), JOB)
    assert "Tuesday 18:00" in message
    assert "slot_taken" in message


async def test_the_notify_sink_does_nothing_without_targets(
    hass: HomeAssistant, notify_calls: list[ServiceCall]
) -> None:
    await NotifySink(hass).async_handle(outcome(succeeded=True), replace(JOB, notify_targets=()))
    await hass.async_block_till_done()

    assert notify_calls == []


async def test_one_dead_notify_target_does_not_silence_the_others(
    hass: HomeAssistant, notify_calls: list[ServiceCall]
) -> None:
    """Notifications are how the user learns anything happened at all."""
    job = replace(JOB, notify_targets=("notify.deleted_by_the_user", "notify.telegram"))

    await NotifySink(hass).async_handle(outcome(succeeded=True), job)
    await hass.async_block_till_done()

    assert len(notify_calls) == 1


async def test_a_malformed_notify_target_is_skipped(
    hass: HomeAssistant, notify_calls: list[ServiceCall]
) -> None:
    job = replace(JOB, notify_targets=("telegram", "notify.telegram"))

    await NotifySink(hass).async_handle(outcome(succeeded=True), job)
    await hass.async_block_till_done()

    assert len(notify_calls) == 1


async def test_dispatch_continues_after_a_failing_sink(hass: HomeAssistant) -> None:
    broken = AsyncMock()
    broken.async_handle.side_effect = RuntimeError("boom")
    good = AsyncMock()

    await async_dispatch([broken, good], outcome(succeeded=True), JOB)

    good.async_handle.assert_awaited_once()


async def test_the_default_sinks_are_the_event_bus_and_notifications(
    hass: HomeAssistant,
) -> None:
    sinks = build_default_sinks(hass)
    assert [type(sink) for sink in sinks] == [HaEventSink, NotifySink]

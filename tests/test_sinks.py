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


async def test_a_notify_entity_is_sent_to_rather_than_called_as_a_service(
    hass: HomeAssistant,
) -> None:
    """Observed live 2026-09-15: every notification failed.

    Modern notify targets are entities, and the job form offers entities. They
    are reached with notify.send_message and an entity_id; calling
    notify.<entity> as a service finds nothing and the user hears nothing.
    """
    sent: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        sent.append(call)

    hass.services.async_register("notify", "send_message", record)
    hass.states.async_set("notify.iphone", "unknown")

    await NotifySink(hass).async_handle(
        outcome(succeeded=True), replace(JOB, notify_targets=("notify.iphone",))
    )
    await hass.async_block_till_done()

    assert len(sent) == 1
    assert sent[0].data["entity_id"] == "notify.iphone"
    assert "Tuesday 18:00" in sent[0].data["message"]


async def test_a_legacy_notify_service_still_works(
    hass: HomeAssistant, notify_calls: list[ServiceCall]
) -> None:
    """Older installations name a service, not an entity; both must reach the user."""
    await NotifySink(hass).async_handle(outcome(succeeded=True), JOB)
    await hass.async_block_till_done()

    assert len(notify_calls) == 1
    assert "entity_id" not in notify_calls[0].data


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


async def test_a_run_that_sent_nothing_sends_no_notification(
    hass: HomeAssistant, notify_calls: list[ServiceCall]
) -> None:
    """A job whose slot is already ours fires nothing and is not news.

    Reported 2026-09-16: a phone buzzing constantly. Every arming of an
    already-booked job ended in a run with no attempts, and every one of those
    was announced.
    """
    nothing_happened = replace(
        outcome(succeeded=False), attempts=(), no_attempt_reason="already_booked"
    )

    await NotifySink(hass).async_handle(nothing_happened, JOB)
    await hass.async_block_till_done()

    assert notify_calls == []


async def test_a_real_attempt_is_always_announced(
    hass: HomeAssistant, notify_calls: list[ServiceCall]
) -> None:
    """Both outcomes matter: one is the court, the other is why not."""
    for result in (True, False):
        await NotifySink(hass).async_handle(outcome(succeeded=result), JOB)
    await hass.async_block_till_done()

    assert len(notify_calls) == 2


async def test_the_event_bus_still_hears_about_every_run(
    hass: HomeAssistant,
) -> None:
    """Events are the automation surface and cost nobody's attention."""
    events = async_capture_events(hass, EVENT_BOOKING_FAILED)
    nothing_happened = replace(
        outcome(succeeded=False), attempts=(), no_attempt_reason="already_booked"
    )

    await HaEventSink(hass).async_handle(nothing_happened, JOB)
    await hass.async_block_till_done()

    assert len(events) == 1


async def test_a_sink_takes_anything_that_can_describe_itself(hass: HomeAssistant) -> None:
    """A watch rule is not a job, and wants the same notification."""
    from custom_components.skedda_scheduler.core.watch import WatchRule

    watching = WatchRule(
        rule_id="r1",
        name="Our evening",
        weekdays=frozenset({3}),
        not_before=time(19, 0),
        not_after=time(21, 0),
        space_ids=("2000001",),
        duration_minutes=60,
        venue_timezone="Europe/Kyiv",
    )

    events = async_capture_events(hass, EVENT_BOOKING_SUCCEEDED)

    assert "Our evening" in build_message(outcome(succeeded=True), watching)
    await async_dispatch(build_default_sinks(hass), outcome(succeeded=True), watching)
    await hass.async_block_till_done()

    assert events[0].data["job_name"] == "Our evening"

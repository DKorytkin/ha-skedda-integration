"""Keeping the calendar in step with the court: retries, and releases."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.skedda_scheduler import google_calendar
from custom_components.skedda_scheduler.api.errors import (
    ApiContractError,
    SkeddaConnectionError,
)
from custom_components.skedda_scheduler.api.google import (
    GoogleEvent,
    GoogleListedEvent,
    event_id_for,
)
from custom_components.skedda_scheduler.sinks.google_calendar import (
    RETRY_DELAYS,
    GoogleCalendarSink,
)
from tests.test_sinks import JOB, SLOT_START, outcome

KYIV = ZoneInfo("Europe/Kyiv")
SLOT_END = SLOT_START.replace(hour=19)
EVENT_ID = event_id_for("c", "2000001", SLOT_START)


def sink(hass: HomeAssistant, client: AsyncMock, **overrides: Any) -> GoogleCalendarSink:
    settings: dict[str, Any] = {
        "calendar_id": "c",
        "title": "Tennis 🎾",
        "location": None,
        "attendees": ("oleh@example.com",),
    }
    return GoogleCalendarSink(hass, client, **{**settings, **overrides})


async def later(hass: HomeAssistant, seconds: int) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds + 1))
    await hass.async_block_till_done()


async def test_the_event_id_comes_from_the_booking(hass: HomeAssistant) -> None:
    """So a retry that had in fact landed is refused rather than doubled."""
    client = AsyncMock()

    await sink(hass, client).async_handle(outcome(succeeded=True), JOB)

    assert client.create_event.await_args.kwargs["event_id"] == EVENT_ID


async def test_a_network_that_is_down_is_tried_again_later(
    hass: HomeAssistant, freezer: Any, caplog: Any
) -> None:
    """Seen 2026-10-06: the court booked, Google unreachable, no event ever."""
    client = AsyncMock()
    client.create_event.side_effect = [
        SkeddaConnectionError("Network unreachable"),
        GoogleEvent(id=EVENT_ID, html_link=None),
    ]

    await sink(hass, client).async_handle(outcome(succeeded=True), JOB)
    assert client.create_event.await_count == 1
    assert "trying again" in caplog.text

    freezer.tick(timedelta(seconds=RETRY_DELAYS[0] + 1))
    await later(hass, 0)

    assert client.create_event.await_count == 2


async def test_the_retries_give_up_in_the_end(
    hass: HomeAssistant, freezer: Any, caplog: Any
) -> None:
    client = AsyncMock()
    client.create_event.side_effect = SkeddaConnectionError("down")

    await sink(hass, client).async_handle(outcome(succeeded=True), JOB)
    for delay in RETRY_DELAYS:
        freezer.tick(timedelta(seconds=delay + 1))
        await later(hass, 0)

    assert client.create_event.await_count == len(RETRY_DELAYS) + 1
    assert "after 7 tries" in caplog.text


async def test_a_refusal_is_not_retried(hass: HomeAssistant, freezer: Any) -> None:
    """Google said no; asking again in a minute gets the same answer."""
    client = AsyncMock()
    client.create_event.side_effect = ApiContractError("bad request")

    await sink(hass, client).async_handle(outcome(succeeded=True), JOB)
    freezer.tick(timedelta(hours=2))
    await later(hass, 0)

    assert client.create_event.await_count == 1


async def test_a_booking_released_before_its_retry_never_gets_an_event(
    hass: HomeAssistant, freezer: Any
) -> None:
    client = AsyncMock()
    client.create_event.side_effect = SkeddaConnectionError("down")
    writer = sink(hass, client)
    await writer.async_handle(outcome(succeeded=True), JOB)

    # Another sink, as the panel builds its own.
    await sink(hass, client).async_release("2000001", SLOT_START, SLOT_END)
    freezer.tick(timedelta(seconds=RETRY_DELAYS[0] + 1))
    await later(hass, 0)

    assert client.create_event.await_count == 1
    client.delete_event.assert_not_awaited()


async def test_a_release_deletes_the_event_and_tells_the_guests(hass: HomeAssistant) -> None:
    client = AsyncMock()
    client.delete_event.return_value = True

    await sink(hass, client).async_release("2000001", SLOT_START, SLOT_END)

    client.delete_event.assert_awaited_once_with("c", EVENT_ID, notify=True)
    client.events_between.assert_not_awaited()


async def test_an_event_without_guests_is_deleted_quietly(hass: HomeAssistant) -> None:
    client = AsyncMock()
    client.delete_event.return_value = True

    await sink(hass, client, attendees=()).async_release("2000001", SLOT_START, SLOT_END)

    client.delete_event.assert_awaited_once_with("c", EVENT_ID, notify=False)


def listed(
    event_id: str,
    *,
    summary: str = "Tennis 🎾",
    start: datetime = SLOT_START,
    description: str = "",
) -> GoogleListedEvent:
    return GoogleListedEvent(id=event_id, summary=summary, start=start, description=description)


async def test_an_event_written_before_ids_were_chosen_is_found_by_time(
    hass: HomeAssistant,
) -> None:
    """The 2026-10 events have Google's own ids; nobody kept them."""
    client = AsyncMock()
    client.delete_event.side_effect = [False, True]
    client.events_between.return_value = [
        listed("someone-elses", summary="Dentist"),
        listed("an-hour-earlier", start=SLOT_START - timedelta(hours=1)),
        listed("other-court", description="Skedda booking 99"),
        listed("ours", description="Tuesday 18:00\nSkedda booking bk-1"),
    ]

    await sink(hass, client).async_release("2000001", SLOT_START, SLOT_END, "bk-1")

    client.events_between.assert_awaited_once_with("c", SLOT_START, SLOT_END)
    assert client.delete_event.await_args_list[-1].args == ("c", "ours")


async def test_a_release_with_no_event_anywhere_is_quiet(hass: HomeAssistant, caplog: Any) -> None:
    client = AsyncMock()
    client.delete_event.return_value = False
    client.events_between.return_value = []

    await sink(hass, client).async_release("2000001", SLOT_START, SLOT_END)

    assert "WARNING" not in caplog.text


async def test_a_calendar_that_cannot_be_reached_does_not_stop_the_release(
    hass: HomeAssistant, caplog: Any
) -> None:
    client = AsyncMock()
    client.delete_event.side_effect = SkeddaConnectionError("down")

    await sink(hass, client).async_release("2000001", SLOT_START, SLOT_END)

    assert "could not remove its calendar event" in caplog.text


async def test_a_release_with_no_calendar_linked_does_nothing(hass: HomeAssistant) -> None:
    with patch.object(google_calendar, "async_build_sink", return_value=None) as build:
        await google_calendar.async_release_event(hass, "2000001", SLOT_START, SLOT_END)

    build.assert_awaited_once()


async def test_a_release_goes_to_the_linked_calendar(hass: HomeAssistant) -> None:
    linked = AsyncMock()
    with patch.object(google_calendar, "async_build_sink", return_value=linked):
        await google_calendar.async_release_event(hass, "2000001", SLOT_START, SLOT_END, "bk-1")

    linked.async_release.assert_awaited_once_with("2000001", SLOT_START, SLOT_END, "bk-1")

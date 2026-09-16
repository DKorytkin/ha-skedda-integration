"""Bookings and pending slots as calendars."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.core.provider import Booking
from tests.helpers import setup_with_job

KYIV = ZoneInfo("Europe/Kyiv")
BOOKED = "calendar.main_account_oleh_bookings"
PENDING = "calendar.main_account_oleh_pending"


def booking(start: datetime, *, mine: bool = True, identifier: str = "bk-1") -> Booking:
    return Booking(
        id=identifier,
        space_ids=("2000001",),
        start=start,
        end=start + timedelta(hours=1),
        title="Tennis",
        is_mine=mine,
    )


async def test_an_account_gets_a_calendar_of_what_it_has_booked(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    start = dt_util.utcnow().astimezone(KYIV) + timedelta(days=3)
    mock_provider.list_bookings.return_value = [booking(start)]

    await setup_with_job(hass, mock_entry)

    state = hass.states.get(BOOKED)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["friendly_name"].endswith("Bookings")


async def test_somebody_else_s_booking_is_not_on_our_calendar(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A venue hands back its whole diary; a calendar of it would be noise."""
    start = dt_util.utcnow().astimezone(KYIV) + timedelta(days=3)
    mock_provider.list_bookings.return_value = [booking(start, mine=False, identifier="theirs")]
    await setup_with_job(hass, mock_entry)

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": BOOKED,
            "start_date_time": dt_util.utcnow().isoformat(),
            "end_date_time": (dt_util.utcnow() + timedelta(days=30)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )

    assert events[BOOKED]["events"] == []


async def test_a_booked_slot_appears_with_its_court_and_job(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    start = (dt_util.utcnow() + timedelta(days=3)).astimezone(KYIV).replace(microsecond=0)
    mock_provider.list_bookings.return_value = [booking(start)]
    await setup_with_job(hass, mock_entry)

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": BOOKED,
            "start_date_time": dt_util.utcnow().isoformat(),
            "end_date_time": (dt_util.utcnow() + timedelta(days=30)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )

    found = events[BOOKED]["events"]
    assert len(found) == 1
    assert "Court 1" in found[0]["summary"]


async def test_a_slot_still_waiting_for_its_window_has_its_own_calendar(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Two calendars rather than one, so Home Assistant colours them apart."""
    await setup_with_job(hass, mock_entry)

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": PENDING,
            "start_date_time": dt_util.utcnow().isoformat(),
            "end_date_time": (dt_util.utcnow() + timedelta(days=60)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )

    found = events[PENDING]["events"]
    assert found, "the job's next slot is pending until it is booked"
    assert "Tuesday 18:00" in found[0]["summary"]
    assert "opens" in found[0]["description"]


async def test_a_pending_slot_disappears_once_it_is_booked(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Otherwise the same court time would sit on both calendars at once.

    Only that slot: a weekly job still means to book the weeks after it.
    """
    subentry_id = await setup_with_job(hass, mock_entry)
    runner = mock_entry.runtime_data.scheduler.runner_for(subentry_id)
    slot_start, _ = runner.job.next_slot(dt_util.utcnow())
    mock_provider.list_bookings.return_value = [booking(slot_start)]
    await mock_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": PENDING,
            "start_date_time": dt_util.utcnow().isoformat(),
            "end_date_time": (dt_util.utcnow() + timedelta(days=60)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )

    starts = [event["start"] for event in events[PENDING]["events"]]
    assert slot_start.isoformat() not in starts
    assert starts, "the weeks after it are still pending"


async def test_the_pending_calendar_stops_at_the_horizon(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A weekly job runs for years; a calendar of every future slot is noise."""
    from custom_components.skedda_scheduler.calendar import PENDING_HORIZON

    await setup_with_job(hass, mock_entry)

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": PENDING,
            "start_date_time": dt_util.utcnow().isoformat(),
            "end_date_time": (dt_util.utcnow() + timedelta(days=365)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )

    latest = max(event["start"] for event in events[PENDING]["events"])
    assert latest < (dt_util.utcnow() + PENDING_HORIZON).isoformat()


async def test_a_fortnightly_job_is_cut_off_by_the_horizon_not_the_count(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Twelve fortnights reach further than three months.

    The two guards catch different jobs, which is why both are there.
    """
    from custom_components.skedda_scheduler.calendar import PENDING_HORIZON

    await setup_with_job(hass, mock_entry, frequency="biweekly")

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": PENDING,
            "start_date_time": dt_util.utcnow().isoformat(),
            "end_date_time": (dt_util.utcnow() + timedelta(days=365)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )

    found = events[PENDING]["events"]
    assert len(found) < 12, "the horizon bit before the count did"
    assert max(event["start"] for event in found) < (dt_util.utcnow() + PENDING_HORIZON).isoformat()

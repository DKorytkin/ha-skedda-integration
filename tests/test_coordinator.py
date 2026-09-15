"""Polling account health, venue rules and upcoming bookings."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api.errors import (
    SkeddaAuthError,
    SkeddaConnectionError,
)
from custom_components.skedda_scheduler.coordinator import (
    DISTANT_INTERVAL,
    IDLE_INTERVAL,
    LOOKAHEAD,
    UPDATE_INTERVAL,
    SkeddaCoordinator,
)
from custom_components.skedda_scheduler.core.provider import Booking
from tests.conftest import VENUE_RULES

KYIV = ZoneInfo("Europe/Kyiv")

BOOKING = Booking(
    id="bk-1",
    space_ids=("2000001",),
    start=datetime(2026, 9, 29, 10, 0, tzinfo=KYIV),
    end=datetime(2026, 9, 29, 11, 0, tzinfo=KYIV),
    title="Tennis",
)


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> SkeddaCoordinator:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: SkeddaCoordinator = entry.runtime_data.coordinator
    return coordinator


async def test_the_coordinator_exposes_spaces_bookings_and_venue_rules(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    mock_provider.list_bookings.return_value = [BOOKING]

    data = (await setup_entry(hass, mock_entry)).data

    assert [space.name for space in data.spaces] == ["Court 1", "Court 2"]
    assert [booking.id for booking in data.bookings] == ["bk-1"]
    # The venue's own limits, so nothing downstream has to ask again.
    assert data.rules.weekly_quota_minutes == 60
    assert data.rules.max_days_ahead == 14


async def test_the_first_refresh_is_what_signs_in(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Setting the entry up does not authenticate; the coordinator does."""
    mock_provider.is_authenticated = False

    await setup_entry(hass, mock_entry)

    mock_provider.authenticate.assert_awaited_once()


async def test_an_existing_session_is_not_thrown_away_on_every_poll(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Re-logging in every 15 minutes is both wasteful and rate-limit bait."""
    coordinator = await setup_entry(hass, mock_entry)
    mock_provider.authenticate.reset_mock()

    await coordinator.async_refresh()

    mock_provider.authenticate.assert_not_awaited()


@freeze_time("2026-09-15T21:30:00Z")
async def test_the_booking_window_is_asked_for_in_venue_local_time(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Skedda's list endpoint speaks venue-local wall clock, with no offset.

    At 21:30 UTC it is already the next day in Kyiv. Passing UTC would ask for
    the wrong day entirely, and the request carries nothing to reveal the slip.
    """
    await setup_entry(hass, mock_entry)

    window = mock_provider.list_bookings.await_args.args[0]
    assert window.start.tzinfo is not None
    assert window.start.replace(tzinfo=None) == datetime(2026, 9, 16, 0, 30)
    assert window.end == window.start + LOOKAHEAD


async def test_rejected_credentials_start_a_reauth_flow(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    mock_provider.is_authenticated = False
    mock_provider.authenticate.side_effect = SkeddaAuthError("rejected")

    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress()
    )


async def test_a_venue_that_is_down_leaves_the_entry_to_retry(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Not a reauth: the password is fine, the venue is not answering."""
    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")

    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_a_failed_poll_after_setup_is_reported_not_fatal(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    coordinator = await setup_entry(hass, mock_entry)
    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.authenticated is False


async def test_a_session_that_expired_mid_poll_is_re_established(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    coordinator = await setup_entry(hass, mock_entry)
    mock_provider.authenticate.reset_mock()
    mock_provider.is_authenticated = False

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    mock_provider.authenticate.assert_awaited_once()


async def test_an_account_with_nothing_due_polls_rarely(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The frequent poll is for the hour before a window, not for all year."""
    coordinator = await setup_entry(hass, mock_entry)
    assert coordinator.update_interval == IDLE_INTERVAL == timedelta(hours=12)
    assert timedelta(minutes=15) == UPDATE_INTERVAL


@freeze_time("2026-09-15T21:30:00Z")
async def test_an_unresolvable_venue_timezone_does_not_stop_the_poll(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A zone name we cannot resolve is worth a warning, not an outage."""
    await hass.config.async_set_time_zone("UTC")
    mock_provider.venue_settings.return_value = replace(VENUE_RULES, timezone="Mars/Olympus")

    coordinator = await setup_entry(hass, mock_entry)

    assert coordinator.last_update_success is True
    window = mock_provider.list_bookings.await_args.args[0]
    assert window.start.replace(tzinfo=None) == datetime(2026, 9, 15, 21, 30)


async def test_the_poll_slows_down_when_nothing_is_due(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Out of season there is nothing to watch for.

    Polling a venue every quarter of an hour all winter is thousands of
    requests nobody asked for, against a service that never invited us.
    """
    coordinator = await setup_entry(hass, mock_entry)

    assert coordinator.update_interval == IDLE_INTERVAL


async def test_the_poll_speeds_up_as_a_window_approaches(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Close to an opening the venue's state is worth knowing accurately."""
    coordinator = await setup_entry(hass, mock_entry)
    now = dt_util.utcnow()

    coordinator.async_note_next_arming(now + timedelta(hours=5))
    assert coordinator.update_interval == DISTANT_INTERVAL

    coordinator.async_note_next_arming(now + timedelta(minutes=20))
    assert coordinator.update_interval == UPDATE_INTERVAL


async def test_a_job_that_will_never_fire_again_leaves_the_poll_idle(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    coordinator = await setup_entry(hass, mock_entry)

    coordinator.async_note_next_arming(None)

    assert coordinator.update_interval == IDLE_INTERVAL


async def test_a_booking_further_off_than_a_day_polls_at_the_idle_rate(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A fortnight before the window there is nothing to watch for yet."""
    coordinator = await setup_entry(hass, mock_entry)

    coordinator.async_note_next_arming(dt_util.utcnow() + timedelta(days=10))

    assert coordinator.update_interval == IDLE_INTERVAL

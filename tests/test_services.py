"""Custom services."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import DOMAIN
from tests.helpers import setup_with_job


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_the_services_are_registered(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)

    assert hass.services.has_service(DOMAIN, "trigger_job_now")
    assert hass.services.has_service(DOMAIN, "refresh_spaces")


async def test_trigger_job_now_runs_the_job(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)
    mock_provider.book.reset_mock()

    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        await hass.services.async_call(
            DOMAIN, "trigger_job_now", {"job_id": "sub-1"}, blocking=True
        )

    mock_provider.book.assert_awaited()


async def test_triggering_a_job_that_does_not_exist_says_so(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Silence would be indistinguishable from a job that ran and failed."""
    await setup_entry(hass, mock_entry)

    with pytest.raises(ServiceValidationError, match="sub-nope"):
        await hass.services.async_call(
            DOMAIN, "trigger_job_now", {"job_id": "sub-nope"}, blocking=True
        )


async def test_refresh_spaces_re_reads_the_venue(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    mock_provider.list_spaces.reset_mock()

    await hass.services.async_call(DOMAIN, "refresh_spaces", {}, blocking=True)
    await hass.async_block_till_done()

    mock_provider.list_spaces.assert_awaited()


async def test_refresh_spaces_ignores_entries_that_are_not_accounts(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A linked calendar is loaded too, and has no coordinator to refresh."""
    from tests.test_google_calendar import calendar_entry

    await setup_with_job(hass, mock_entry)
    linked = calendar_entry()
    linked.add_to_hass(hass)
    with patch(
        "custom_components.skedda_scheduler.google_calendar.async_build_client",
        return_value=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(linked.entry_id)
        await hass.async_block_till_done()
    mock_provider.list_spaces.reset_mock()

    await hass.services.async_call(DOMAIN, "refresh_spaces", blocking=True)

    assert mock_provider.list_spaces.await_count >= 1


async def test_slot_freed_makes_the_watch_look_now(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A Telegram automation can beat the poll by minutes."""
    from tests.helpers import setup_account, watch_entry_with_rule

    # No booking job: a week one is aiming at is deliberately left alone.
    await setup_account(hass, mock_entry)
    await watch_entry_with_rule(hass)
    mock_provider.list_bookings.return_value = []
    mock_provider.book.reset_mock()

    await hass.services.async_call(DOMAIN, "slot_freed", {}, blocking=True)

    assert mock_provider.book.await_count >= 1


async def test_slot_freed_accepts_what_the_automation_could_parse(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Every field optional: the watch re-reads the venue and decides itself."""
    from tests.helpers import watch_entry_with_rule

    await setup_with_job(hass, mock_entry)
    await watch_entry_with_rule(hass)

    await hass.services.async_call(
        DOMAIN,
        "slot_freed",
        {"space": "Court 1", "start": "2026-10-01T20:00:00"},
        blocking=True,
    )


async def test_slot_freed_without_a_watch_is_not_an_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)

    await hass.services.async_call(DOMAIN, "slot_freed", {}, blocking=True)

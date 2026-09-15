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

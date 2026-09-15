"""Enabling and disabling a booking job."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.helpers import setup_with_job

ENTITY = "switch.tuesday_18_00_job_enabled"


async def toggle(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call("switch", service, {"entity_id": ENTITY}, blocking=True)
    await hass.async_block_till_done()


async def test_a_new_job_is_enabled(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)

    assert hass.states.get(ENTITY).state == "on"


async def test_turning_the_switch_off_persists_into_the_subentry(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A disabled job survives a restart; that is the point of it."""
    await setup_with_job(hass, mock_entry)

    await toggle(hass, SERVICE_TURN_OFF)

    assert mock_entry.subentries["sub-1"].data["enabled"] is False
    assert hass.states.get(ENTITY).state == "off"


async def test_a_disabled_job_is_disarmed(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)

    await toggle(hass, SERVICE_TURN_OFF)

    runner = mock_entry.runtime_data.scheduler.runner_for("sub-1")
    assert runner.job.enabled is False
    assert runner.armed_for is None


async def test_turning_the_switch_back_on_re_arms_the_job(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)
    await toggle(hass, SERVICE_TURN_OFF)

    await toggle(hass, SERVICE_TURN_ON)

    runner = mock_entry.runtime_data.scheduler.runner_for("sub-1")
    assert runner.job.enabled is True
    assert runner.armed_for is not None

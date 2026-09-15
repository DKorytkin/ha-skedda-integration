"""Per-account authentication health."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError

AUTH = "binary_sensor.main_account_oleh_authentication"


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_working_account_reports_no_problem(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)

    state = hass.states.get(AUTH)
    assert state.state == "off"
    assert state.attributes["device_class"] == "problem"


async def test_an_account_that_stopped_working_reports_a_problem(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")

    await mock_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(AUTH).state == "on"


async def test_the_problem_sensor_stays_readable_while_the_account_is_failing(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A diagnostic that goes unavailable exactly when it matters is no use.

    Coordinator entities normally follow the poll's success; this one reports
    on that failure, so it has to outlive it.
    """
    await setup_entry(hass, mock_entry)
    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")

    await mock_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(AUTH).state != "unavailable"

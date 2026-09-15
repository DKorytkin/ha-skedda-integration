"""The run-now button."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.button import SERVICE_PRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.helpers import setup_with_job

ENTITY = "button.tuesday_18_00_run_now"


async def test_pressing_the_button_books_without_waiting_for_the_window(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)
    mock_provider.book.reset_mock()

    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        await hass.services.async_call(
            "button", SERVICE_PRESS, {"entity_id": ENTITY}, blocking=True
        )
    await hass.async_block_till_done()

    mock_provider.book.assert_awaited()
    assert hass.states.get("sensor.tuesday_18_00_last_outcome").state == "success"

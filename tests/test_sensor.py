"""Per-job sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.helpers import setup_with_job

NEXT_RUN = "sensor.tuesday_18_00_next_run"
LAST_OUTCOME = "sensor.tuesday_18_00_last_outcome"


async def test_each_job_gets_a_next_run_and_last_outcome_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)

    assert hass.states.get(NEXT_RUN) is not None
    assert hass.states.get(LAST_OUTCOME) is not None


async def test_the_next_run_sensor_is_a_timestamp(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """So the dashboard can show "in 3 days" rather than a raw string."""
    await setup_with_job(hass, mock_entry)

    assert hass.states.get(NEXT_RUN).attributes["device_class"] == "timestamp"


async def test_the_last_outcome_reports_what_actually_happened(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    subentry_id = await setup_with_job(hass, mock_entry)
    await mock_entry.runtime_data.scheduler.async_run_now(subentry_id)
    await hass.async_block_till_done()

    state = hass.states.get(LAST_OUTCOME)
    assert state.state == "success"
    assert state.attributes["booking_id"] == "bk-fixture"
    assert state.attributes["attempts"] == 1


async def test_a_failure_reports_its_reason_rather_than_just_failing(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """ "slot_taken" and "quota_exceeded" call for very different responses."""
    from custom_components.skedda_scheduler.api.errors import SlotTakenError

    subentry_id = await setup_with_job(hass, mock_entry)
    mock_provider.book.side_effect = SlotTakenError("gone")
    await mock_entry.runtime_data.scheduler.async_run_now(subentry_id)
    await hass.async_block_till_done()

    assert hass.states.get(LAST_OUTCOME).state == "slot_taken"


async def test_a_job_that_has_never_run_has_no_outcome_to_report(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry, season_start="2027-01-05")

    assert hass.states.get(LAST_OUTCOME).state == "unknown"
    assert hass.states.get(LAST_OUTCOME).attributes["booking_id"] is None


async def test_each_job_is_its_own_device_under_the_account(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """So a job's entities group together and can be renamed as one."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.skedda_scheduler.const import DOMAIN

    subentry_id = await setup_with_job(hass, mock_entry)
    registry = dr.async_get(hass)

    account = registry.async_get_device_by_identifier(
        (DOMAIN, mock_entry.entry_id), mock_entry.entry_id
    )
    job = registry.async_get_device_by_identifier(
        (DOMAIN, f"{mock_entry.entry_id}:{subentry_id}"), mock_entry.entry_id
    )
    assert account is not None
    assert job is not None
    assert job.via_device_id == account.id

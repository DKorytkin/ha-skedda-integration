"""Per-job sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.helpers import setup_with_job

NEXT_RUN = "sensor.tuesday_18_00_next_run"
LAST_OUTCOME = "sensor.tuesday_18_00_last_outcome"
STATUS = "sensor.tuesday_18_00_status"


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


async def test_a_job_is_a_device_and_an_account_is_not(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A job groups five entities, which is what a device is for.

    An account is the config entry. Giving it a device too produced a service
    named "Denys" containing a device named "Denys", under a heading about
    devices that belong to no sub-entry.
    """
    from homeassistant.helpers import device_registry as dr

    from custom_components.skedda_scheduler.const import DOMAIN

    subentry_id = await setup_with_job(hass, mock_entry)
    registry = dr.async_get(hass)

    job = registry.async_get_device_by_identifier(
        (DOMAIN, f"{mock_entry.entry_id}:{subentry_id}"), mock_entry.entry_id
    )
    assert job is not None
    assert job.entry_type is dr.DeviceEntryType.SERVICE

    assert (
        registry.async_get_device_by_identifier((DOMAIN, mock_entry.entry_id), mock_entry.entry_id)
        is None
    )
    assert len(dr.async_entries_for_config_entry(registry, mock_entry.entry_id)) == 1


async def test_the_account_entity_carries_the_account_in_its_id(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Device-less entities are named by their own name alone, so two accounts
    would otherwise both want binary_sensor.authentication."""
    await setup_with_job(hass, mock_entry)

    assert hass.states.get("binary_sensor.main_account_oleh_authentication") is not None


async def test_a_job_says_plainly_whether_it_is_going_to_do_anything(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Out of season a job is not broken, it is asleep - and should say so.

    "Next run: unknown" reads like a fault. The court is shut for the winter
    and the subscription is unpaid; that is a state, not a failure.
    """
    await setup_with_job(hass, mock_entry)
    assert hass.states.get(STATUS).state == "armed"

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.tuesday_18_00_job_enabled"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "disabled"


async def test_a_job_past_its_season_reads_as_out_of_season(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry, season_end="2026-09-02")

    assert hass.states.get(STATUS).state == "out_of_season"
    assert hass.states.get(NEXT_RUN).state == "unknown"


async def test_a_job_device_is_labelled_with_its_court(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A device model is free text Home Assistant cannot translate.

    "Booking job" would stay English under a Ukrainian interface, and the page
    already says what kind of thing this is.
    """
    from homeassistant.helpers import device_registry as dr

    from custom_components.skedda_scheduler.const import DOMAIN

    subentry_id = await setup_with_job(hass, mock_entry)

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{mock_entry.entry_id}:{subentry_id}"), mock_entry.entry_id
    )
    assert device.model == "Court 1"


async def test_a_job_on_a_court_the_venue_no_longer_lists_still_has_a_device(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A court can be renamed or removed while a job still points at it."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.skedda_scheduler.const import DOMAIN

    subentry_id = await setup_with_job(hass, mock_entry, space_id="9999999")

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{mock_entry.entry_id}:{subentry_id}"), mock_entry.entry_id
    )
    assert device.model == "9999999"

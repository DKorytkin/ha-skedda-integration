"""Creating and editing a booking job through the subentry UI."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError
from custom_components.skedda_scheduler.config_flow import SkeddaConfigFlow
from custom_components.skedda_scheduler.const import (
    CONF_DURATION,
    CONF_SPACE_ID,
    CONF_WINDOW_DAYS,
    SUBENTRY_TYPE_JOB,
)

JOB_INPUT: dict[str, Any] = {
    "name": "Tuesday 18:00",
    "space_id": "2000001",
    "weekday": "1",
    "start_time": "18:00:00",
    "duration_minutes": 60,
    "window_days": 14,
    "frequency": "weekly",
    "season_start": "2026-09-01",
    "title": "Tennis (auto)",
    "strategy": "precise",
}


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def start_job_flow(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_JOB), context={"source": "user"}
    )


def field(schema: vol.Schema, key: str) -> Any:
    """The selector behind one form field."""
    for marker, value in schema.schema.items():
        if str(marker) == key:
            return value
    raise AssertionError(f"{key} is not in the form")


def default_for(schema: vol.Schema, key: str) -> Any:
    for marker in schema.schema:
        if str(marker) == key:
            return marker.default()
    raise AssertionError(f"{key} is not in the form")


async def test_the_job_subentry_type_is_advertised(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    supported = SkeddaConfigFlow.async_get_supported_subentry_types(mock_entry)
    assert SUBENTRY_TYPE_JOB in supported


async def test_a_job_is_created_and_titled_with_its_name(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tuesday 18:00"
    subentries = list(mock_entry.subentries.values())
    assert len(subentries) == 1
    assert subentries[0].data[CONF_SPACE_ID] == "2000001"


async def test_the_courts_come_from_the_venue_rather_than_being_typed(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A mistyped space id books nothing, and says so only once it is too late."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    options = field(result["data_schema"], CONF_SPACE_ID).config["options"]
    assert options == [
        {"value": "2000001", "label": "Court 1"},
        {"value": "2000002", "label": "Court 2"},
    ]


async def test_the_window_default_is_the_venue_s_own_horizon(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The window has to match the venue exactly.

    Fire earlier and the server refuses the booking; fire later and the slot
    has already gone. Reading it from the venue is the only reliable source.
    """
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)
    assert default_for(result["data_schema"], CONF_WINDOW_DAYS) == 14


async def test_the_duration_steps_in_the_venue_s_own_slot_size(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The venue books in whole hours; offering 15 minutes offers a refusal."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    config = field(result["data_schema"], CONF_DURATION).config
    assert config["step"] == 60
    assert config["min"] == 60


async def test_a_duration_beyond_the_weekly_quota_is_refused_up_front(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The venue allows 60 minutes a week; a 120-minute job can never succeed.

    Letting it be saved would mean a job that fails silently every week.
    """
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**JOB_INPUT, CONF_DURATION: 120}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DURATION: "over_quota"}
    assert result["description_placeholders"] == {"quota": "60", "horizon": "14"}


async def test_a_window_beyond_the_venue_s_horizon_is_refused_up_front(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**JOB_INPUT, CONF_WINDOW_DAYS: 30}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WINDOW_DAYS: "beyond_horizon"}


async def test_a_venue_that_was_down_at_startup_still_lets_a_job_be_added(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Skedda being unreachable must not block configuring anything."""
    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await start_job_flow(hass, mock_entry)
    # A free-text id rather than a dropdown: there is no list to choose from.
    assert isinstance(field(result["data_schema"], CONF_SPACE_ID), selector.TextSelector)

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_an_existing_job_can_be_edited_in_place(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)
    await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    subentry_id = next(iter(mock_entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_JOB),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**JOB_INPUT, "name": "Tuesday 19:00", "start_time": "19:00:00"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    updated = mock_entry.subentries[subentry_id]
    assert updated.title == "Tuesday 19:00"
    assert updated.data["start_time"] == "19:00:00"


async def test_editing_a_job_is_held_to_the_same_venue_rules(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Otherwise an impossible job could be edited into existence."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)
    await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    subentry_id = next(iter(mock_entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_JOB),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**JOB_INPUT, CONF_DURATION: 120}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DURATION: "over_quota"}
    assert mock_entry.subentries[subentry_id].data[CONF_DURATION] == 60


async def test_a_job_can_be_added_before_the_account_has_ever_loaded(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """An entry that failed to set up has no provider to ask about the venue."""
    mock_entry.add_to_hass(hass)

    result = await start_job_flow(hass, mock_entry)
    assert result["description_placeholders"] == {
        "quota": "unlimited",
        "horizon": "unlimited",
    }

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY

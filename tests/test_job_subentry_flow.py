"""Creating and editing a booking job through the subentry UI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError
from custom_components.skedda_scheduler.config_flow import SkeddaConfigFlow
from custom_components.skedda_scheduler.const import (
    CONF_ADVANCED,
    CONF_DURATION,
    CONF_FREQUENCY,
    CONF_NAME,
    CONF_SPACE_ID,
    CONF_START_DATE,
    CONF_TITLE,
    CONF_WINDOW_DAYS,
    SUBENTRY_TYPE_JOB,
)
from custom_components.skedda_scheduler.core.provider import Booking

KYIV = ZoneInfo("Europe/Kyiv")

#: 29 September 2026 is a Tuesday.
#: What the short form collects, minus the toggle that opens the second step.
ESSENTIALS: dict[str, Any] = {
    "space_id": "2000001",
    "start_date": "2026-09-29",
    "start_time": "20:00:00",
    "duration_minutes": 60,
    "frequency": "once",
}

JOB_INPUT: dict[str, Any] = {
    "space_id": "2000001",
    "start_date": "2026-09-29",
    "start_time": "20:00:00",
    "duration_minutes": 60,
    "frequency": "once",
    "advanced": False,
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
    assert SUBENTRY_TYPE_JOB in SkeddaConfigFlow.async_get_supported_subentry_types(mock_entry)


async def test_four_answers_are_enough_to_book_a_court(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Court, date, time, length. Everything else has a sensible answer."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_SPACE_ID] == "2000001"
    assert data[CONF_FREQUENCY] == "once"
    assert data[CONF_WINDOW_DAYS] == 14
    assert data["strategy"] == "precise"


async def test_the_job_names_itself_the_way_a_person_would_say_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Two fewer fields, and no booking ends up titled after a dashboard."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)

    assert result["title"] == "Court 1 · 29 Sep 20:00"
    assert result["data"][CONF_NAME] == "Court 1 · 29 Sep 20:00"
    assert result["data"][CONF_TITLE] == "Court 1 · 29 Sep 20:00"


async def test_a_repeating_job_names_itself_by_the_weekday(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**JOB_INPUT, CONF_FREQUENCY: "weekly"}
    )

    assert result["title"] == "Court 1 · Tuesdays 20:00"


async def test_the_form_asks_for_a_date_not_a_weekday(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A calendar is how people think about booking a court."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    assert isinstance(field(result["data_schema"], CONF_START_DATE), selector.DateSelector)
    with pytest.raises(AssertionError):
        field(result["data_schema"], "weekday")


async def test_the_date_starts_at_the_far_edge_of_the_venue_s_horizon(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The furthest slot is the one worth racing for; the nearer ones are gone."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    expected = (dt_util.utcnow() + timedelta(days=14)).date().isoformat()
    assert default_for(result["data_schema"], CONF_START_DATE) == expected


async def test_the_duration_is_learned_from_what_you_already_book(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Your own history is the best answer the form can give."""
    start = datetime(2026, 9, 1, 18, 0, tzinfo=KYIV)

    def booked(identifier: str, minutes: int) -> Booking:
        return Booking(
            id=identifier,
            space_ids=("2000001",),
            start=start,
            end=start + timedelta(minutes=minutes),
            title="",
        )

    mock_provider.list_bookings.return_value = [
        Booking(
            id="1",
            space_ids=("2000001",),
            start=start,
            end=start + timedelta(minutes=120),
            title="",
        ),
        Booking(
            id="2",
            space_ids=("2000001",),
            start=start,
            end=start + timedelta(minutes=120),
            title="",
        ),
        Booking(
            id="3", space_ids=("2000001",), start=start, end=start + timedelta(minutes=60), title=""
        ),
    ]
    await setup_entry(hass, mock_entry)

    result = await start_job_flow(hass, mock_entry)

    # 120 is the usual length, but the venue allows 60 minutes a week.
    assert default_for(result["data_schema"], CONF_DURATION) == 60


async def test_repeating_is_off_until_you_ask_for_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    assert default_for(result["data_schema"], CONF_FREQUENCY) == "once"
    assert default_for(result["data_schema"], CONF_ADVANCED) is False


async def test_the_second_step_is_there_for_those_who_want_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**JOB_INPUT, CONF_FREQUENCY: "weekly", CONF_ADVANCED: True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Tennis with Oleh",
            CONF_TITLE: "Tennis",
            "season_end": "2026-11-30",
            CONF_WINDOW_DAYS: 14,
            "strategy": "immediate",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tennis with Oleh"
    assert result["data"]["season_end"] == "2026-11-30"
    assert result["data"]["strategy"] == "immediate"
    # The essentials survive the second step.
    assert result["data"][CONF_START_DATE] == "2026-09-29"


async def test_a_duration_beyond_the_weekly_quota_is_refused_up_front(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The venue allows 60 minutes a week; a 120-minute job can never succeed."""
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
        result["flow_id"], {**JOB_INPUT, CONF_ADVANCED: True}
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "x", CONF_TITLE: "x", CONF_WINDOW_DAYS: 30, "strategy": "precise"},
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
    assert isinstance(field(result["data_schema"], CONF_SPACE_ID), selector.TextSelector)

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_editing_a_job_shows_everything_at_once(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A job that already exists must not be harder to change than to create."""
    await setup_entry(hass, mock_entry)
    result = await start_job_flow(hass, mock_entry)
    await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    subentry_id = next(iter(mock_entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_JOB),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    assert field(result["data_schema"], CONF_NAME) is not None
    with pytest.raises(AssertionError):
        field(result["data_schema"], CONF_ADVANCED)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **ESSENTIALS,
            CONF_NAME: "Renamed",
            CONF_TITLE: "Tennis",
            CONF_WINDOW_DAYS: 14,
            "strategy": "precise",
            "start_time": "19:00:00",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.subentries[subentry_id].title == "Renamed"
    assert mock_entry.subentries[subentry_id].data["start_time"] == "19:00:00"


async def test_editing_a_job_is_held_to_the_same_venue_rules(
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
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**ESSENTIALS, CONF_DURATION: 120, CONF_NAME: "x", CONF_TITLE: "x", CONF_WINDOW_DAYS: 14},
    )

    assert result["errors"] == {CONF_DURATION: "over_quota"}


async def test_a_job_can_be_added_before_the_account_has_ever_loaded(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """An entry that failed to set up has no provider to ask about the venue."""
    mock_entry.add_to_hass(hass)

    result = await start_job_flow(hass, mock_entry)
    assert result["description_placeholders"] == {"quota": "unlimited", "horizon": "unlimited"}

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], JOB_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY

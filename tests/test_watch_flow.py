"""Adding a slot watch, and writing the rules that live under it."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import DOMAIN, SUBENTRY_TYPE_WATCH_RULE
from tests.helpers import setup_with_job, watch_entry_with_rule

RULE_INPUT: dict[str, Any] = {
    "name": "Weekends",
    "weekdays": ["sat", "sun"],
    "not_before": "10:00:00",
    "not_after": "13:00:00",
    "space_ids": [],
    "duration_minutes": 60,
    "mode": "both",
    "max_block_minutes": 180,
    "min_lead_minutes": 180,
    "speed": "stepped",
    "book": True,
}


async def test_the_menu_offers_a_slot_watch(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert "watch" in result["menu_options"]


async def test_a_watch_without_an_account_says_so(hass: HomeAssistant) -> None:
    """Nothing to watch with, and no court list to choose from."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "watch"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_account"


async def test_the_watch_takes_the_venue_from_the_account(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "watch"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["venue"] == "myclub"


async def test_only_one_watch_per_venue(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Rules are subentries of it; a second entry would split them in two."""
    await setup_with_job(hass, mock_entry)
    await watch_entry_with_rule(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "watch"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_rule_is_created_from_the_form(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    result = await hass.config_entries.subentries.async_init(
        (watch.entry_id, SUBENTRY_TYPE_WATCH_RULE), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], RULE_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(watch.subentries) == 2
    assert {rule.name for rule in watch.runtime_data.watcher.rules} == {"Our evening", "Weekends"}


async def test_the_form_lists_the_venues_courts(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Choosing a court by id would be choosing a number nobody recognises."""
    await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    result = await hass.config_entries.subentries.async_init(
        (watch.entry_id, SUBENTRY_TYPE_WATCH_RULE), context={"source": "user"}
    )

    courts = result["data_schema"].schema["space_ids"].config["options"]
    assert [option["label"] for option in courts] == ["Court 1", "Court 2"]


async def test_a_rule_can_be_edited_afterwards(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    rule_id = next(iter(watch.subentries))

    result = await hass.config_entries.subentries.async_init(
        (watch.entry_id, SUBENTRY_TYPE_WATCH_RULE),
        context={"source": "reconfigure", "subentry_id": rule_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**RULE_INPUT, "name": "Renamed"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert watch.subentries[rule_id].title == "Renamed"


async def test_a_watch_with_no_account_loaded_offers_no_courts(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The form still opens; the court list is simply empty."""
    watch = await watch_entry_with_rule(hass)

    result = await hass.config_entries.subentries.async_init(
        (watch.entry_id, SUBENTRY_TYPE_WATCH_RULE), context={"source": "user"}
    )

    assert result["data_schema"].schema["space_ids"].config["options"] == []

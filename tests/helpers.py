"""Test helpers shared across entity tests."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import (
    CONF_ENTRY_KIND,
    CONF_VENUE,
    DOMAIN,
    ENTRY_KIND_WATCH,
    SUBENTRY_TYPE_JOB,
    SUBENTRY_TYPE_WATCH_RULE,
)

JOB_DATA: dict[str, Any] = {
    "name": "Tuesday 18:00",
    "space_id": "2000001",
    "start_date": "2026-09-01",
    "start_time": "18:00:00",
    "duration_minutes": 60,
    "window_days": 14,
    "frequency": "weekly",
    "season_end": None,
    "title": "Tennis (auto)",
    "strategy": "precise",
    "notify_targets": [],
    "enabled": True,
}


def add_job_subentry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    subentry_id: str = "sub-1",
    **overrides: Any,
) -> str:
    """Attach a job to an entry that is already known to Home Assistant."""
    data = {**JOB_DATA, **overrides}
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=data,
            subentry_id=subentry_id,
            subentry_type=SUBENTRY_TYPE_JOB,
            title=str(data["name"]),
            unique_id=None,
        ),
    )
    return subentry_id


async def setup_account(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """An account with no booking job: nothing has a claim on its quota."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def setup_with_job(hass: HomeAssistant, entry: MockConfigEntry, **overrides: Any) -> str:
    entry.add_to_hass(hass)
    subentry_id = add_job_subentry(hass, entry, **overrides)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return subentry_id


WATCH_RULE_DATA: dict[str, Any] = {
    "name": "Our evening",
    "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "not_before": "19:00:00",
    "not_after": "21:00:00",
    "space_ids": [],
    "duration_minutes": 60,
    "mode": "both",
    "speed": "stepped",
    "book": True,
    "enabled": True,
}


async def watch_entry_with_rule(
    hass: HomeAssistant, entry_id: str = "entry-watch", **overrides: Any
) -> MockConfigEntry:
    """A loaded watch entry holding one rule, with the venue of the fixtures."""
    data = {**WATCH_RULE_DATA, **overrides}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Slot watch",
        entry_id=entry_id,
        data={CONF_ENTRY_KIND: ENTRY_KIND_WATCH, CONF_VENUE: "myclub"},
        subentries_data=[
            ConfigSubentryData(
                data=data,
                subentry_type=SUBENTRY_TYPE_WATCH_RULE,
                title=str(data["name"]),
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry

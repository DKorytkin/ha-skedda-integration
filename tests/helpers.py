"""Test helpers shared across entity tests."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import SUBENTRY_TYPE_JOB

JOB_DATA: dict[str, Any] = {
    "name": "Tuesday 18:00",
    "space_id": "2000001",
    "weekday": "1",
    "start_time": "18:00:00",
    "duration_minutes": 60,
    "window_days": 14,
    "frequency": "weekly",
    "season_start": "2026-09-01",
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


async def setup_with_job(hass: HomeAssistant, entry: MockConfigEntry, **overrides: Any) -> str:
    entry.add_to_hass(hass)
    subentry_id = add_job_subentry(hass, entry, **overrides)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return subentry_id

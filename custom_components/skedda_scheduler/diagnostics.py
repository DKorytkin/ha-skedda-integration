"""Diagnostics: enough to explain a lost race, never enough to log in."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import SkeddaConfigEntry
from .const import SUBENTRY_TYPE_JOB

#: The venue subdomain deliberately stays: it identifies which venue's rules
#: are in play, and a bug report without it is unanswerable.
TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SkeddaConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    clock = getattr(getattr(runtime.provider, "client", None), "clock", None)
    data = runtime.coordinator.data

    jobs: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        runner = runtime.scheduler.runner_for(subentry_id) if runtime.scheduler else None
        armed_for = runner.armed_for if runner else None
        jobs.append(
            {
                "job_id": subentry_id,
                "config": dict(subentry.data),
                "armed_for": armed_for.isoformat() if armed_for else None,
                # Every attempt of every run: the timings and the server's own
                # words are what answer "why did this one not land?".
                "history": runtime.store.history_for(subentry_id),
            }
        )

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "authenticated": runtime.coordinator.authenticated,
        "last_poll_succeeded": runtime.coordinator.last_update_success,
        # The offset between our clock and Skedda's decides whether a burst
        # straddles the opening instant or misses it entirely.
        "clock_offset_seconds": getattr(clock, "offset_seconds", None),
        "clock_samples": getattr(clock, "samples", None),
        "venue_rules": {
            "timezone": data.rules.timezone,
            "slot_minutes": data.rules.slot_minutes,
            "max_days_ahead": data.rules.max_days_ahead,
            "weekly_quota_minutes": data.rules.weekly_quota_minutes,
        },
        "spaces": [{"id": space.id, "name": space.name} for space in data.spaces],
        "jobs": jobs,
    }

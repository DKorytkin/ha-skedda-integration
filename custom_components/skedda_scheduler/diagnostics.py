"""Diagnostics: enough to explain a lost race, never enough to log in."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import SkeddaConfigEntry
from .const import (
    CONF_ATTENDEES,
    CONF_CALENDAR_ID,
    CONF_LOCATION,
    ENTRY_KIND_CALENDAR,
    ENTRY_KIND_WATCH,
    SUBENTRY_TYPE_JOB,
)
from .entry_kinds import entry_kind

#: The venue subdomain deliberately stays: it identifies which venue's rules
#: are in play, and a bug report without it is unanswerable.
TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}

#: The token signs in to Google; the calendar id is usually an email address,
#: and the address and guest list are nobody else's business.
CALENDAR_REDACT = {"token", CONF_CALENDAR_ID, CONF_LOCATION, CONF_ATTENDEES}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SkeddaConfigEntry
) -> dict[str, Any]:
    # Home Assistant offers the download on every entry. Only an account has a
    # coordinator; the other two would fail on it with a server error.
    kind = entry_kind(entry)
    if kind == ENTRY_KIND_CALENDAR:
        return {"entry": async_redact_data(dict(entry.data), CALENDAR_REDACT)}
    if kind == ENTRY_KIND_WATCH:
        return _watch_diagnostics(entry)

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


def _watch_diagnostics(entry: SkeddaConfigEntry) -> dict[str, Any]:
    """What each rule wants, and whether the watch is looking at all."""
    runner = getattr(getattr(entry, "runtime_data", None), "watcher", None)
    interval = runner.interval if runner else None
    catch = runner.last_catch if runner else None
    return {
        "entry": dict(entry.data),
        "rules": [
            {"rule_id": subentry_id, "config": dict(subentry.data)}
            for subentry_id, subentry in entry.subentries.items()
        ],
        "gate_open": runner.gate_open if runner else None,
        "poll_interval_minutes": interval.total_seconds() / 60 if interval else None,
        "last_catch": catch.start.isoformat() if catch else None,
    }

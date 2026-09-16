"""One snapshot for the panel.

The panel could stitch this together from entity states and config entries,
but it would have to know how the integration models jobs to do it. This keeps
that knowledge here and hands over a shape meant for reading.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.websocket_api import async_register_command
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import require_admin, websocket_command
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENABLED,
    CONF_VENUE,
    DOMAIN,
    STATUS_ARMED,
    STATUS_DISABLED,
    STATUS_OUT_OF_SEASON,
    SUBENTRY_TYPE_JOB,
)
from .job_factory import build_job, venue_timezone_for

TYPE_OVERVIEW = f"{DOMAIN}/overview"


@callback
def async_register(hass: HomeAssistant) -> None:
    async_register_command(hass, websocket_overview)


@require_admin
@websocket_command({vol.Required("type"): TYPE_OVERVIEW})
@callback
def websocket_overview(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Accounts, jobs and bookings, in one reply.

    Admin only: it names the venue and every account's label, which is more
    than a household member needs to see.
    """
    accounts: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    bookings: list[dict[str, Any]] = []

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            # An entry that failed to set up has no runtime data to read. It
            # is still worth listing, so the panel can show why.
            accounts.append(_account(entry, authenticated=False))
            continue
        accounts.append(_account(entry, authenticated=entry.runtime_data.coordinator.authenticated))
        jobs.extend(_jobs(hass, entry))
        bookings.extend(_bookings(entry))

    connection.send_result(
        msg["id"],
        {
            "accounts": accounts,
            "jobs": sorted(jobs, key=lambda job: job["next_slot"] or ""),
            "bookings": sorted(bookings, key=lambda booking: booking["start"]),
        },
    )


def _account(entry: ConfigEntry, *, authenticated: bool) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "venue": entry.data.get(CONF_VENUE),
        "authenticated": authenticated,
        "state": entry.state.value,
    }


def _jobs(hass: HomeAssistant, entry: ConfigEntry) -> list[dict[str, Any]]:
    runtime = entry.runtime_data
    courts = {space.id: space.name for space in runtime.coordinator.data.spaces}
    timezone = venue_timezone_for(hass, entry)
    found: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        try:
            job = build_job(subentry_id, subentry.data, timezone)
        except ValueError, KeyError:
            continue
        runner = runtime.scheduler.runner_for(subentry_id) if runtime.scheduler else None
        slot = job.next_slot(dt_util.utcnow())
        found.append(
            {
                "job_id": subentry_id,
                "entry_id": entry.entry_id,
                "account": entry.title,
                "name": job.name,
                "court": courts.get(job.primary_space_id, job.primary_space_id),
                "repeat": job.recurrence.frequency.value,
                "enabled": bool(subentry.data.get(CONF_ENABLED, True)),
                "next_slot": slot[0].isoformat() if slot else None,
                "opens_at": job.window.opens_at(slot[0]).isoformat() if slot else None,
                "armed_for": runner.armed_for.isoformat() if runner and runner.armed_for else None,
                "status": _status(subentry.data, runner),
                "last_outcome": runtime.store.last_outcome(subentry_id),
            }
        )
    return found


def _bookings(entry: ConfigEntry) -> list[dict[str, Any]]:
    runtime = entry.runtime_data
    courts = {space.id: space.name for space in runtime.coordinator.data.spaces}
    return [
        {
            "booking_id": booking.id,
            "entry_id": entry.entry_id,
            "account": entry.title,
            "court": ", ".join(courts.get(space, space) for space in booking.space_ids),
            "start": booking.start.isoformat(),
            "end": booking.end.isoformat(),
            "title": booking.title,
        }
        for booking in runtime.coordinator.data.bookings
        if booking.is_mine
    ]


def _status(data: Any, runner: Any) -> str:
    if not data.get(CONF_ENABLED, True):
        return STATUS_DISABLED
    if runner is not None and runner.armed_for is not None:
        return STATUS_ARMED
    return STATUS_OUT_OF_SEASON

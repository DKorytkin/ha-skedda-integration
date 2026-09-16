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
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api.errors import SkeddaError
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
TYPE_CANCEL = f"{DOMAIN}/cancel_booking"


@callback
def async_register(hass: HomeAssistant) -> None:
    async_register_command(hass, websocket_overview)
    async_register_command(hass, websocket_cancel_booking)


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
        # What the runner is aiming at, not merely the nearest occurrence: the
        # two differ once a job has caught up on an already-open window.
        aimed = runner.armed_slot if runner and runner.armed_slot else None
        slot = (aimed, aimed) if aimed else job.next_slot(dt_util.utcnow())
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


@require_admin
@websocket_command(
    {
        vol.Required("type"): TYPE_CANCEL,
        vol.Required("entry_id"): str,
        vol.Required("booking_id"): str,
    }
)
@async_response
async def websocket_cancel_booking(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Release a court time.

    The one action the panel performs itself: Home Assistant has no dialog to
    borrow for it, and a booking you can see but not release is an invitation
    to go and do it somewhere else.
    """
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], "not_loaded", "That account is not set up.")
        return
    try:
        await entry.runtime_data.provider.cancel(msg["booking_id"])
    except SkeddaError as err:
        connection.send_error(msg["id"], "cancel_failed", str(err))
        return
    # The panel reads the diary, so it has to change before the reply lands.
    await entry.runtime_data.coordinator.async_refresh()
    connection.send_result(msg["id"], {"cancelled": msg["booking_id"]})

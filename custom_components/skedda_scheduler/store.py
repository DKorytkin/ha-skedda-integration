"""Persisted history of booking attempts.

A lost race has to be explainable afterwards, so every outcome is kept, not
just the last one - and each outcome keeps its individual attempts, with the
timings and whatever the server said.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .core.result import BookingOutcome

STORAGE_VERSION = 1
MAX_HISTORY_PER_JOB = 50

#: Slots we gave up live in the same file as the attempts, under a key no
#: subentry id can collide with. They are history too - the history of a court
#: we decided we did not want.
RELEASED_KEY = "__released__"
STATUS_RELEASED = "released"


def _as_datetime(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def storage_key(entry: ConfigEntry) -> str:
    """One file per account.

    A single shared file would be read into one dictionary per account and
    written back whole, so whichever account recorded last would erase every
    other account's history.
    """
    return f"{DOMAIN}.{entry.entry_id}.history"


class AttemptStore:
    """The booking history of one account, keyed by job."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._store: Store[dict[str, list[dict[str, Any]]]] = Store(
            hass, STORAGE_VERSION, storage_key(entry)
        )
        self._data: dict[str, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        self._data = await self._store.async_load() or {}
        self._prune_released()

    async def async_record(self, outcome: BookingOutcome) -> None:
        history = self._data.setdefault(outcome.job_id, [])
        history.insert(0, outcome.as_record())
        del history[MAX_HISTORY_PER_JOB:]
        await self._store.async_save(self._data)

    async def async_note_released(self, space_id: str, start: datetime, end: datetime) -> None:
        """Remember that this slot was ours and is not any more.

        A watch rule looks for free slots, and a slot we just gave up is free.
        Without this it would take back the court somebody cancelled on
        purpose, minutes after they cancelled it.
        """
        released = self._data.setdefault(RELEASED_KEY, [])
        released.insert(
            0,
            {
                "status": STATUS_RELEASED,
                "space_id": space_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        self._prune_released()
        await self._store.async_save(self._data)

    def released_slots(self) -> set[tuple[str, str]]:
        """Slots we let go, as (space id, start) - the watch skips these."""
        return {
            (str(item["space_id"]), str(item["start"])) for item in self._data.get(RELEASED_KEY, [])
        }

    def _prune_released(self) -> None:
        """Forget a released slot once its time has passed.

        Nothing can be booked in the past, so the record has no further use -
        and this is what keeps the list from growing for ever.
        """
        released = self._data.get(RELEASED_KEY)
        if not released:
            return
        now = dt_util.utcnow()
        kept: list[dict[str, Any]] = []
        for item in released:
            ends = _as_datetime(item.get("end"))
            if ends is None or ends > now:
                kept.append(item)
        self._data[RELEASED_KEY] = kept

    async def async_forget(self, job_ids: set[str]) -> None:
        """Drop the history of jobs that no longer exist.

        Deleting a job gives back a fresh id if it is ever re-added, so its
        old history would otherwise sit in the file forever.
        """
        stale = set(self._data) - job_ids - {RELEASED_KEY}
        if not stale:
            return
        for job_id in stale:
            del self._data[job_id]
        await self._store.async_save(self._data)

    async def async_remove(self) -> None:
        """Delete the file, for when the account itself is removed."""
        self._data = {}
        await self._store.async_remove()

    def history_for(self, job_id: str) -> list[dict[str, Any]]:
        return list(self._data.get(job_id, []))

    def last_outcome(self, job_id: str) -> dict[str, Any] | None:
        history = self._data.get(job_id)
        return history[0] if history else None

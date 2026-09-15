"""Persisted history of booking attempts.

A lost race has to be explainable afterwards, so every outcome is kept, not
just the last one - and each outcome keeps its individual attempts, with the
timings and whatever the server said.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .core.result import BookingOutcome

STORAGE_VERSION = 1
MAX_HISTORY_PER_JOB = 50


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

    async def async_record(self, outcome: BookingOutcome) -> None:
        history = self._data.setdefault(outcome.job_id, [])
        history.insert(0, outcome.as_record())
        del history[MAX_HISTORY_PER_JOB:]
        await self._store.async_save(self._data)

    async def async_forget(self, job_ids: set[str]) -> None:
        """Drop the history of jobs that no longer exist.

        Deleting a job gives back a fresh id if it is ever re-added, so its
        old history would otherwise sit in the file forever.
        """
        stale = set(self._data) - job_ids
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

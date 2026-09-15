"""Config entry lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.store import AttemptStore, storage_key
from tests.test_store import outcome

OUTCOME = outcome("job-1")


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entry_sets_up_and_exposes_runtime_data(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.LOADED
    assert mock_entry.runtime_data.provider is mock_provider


async def test_entry_unloads_cleanly(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_entry(hass, mock_entry)
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.NOT_LOADED


async def test_runtime_data_carries_a_single_flight_semaphore(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """One booking in flight per account, so two jobs cannot race each other.

    Racing matters here beyond tidiness: this venue allows 60 minutes a week,
    so two concurrent bookings would have one of them burn the quota the other
    needed.
    """
    await setup_entry(hass, mock_entry)
    semaphore = mock_entry.runtime_data.semaphore
    assert isinstance(semaphore, asyncio.Semaphore)
    assert not semaphore.locked()
    async with semaphore:
        assert semaphore.locked()


async def test_each_account_gets_its_own_semaphore(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Two accounts booking at once is the whole point of multi-account support."""
    second = MockConfigEntry(
        domain=mock_entry.domain,
        data={**mock_entry.data, "alias": "Partner"},
        title="Partner",
        unique_id="myclub:partner@example.com",
        entry_id="entry-2",
    )
    await setup_entry(hass, mock_entry)
    await setup_entry(hass, second)
    assert mock_entry.runtime_data.semaphore is not second.runtime_data.semaphore


async def test_the_client_is_built_from_this_entry_s_credentials(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A mix-up here would send one account's password to another venue."""
    with patch("custom_components.skedda_scheduler.SkeddaClient") as client_cls:
        await setup_entry(hass, mock_entry)
    credentials = client_cls.call_args.args[1]
    assert credentials.venue == "myclub"
    assert credentials.email == "user@example.com"
    assert credentials.password == "secret"


async def test_updating_the_entry_reloads_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Editing an account must take effect without restarting Home Assistant."""
    await setup_entry(hass, mock_entry)
    hass.config_entries.async_update_entry(mock_entry, data={**mock_entry.data, "alias": "Renamed"})
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.LOADED


async def test_the_history_of_a_deleted_job_is_not_kept_forever(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A re-added job gets a fresh id, so the old rows could never be read."""
    store = AttemptStore(hass, mock_entry)
    await store.async_load()
    await store.async_record(OUTCOME)

    await setup_entry(hass, mock_entry)

    assert mock_entry.runtime_data.store.history_for("job-1") == []


async def test_removing_the_account_removes_its_history(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    await setup_entry(hass, mock_entry)
    await mock_entry.runtime_data.store.async_record(OUTCOME)

    assert await hass.config_entries.async_remove(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert hass_storage.get(storage_key(mock_entry), {}).get("data") in (None, {})

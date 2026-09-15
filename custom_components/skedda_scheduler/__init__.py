"""The Skedda Scheduler integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.client import SkeddaClient
from .api.models import SkeddaCredentials
from .const import CONF_VENUE
from .coordinator import SkeddaCoordinator
from .scheduler import JobScheduler
from .skedda_provider import SkeddaProvider
from .store import AttemptStore

PLATFORMS: list[Platform] = []


@dataclass
class SkeddaRuntimeData:
    """Everything an entry's platforms and scheduler need at runtime."""

    provider: SkeddaProvider
    coordinator: SkeddaCoordinator
    store: AttemptStore
    #: One booking in flight per account. Two jobs racing each other would be
    #: bad anywhere, but at a venue with a weekly quota one job can burn the
    #: allowance the other needed.
    semaphore: asyncio.Semaphore
    #: Filled in once the runtime data exists, because the scheduler reads it.
    scheduler: JobScheduler | None = None


type SkeddaConfigEntry = ConfigEntry[SkeddaRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    """Set up one Skedda account.

    Credentials are not verified here: the coordinator's first refresh does
    that, and it is the piece that knows how to turn a rejection into a reauth
    flow rather than a failed startup.
    """
    credentials = SkeddaCredentials(
        venue=entry.data[CONF_VENUE],
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
    )
    # Home Assistant's shared session: it is closed on shutdown for us, and
    # reuses connections, which matters when a burst fires at a window opening.
    client = SkeddaClient(async_get_clientsession(hass), credentials)
    provider = SkeddaProvider(client)
    coordinator = SkeddaCoordinator(hass, entry, provider)
    # The first refresh is what proves the credentials: it turns a rejection
    # into a reauth flow and a venue outage into a retry, neither of which a
    # bare setup failure would do.
    await coordinator.async_config_entry_first_refresh()
    store = AttemptStore(hass, entry)
    await store.async_load()
    # Jobs deleted while this account was unloaded would otherwise keep their
    # history in the file for good; a re-added job gets a fresh id anyway.
    await store.async_forget(set(entry.subentries))
    entry.runtime_data = SkeddaRuntimeData(
        provider=provider,
        coordinator=coordinator,
        store=store,
        semaphore=asyncio.Semaphore(1),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    scheduler = JobScheduler(hass, entry)
    entry.runtime_data.scheduler = scheduler
    scheduler.async_sync_jobs()
    entry.async_on_unload(scheduler.async_shutdown)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> None:
    """Take the account's booking history with it."""
    await AttemptStore(hass, entry).async_remove()

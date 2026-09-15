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
from .skedda_provider import SkeddaProvider

PLATFORMS: list[Platform] = []


@dataclass
class SkeddaRuntimeData:
    """Everything an entry's platforms and scheduler need at runtime."""

    provider: SkeddaProvider
    coordinator: SkeddaCoordinator
    #: One booking in flight per account. Two jobs racing each other would be
    #: bad anywhere, but at a venue with a weekly quota one job can burn the
    #: allowance the other needed.
    semaphore: asyncio.Semaphore


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
    entry.runtime_data = SkeddaRuntimeData(
        provider=provider,
        coordinator=coordinator,
        semaphore=asyncio.Semaphore(1),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

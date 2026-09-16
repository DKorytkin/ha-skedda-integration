"""The Skedda Scheduler integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

# Imported as a module: a from-import binds the function here at import
# time, and the tests that stand in for Google would never be seen.
from . import google_calendar
from .api.client import SkeddaClient
from .api.errors import SkeddaAuthError, SkeddaError
from .api.models import SkeddaCredentials
from .const import CONF_VENUE, DOMAIN
from .coordinator import SkeddaCoordinator
from .google_calendar import is_calendar_entry
from .panel import async_register_panel
from .scheduler import JobScheduler, async_build_job_sinks
from .services import async_setup_services
from .skedda_provider import SkeddaProvider
from .store import AttemptStore
from .websocket import async_register as async_register_websocket

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]


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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services and the panel's data source, once."""
    async_setup_services(hass)
    async_register_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    """Set up one entry, of whichever kind it is."""
    if is_calendar_entry(entry):
        return await _async_setup_calendar(hass, entry)
    return await _async_setup_account(hass, entry)


async def _async_setup_calendar(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    """Prove the Google link still works, and let the accounts find it.

    Nothing else to set up: it has no coordinator, no scheduler and no
    entities. Every booking job reads it when a booking lands.
    """
    try:
        await google_calendar.async_build_client(hass, entry)
    except SkeddaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SkeddaError as err:
        raise ConfigEntryNotReady(str(err)) from err
    # An account set up before the calendar would otherwise write nothing
    # until its next reload.
    for other in hass.config_entries.async_entries(DOMAIN):
        if not is_calendar_entry(other) and other.state is ConfigEntryState.LOADED:
            hass.config_entries.async_schedule_reload(other.entry_id)
    return True


async def _async_setup_account(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
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
    # Its own session, not Home Assistant's shared one: this integration needs
    # a cookie jar of its own. Skedda refuses a sign-in made while an older
    # session is still held, and a shared jar carries one across every reload.
    # Created during entry setup, so Home Assistant detaches it when the entry
    # is unloaded; closing it here would be closing it twice.
    client = SkeddaClient(async_create_clientsession(hass), credentials)
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
    await async_register_panel(hass)

    _async_forget_the_account_device(hass, entry)

    # Before the platforms: an entity that asks the scheduler what a job is
    # doing would otherwise be created while there is nothing to ask, and
    # would report "out of season" until the next poll.
    scheduler = JobScheduler(hass, entry)
    entry.runtime_data.scheduler = scheduler
    scheduler.async_sync_jobs(await async_build_job_sinks(hass))
    entry.async_on_unload(scheduler.async_shutdown)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    if is_calendar_entry(entry):
        return True
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> None:
    """Take the account's booking history with it."""
    if is_calendar_entry(entry):
        return
    await AttemptStore(hass, entry).async_remove()


@callback
def _async_forget_the_account_device(hass: HomeAssistant, entry: SkeddaConfigEntry) -> None:
    """Remove the device an earlier version gave the account.

    Home Assistant keeps a device an integration has stopped creating, so
    without this the page lists a thing with no entities and no purpose.
    """
    registry = dr.async_get(hass)
    stale = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    if stale is not None:
        registry.async_remove_device(stale.id)

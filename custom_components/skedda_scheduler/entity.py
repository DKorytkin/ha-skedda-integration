"""Shared entity bases and device wiring.

A booking job is a device: it groups five entities, and a device is what lets
someone rename or hide the lot in one place.

An account is not. It is the config entry, and giving it a device as well
produced a service named "Denys" containing a device named "Denys", under a
heading about devices belonging to no sub-entry. Its one entity carries the
account in its id instead.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import CONF_VENUE, DOMAIN
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob
from .core.watch import WatchRule
from .watcher import WatchRunner


class SkeddaAccountEntity(CoordinatorEntity[SkeddaCoordinator]):
    """An entity describing the account itself. Deliberately device-less."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SkeddaCoordinator, domain: str) -> None:
        super().__init__(coordinator)
        # Without a device the entity name alone would decide the id, and two
        # accounts would both want binary_sensor.authentication. Naming it
        # here keeps each account's entity id readable and its own.
        self.entity_id = (
            f"{domain}.{slugify(coordinator.config_entry.title)}_{self.entity_description.key}"
        )


class SkeddaJobEntity(CoordinatorEntity[SkeddaCoordinator]):
    """An entity describing one booking job."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SkeddaCoordinator, job: BookingJob) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.job = job
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}:{job.job_id}")},
            name=job.name,
            manufacturer="Skedda",
            # The court rather than the words "Booking job": a device model is
            # free text that Home Assistant cannot translate, and the interface
            # already says what kind of thing this is.
            model=_court_name(coordinator, job),
            # Not a thing in a room, but still a thing worth grouping.
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://{entry.data[CONF_VENUE]}.skedda.com",
        )


def _court_name(coordinator: SkeddaCoordinator, job: BookingJob) -> str:
    for space in coordinator.data.spaces:
        if space.id == job.primary_space_id:
            return space.name
    return job.primary_space_id


class SkeddaWatchEntity(Entity):
    """An entity describing one watch rule.

    No coordinator: the watch reads the accounts' snapshots rather than
    polling for itself, so its entities follow the runner instead.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, runner: WatchRunner, rule: WatchRule) -> None:
        self._entry = entry
        self.runner = runner
        self.rule = rule
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}:{rule.rule_id}")},
            name=rule.name,
            manufacturer="Skedda",
            model="Watch rule",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://{entry.data[CONF_VENUE]}.skedda.com",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.runner.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        """The runner scanned; whatever this entity shows may have moved."""
        current = next(
            (rule for rule in self.runner.rules if rule.rule_id == self.rule.rule_id), self.rule
        )
        self.rule = current
        self.async_write_ha_state()

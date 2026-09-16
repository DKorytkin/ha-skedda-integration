"""Shared entity bases and device wiring.

A booking job is a device: it groups five entities, and a device is what lets
someone rename or hide the lot in one place.

An account is not. It is the config entry, and giving it a device as well
produced a service named "Denys" containing a device named "Denys", under a
heading about devices belonging to no sub-entry. Its one entity carries the
account in its id instead.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import CONF_VENUE, DOMAIN
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob


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
            model="Booking job",
            # Not a thing in a room, but still a thing worth grouping.
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://{entry.data[CONF_VENUE]}.skedda.com",
        )

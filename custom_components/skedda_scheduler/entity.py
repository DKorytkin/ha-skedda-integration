"""Shared entity bases and device wiring.

An account is a device; each booking job is a device attached to it, so Home
Assistant groups a job's entities together and a user can rename or disable a
whole job at once.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_VENUE, DOMAIN
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob


class SkeddaAccountEntity(CoordinatorEntity[SkeddaCoordinator]):
    """An entity describing the account itself."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SkeddaCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        venue = entry.data[CONF_VENUE]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Skedda",
            model="Account",
            # A login is not a thing in a room. Without this Home Assistant
            # treats it as an appliance and asks which room it lives in.
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://{venue}.skedda.com",
        )


class SkeddaJobEntity(CoordinatorEntity[SkeddaCoordinator]):
    """An entity describing one booking job."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SkeddaCoordinator, job: BookingJob, account_device_id: str
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.job = job
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}:{job.job_id}")},
            name=job.name,
            manufacturer="Skedda",
            model="Booking job",
            entry_type=DeviceEntryType.SERVICE,
            via_device_id=account_device_id,
        )

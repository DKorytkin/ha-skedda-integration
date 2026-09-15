"""Per-account authentication health."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .coordinator import SkeddaCoordinator
from .entity import SkeddaAccountEntity

AUTHENTICATION = BinarySensorEntityDescription(
    key="authentication",
    translation_key="authentication",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AccountAuthenticatedBinarySensor(entry.runtime_data.coordinator)])


class AccountAuthenticatedBinarySensor(SkeddaAccountEntity, BinarySensorEntity):
    """On when the account cannot currently be used to book."""

    entity_description = AUTHENTICATION

    def __init__(self, coordinator: SkeddaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:authentication"

    @property
    def available(self) -> bool:
        """Always. This entity reports the very failure that would hide it.

        Coordinator entities normally go unavailable when a poll fails, which
        for a problem sensor means disappearing exactly when it has something
        to say.
        """
        return True

    @property
    def is_on(self) -> bool:
        """True means there is a problem: the account is not usable."""
        return not self.coordinator.authenticated

"""Enable or disable a booking job without deleting it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .const import CONF_ENABLED, SUBENTRY_TYPE_JOB
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob
from .entity import SkeddaJobEntity
from .job_factory import build_job, venue_timezone_for

JOB_ENABLED = SwitchEntityDescription(key="job_enabled", translation_key="job_enabled")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    timezone = venue_timezone_for(hass, entry)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        try:
            job = build_job(subentry_id, subentry.data, timezone)
        except ValueError, KeyError:
            continue
        async_add_entities(
            [JobEnabledSwitch(entry.runtime_data.coordinator, job, entry)],
            config_subentry_id=subentry_id,
        )


class JobEnabledSwitch(SkeddaJobEntity, SwitchEntity):
    """Whether this job will arm for its next window."""

    entity_description = JOB_ENABLED

    def __init__(
        self, coordinator: SkeddaCoordinator, job: BookingJob, entry: SkeddaConfigEntry
    ) -> None:
        super().__init__(coordinator, job, entry.runtime_data.account_device_id)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:job_enabled"

    @property
    def is_on(self) -> bool:
        subentry = self._entry.subentries.get(self.job.job_id)
        return bool(subentry.data.get(CONF_ENABLED, True)) if subentry else False

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(enabled=False)

    async def _async_set_enabled(self, *, enabled: bool) -> None:
        """Persist the choice and let the entry reload re-arm the jobs.

        Updating a subentry fires the entry's update listeners, which reload
        the account; disarming here as well would only race that reload.
        """
        subentry = self._entry.subentries[self.job.job_id]
        self.hass.config_entries.async_update_subentry(
            self._entry, subentry, data={**subentry.data, CONF_ENABLED: enabled}
        )
        self.async_write_ha_state()

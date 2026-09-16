"""Run a booking job right now, without waiting for its window."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .const import SUBENTRY_TYPE_JOB
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob
from .entity import SkeddaJobEntity
from .job_factory import build_job, venue_timezone_for

RUN_NOW = ButtonEntityDescription(key="run_now", translation_key="run_now")


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
            [RunNowButton(entry.runtime_data.coordinator, job, entry)],
            config_subentry_id=subentry_id,
        )


class RunNowButton(SkeddaJobEntity, ButtonEntity):
    """Fires the job's booking attempt immediately."""

    entity_description = RUN_NOW

    def __init__(
        self, coordinator: SkeddaCoordinator, job: BookingJob, entry: SkeddaConfigEntry
    ) -> None:
        super().__init__(coordinator, job)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:run_now"

    async def async_press(self) -> None:
        scheduler = self._entry.runtime_data.scheduler
        if scheduler is not None:
            await scheduler.async_run_now(self.job.job_id)

"""Per-job state sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .const import SUBENTRY_TYPE_JOB
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob
from .entity import SkeddaJobEntity
from .job_factory import build_job, venue_timezone_for

NEXT_RUN = SensorEntityDescription(
    key="next_run", translation_key="next_run", device_class=SensorDeviceClass.TIMESTAMP
)
LAST_OUTCOME = SensorEntityDescription(key="last_outcome", translation_key="last_outcome")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    timezone = venue_timezone_for(hass, entry)

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        try:
            job = build_job(subentry_id, subentry.data, timezone)
        except ValueError, KeyError:
            # The scheduler logs and skips the same job; a broken one must not
            # cost the account its other entities.
            continue
        async_add_entities(
            [
                NextRunSensor(runtime.coordinator, job, entry),
                LastOutcomeSensor(runtime.coordinator, job, entry),
            ],
            config_subentry_id=subentry_id,
        )


class NextRunSensor(SkeddaJobEntity, SensorEntity):
    """When this job will next try to book."""

    entity_description = NEXT_RUN

    def __init__(
        self, coordinator: SkeddaCoordinator, job: BookingJob, entry: SkeddaConfigEntry
    ) -> None:
        super().__init__(coordinator, job, entry.runtime_data.account_device_id)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:next_run"

    @property
    def native_value(self) -> datetime | None:
        scheduler = self._entry.runtime_data.scheduler
        runner = scheduler.runner_for(self.job.job_id) if scheduler else None
        return runner.armed_for if runner else None


class LastOutcomeSensor(SkeddaJobEntity, SensorEntity):
    """How the job's last run ended, and why."""

    entity_description = LAST_OUTCOME

    def __init__(
        self, coordinator: SkeddaCoordinator, job: BookingJob, entry: SkeddaConfigEntry
    ) -> None:
        super().__init__(coordinator, job, entry.runtime_data.account_device_id)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:last_outcome"

    @property
    def native_value(self) -> str | None:
        last = self._entry.runtime_data.store.last_outcome(self.job.job_id)
        if last is None:
            return None
        # The reason, not just "failed": slot_taken and quota_exceeded call for
        # very different responses from the user.
        return "success" if last["succeeded"] else str(last["failure_reason"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self._entry.runtime_data.store.last_outcome(self.job.job_id) or {}
        return {
            "booking_id": last.get("booking_id"),
            "slot_start": last.get("slot_start"),
            "attempts": last.get("attempts"),
            "finished_at": last.get("finished_at"),
        }

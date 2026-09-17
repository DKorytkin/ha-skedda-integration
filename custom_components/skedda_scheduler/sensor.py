"""Per-job state sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry, WatchConfigEntry
from .const import (
    CONF_ENABLED,
    ENTRY_KIND_WATCH,
    STATUS_ARMED,
    STATUS_DISABLED,
    STATUS_OUT_OF_SEASON,
    SUBENTRY_TYPE_JOB,
    WATCH_STATUS_DISABLED,
    WATCH_STATUS_NO_QUOTA,
    WATCH_STATUS_WATCHING,
)
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob
from .core.watch import WatchRule
from .entity import SkeddaJobEntity, SkeddaWatchEntity
from .entry_kinds import entry_kind
from .job_factory import build_job, venue_timezone_for
from .watcher import WatchRunner

NEXT_RUN = SensorEntityDescription(
    key="next_run", translation_key="next_run", device_class=SensorDeviceClass.TIMESTAMP
)
LAST_OUTCOME = SensorEntityDescription(key="last_outcome", translation_key="last_outcome")

STATUS = SensorEntityDescription(
    key="status",
    translation_key="status",
    device_class=SensorDeviceClass.ENUM,
    options=[STATUS_ARMED, STATUS_DISABLED, STATUS_OUT_OF_SEASON],
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry_kind(entry) == ENTRY_KIND_WATCH:
        watch = cast(WatchConfigEntry, entry)
        for rule in watch.runtime_data.watcher.rules:
            async_add_entities(
                [WatchRuleSensor(watch, watch.runtime_data.watcher, rule)],
                config_subentry_id=rule.rule_id,
            )
        return

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
                JobStatusSensor(runtime.coordinator, job, entry),
            ],
            config_subentry_id=subentry_id,
        )


class NextRunSensor(SkeddaJobEntity, SensorEntity):
    """When this job will next try to book."""

    entity_description = NEXT_RUN

    def __init__(
        self, coordinator: SkeddaCoordinator, job: BookingJob, entry: SkeddaConfigEntry
    ) -> None:
        super().__init__(coordinator, job)
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
        super().__init__(coordinator, job)
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


class JobStatusSensor(SkeddaJobEntity, SensorEntity):
    """Whether this job is going to do anything, and if not, why not."""

    entity_description = STATUS

    def __init__(
        self, coordinator: SkeddaCoordinator, job: BookingJob, entry: SkeddaConfigEntry
    ) -> None:
        super().__init__(coordinator, job)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:status"

    @property
    def native_value(self) -> str:
        """Out of season is a state, not a fault.

        The court shuts for the winter and the subscription lapses; a job that
        showed only "next run: unknown" would read like something broken.
        """
        subentry = self._entry.subentries.get(self.job.job_id)
        if subentry is not None and not subentry.data.get(CONF_ENABLED, True):
            return STATUS_DISABLED
        scheduler = self._entry.runtime_data.scheduler
        runner = scheduler.runner_for(self.job.job_id) if scheduler else None
        if runner is not None and runner.armed_for is not None:
            return STATUS_ARMED
        return STATUS_OUT_OF_SEASON


WATCH = SensorEntityDescription(
    key="watch",
    translation_key="watch",
    device_class=SensorDeviceClass.ENUM,
    options=[WATCH_STATUS_WATCHING, WATCH_STATUS_DISABLED, WATCH_STATUS_NO_QUOTA],
)


class WatchRuleSensor(SkeddaWatchEntity, SensorEntity):
    """What this rule is doing, and why it is not doing more."""

    entity_description = WATCH

    def __init__(self, entry: WatchConfigEntry, runner: WatchRunner, rule: WatchRule) -> None:
        super().__init__(entry, runner, rule)
        self._attr_unique_id = f"{entry.entry_id}:{rule.rule_id}:watch"

    @property
    def native_value(self) -> str:
        if not self.rule.enabled:
            return WATCH_STATUS_DISABLED
        # A shut gate is the ordinary resting state at a venue with a weekly
        # allowance, and saying so beats reporting nothing.
        return WATCH_STATUS_WATCHING if self.runner.gate_open else WATCH_STATUS_NO_QUOTA

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        interval = self.runner.interval
        catch = self.runner.last_catch
        return {
            "gate_open": self.runner.gate_open,
            "poll_interval_minutes": interval.total_seconds() / 60 if interval else None,
            "courts": list(self.rule.space_ids),
            "days": sorted(self.rule.weekdays),
            "hours": f"{self.rule.not_before:%H:%M}-{self.rule.not_after:%H:%M}",
            "last_catch": catch.start.isoformat() if catch else None,
        }

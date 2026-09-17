"""Enable or disable a booking job without deleting it."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry, WatchConfigEntry
from .const import CONF_ENABLED, ENTRY_KIND_WATCH, SUBENTRY_TYPE_JOB
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob
from .core.watch import WatchRule
from .entity import SkeddaJobEntity, SkeddaWatchEntity
from .entry_kinds import entry_kind
from .job_factory import build_job, venue_timezone_for
from .watcher import WatchRunner

JOB_ENABLED = SwitchEntityDescription(key="job_enabled", translation_key="job_enabled")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry_kind(entry) == ENTRY_KIND_WATCH:
        runner = cast(WatchConfigEntry, entry).runtime_data.watcher
        for rule in runner.rules:
            async_add_entities(
                [WatchRuleEnabledSwitch(entry, runner, rule)], config_subentry_id=rule.rule_id
            )
        return

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
        super().__init__(coordinator, job)
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


WATCH_RULE_ENABLED = SwitchEntityDescription(key="rule_enabled", translation_key="rule_enabled")


class WatchRuleEnabledSwitch(SkeddaWatchEntity, SwitchEntity):
    """Whether this rule will take anything it finds."""

    entity_description = WATCH_RULE_ENABLED

    def __init__(self, entry: SkeddaConfigEntry, runner: WatchRunner, rule: WatchRule) -> None:
        super().__init__(entry, runner, rule)
        self._attr_unique_id = f"{entry.entry_id}:{rule.rule_id}:rule_enabled"

    @property
    def is_on(self) -> bool:
        subentry = self._entry.subentries.get(self.rule.rule_id)
        return bool(subentry.data.get(CONF_ENABLED, True)) if subentry else False

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(enabled=False)

    async def _async_set_enabled(self, *, enabled: bool) -> None:
        subentry = self._entry.subentries[self.rule.rule_id]
        self.hass.config_entries.async_update_subentry(
            self._entry, subentry, data={**subentry.data, CONF_ENABLED: enabled}
        )
        self.async_write_ha_state()

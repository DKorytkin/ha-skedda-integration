"""Custom services for Skedda Scheduler."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import ATTR_JOB_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_TRIGGER_JOB_NOW = "trigger_job_now"
SERVICE_REFRESH_SPACES = "refresh_spaces"

TRIGGER_SCHEMA = vol.Schema({vol.Required(ATTR_JOB_ID): cv.string})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def _trigger_job_now(call: ServiceCall) -> None:
        job_id = call.data[ATTR_JOB_ID]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            scheduler = entry.runtime_data.scheduler
            if scheduler is not None and scheduler.runner_for(job_id) is not None:
                await scheduler.async_run_now(job_id)
                return
        # Silence here would look exactly like a job that ran and failed.
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_job",
            translation_placeholders={"job_id": job_id},
        )

    async def _refresh_spaces(_call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.coordinator.async_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_TRIGGER_JOB_NOW, _trigger_job_now, schema=TRIGGER_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_SPACES, _refresh_spaces)

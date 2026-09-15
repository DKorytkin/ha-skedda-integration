"""Publish the outcome on the Home Assistant event bus.

This is the seam users automate against, so the payload is part of the public
contract: add fields, never rename them.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..const import EVENT_BOOKING_FAILED, EVENT_BOOKING_SUCCEEDED
from ..core.job import BookingJob
from ..core.result import BookingOutcome


class HaEventSink:
    """Fires skedda_scheduler_booking_succeeded / _failed."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_handle(self, outcome: BookingOutcome, job: BookingJob) -> None:
        event = EVENT_BOOKING_SUCCEEDED if outcome.succeeded else EVENT_BOOKING_FAILED
        self._hass.bus.async_fire(event, {"job_name": job.name, **outcome.as_dict()})

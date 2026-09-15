"""Tell the user what happened, through whichever notify services they picked."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from ..core.job import BookingJob
from ..core.result import BookingOutcome

_LOGGER = logging.getLogger(__name__)


def build_message(outcome: BookingOutcome, job: BookingJob) -> str:
    """One line describing the run, in the venue's own time.

    Not Home Assistant's local time: the court is booked for 18:00 *there*,
    and a user whose home is in another zone would otherwise be told a time
    the booking was never for.
    """
    when = outcome.slot_start.astimezone(job.tz).strftime("%a %d %b %H:%M")
    if outcome.succeeded:
        return f"Booked: {job.name} - {when} (booking {outcome.booking_id})"
    return f"Booking failed: {job.name} - {when} ({outcome.failure_reason})"


class NotifySink:
    """Calls each of the job's notify services with the result."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_handle(self, outcome: BookingOutcome, job: BookingJob) -> None:
        message = build_message(outcome, job)
        for target in job.notify_targets:
            domain, _, service = target.partition(".")
            if not service:
                _LOGGER.warning("Ignoring malformed notify target %s", target)
                continue
            try:
                await self._hass.services.async_call(
                    domain, service, {"message": message}, blocking=False
                )
            except HomeAssistantError:
                # One target the user has since deleted must not cost them the
                # notification on the others - this is how they learn anything
                # happened at all.
                _LOGGER.warning(
                    "Could not notify %s about job %s", target, job.job_id, exc_info=True
                )

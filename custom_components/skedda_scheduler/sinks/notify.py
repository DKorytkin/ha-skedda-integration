"""Tell the user what happened, through whichever notify services they picked."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from ..core.result import BookingOutcome
from ..core.subject import BookingSubject

_LOGGER = logging.getLogger(__name__)


#: Modern notify targets are entities, reached through one shared service.
NOTIFY_DOMAIN = "notify"
SERVICE_SEND_MESSAGE = "send_message"


def build_message(outcome: BookingOutcome, subject: BookingSubject) -> str:
    """One line describing the run, in the venue's own time.

    Not Home Assistant's local time: the court is booked for 18:00 *there*,
    and a user whose home is in another zone would otherwise be told a time
    the booking was never for.
    """
    when = outcome.slot_start.astimezone(subject.tz).strftime("%a %d %b %H:%M")
    if outcome.succeeded:
        return f"Booked: {subject.name} - {when} (booking {outcome.booking_id})"
    return f"Booking failed: {subject.name} - {when} ({outcome.failure_reason})"


class NotifySink:
    """Calls each of the subject's notify services with the result."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_handle(self, outcome: BookingOutcome, subject: BookingSubject) -> None:
        if not outcome.attempts:
            # Nothing was sent to Skedda, so nothing happened worth a buzz: the
            # slot was already ours, or the season is over. Arming a job of
            # that kind is routine, and announcing routine is what makes
            # notifications worth turning off.
            _LOGGER.debug(
                "Job %s finished without an attempt (%s); not notifying",
                subject.subject_id,
                outcome.failure_reason,
            )
            return
        message = build_message(outcome, subject)
        for target in subject.notify_targets:
            domain, _, service = target.partition(".")
            if not service:
                _LOGGER.warning("Ignoring malformed notify target %s", target)
                continue
            try:
                await self._hass.services.async_call(
                    *self._call_for(target, domain, service, message), blocking=False
                )
            except HomeAssistantError:
                # One target the user has since deleted must not cost them the
                # notification on the others - this is how they learn anything
                # happened at all.
                _LOGGER.warning(
                    "Could not notify %s about job %s", target, subject.subject_id, exc_info=True
                )

    def _call_for(
        self, target: str, domain: str, service: str, message: str
    ) -> tuple[str, str, dict[str, Any]]:
        """Decide how to reach one target.

        A notify target can be either an entity or, on older installations, a
        service of its own. The job form offers entities, and an entity is
        reached with notify.send_message and an entity_id - calling
        notify.<entity> as a service finds nothing and the user hears nothing.
        """
        if domain == NOTIFY_DOMAIN and self._hass.states.get(target) is not None:
            return (
                NOTIFY_DOMAIN,
                SERVICE_SEND_MESSAGE,
                {"entity_id": target, "message": message},
            )
        return domain, service, {"message": message}

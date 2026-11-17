"""Tell the user what happened, through whichever notify services they picked."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from ..const import PANEL_URL_PATH
from ..core.result import BookingOutcome
from ..core.subject import BookingSubject

_LOGGER = logging.getLogger(__name__)


#: Modern notify targets are entities, reached through one shared service.
NOTIFY_DOMAIN = "notify"
SERVICE_SEND_MESSAGE = "send_message"

#: The companion app's integration, whose notify entities cannot carry a link.
MOBILE_APP = "mobile_app"

#: What a tapped notification opens: `url` on iOS, `clickAction` on Android.
PANEL_LINK = {"url": f"/{PANEL_URL_PATH}", "clickAction": f"/{PANEL_URL_PATH}"}


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
            if phone := self._phone_service(target):
                return NOTIFY_DOMAIN, phone, {"message": message, "data": PANEL_LINK}
            return (
                NOTIFY_DOMAIN,
                SERVICE_SEND_MESSAGE,
                {"entity_id": target, "message": message},
            )
        return domain, service, {"message": message}

    def _phone_service(self, entity_id: str) -> str | None:
        """The companion app's own service behind a phone's notify entity.

        notify.send_message carries a message and a title and nothing else,
        so a push sent through it opens the app at whatever page it was last
        on. Reported 2026-11-17: "pushes arrive but lead nowhere". The app's
        legacy service, notify.mobile_app_<device>, still takes `data`.
        """
        registered = er.async_get(self._hass).async_get(entity_id)
        if registered is None or registered.platform != MOBILE_APP:
            return None
        phone = self._hass.config_entries.async_get_entry(registered.config_entry_id or "")
        name = phone.data.get("device_name") if phone else None
        if not name:
            return None
        service = f"{MOBILE_APP}_{slugify(name)}"
        return service if self._hass.services.has_service(NOTIFY_DOMAIN, service) else None

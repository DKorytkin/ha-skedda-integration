"""Put a booking in a Google calendar, and invite whoever is coming."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from ..api.errors import SkeddaError
from ..api.google import GoogleCalendarClient
from ..core.job import BookingJob
from ..core.result import BookingOutcome

_LOGGER = logging.getLogger(__name__)


class GoogleCalendarSink:
    """Creates one event per booking that landed.

    Only for bookings that landed: a failed run has no court to put in a
    calendar, and an event for a booking that does not exist is worse than
    no event at all.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: GoogleCalendarClient,
        *,
        calendar_id: str,
        title: str,
        location: str | None,
        attendees: tuple[str, ...],
    ) -> None:
        self._hass = hass
        self._client = client
        self._calendar_id = calendar_id
        self._title = title
        self._location = location
        self._attendees = attendees

    async def async_handle(self, outcome: BookingOutcome, job: BookingJob) -> None:
        if not outcome.succeeded:
            return
        try:
            event = await self._client.create_event(
                self._calendar_id,
                summary=self._title,
                start=outcome.slot_start.astimezone(job.tz),
                end=outcome.slot_end.astimezone(job.tz),
                timezone=job.venue_timezone,
                location=self._location,
                description=_description(outcome, job),
                attendees=self._attendees,
            )
        except SkeddaError as err:
            # The court is booked either way; losing the calendar entry must
            # not look like losing the court.
            _LOGGER.warning("Booked %s but could not add it to the calendar: %s", job.name, err)
            return
        _LOGGER.debug("Added %s to the calendar as %s", job.name, event.id)


def _description(outcome: BookingOutcome, job: BookingJob) -> str:
    """Enough to find the booking again, and nothing anybody has to read."""
    lines = [job.name]
    if outcome.booking_id:
        lines.append(f"Skedda booking {outcome.booking_id}")
    return "\n".join(lines)

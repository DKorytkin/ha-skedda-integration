"""Put a booking in a Google calendar, and invite whoever is coming."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..api.errors import SkeddaConnectionError, SkeddaError
from ..api.google import GoogleCalendarClient, event_id_for
from ..const import DOMAIN
from ..core.result import BookingOutcome
from ..core.subject import BookingSubject

_LOGGER = logging.getLogger(__name__)

#: Seconds between tries when Google cannot be reached. A home network that
#: drops out - DNS gone for twenty minutes, seen 2026-10-06 - is back long
#: before the last of these, and the court is days away.
RETRY_DELAYS: tuple[int, ...] = (30, 120, 300, 900, 1800, 3600)

#: Retries still waiting, by event id. Shared across sinks: the account that
#: booked and the panel that cancels each build their own, and a cancelled
#: booking must stop the other's retry from putting its event back.
_PENDING = f"{DOMAIN}_calendar_pending"


def _pending(hass: HomeAssistant) -> dict[str, CALLBACK_TYPE]:
    pending: dict[str, CALLBACK_TYPE] = hass.data.setdefault(_PENDING, {})
    return pending


class GoogleCalendarSink:
    """Creates one event per booking that landed, and removes it on release.

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
        color_id: str | None = None,
    ) -> None:
        self._hass = hass
        self._client = client
        self._calendar_id = calendar_id
        self._title = title
        self._location = location
        self._attendees = attendees
        self._color_id = color_id

    async def async_handle(self, outcome: BookingOutcome, subject: BookingSubject) -> None:
        if not outcome.succeeded:
            return
        await self._async_write(outcome, subject, tries=0)

    async def _async_write(
        self, outcome: BookingOutcome, subject: BookingSubject, tries: int
    ) -> None:
        event_id = event_id_for(self._calendar_id, outcome.space_id or "", outcome.slot_start)
        _pending(self._hass).pop(event_id, None)
        try:
            event = await self._client.create_event(
                self._calendar_id,
                summary=self._title,
                start=outcome.slot_start.astimezone(subject.tz),
                end=outcome.slot_end.astimezone(subject.tz),
                timezone=subject.venue_timezone,
                location=self._location,
                description=_description(outcome, subject),
                attendees=self._attendees,
                color_id=self._color_id,
                event_id=event_id,
            )
        except SkeddaConnectionError as err:
            if tries < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[tries]
                _LOGGER.warning(
                    "Booked %s but could not add it to the calendar yet: %s; trying again in %s s",
                    subject.name,
                    err,
                    delay,
                )
                self._retry_later(event_id, delay, outcome, subject, tries + 1)
                return
            _LOGGER.warning(
                "Booked %s but could not add it to the calendar after %s tries: %s",
                subject.name,
                tries + 1,
                err,
            )
            return
        except SkeddaError as err:
            # The court is booked either way; losing the calendar entry must
            # not look like losing the court.
            _LOGGER.warning("Booked %s but could not add it to the calendar: %s", subject.name, err)
            return
        _LOGGER.debug("Added %s to the calendar as %s", subject.name, event.id)

    def _retry_later(
        self,
        event_id: str,
        delay: int,
        outcome: BookingOutcome,
        subject: BookingSubject,
        tries: int,
    ) -> None:
        async def retry(_now: datetime) -> None:
            await self._async_write(outcome, subject, tries)

        _pending(self._hass)[event_id] = async_call_later(
            self._hass, delay, HassJob(retry, cancel_on_shutdown=True)
        )

    async def async_release(
        self, space_id: str, start: datetime, end: datetime, booking_id: str | None = None
    ) -> None:
        """Take a released booking's event out of the calendar.

        Events written before ids were chosen from the booking have ids
        nobody kept, so those are found by their time and title instead.
        """
        event_id = event_id_for(self._calendar_id, space_id, start)
        if (cancel := _pending(self._hass).pop(event_id, None)) is not None:
            # Never written, and now never will be.
            cancel()
            return
        notify = bool(self._attendees)
        try:
            if await self._client.delete_event(self._calendar_id, event_id, notify=notify):
                _LOGGER.debug("Removed the calendar event for %s", start)
                return
            for found in await self._client.events_between(self._calendar_id, start, end):
                if found.summary != self._title or found.start != start:
                    continue
                if (
                    booking_id
                    and "Skedda booking" in found.description
                    and f"Skedda booking {booking_id}" not in found.description
                ):
                    # Another of our bookings at the same time, on another court.
                    continue
                await self._client.delete_event(self._calendar_id, found.id, notify=notify)
                _LOGGER.debug("Removed the calendar event %s for %s", found.id, start)
                return
        except SkeddaError as err:
            _LOGGER.warning("Released %s but could not remove its calendar event: %s", start, err)
            return
        _LOGGER.debug("Released %s; it had no calendar event", start)


def _description(outcome: BookingOutcome, subject: BookingSubject) -> str:
    """Enough to find the booking again, and nothing anybody has to read.

    The account matters when several people book for the same group: the court
    is held by one of them, and only that one can change or release it.
    """
    lines = [subject.name]
    if outcome.account:
        lines.append(f"Booked with: {outcome.account}")
    if outcome.booking_id:
        lines.append(f"Skedda booking {outcome.booking_id}")
    return "\n".join(lines)

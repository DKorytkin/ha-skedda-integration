"""The account's court times, as calendars.

Two entities rather than one: Home Assistant colours calendars separately, so
what is booked and what is still waiting for its window can be told apart at a
glance in the built-in calendar view.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityDescription,
    CalendarEvent,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SkeddaConfigEntry
from .const import SUBENTRY_TYPE_JOB
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob
from .core.provider import Booking
from .entity import SkeddaAccountEntity
from .job_factory import build_job, venue_timezone_for

BOOKINGS = CalendarEntityDescription(key="bookings", translation_key="bookings")
PENDING = CalendarEntityDescription(key="pending", translation_key="pending")

#: How far ahead pending slots are listed. Past the venue's horizon there is
#: nothing to show but arithmetic.
PENDING_HORIZON = timedelta(days=90)
#: A job repeating for years would otherwise fill the calendar to the horizon.
MAX_PENDING_PER_JOB = 12


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities([BookedCalendar(coordinator), PendingCalendar(coordinator, entry)])


def _space_names(coordinator: SkeddaCoordinator) -> dict[str, str]:
    return {space.id: space.name for space in coordinator.data.spaces}


class BookedCalendar(SkeddaAccountEntity, CalendarEntity):
    """Court times this account holds."""

    entity_description = BOOKINGS

    def __init__(self, coordinator: SkeddaCoordinator) -> None:
        super().__init__(coordinator, Platform.CALENDAR)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:calendar_bookings"

    def _events(self) -> list[CalendarEvent]:
        names = _space_names(self.coordinator)
        # Skedda returns the whole venue's diary; only ours belongs here.
        return [
            CalendarEvent(
                start=booking.start,
                end=booking.end,
                summary=_summary(booking, names),
                description=f"Skedda booking {booking.id}",
            )
            for booking in self.coordinator.data.bookings
            if booking.is_mine
        ]

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.utcnow()
        upcoming = sorted(
            (event for event in self._events() if event.end > now), key=lambda e: e.start
        )
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            event for event in self._events() if event.start < end_date and event.end > start_date
        ]


class PendingCalendar(SkeddaAccountEntity, CalendarEntity):
    """Court times a job intends to take but has not taken yet."""

    entity_description = PENDING

    def __init__(self, coordinator: SkeddaCoordinator, entry: SkeddaConfigEntry) -> None:
        super().__init__(coordinator, Platform.CALENDAR)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:calendar_pending"

    def _jobs(self) -> list[BookingJob]:
        timezone = venue_timezone_for(self.hass, self._entry)
        jobs: list[BookingJob] = []
        for subentry_id, subentry in self._entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_JOB:
                continue
            try:
                jobs.append(build_job(subentry_id, subentry.data, timezone))
            except ValueError, KeyError:
                continue
        return jobs

    def _events(self) -> list[CalendarEvent]:
        held = {
            (booking.start, space)
            for booking in self.coordinator.data.bookings
            if booking.is_mine
            for space in booking.space_ids
        }
        names = _space_names(self.coordinator)
        now = dt_util.utcnow()
        events: list[CalendarEvent] = []
        for job in self._jobs():
            if not job.enabled:
                continue
            events.extend(_pending_for(job, now, held, names))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        upcoming = sorted(self._events(), key=lambda event: event.start)
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            event for event in self._events() if event.start < end_date and event.end > start_date
        ]


def _pending_for(
    job: BookingJob,
    now: datetime,
    held: set[tuple[datetime, str]],
    names: dict[str, str],
) -> list[CalendarEvent]:
    """Every slot this job still means to book, up to the horizon."""
    events: list[CalendarEvent] = []
    probe = now
    limit = now + PENDING_HORIZON
    for _ in range(MAX_PENDING_PER_JOB):
        slot = job.next_slot(probe)
        if slot is None:
            break
        start, end = slot
        if start > limit:
            break
        probe = start
        # A slot already on the books belongs to the other calendar; showing
        # it on both would double every court time the moment it succeeds.
        if any((start, space) in held for space in job.space_ids):
            continue
        opens = job.window.opens_at(start)
        events.append(
            CalendarEvent(
                start=start,
                end=end,
                summary=f"{names.get(job.primary_space_id, job.primary_space_id)} · {job.name}",
                description=f"Booking opens {dt_util.as_local(opens):%-d %b %H:%M}",
            )
        )
    return events


def _summary(booking: Booking, names: dict[str, str]) -> str:
    courts = ", ".join(names.get(space, space) for space in booking.space_ids)
    return f"{courts} · {booking.title}" if booking.title else courts

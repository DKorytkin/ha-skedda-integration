"""What the panel does by hand: take a free neighbour, or move ours onto it.

The decision of what to offer lives in core/offers.py; this module supplies the
venue it decides about, and carries out what somebody clicked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import google_calendar
from .const import CONF_VENUE, DEFAULT_WINDOW_DAYS, DOMAIN
from .core.offers import Offer, neighbour_offers
from .core.provider import Booking, BookingRequest
from .core.result import AttemptStatus, BookingAttempt, BookingOutcome
from .entry_kinds import is_account_entry
from .watcher import aimed_weeks

#: Booking title for a slot taken from the panel.
PANEL_TITLE = "Skedda panel"


class ActionError(Exception):
    """Something the person who clicked should be told, in plain words."""


@dataclass(frozen=True, slots=True)
class PanelSubject:
    """The panel, as the calendar sink sees whoever caused a booking."""

    venue_timezone: str
    subject_id: str = "panel"
    name: str = "Booked from the panel"
    notify_targets: tuple[str, ...] = ()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.venue_timezone)


def _loaded_accounts(hass: HomeAssistant) -> list[ConfigEntry]:
    return sorted(
        (
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if is_account_entry(entry)
        ),
        key=lambda entry: entry.entry_id,
    )


def _mine(entry: ConfigEntry) -> list[Booking]:
    data = entry.runtime_data.coordinator.data
    return [booking for booking in data.bookings if booking.is_mine] if data else []


def offers(hass: HomeAssistant) -> list[Offer]:
    """Every free neighbour of a lone booking of ours, venue by venue."""
    now = dt_util.utcnow()
    by_venue: dict[str, list[ConfigEntry]] = {}
    for entry in _loaded_accounts(hass):
        if entry.runtime_data.coordinator.data is not None:
            by_venue.setdefault(str(entry.data.get(CONF_VENUE, "")), []).append(entry)
    found: list[Offer] = []
    for accounts in by_venue.values():
        data = accounts[0].runtime_data.coordinator.data
        horizon = now + timedelta(days=data.rules.max_days_ahead or DEFAULT_WINDOW_DAYS)
        found.extend(
            neighbour_offers(
                {entry.entry_id: _mine(entry) for entry in accounts},
                data.bookings,
                quota_minutes=data.rules.weekly_quota_minutes,
                now=now,
                reserved={entry.entry_id: aimed_weeks(entry, now, horizon) for entry in accounts},
                is_open=data.rules.is_open,
                min_lead_minutes=data.rules.min_minutes_ahead,
            )
        )
    return sorted(found, key=lambda offer: offer.start)


async def async_take(
    hass: HomeAssistant, entry: ConfigEntry, space_id: str, start: datetime, end: datetime
) -> Booking:
    """Book a free slot with this account, and put it in the calendar."""
    request = BookingRequest(space_id=space_id, start=start, end=end, title=PANEL_TITLE)
    async with entry.runtime_data.semaphore:
        booked: Booking = await entry.runtime_data.provider.book(request)
    await _async_after(hass, entry, booked)
    return booked


async def async_move(
    hass: HomeAssistant, entry: ConfigEntry, booking_id: str, start: datetime, end: datetime
) -> Booking:
    """Move one of this account's bookings onto a free slot.

    The hour left behind is one we gave up on purpose: noted as released, so
    no watch takes it back, and its calendar event goes with it.
    """
    held = next((booking for booking in _mine(entry) if booking.id == booking_id), None)
    if held is None:
        raise ActionError("That booking is no longer held by this account.")
    async with entry.runtime_data.semaphore:
        moved: Booking = await entry.runtime_data.provider.move(held, start, end)
    space_id = held.space_ids[0] if held.space_ids else ""
    await entry.runtime_data.store.async_note_released(space_id, held.start, held.end)
    await google_calendar.async_release_event(hass, space_id, held.start, held.end, held.id)
    await _async_after(hass, entry, moved)
    return moved


async def _async_after(hass: HomeAssistant, entry: ConfigEntry, booking: Booking) -> None:
    """Refresh the account, and write the booking to the calendar."""
    now = dt_util.utcnow()
    outcome = BookingOutcome(
        job_id="panel",
        succeeded=True,
        booking_id=booking.id,
        space_id=booking.space_ids[0] if booking.space_ids else None,
        slot_start=booking.start,
        slot_end=booking.end,
        attempts=(BookingAttempt(1, now, AttemptStatus.SUCCESS, 0.0),),
        finished_at=now,
        account=entry.title,
    )
    timezone = str(entry.runtime_data.coordinator.data.rules.timezone)
    await google_calendar.async_add_event(hass, outcome, PanelSubject(timezone))
    await entry.runtime_data.coordinator.async_refresh()

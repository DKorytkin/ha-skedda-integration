"""Bridge between stored Home Assistant config and the domain model.

Subentry data is JSON, and Home Assistant's selectors hand back their own
types: times and dates arrive as strings, numbers as floats. This is the only
place that parses them, so core/ can stay strongly typed and unaware that a UI
exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DURATION,
    CONF_ENABLED,
    CONF_FREQUENCY,
    CONF_NAME,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SEASON_START,
    CONF_SPACE_ID,
    CONF_START_DATE,
    CONF_START_TIME,
    CONF_STRATEGY,
    CONF_TITLE,
    CONF_VENUE_TIMEZONE,
    CONF_WEEKDAY,
    CONF_WINDOW_DAYS,
)
from .core.job import BookingJob
from .core.recurrence import Frequency, RecurrenceRule
from .core.window import BookingWindow

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def describe(space_name: str, start: date, start_time: time, frequency: Frequency) -> str:
    """Name a job the way a person would say it out loud.

    Deriving the name means two fewer fields to fill in, and no booking ends up
    titled after whatever text happened to be typed into the wrong box.
    """
    when = (
        f"{WEEKDAY_NAMES[start.weekday()]}s"
        if frequency is not Frequency.ONCE
        else start.strftime("%-d %b")
    )
    return f"{space_name} · {when} {start_time.strftime('%H:%M')}"


def _time(value: str | time) -> time:
    return value if isinstance(value, time) else time.fromisoformat(value)


def _date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _recurrence(data: Mapping[str, Any]) -> RecurrenceRule:
    """Read the repetition, whichever shape it was stored in.

    The form asks for a date, the way a calendar does, and derives the weekday
    from it - a separate weekday field could contradict the date the user
    chose. Jobs stored by v0.0.1 carry a weekday and a season start instead,
    and keep working.
    """
    frequency = Frequency(data.get(CONF_FREQUENCY, Frequency.WEEKLY))
    start_raw = data.get(CONF_START_DATE) or data[CONF_SEASON_START]
    start = _date(start_raw)
    season_end = data.get(CONF_SEASON_END)
    if frequency is Frequency.ONCE:
        # One date, so the season is that date. Anything else would leave the
        # job looking open-ended in the UI when it has nothing left to do.
        return RecurrenceRule(
            frequency=frequency, weekday=start.weekday(), season_start=start, season_end=start
        )
    weekday = int(data[CONF_WEEKDAY]) if CONF_WEEKDAY in data else start.weekday()
    return RecurrenceRule(
        frequency=frequency,
        weekday=weekday,
        season_start=start,
        season_end=_date(season_end) if season_end else None,
    )


def build_job(subentry_id: str, data: Mapping[str, Any], venue_timezone: str) -> BookingJob:
    """Rebuild the job a subentry describes.

    Raises ValueError if the stored config cannot make a valid job - the
    dataclass validates itself, and the caller turns that into a repair issue
    rather than a crash at the moment a window opens.
    """
    recurrence = _recurrence(data)
    start_time = _time(data[CONF_START_TIME])
    space_id = str(data[CONF_SPACE_ID])
    # A job stored without a name predates the form deriving one, or was
    # written by hand. Either way it needs something to be called.
    name = str(
        data.get(CONF_NAME)
        or describe(space_id, recurrence.first_occurrence, start_time, recurrence.frequency)
    )
    return BookingJob(
        job_id=subentry_id,
        name=name,
        # Skedda's ids are strings on the wire. A number here would survive
        # storage and then match no space at all.
        space_ids=(space_id,),
        start_time=start_time,
        duration_minutes=int(data[CONF_DURATION]),
        recurrence=recurrence,
        window=BookingWindow(window_days=int(data[CONF_WINDOW_DAYS])),
        venue_timezone=venue_timezone,
        title=str(data.get(CONF_TITLE) or name),
        strategy=str(data.get(CONF_STRATEGY, "precise")),
        notify_targets=tuple(data.get(CONF_NOTIFY_TARGETS) or ()),
        enabled=bool(data.get(CONF_ENABLED, True)),
    )


def venue_timezone_for(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """The venue's timezone: what it last told us, then what setup stored.

    Never Home Assistant's own zone unless nothing else is known - the venue's
    clock is what decides when a window opens, and booking times go on the wire
    as a bare wall clock with nothing to correct a wrong guess.
    """
    try:
        return str(entry.runtime_data.coordinator.data.rules.timezone)
    except AttributeError:
        pass
    return str(entry.data.get(CONF_VENUE_TIMEZONE) or hass.config.time_zone)

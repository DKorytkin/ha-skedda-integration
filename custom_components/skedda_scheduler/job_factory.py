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

from .const import (
    CONF_DURATION,
    CONF_ENABLED,
    CONF_FREQUENCY,
    CONF_NAME,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SEASON_START,
    CONF_SPACE_ID,
    CONF_START_TIME,
    CONF_STRATEGY,
    CONF_TITLE,
    CONF_WEEKDAY,
    CONF_WINDOW_DAYS,
)
from .core.job import BookingJob
from .core.recurrence import Frequency, RecurrenceRule
from .core.window import BookingWindow


def _time(value: str | time) -> time:
    return value if isinstance(value, time) else time.fromisoformat(value)


def _date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def build_job(subentry_id: str, data: Mapping[str, Any], venue_timezone: str) -> BookingJob:
    """Rebuild the job a subentry describes.

    Raises ValueError if the stored config cannot make a valid job - the
    dataclass validates itself, and the caller turns that into a repair issue
    rather than a crash at the moment a window opens.
    """
    season_end = data.get(CONF_SEASON_END)
    return BookingJob(
        job_id=subentry_id,
        name=str(data[CONF_NAME]),
        # Skedda's ids are strings on the wire. A number here would survive
        # storage and then match no space at all.
        space_ids=(str(data[CONF_SPACE_ID]),),
        start_time=_time(data[CONF_START_TIME]),
        duration_minutes=int(data[CONF_DURATION]),
        recurrence=RecurrenceRule(
            frequency=Frequency(data[CONF_FREQUENCY]),
            weekday=int(data[CONF_WEEKDAY]),
            season_start=_date(data[CONF_SEASON_START]),
            season_end=_date(season_end) if season_end else None,
        ),
        window=BookingWindow(window_days=int(data[CONF_WINDOW_DAYS])),
        venue_timezone=venue_timezone,
        title=str(data[CONF_TITLE]),
        strategy=str(data.get(CONF_STRATEGY, "precise")),
        notify_targets=tuple(data.get(CONF_NOTIFY_TARGETS) or ()),
        enabled=bool(data.get(CONF_ENABLED, True)),
    )

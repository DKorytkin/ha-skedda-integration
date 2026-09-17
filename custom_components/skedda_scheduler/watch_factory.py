"""Turn stored subentry data into a WatchRule.

A form stores what a form can store - strings, lists, numbers. Everything that
turns those into domain types belongs here, so core/watch.py never has to know
what a config entry is.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from typing import Any

from .const import (
    CONF_ALLOW_OTHER_COURT,
    CONF_BOOK,
    CONF_DURATION,
    CONF_ENABLED,
    CONF_MAX_BLOCK_MINUTES,
    CONF_MIN_LEAD_MINUTES,
    CONF_MODE,
    CONF_NAME,
    CONF_NOT_AFTER,
    CONF_NOT_BEFORE,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SPACE_IDS,
    CONF_SPEED,
    CONF_WEEKDAYS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_MAX_BLOCK_MINUTES,
    DEFAULT_MIN_LEAD_MINUTES,
)
from .core.watch import WatchMode, WatchRule, WatchSpeed

#: The form speaks day names; datetime.weekday() speaks numbers.
WEEKDAYS: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def build_rule(subentry_id: str, data: Mapping[str, Any], venue_timezone: str) -> WatchRule:
    """One rule, as the watcher needs it.

    Raises ValueError for anything the domain refuses - a rule with no days,
    hours that run backwards - so a broken rule is skipped rather than watched
    for ever without ever matching.
    """
    return WatchRule(
        rule_id=subentry_id,
        name=str(data[CONF_NAME]),
        weekdays=frozenset(WEEKDAYS[day] for day in data.get(CONF_WEEKDAYS) or ()),
        not_before=_time(data[CONF_NOT_BEFORE]),
        not_after=_time(data[CONF_NOT_AFTER]),
        space_ids=tuple(data.get(CONF_SPACE_IDS) or ()),
        duration_minutes=int(data.get(CONF_DURATION, DEFAULT_DURATION_MINUTES)),
        venue_timezone=venue_timezone,
        mode=WatchMode(data.get(CONF_MODE, WatchMode.BOTH)),
        max_block_minutes=int(data.get(CONF_MAX_BLOCK_MINUTES, DEFAULT_MAX_BLOCK_MINUTES)),
        allow_other_court=bool(data.get(CONF_ALLOW_OTHER_COURT, False)),
        min_lead_minutes=int(data.get(CONF_MIN_LEAD_MINUTES, DEFAULT_MIN_LEAD_MINUTES)),
        speed=WatchSpeed(data.get(CONF_SPEED, WatchSpeed.STEPPED)),
        book=bool(data.get(CONF_BOOK, True)),
        active_until=_date(data.get(CONF_SEASON_END)),
        enabled=bool(data.get(CONF_ENABLED, True)),
        notify_targets=tuple(data.get(CONF_NOTIFY_TARGETS) or ()),
    )


def _time(value: str | time) -> time:
    return value if isinstance(value, time) else time.fromisoformat(value)


def _date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, date) else date.fromisoformat(value)

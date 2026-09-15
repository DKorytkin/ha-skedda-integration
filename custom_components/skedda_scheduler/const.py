"""Constants for the Skedda Scheduler integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "skedda_scheduler"

CONF_VENUE: Final = "venue"
CONF_ALIAS: Final = "alias"
#: Read from /webs during setup rather than typed: every booking time is
#: venue-local, and a guessed zone silently shifts every slot.
CONF_VENUE_TIMEZONE: Final = "venue_timezone"

SUBENTRY_TYPE_JOB: Final = "job"

CONF_SPACE_ID: Final = "space_id"
CONF_WEEKDAY: Final = "weekday"
CONF_START_TIME: Final = "start_time"
CONF_DURATION: Final = "duration_minutes"
CONF_WINDOW_DAYS: Final = "window_days"
CONF_FREQUENCY: Final = "frequency"
CONF_SEASON_START: Final = "season_start"
CONF_SEASON_END: Final = "season_end"
CONF_TITLE: Final = "title"
CONF_STRATEGY: Final = "strategy"
CONF_NOTIFY_TARGETS: Final = "notify_targets"
CONF_ENABLED: Final = "enabled"
CONF_NAME: Final = "name"

ATTR_JOB_ID: Final = "job_id"

#: Venues commonly cap a member's weekly allowance at an hour - the venue this
#: was built against allows exactly 60 minutes per week (quotaRules in the
#: contract doc). The job form raises it where the venue allows more.
DEFAULT_DURATION_MINUTES: Final = 60
#: Only a fallback. The real value is the venue's own bookingWindow rule, read
#: from /webs: a job that fires earlier than the window opens is refused, and
#: one that fires later arrives after the slot is gone.
DEFAULT_WINDOW_DAYS: Final = 14

EVENT_BOOKING_SUCCEEDED: Final = f"{DOMAIN}_booking_succeeded"
EVENT_BOOKING_FAILED: Final = f"{DOMAIN}_booking_failed"

DEFAULT_PREWARM_SECONDS: Final = 120
DEFAULT_LEAD_MS: Final = 150
DEFAULT_BURST_COUNT: Final = 5
DEFAULT_BURST_SPACING_MS: Final = 250
MAX_ATTEMPTS_PER_RUN: Final = 8

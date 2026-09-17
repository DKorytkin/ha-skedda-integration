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
SUBENTRY_TYPE_WATCH_RULE: Final = "watch_rule"

#: A config entry is a Skedda account, the Google calendar link, or the slot
#: watch. One domain, three kinds: neither the calendar nor the watch is owned
#: by any one account - the watch spends whichever account still has quota.
CONF_ENTRY_KIND: Final = "entry_kind"
ENTRY_KIND_ACCOUNT: Final = "account"
ENTRY_KIND_CALENDAR: Final = "google_calendar"
ENTRY_KIND_WATCH: Final = "slot_watch"

CONF_CALENDAR_ID: Final = "calendar_id"
CONF_EVENT_TITLE: Final = "event_title"
CONF_LOCATION: Final = "location"
CONF_ATTENDEES: Final = "attendees"

#: What a court booking is called in a calendar, unless you say otherwise.
DEFAULT_EVENT_TITLE: Final = "Tennis 🎾"

CONF_SPACE_ID: Final = "space_id"
CONF_WEEKDAY: Final = "weekday"
CONF_START_DATE: Final = "start_date"
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

CONF_WEEKDAYS: Final = "weekdays"
CONF_NOT_BEFORE: Final = "not_before"
CONF_NOT_AFTER: Final = "not_after"
CONF_SPACE_IDS: Final = "space_ids"
CONF_MODE: Final = "mode"
CONF_MAX_BLOCK_MINUTES: Final = "max_block_minutes"
CONF_ALLOW_OTHER_COURT: Final = "allow_other_court"
CONF_MIN_LEAD_MINUTES: Final = "min_lead_minutes"
CONF_SPEED: Final = "speed"
CONF_BOOK: Final = "book"

ATTR_JOB_ID: Final = "job_id"

#: Why a job is not about to do anything, in words rather than by absence.
#: Shared by the status sensor and the panel, so the two cannot drift apart.
STATUS_ARMED: Final = "armed"
STATUS_DISABLED: Final = "disabled"
STATUS_OUT_OF_SEASON: Final = "out_of_season"

#: Why a watch rule is not about to catch anything. "No quota" is the ordinary
#: resting state at a venue with a weekly allowance, not a fault.
WATCH_STATUS_WATCHING: Final = "watching"
WATCH_STATUS_DISABLED: Final = "disabled"
WATCH_STATUS_NO_QUOTA: Final = "no_quota"

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
EVENT_SLOT_CAUGHT: Final = f"{DOMAIN}_slot_caught"

SERVICE_SLOT_FREED: Final = "slot_freed"

#: Two players' worth of doubles either side of an hour, and the default cap on
#: a block a watch rule may build.
DEFAULT_MAX_BLOCK_MINUTES: Final = 180
#: A court starting sooner than this cannot be filled with people.
DEFAULT_MIN_LEAD_MINUTES: Final = 180

DEFAULT_PREWARM_SECONDS: Final = 120
DEFAULT_LEAD_MS: Final = 150
DEFAULT_BURST_COUNT: Final = 5
DEFAULT_BURST_SPACING_MS: Final = 250
MAX_ATTEMPTS_PER_RUN: Final = 8

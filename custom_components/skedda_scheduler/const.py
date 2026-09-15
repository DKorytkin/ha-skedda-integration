"""Constants for the Skedda Scheduler integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "skedda_scheduler"

CONF_VENUE: Final = "venue"
CONF_ALIAS: Final = "alias"

SUBENTRY_TYPE_JOB: Final = "job"

EVENT_BOOKING_SUCCEEDED: Final = f"{DOMAIN}_booking_succeeded"
EVENT_BOOKING_FAILED: Final = f"{DOMAIN}_booking_failed"

DEFAULT_PREWARM_SECONDS: Final = 120
DEFAULT_LEAD_MS: Final = 150
DEFAULT_BURST_COUNT: Final = 5
DEFAULT_BURST_SPACING_MS: Final = 250
MAX_ATTEMPTS_PER_RUN: Final = 8

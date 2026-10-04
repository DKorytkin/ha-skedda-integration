"""Estimate how far the Skedda server's clock is from ours.

The precise strategy fires a fraction of a second before a booking window
opens. If the Home Assistant host's clock runs fast, it fires into a closed
window and loses the slot; if it runs slow, someone else gets there first.

offset = server_time - local_time, corrected for half the round trip and
smoothed with an exponentially weighted moving average.

Two properties of the source data drive the design:

* Skedda's ``Date`` header has one-second resolution and is *truncated*, not
  rounded: "18:00:00" means some instant in [18:00:00, 18:00:01). Read as the
  start of that second, every sample is half a second early on average, and an
  average of such samples is too. The middle of the second is the unbiased
  reading. Observed live 2026-09-29: the burst left 0.29-0.34 s after the open
  instant instead of 0.15 s before it, and lost a slot by that margin.
* Averaging several samples is what buys sub-second accuracy - hence the EWMA
  rather than last-value-wins.
* The half-round-trip correction assumes the request and response legs take
  about the same time. A stalled response breaks that assumption badly, so
  samples with an implausibly long round trip are discarded instead of being
  averaged in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

#: Where in a truncated whole-second ``Date`` stamp the server's clock most
#: likely stood.
_TRUNCATION_MIDPOINT = timedelta(milliseconds=500)

# Observed round trips to a venue host were 165-210 ms. A second is already far
# outside that; anything beyond is a stall, not latency.
DEFAULT_MAX_ROUND_TRIP_SECONDS = 2.0


def parse_date_header(value: str) -> datetime:
    """Parse an HTTP ``Date`` header into a UTC-aware datetime."""
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(slots=True)
class ClockSync:
    alpha: float = 0.3
    max_round_trip_seconds: float = DEFAULT_MAX_ROUND_TRIP_SECONDS
    offset_seconds: float = field(default=0.0, init=False)
    samples: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError(f"alpha must be in (0, 1], got {self.alpha}")

    def observe(
        self,
        server_time: datetime,
        request_sent: datetime,
        response_received: datetime,
    ) -> float:
        """Fold one observation into the estimate and return the new offset.

        A sample whose round trip exceeds ``max_round_trip_seconds`` is
        discarded; the previous estimate is returned unchanged.
        """
        round_trip = (response_received - request_sent).total_seconds()
        if round_trip > self.max_round_trip_seconds:
            return self.offset_seconds
        if server_time.microsecond == 0:
            server_time += _TRUNCATION_MIDPOINT
        local_midpoint = request_sent + timedelta(seconds=round_trip / 2)
        sample = (server_time - local_midpoint).total_seconds()
        if self.samples == 0:
            self.offset_seconds = sample
        else:
            self.offset_seconds = self.alpha * sample + (1 - self.alpha) * self.offset_seconds
        self.samples += 1
        return self.offset_seconds

    def local_instant_for(self, server_instant: datetime) -> datetime:
        """Return the local instant at which the server's clock reads ``server_instant``."""
        return server_instant - timedelta(seconds=self.offset_seconds)

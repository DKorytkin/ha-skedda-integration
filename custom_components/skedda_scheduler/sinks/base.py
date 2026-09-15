"""What to do with a booking outcome."""

from __future__ import annotations

from typing import Protocol

from ..core.job import BookingJob
from ..core.result import BookingOutcome


class ResultSink(Protocol):
    """One destination for the result of a booking run."""

    async def async_handle(self, outcome: BookingOutcome, job: BookingJob) -> None: ...

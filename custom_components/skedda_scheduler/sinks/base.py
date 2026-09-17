"""What to do with a booking outcome."""

from __future__ import annotations

from typing import Protocol

from ..core.result import BookingOutcome
from ..core.subject import BookingSubject


class ResultSink(Protocol):
    """One destination for the result of a booking run."""

    async def async_handle(self, outcome: BookingOutcome, subject: BookingSubject) -> None: ...

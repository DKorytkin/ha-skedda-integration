"""What a sink needs to know about whatever caused a booking.

A booking job and a watch rule have almost nothing in common, and the sinks
care about neither: they want a name to print, a zone to print it in, and
somewhere to send it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo


@runtime_checkable
class BookingSubject(Protocol):
    """Whatever a booking outcome is reported on behalf of."""

    @property
    def subject_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def tz(self) -> ZoneInfo: ...

    @property
    def venue_timezone(self) -> str: ...

    @property
    def notify_targets(self) -> tuple[str, ...]: ...

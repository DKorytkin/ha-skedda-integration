"""Outcome fan-out."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from homeassistant.core import HomeAssistant

from ..core.job import BookingJob
from ..core.result import BookingOutcome
from .base import ResultSink
from .ha_event import HaEventSink
from .notify import NotifySink

_LOGGER = logging.getLogger(__name__)

__all__ = ["ResultSink", "async_dispatch", "build_default_sinks", "build_sinks"]


def build_default_sinks(hass: HomeAssistant) -> list[ResultSink]:
    """What every booking outcome goes to, whatever else is configured."""
    return [HaEventSink(hass), NotifySink(hass)]


def build_sinks(hass: HomeAssistant, calendar: ResultSink | None = None) -> list[ResultSink]:
    """The default sinks, plus the calendar if one has been linked.

    No calendar entry means no calendar sink, which is what "if there is none,
    nothing happens" looks like from here.
    """
    sinks = build_default_sinks(hass)
    if calendar is not None:
        sinks.append(calendar)
    return sinks


async def async_dispatch(
    sinks: Sequence[ResultSink], outcome: BookingOutcome, job: BookingJob
) -> None:
    """Run every sink. A broken sink must never lose the other sinks' output."""
    for sink in sinks:
        try:
            await sink.async_handle(outcome, job)
        except Exception:
            _LOGGER.exception("Sink %s failed for job %s", type(sink).__name__, job.job_id)

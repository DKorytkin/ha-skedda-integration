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

__all__ = ["ResultSink", "async_dispatch", "build_default_sinks"]


def build_default_sinks(hass: HomeAssistant) -> list[ResultSink]:
    return [HaEventSink(hass), NotifySink(hass)]


async def async_dispatch(
    sinks: Sequence[ResultSink], outcome: BookingOutcome, job: BookingJob
) -> None:
    """Run every sink. A broken sink must never lose the other sinks' output."""
    for sink in sinks:
        try:
            await sink.async_handle(outcome, job)
        except Exception:
            _LOGGER.exception("Sink %s failed for job %s", type(sink).__name__, job.job_id)

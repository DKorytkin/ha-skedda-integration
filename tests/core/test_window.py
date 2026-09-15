"""Booking-window arithmetic, including the DST edge that loses an hour.

Skedda's horizon is rolling: a slot starting at T becomes bookable at
T - window_days, at the slot's own wall-clock time. Confirmed 2026-09-15; see
the contract doc, "The horizon is rolling, not a daily unlock".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.core.window import BookingWindow

KYIV = ZoneInfo("Europe/Kyiv")
WINDOW = BookingWindow(window_days=14)


def test_window_opens_at_the_slots_own_time_of_day_fourteen_days_earlier() -> None:
    """Not midnight: the opening instant tracks the slot's wall clock."""
    slot = datetime(2026, 9, 29, 21, 0, tzinfo=KYIV)
    # Kyiv is UTC+3 in September, so 21:00 local is 18:00 UTC.
    assert WINDOW.opens_at(slot) == datetime(2026, 9, 15, 18, 0, tzinfo=UTC)


def test_an_early_slot_opens_earlier_in_the_day_than_a_late_one() -> None:
    """The property that distinguishes a rolling horizon from a daily unlock."""
    morning = WINDOW.opens_at(datetime(2026, 9, 29, 10, 0, tzinfo=KYIV))
    evening = WINDOW.opens_at(datetime(2026, 9, 29, 21, 0, tzinfo=KYIV))
    assert evening - morning == timedelta(hours=11)


def test_a_shorter_window_opens_later() -> None:
    slot = datetime(2026, 9, 29, 21, 0, tzinfo=KYIV)
    assert BookingWindow(window_days=7).opens_at(slot) == datetime(
        2026, 9, 22, 18, 0, tzinfo=UTC
    )


def test_the_opening_instant_uses_the_offset_in_force_on_the_opening_date() -> None:
    """The slot and its opening moment can sit on opposite sides of a DST switch.

    Kyiv moves to UTC+3 on 2026-03-29. A slot on 2026-03-31 at 18:00 (+03:00)
    opens on 2026-03-17, which is still +02:00 - so the UTC instant is 16:00,
    not 15:00. Resolving the offset against the slot's date instead would fire
    the scheduler an hour late and lose the slot.
    """
    slot = datetime(2026, 3, 31, 18, 0, tzinfo=KYIV)
    opens = BookingWindow(window_days=14).opens_at(slot)
    assert opens == datetime(2026, 3, 17, 16, 0, tzinfo=UTC)
    assert opens.astimezone(KYIV).hour == 18


def test_a_zero_day_window_opens_exactly_at_the_slot_start() -> None:
    slot = datetime(2026, 9, 29, 21, 0, tzinfo=KYIV)
    assert BookingWindow(window_days=0).opens_at(slot) == slot.astimezone(UTC)


def test_the_result_is_always_utc() -> None:
    """Everything downstream schedules in UTC; a venue-local return would rot."""
    opens = WINDOW.opens_at(datetime(2026, 9, 29, 21, 0, tzinfo=KYIV))
    assert opens.tzinfo is UTC


def test_is_open_compares_against_a_given_instant() -> None:
    slot = datetime(2026, 9, 29, 21, 0, tzinfo=KYIV)
    opens = WINDOW.opens_at(slot)
    assert not WINDOW.is_open(slot, now=opens - timedelta(seconds=1))
    assert WINDOW.is_open(slot, now=opens)
    assert WINDOW.is_open(slot, now=opens + timedelta(hours=1))


def test_naive_slot_start_is_rejected() -> None:
    """A naive datetime means the venue timezone was lost somewhere upstream."""
    with pytest.raises(ValueError, match="timezone-aware"):
        WINDOW.opens_at(datetime(2026, 9, 29, 21, 0))


def test_a_fixed_offset_timezone_is_rejected() -> None:
    """A fixed offset cannot express DST, so it would silently misfire twice a year."""
    slot = datetime(2026, 9, 29, 21, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="named timezone"):
        WINDOW.opens_at(slot)


def test_negative_window_days_is_rejected() -> None:
    with pytest.raises(ValueError, match="window_days"):
        BookingWindow(window_days=-1)


def test_windows_are_immutable() -> None:
    with pytest.raises(AttributeError):
        WINDOW.window_days = 3  # type: ignore[misc]

"""Server clock-offset estimation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.skedda_scheduler.api.clock import ClockSync, parse_date_header

SENT = datetime(2026, 5, 19, 18, 0, 0, tzinfo=UTC)
RECEIVED = SENT + timedelta(milliseconds=100)
MIDPOINT = timedelta(milliseconds=50)


def test_parse_date_header_returns_utc_aware_datetime() -> None:
    parsed = parse_date_header("Tue, 15 Sep 2026 11:43:53 GMT")
    assert parsed == datetime(2026, 9, 15, 11, 43, 53, tzinfo=UTC)


def test_parse_date_header_treats_an_unknown_zone_as_utc() -> None:
    """RFC 5322 spells "zone unknown" as -0000, which parses to a naive value.

    Leaving it naive would break every later subtraction against UTC instants.
    """
    parsed = parse_date_header("Tue, 15 Sep 2026 11:43:53 -0000")
    assert parsed == datetime(2026, 9, 15, 11, 43, 53, tzinfo=UTC)


def test_offset_is_zero_when_clocks_agree_and_latency_is_symmetric() -> None:
    sync = ClockSync()
    assert sync.observe(SENT + MIDPOINT, SENT, RECEIVED) == 0.0


def test_positive_offset_when_server_clock_is_ahead() -> None:
    sync = ClockSync()
    assert sync.observe(SENT + MIDPOINT + timedelta(seconds=2), SENT, RECEIVED) == 2.0


def test_later_samples_are_smoothed_not_replaced() -> None:
    sync = ClockSync(alpha=0.5)
    sync.observe(SENT + MIDPOINT + timedelta(seconds=2), SENT, RECEIVED)
    second = sync.observe(SENT + MIDPOINT + timedelta(seconds=4), SENT, RECEIVED)
    assert second == 3.0
    assert sync.samples == 2


def test_local_instant_for_subtracts_the_offset() -> None:
    sync = ClockSync()
    sync.observe(SENT + MIDPOINT + timedelta(seconds=2), SENT, RECEIVED)
    target = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
    assert sync.local_instant_for(target) == target - timedelta(seconds=2)


def test_a_stalled_response_is_discarded_rather_than_averaged_in() -> None:
    """A long round trip makes the midpoint estimate meaningless.

    The Date header has one-second resolution, so the whole estimate rests on
    assuming symmetric latency. A multi-second stall breaks that assumption and
    would drag the offset by seconds - worse than having no sample at all.
    """
    sync = ClockSync(max_round_trip_seconds=2.0)
    sync.observe(SENT + MIDPOINT + timedelta(seconds=2), SENT, RECEIVED)
    stalled_received = SENT + timedelta(seconds=9)
    unchanged = sync.observe(SENT + timedelta(seconds=30), SENT, stalled_received)
    assert unchanged == 2.0
    assert sync.samples == 1


def test_a_stalled_first_response_leaves_the_estimate_unset() -> None:
    sync = ClockSync(max_round_trip_seconds=2.0)
    assert sync.observe(SENT, SENT, SENT + timedelta(seconds=9)) == 0.0
    assert sync.samples == 0


def test_alpha_must_be_a_usable_weight() -> None:
    with pytest.raises(ValueError, match="alpha"):
        ClockSync(alpha=0.0)
    with pytest.raises(ValueError, match="alpha"):
        ClockSync(alpha=1.5)

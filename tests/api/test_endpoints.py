"""The contract module is the only place URLs and payload shapes may live.

Everything asserted here was observed on live traffic 2026-09-15; see
.claude/specs/skedda-api-contract.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.errors import (
    ApiContractError,
    BookingWindowClosedError,
    QuotaExceededError,
    RateLimitedError,
    SkeddaAuthError,
    SlotTakenError,
)
from custom_components.skedda_scheduler.api.models import (
    SkeddaBookingRequest,
    SkeddaIdentity,
)

FIXTURES = Path("tests/fixtures/skedda")
KYIV = ZoneInfo("Europe/Kyiv")


IDENTITY = SkeddaIdentity(venue_id="182167", venueuser_id="3985917")


def make_request(**overrides: object) -> SkeddaBookingRequest:
    defaults: dict[str, object] = {
        "space_ids": ("1011034",),
        "start": datetime(2026, 9, 16, 15, 0, tzinfo=KYIV),
        "end": datetime(2026, 9, 16, 16, 0, tzinfo=KYIV),
        "title": "Tennis",
    }
    return SkeddaBookingRequest(**(defaults | overrides))  # type: ignore[arg-type]


def test_base_url_is_built_from_the_venue_subdomain() -> None:
    assert endpoints.base_url("galaktyka") == "https://galaktyka.skedda.com"


def test_login_lives_on_the_central_host_not_the_venue() -> None:
    assert endpoints.LOGIN_HOST == "https://app.skedda.com"
    assert endpoints.LOGIN.path == "/logins"


def test_booking_path_has_no_api_prefix() -> None:
    assert endpoints.booking_path("120962300") == "/bookings/120962300"


def test_booking_payload_serialises_times_as_naive_venue_local() -> None:
    payload = endpoints.booking_payload(make_request(), IDENTITY)["booking"]
    # Skedda sends local wall-clock with no offset - never converted to UTC.
    assert payload["start"] == "2026-09-16T15:00:00"
    assert payload["end"] == "2026-09-16T16:00:00"


def test_booking_payload_uses_the_spaces_key_with_string_ids() -> None:
    payload = endpoints.booking_payload(make_request(), IDENTITY)["booking"]
    assert payload["spaces"] == ["1011034"]
    assert "spaceIds" not in payload


def test_booking_payload_carries_the_venue_and_the_acting_user() -> None:
    """The server does not infer the booker; omitting venueuser fails."""
    payload = endpoints.booking_payload(make_request(), IDENTITY)["booking"]
    assert payload["venue"] == "182167"
    assert payload["venueuser"] == "3985917"


def test_booking_payload_rejects_naive_datetimes() -> None:
    """A naive datetime means the caller lost track of the venue timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        endpoints.booking_payload(make_request(start=datetime(2026, 9, 16, 15, 0)), IDENTITY)


def test_login_payload_wraps_credentials_in_a_login_envelope() -> None:
    payload = endpoints.login_payload("user@example.com", "secret")
    assert payload["login"]["username"] == "user@example.com"
    assert payload["login"]["password"] == "secret"


def test_classify_error_reads_the_window_marker() -> None:
    body = json.loads((FIXTURES / "error_window.json").read_text())
    assert endpoints.classify_error(422, body) is BookingWindowClosedError


def test_classify_error_reads_the_quota_marker() -> None:
    body = json.loads((FIXTURES / "error_quota.json").read_text())
    assert endpoints.classify_error(422, body) is QuotaExceededError


def test_classify_error_falls_back_when_a_422_marker_is_unknown() -> None:
    """An unrecognised rule means Skedda added one; do not guess it away."""
    body = {"errors": [{"detail": "<span>something new</span>"}]}
    assert endpoints.classify_error(422, body) is ApiContractError


def test_classify_error_maps_plain_statuses() -> None:
    assert endpoints.classify_error(403, {}) is SkeddaAuthError
    assert endpoints.classify_error(429, {}) is RateLimitedError


def test_classify_error_returns_none_for_success() -> None:
    assert endpoints.classify_error(200, {}) is None
    assert endpoints.classify_error(204, None) is None


def test_error_detail_is_stripped_of_markup() -> None:
    """detail is localised HTML; users must not see tags."""
    body = json.loads((FIXTURES / "error_window.json").read_text())
    assert endpoints.error_detail(body) == (
        "You can only book at Test Venue up to 14 day(s) ahead."
    )


def test_error_detail_is_empty_for_a_bodyless_response() -> None:
    assert endpoints.error_detail(None) == ""


@pytest.mark.parametrize(
    "body",
    [
        None,
        "a plain string body",
        {"errors": "not a list"},
        {"errors": [{"detail": 42}]},
        {},
    ],
    ids=["none", "string", "errors-not-list", "detail-not-string", "empty"],
)
def test_malformed_422_bodies_degrade_to_a_contract_error(body: object) -> None:
    """A shape we cannot read means Skedda changed something.

    ApiContractError surfaces as a repairs issue instead of being retried
    forever against a server that will keep refusing.
    """
    assert endpoints.classify_error(422, body) is ApiContractError
    assert endpoints.error_detail(body) == ""


def test_classify_error_recognises_a_slot_conflict_by_phrase() -> None:
    """The conflict error carries no data-var markers, only prose.

    Confirmed 2026-09-15. Falling through to ApiContractError would cost the
    reserve-space fallback, which is the one recovery a conflict allows.
    """
    body = json.loads((FIXTURES / "error_conflict.json").read_text())
    assert endpoints.classify_error(422, body) is SlotTakenError


def test_a_conflict_detail_needs_no_tag_stripping() -> None:
    body = json.loads((FIXTURES / "error_conflict.json").read_text())
    detail = endpoints.error_detail(body)
    assert detail.startswith("We couldn't put in your booking")
    assert "<" not in detail


def test_marker_matching_wins_over_phrase_matching() -> None:
    """A quota body that happens to contain the word "conflicts" is still quota.

    Markers are structured and language-independent; phrases are neither, so
    they must only ever be the fallback.
    """
    body = {
        "errors": [
            {
                "detail": "<span>conflicts with your quota for the week "
                '<var data-var="period-start">28.09.26</var> to '
                '<var data-var="period-end">04.10.26</var>, max '
                '<var data-var="time-value">1h</var></span>'
            }
        ]
    }
    assert endpoints.classify_error(422, body) is QuotaExceededError

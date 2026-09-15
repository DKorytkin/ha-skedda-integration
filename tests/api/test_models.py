"""Transport DTO parsing.

Every field name asserted here was observed on live traffic 2026-09-15 and is
recorded in .claude/specs/skedda-api-contract.md. If a test here fails after a
Skedda release, re-capture before changing the assertion.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.skedda_scheduler.api.errors import ApiContractError
from custom_components.skedda_scheduler.api.models import (
    SkeddaBooking,
    SkeddaCredentials,
    SkeddaSession,
    SkeddaSpace,
)

FIXTURES = Path("tests/fixtures/skedda")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_spaces_are_parsed_from_the_assets_key() -> None:
    payload = _load("webs.json")
    spaces = [SkeddaSpace.from_payload(item) for item in payload["assets"]]
    assert [space.name for space in spaces] == ["Court 1", "Court 2"]


def test_space_ids_stay_strings() -> None:
    payload = _load("webs.json")
    space = SkeddaSpace.from_payload(payload["assets"][0])
    assert space.id == "2000001"


def test_space_from_payload_raises_contract_error_on_unknown_shape() -> None:
    with pytest.raises(ApiContractError, match="id"):
        SkeddaSpace.from_payload({"unexpected": "shape"})


def test_booking_times_are_parsed_as_naive_venue_local() -> None:
    booking = SkeddaBooking.from_payload(_load("bookings_list.json")["bookings"][0])
    assert booking.start == datetime(2026, 9, 28, 8, 0)
    assert booking.end == datetime(2026, 9, 28, 9, 0)
    assert booking.start.tzinfo is None


def test_booking_spaces_are_read_from_the_spaces_key() -> None:
    booking = SkeddaBooking.from_payload(_load("bookings_list.json")["bookings"][0])
    assert booking.space_ids == ("2000001",)
    assert booking.id == "300000001"


def test_booking_tolerates_a_null_title() -> None:
    booking = SkeddaBooking.from_payload(_load("bookings_list.json")["bookings"][0])
    assert booking.title == ""


def test_booking_from_payload_raises_contract_error_when_start_is_missing() -> None:
    with pytest.raises(ApiContractError, match="start"):
        SkeddaBooking.from_payload({"id": "1", "spaces": ["2000001"]})


def test_booking_from_payload_raises_contract_error_on_unparseable_datetime() -> None:
    payload = _load("bookings_list.json")["bookings"][0] | {"start": "yesterday"}
    with pytest.raises(ApiContractError, match="start"):
        SkeddaBooking.from_payload(payload)


def test_session_is_expired_compares_against_given_now() -> None:
    session = SkeddaSession(
        cookies={"x": "y"},
        antiforgery_token=None,
        expires_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert session.is_expired(datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC))
    assert not session.is_expired(datetime(2026, 1, 1, 11, 59, tzinfo=UTC))


def test_session_without_expiry_is_never_expired() -> None:
    session = SkeddaSession(cookies={"x": "y"}, antiforgery_token=None, expires_at=None)
    assert not session.is_expired(datetime(2030, 1, 1, tzinfo=UTC))


def test_booking_rejects_a_timezone_aware_start() -> None:
    """A Z-suffixed start would mean Skedda changed the wire format.

    Silently accepting it would shift every booking by the venue's UTC offset,
    so this is a contract violation, not something to normalise away.
    """
    payload = _load("bookings_list.json")["bookings"][0] | {"start": "2026-09-28T08:00:00Z"}
    with pytest.raises(ApiContractError, match="naive venue-local"):
        SkeddaBooking.from_payload(payload)


def test_credentials_repr_hides_the_password() -> None:
    """Credentials land in tracebacks and debug logs; the password must not."""
    creds = SkeddaCredentials(venue="myclub", email="user@example.com", password="hunter2")
    rendered = repr(creds)
    assert "hunter2" not in rendered
    assert "myclub" in rendered
    assert "user@example.com" in rendered

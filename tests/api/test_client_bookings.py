"""Booking operations, against the fixtures recorded from live traffic."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.client import SkeddaClient
from custom_components.skedda_scheduler.api.errors import (
    ApiContractError,
    AuthExpiredError,
    BookingWindowClosedError,
    SlotTakenError,
)
from custom_components.skedda_scheduler.api.models import SkeddaBookingRequest
from tests.conftest import FakeSkedda

from .test_client import authenticated, stub_login

FIXTURES = Path("tests/fixtures/skedda")
KYIV = ZoneInfo("Europe/Kyiv")

REQUEST = SkeddaBookingRequest(
    space_ids=("2000001",),
    start=datetime(2026, 9, 28, 8, 0, tzinfo=KYIV),
    end=datetime(2026, 9, 28, 9, 0, tzinfo=KYIV),
    title="Tennis",
)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


async def booking_client(http: aiohttp.ClientSession, skedda: FakeSkedda) -> SkeddaClient:
    """An authenticated client whose identity lookup is already satisfied."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    return client


async def test_list_spaces_reads_the_assets_key(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Skedda calls bookable spaces "assets" in /webs."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    spaces = await client.list_spaces()
    assert [s.name for s in spaces] == ["Court 1", "Court 2"]
    assert spaces[0].id == "2000001"


async def test_list_spaces_rejects_a_payload_without_assets(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json={"venue": []})
    with pytest.raises(ApiContractError, match="assets"):
        await client.list_spaces()


async def test_venue_settings_expose_the_timezone_and_the_rules(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """The booking window and quota drive scheduling, so they come from here."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    venue = await client.venue_settings()
    assert venue.timezone == "Europe/Kyiv"
    assert venue.slot_minutes == 60
    assert venue.max_days_ahead == 14
    assert venue.weekly_quota_minutes == 60


async def test_venue_settings_tolerate_a_venue_without_quota_rules(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Most venues have no quota; absence must not read as a zero allowance."""
    payload = load("webs.json")
    payload["venue"][0].pop("quotaRules")
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=payload)
    venue = await client.venue_settings()
    assert venue.weekly_quota_minutes is None


async def test_venue_settings_tolerate_a_venue_without_a_booking_window(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    payload = load("webs.json")
    payload["venue"][0].pop("bookingWindow")
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=payload)
    venue = await client.venue_settings()
    assert venue.max_days_ahead is None


async def test_create_booking_returns_the_created_booking(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await booking_client(http, skedda)
    skedda.stub("POST", endpoints.BOOKINGS.path, json=load("booking_created.json"))
    booking = await client.create_booking(REQUEST)
    assert booking.id == "300000001"
    assert booking.start == datetime(2026, 9, 28, 8, 0)


async def test_create_booking_accepts_a_bare_booking_object(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """The create response envelope was never captured, only inferred.

    Accepting both shapes costs one branch and removes a guess that would
    otherwise fail at the worst possible moment - the instant a window opens.
    """
    client = await booking_client(http, skedda)
    skedda.stub("POST", endpoints.BOOKINGS.path, json=load("booking_created.json")["booking"])
    booking = await client.create_booking(REQUEST)
    assert booking.id == "300000001"


async def test_create_booking_sends_naive_venue_local_times(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await booking_client(http, skedda)
    skedda.stub("POST", endpoints.BOOKINGS.path, json=load("booking_created.json"))
    await client.create_booking(REQUEST)
    sent = skedda.requests_for("POST", endpoints.BOOKINGS.path)[0]
    assert sent.json["booking"]["start"] == "2026-09-28T08:00:00"
    assert sent.json["booking"]["spaces"] == ["2000001"]


async def test_create_booking_surfaces_a_window_rejection(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await booking_client(http, skedda)
    skedda.stub("POST", endpoints.BOOKINGS.path, status=422, json=load("error_window.json"))
    with pytest.raises(BookingWindowClosedError, match="14 day"):
        await client.create_booking(REQUEST)


async def test_create_booking_rejects_an_unreadable_response(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await booking_client(http, skedda)
    skedda.stub("POST", endpoints.BOOKINGS.path, json={"nothing": "useful"})
    with pytest.raises(ApiContractError):
        await client.create_booking(REQUEST)


async def test_list_bookings_parses_the_recorded_fixture(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, json=load("bookings_list.json"))
    bookings = await client.list_bookings(
        datetime(2026, 9, 28, 0, 0, tzinfo=KYIV),
        datetime(2026, 9, 28, 23, 59, 59, 999000, tzinfo=KYIV),
    )
    assert [b.id for b in bookings] == ["300000001", "300000002"]


async def test_list_bookings_sends_the_window_the_way_skedda_does(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Naive venue-local, with the end stamped to the millisecond."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, json={"bookings": []})
    await client.list_bookings(
        datetime(2026, 9, 28, 0, 0, tzinfo=KYIV),
        datetime(2026, 9, 28, 23, 59, 59, 999000, tzinfo=KYIV),
    )
    sent = skedda.requests_for("GET", endpoints.BOOKINGS_LIST.path)[0]
    assert sent.query["start"] == "2026-09-28T00:00:00"
    assert sent.query["end"] == "2026-09-28T23:59:59.999"


async def test_list_bookings_rejects_a_payload_without_bookings(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, json={"venueusers": []})
    with pytest.raises(ApiContractError, match="bookings"):
        await client.list_bookings(
            datetime(2026, 9, 28, tzinfo=KYIV), datetime(2026, 9, 29, tzinfo=KYIV)
        )


async def test_cancel_booking_deletes_the_booking_path(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Confirmed 2026-09-15: DELETE /bookings/{id} answers 204, no body."""
    client = await authenticated(http, skedda)
    skedda.stub("DELETE", endpoints.booking_path("300000001"), status=204)
    await client.cancel_booking("300000001")
    assert skedda.requests_for("DELETE", endpoints.booking_path("300000001"))


async def test_venue_settings_reject_a_payload_without_a_venue(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json={"assets": []})
    with pytest.raises(ApiContractError, match="venue"):
        await client.venue_settings()


@pytest.mark.parametrize("body", ["[]", '"a string"'], ids=["list", "string"])
async def test_a_webs_payload_that_is_not_an_object_is_a_contract_error(
    http: aiohttp.ClientSession, skedda: FakeSkedda, body: str
) -> None:
    """A JSON array where an object belongs means the endpoint changed."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=json.loads(body))
    with pytest.raises(ApiContractError, match="not an object"):
        await client.list_spaces()


async def test_a_booking_response_that_is_not_an_object_is_a_contract_error(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await booking_client(http, skedda)
    skedda.stub("POST", endpoints.BOOKINGS.path, json=[])
    with pytest.raises(ApiContractError, match="not an object"):
        await client.create_booking(REQUEST)


async def test_create_booking_surfaces_a_slot_conflict(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """The one failure where falling back to a reserve space is worth trying."""
    client = await booking_client(http, skedda)
    skedda.stub("POST", endpoints.BOOKINGS.path, status=422, json=load("error_conflict.json"))
    with pytest.raises(SlotTakenError, match="conflicts with"):
        await client.create_booking(REQUEST)
    assert client.is_authenticated


async def test_identity_reads_the_venue_and_venueuser_ids(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """These go into every booking payload and the server does not infer them."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    identity = await client.identity()
    assert identity.venue_id == "100000"
    assert identity.venueuser_id == "900001"


async def test_identity_is_fetched_once_and_cached(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """It is fixed for the session, and the burst loop cannot afford a round trip."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    await client.identity()
    await client.identity()
    assert len(skedda.requests_for("GET", endpoints.SPACES.path)) == 1


async def test_identity_is_dropped_when_the_session_is(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """A different account could be signed in next; stale ids would misbook."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    await client.identity()
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, status=401, json={})
    with pytest.raises(AuthExpiredError):
        await client.list_bookings(
            datetime(2026, 9, 28, tzinfo=KYIV), datetime(2026, 9, 29, tzinfo=KYIV)
        )
    stub_login(skedda)
    await client.authenticate()
    await client.identity()
    assert len(skedda.requests_for("GET", endpoints.SPACES.path)) == 2


async def test_identity_rejects_a_payload_without_the_ids(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json={"web": {}})
    with pytest.raises(ApiContractError, match="venue"):
        await client.identity()


async def test_create_booking_fills_in_the_identity_itself(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Callers describe what to book; who we are is the client's business."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    skedda.stub("POST", endpoints.BOOKINGS.path, json=load("booking_created.json"))
    await client.create_booking(REQUEST)
    sent = skedda.requests_for("POST", endpoints.BOOKINGS.path)[0]
    assert sent.json["booking"]["venue"] == "100000"
    assert sent.json["booking"]["venueuser"] == "900001"


async def test_identity_rejects_a_payload_without_a_web_block(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Losing the block entirely is a different failure from losing a key."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json={"assets": []})
    with pytest.raises(ApiContractError, match="'web' block"):
        await client.identity()

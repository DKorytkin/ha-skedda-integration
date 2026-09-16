"""The adapter maps transport DTOs onto domain types and nothing more."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.client import SkeddaClient
from custom_components.skedda_scheduler.api.errors import SlotTakenError
from custom_components.skedda_scheduler.core.provider import (
    Booking,
    BookingRequest,
    DateRange,
    Space,
)
from custom_components.skedda_scheduler.skedda_provider import SkeddaProvider
from tests.api.test_client import CREDS, stub_login
from tests.conftest import FakeSkedda

FIXTURES = Path("tests/fixtures/skedda")
KYIV = ZoneInfo("Europe/Kyiv")

REQUEST = BookingRequest(
    space_id="2000001",
    start=datetime(2026, 9, 28, 8, 0, tzinfo=KYIV),
    end=datetime(2026, 9, 28, 9, 0, tzinfo=KYIV),
    title="Tennis",
)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
async def provider(http: aiohttp.ClientSession, skedda: FakeSkedda) -> SkeddaProvider:
    made = SkeddaProvider(SkeddaClient(http, CREDS))
    stub_login(skedda)
    await made.authenticate()
    skedda.stub("GET", endpoints.SPACES.path, json=load("webs.json"))
    return made


async def test_list_spaces_returns_domain_spaces_not_transport_dtos(
    provider: SkeddaProvider,
) -> None:
    spaces = await provider.list_spaces()
    assert spaces == [Space(id="2000001", name="Court 1"), Space(id="2000002", name="Court 2")]


async def test_book_maps_a_single_space_request_onto_the_transport_shape(
    provider: SkeddaProvider, skedda: FakeSkedda
) -> None:
    skedda.stub("POST", endpoints.BOOKINGS.path, json=load("booking_created.json"))
    booking = await provider.book(REQUEST)
    assert isinstance(booking, Booking)
    assert booking.id == "300000001"
    sent = skedda.requests_for("POST", endpoints.BOOKINGS.path)[0]
    assert sent.json["booking"]["spaces"] == ["2000001"]


async def test_book_lets_the_transport_error_taxonomy_through(
    provider: SkeddaProvider, skedda: FakeSkedda
) -> None:
    """The scheduler branches on these types; swallowing them would blind it."""
    skedda.stub("POST", endpoints.BOOKINGS.path, status=422, json=load("error_conflict.json"))
    with pytest.raises(SlotTakenError):
        await provider.book(REQUEST)


async def test_list_bookings_maps_a_window_onto_domain_bookings(
    provider: SkeddaProvider, skedda: FakeSkedda
) -> None:
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, json=load("bookings_list.json"))
    window = DateRange(
        start=datetime(2026, 9, 28, 0, 0, tzinfo=KYIV),
        end=datetime(2026, 9, 28, 23, 59, 59, tzinfo=KYIV),
    )
    bookings = await provider.list_bookings(window)
    assert [b.id for b in bookings] == ["300000001", "300000002"]
    assert all(isinstance(b, Booking) for b in bookings)


async def test_cancel_delegates_to_the_client(provider: SkeddaProvider, skedda: FakeSkedda) -> None:
    skedda.stub("DELETE", endpoints.booking_path("300000001"), status=204)
    await provider.cancel("300000001")
    assert skedda.requests_for("DELETE", endpoints.booking_path("300000001"))


async def test_provider_reports_authentication_state_from_the_client(
    http: aiohttp.ClientSession,
) -> None:
    assert SkeddaProvider(SkeddaClient(http, CREDS)).is_authenticated is False


async def test_provider_reports_authentication_state_after_signing_in(
    provider: SkeddaProvider,
) -> None:
    assert provider.is_authenticated is True


async def test_venue_settings_are_exposed_for_the_config_flow(
    provider: SkeddaProvider,
) -> None:
    """The config flow needs the quota and window to reject an impossible job."""
    venue = await provider.venue_settings()
    assert venue.timezone == "Europe/Kyiv"
    assert venue.weekly_quota_minutes == 60


async def test_the_underlying_client_is_reachable_for_diagnostics(
    provider: SkeddaProvider,
) -> None:
    """Diagnostics and the clock estimate read it; booking never should."""
    assert provider.client.clock.samples > 0


async def test_bookings_are_marked_as_ours_or_somebody_else_s(
    provider: SkeddaProvider, skedda: FakeSkedda
) -> None:
    """The venue returns its whole diary; only ours belong on our calendar."""
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, json=load("bookings_list.json"))

    bookings = await provider.list_bookings(
        DateRange(
            start=datetime(2026, 9, 28, tzinfo=KYIV),
            end=datetime(2026, 9, 30, tzinfo=KYIV),
        )
    )

    assert [booking.is_mine for booking in bookings] == [True, False]


async def test_booking_times_arrive_with_the_venue_s_zone_attached(
    provider: SkeddaProvider, skedda: FakeSkedda
) -> None:
    """Skedda writes a bare wall clock: no offset, no Z.

    Observed live 2026-09-16: the calendar refused to load the event, and the
    guard against booking a slot twice had never once matched, because a naive
    time never equals an aware one.
    """
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, json=load("bookings_list.json"))

    bookings = await provider.list_bookings(
        DateRange(
            start=datetime(2026, 9, 28, tzinfo=KYIV),
            end=datetime(2026, 9, 30, tzinfo=KYIV),
        )
    )

    assert bookings[0].start.tzinfo is not None
    assert bookings[0].start == datetime(2026, 9, 28, 8, 0, tzinfo=KYIV)
    assert bookings[0].end == datetime(2026, 9, 28, 9, 0, tzinfo=KYIV)


async def test_a_created_booking_comes_back_with_a_zone_too(
    provider: SkeddaProvider, skedda: FakeSkedda
) -> None:
    skedda.stub("POST", endpoints.BOOKINGS.path, json=load("booking_created.json"))

    booking = await provider.book(REQUEST)

    assert booking.start.tzinfo is not None
    assert booking.start.utcoffset() == datetime(2026, 9, 28, tzinfo=KYIV).utcoffset()

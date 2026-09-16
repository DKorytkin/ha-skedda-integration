"""The Google Calendar transport."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from custom_components.skedda_scheduler.api.errors import (
    ApiContractError,
    SkeddaAuthError,
    SkeddaConnectionError,
)
from custom_components.skedda_scheduler.api.google import (
    GoogleCalendar,
    GoogleCalendarClient,
)
from tests.conftest import FakeSkedda

KYIV = ZoneInfo("Europe/Kyiv")
START = datetime(2026, 9, 29, 20, 0, tzinfo=KYIV)
END = datetime(2026, 9, 29, 21, 0, tzinfo=KYIV)

CALENDAR_LIST = {
    "items": [
        {"id": "denys@example.com", "summary": "Denys", "accessRole": "owner"},
        {"id": "family@example.com", "summary": "Family", "accessRole": "writer"},
        {"id": "holidays@example.com", "summary": "Holidays", "accessRole": "reader"},
    ]
}
CREATED = {"id": "evt-1", "htmlLink": "https://calendar.google.com/evt-1"}


def client(
    http: aiohttp.ClientSession, google: FakeSkedda, token: str = "tok"
) -> GoogleCalendarClient:
    async def provide() -> str:
        return token

    return GoogleCalendarClient(http, provide, base_url=google.base_url)


async def test_only_calendars_we_may_write_to_are_offered(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    """A read-only calendar would refuse every event it was offered."""
    google.stub("GET", "/users/me/calendarList", json=CALENDAR_LIST)

    calendars = await client(http, google).list_calendars()

    assert calendars == [
        GoogleCalendar(id="denys@example.com", name="Denys"),
        GoogleCalendar(id="family@example.com", name="Family"),
    ]


async def test_an_event_carries_the_court_the_time_and_the_place(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("POST", "/calendars/denys@example.com/events", json=CREATED)

    event = await client(http, google).create_event(
        "denys@example.com",
        summary="Tennis 🎾",
        start=START,
        end=END,
        timezone="Europe/Kyiv",
        location="Kyiv, Some Street 12",
        description="Court 1",
    )

    assert event.id == "evt-1"
    sent = google.requests_for("POST", "/calendars/denys@example.com/events")[0].json
    assert sent["summary"] == "Tennis 🎾"
    assert sent["location"] == "Kyiv, Some Street 12"
    assert sent["start"] == {"dateTime": START.isoformat(), "timeZone": "Europe/Kyiv"}


async def test_attendees_are_invited_rather_than_merely_recorded(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    """The whole reason for going direct: Home Assistant cannot invite anyone."""
    google.stub("POST", "/calendars/c/events", json=CREATED)

    await client(http, google).create_event(
        "c",
        summary="Tennis 🎾",
        start=START,
        end=END,
        timezone="Europe/Kyiv",
        attendees=("oleh@example.com", "vika@example.com"),
    )

    request = google.requests_for("POST", "/calendars/c/events")[0]
    assert request.json["attendees"] == [
        {"email": "oleh@example.com"},
        {"email": "vika@example.com"},
    ]
    assert request.query["sendUpdates"] == "all", "created but silent is no use"


async def test_an_event_for_nobody_else_does_not_ask_google_to_email_anyone(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("POST", "/calendars/c/events", json=CREATED)

    await client(http, google).create_event(
        "c", summary="Tennis 🎾", start=START, end=END, timezone="Europe/Kyiv"
    )

    request = google.requests_for("POST", "/calendars/c/events")[0]
    assert "attendees" not in request.json
    assert "sendUpdates" not in request.query


async def test_the_token_travels_as_a_bearer(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("GET", "/users/me/calendarList", json=CALENDAR_LIST)

    await client(http, google, token="secret-token").list_calendars()

    sent = google.requests_for("GET", "/users/me/calendarList")[0]
    assert sent.headers["Authorization"] == "Bearer secret-token"


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_token_is_an_auth_error(
    http: aiohttp.ClientSession, google: FakeSkedda, status: int
) -> None:
    """Nothing else can be wrong here in a way a retry would fix."""
    google.stub(
        "GET",
        "/users/me/calendarList",
        status=status,
        json={"error": {"message": "Request had invalid authentication credentials."}},
    )

    with pytest.raises(SkeddaAuthError, match="invalid authentication"):
        await client(http, google).list_calendars()


async def test_googles_own_words_survive_a_refusal(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub(
        "POST",
        "/calendars/c/events",
        status=400,
        json={"error": {"message": "Invalid attendee email"}},
    )

    with pytest.raises(ApiContractError, match="Invalid attendee email"):
        await client(http, google).create_event(
            "c", summary="x", start=START, end=END, timezone="Europe/Kyiv"
        )


async def test_a_calendar_list_in_an_unknown_shape_is_a_contract_error(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("GET", "/users/me/calendarList", json={"nope": []})

    with pytest.raises(ApiContractError, match="items"):
        await client(http, google).list_calendars()


async def test_an_event_without_an_id_is_a_contract_error(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    """Without an id there is nothing to record and nothing to cancel."""
    google.stub("POST", "/calendars/c/events", json={"htmlLink": "x"})

    with pytest.raises(ApiContractError, match="no id"):
        await client(http, google).create_event(
            "c", summary="x", start=START, end=END, timezone="Europe/Kyiv"
        )


async def test_an_unreachable_google_is_a_connection_error(
    http: aiohttp.ClientSession,
) -> None:
    async def provide() -> str:
        return "tok"

    unreachable = GoogleCalendarClient(http, provide, base_url="http://127.0.0.1:1")

    with pytest.raises(SkeddaConnectionError):
        await unreachable.list_calendars()


async def test_a_non_json_answer_does_not_crash_the_client(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("GET", "/users/me/calendarList", text="<html>502</html>")

    with pytest.raises(ApiContractError):
        await client(http, google).list_calendars()


async def test_a_refusal_google_explains_badly_still_carries_a_status(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    """Not every 4xx arrives with a message to quote."""
    google.stub("POST", "/calendars/c/events", status=409, json={"error": "conflict"})

    with pytest.raises(ApiContractError, match="409"):
        await client(http, google).create_event(
            "c", summary="x", start=START, end=END, timezone="Europe/Kyiv"
        )

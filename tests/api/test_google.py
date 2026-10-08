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
    GoogleListedEvent,
    event_id_for,
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


async def test_a_colour_is_sent_as_googles_own_palette_number(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    """The API takes no names and no hex values, only its own numbers."""
    google.stub("POST", "/calendars/c/events", json=CREATED)

    await client(http, google).create_event(
        "c",
        summary="Tennis 🎾",
        start=START,
        end=END,
        timezone="Europe/Kyiv",
        color_id="9",
    )

    assert google.requests_for("POST", "/calendars/c/events")[0].json["colorId"] == "9"


async def test_an_event_without_a_colour_lets_the_calendar_decide(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("POST", "/calendars/c/events", json=CREATED)

    await client(http, google).create_event(
        "c", summary="Tennis 🎾", start=START, end=END, timezone="Europe/Kyiv"
    )

    assert "colorId" not in google.requests_for("POST", "/calendars/c/events")[0].json


def test_an_event_id_is_worked_out_from_the_booking() -> None:
    """Same calendar, court and instant - same id, whatever the zone says."""
    same = event_id_for("c", "court-1", START.astimezone(ZoneInfo("UTC")))

    assert event_id_for("c", "court-1", START) == same
    assert event_id_for("c", "court-2", START) != same
    assert set(same) <= set("0123456789abcdef"), "Google takes base32hex only"


async def test_an_event_can_be_given_its_id_up_front(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("POST", "/calendars/c/events", json={"id": "abc123"})

    event = await client(http, google).create_event(
        "c", summary="x", start=START, end=END, timezone="Europe/Kyiv", event_id="abc123"
    )

    assert event.id == "abc123"
    assert google.requests_for("POST", "/calendars/c/events")[0].json["id"] == "abc123"


async def test_an_id_already_taken_is_written_over_not_refused(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    """A retry whose first try landed, or a slot booked again after a cancel."""
    google.stub("POST", "/calendars/c/events", status=409, json={"error": {"message": "dup"}})
    google.stub("PUT", "/calendars/c/events/abc123", json={"id": "abc123"})

    event = await client(http, google).create_event(
        "c",
        summary="x",
        start=START,
        end=END,
        timezone="Europe/Kyiv",
        attendees=("oleh@example.com",),
        event_id="abc123",
    )

    assert event.id == "abc123"
    put = google.requests_for("PUT", "/calendars/c/events/abc123")[0]
    assert put.json["status"] == "confirmed"
    assert put.query["sendUpdates"] == "all"


async def test_a_refusal_other_than_a_taken_id_still_raises(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("POST", "/calendars/c/events", status=400, json={"error": {"message": "bad"}})

    with pytest.raises(ApiContractError, match="bad"):
        await client(http, google).create_event(
            "c", summary="x", start=START, end=END, timezone="Europe/Kyiv", event_id="abc123"
        )


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_google_that_is_struggling_is_worth_retrying(
    http: aiohttp.ClientSession, google: FakeSkedda, status: int
) -> None:
    google.stub("POST", "/calendars/c/events", status=status, json={})

    with pytest.raises(SkeddaConnectionError):
        await client(http, google).create_event(
            "c", summary="x", start=START, end=END, timezone="Europe/Kyiv"
        )


async def test_an_event_is_deleted_and_its_guests_told(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("DELETE", "/calendars/c/events/abc123", status=204)

    assert await client(http, google).delete_event("c", "abc123", notify=True)

    request = google.requests_for("DELETE", "/calendars/c/events/abc123")[0]
    assert request.query["sendUpdates"] == "all"


async def test_deleting_an_event_nobody_was_invited_to_tells_nobody(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("DELETE", "/calendars/c/events/abc123", status=204)

    await client(http, google).delete_event("c", "abc123", notify=False)

    assert "sendUpdates" not in google.requests_for("DELETE", "/calendars/c/events/abc123")[0].query


@pytest.mark.parametrize("status", [404, 410])
async def test_an_event_that_is_not_there_is_not_an_error(
    http: aiohttp.ClientSession, google: FakeSkedda, status: int
) -> None:
    google.stub("DELETE", "/calendars/c/events/abc123", status=status, json={})

    assert not await client(http, google).delete_event("c", "abc123", notify=True)


async def test_a_delete_google_refuses_raises(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("DELETE", "/calendars/c/events/abc123", status=400, json={})

    with pytest.raises(ApiContractError):
        await client(http, google).delete_event("c", "abc123", notify=True)


async def test_events_are_found_by_the_time_they_cover(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub(
        "GET",
        "/calendars/c/events",
        json={
            "items": [
                {
                    "id": "old-1",
                    "summary": "Tennis 🎾",
                    "description": "Skedda booking 7",
                    "start": {"dateTime": "2026-09-29T20:00:00+03:00"},
                },
                {"id": "all-day", "start": {"date": "2026-09-29"}},
                {"id": "odd", "start": {"dateTime": "not a time"}},
                "not an event",
            ]
        },
    )

    found = await client(http, google).events_between("c", START, END)

    assert found == [
        GoogleListedEvent(
            id="old-1", summary="Tennis 🎾", start=START, description="Skedda booking 7"
        ),
        GoogleListedEvent(id="all-day", summary="", start=None, description=""),
        GoogleListedEvent(id="odd", summary="", start=None, description=""),
    ]
    query = google.requests_for("GET", "/calendars/c/events")[0].query
    assert query["timeMin"] == START.isoformat()
    assert query["timeMax"] == END.isoformat()
    assert query["singleEvents"] == "true"


async def test_an_event_list_in_an_unknown_shape_is_a_contract_error(
    http: aiohttp.ClientSession, google: FakeSkedda
) -> None:
    google.stub("GET", "/calendars/c/events", json={"nothing": []})

    with pytest.raises(ApiContractError):
        await client(http, google).events_between("c", START, END)

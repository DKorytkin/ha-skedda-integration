"""Linking a Google calendar, and writing bookings into it."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError
from custom_components.skedda_scheduler.api.google import GoogleCalendar
from custom_components.skedda_scheduler.const import (
    CONF_ATTENDEES,
    CONF_CALENDAR_ID,
    CONF_ENTRY_KIND,
    CONF_EVENT_TITLE,
    CONF_LOCATION,
    DOMAIN,
    ENTRY_KIND_CALENDAR,
)
from custom_components.skedda_scheduler.sinks.google_calendar import GoogleCalendarSink
from tests.helpers import setup_with_job

KYIV = ZoneInfo("Europe/Kyiv")
CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
REDIRECT = "https://example.com/auth/external/callback"

CALENDARS = [
    GoogleCalendar(id="denys@example.com", name="Denys"),
    GoogleCalendar(id="family@example.com", name="Family"),
]

LIST_CALENDARS = (
    "custom_components.skedda_scheduler.flows.calendar.GoogleCalendarClient.list_calendars"
)


@pytest.fixture
async def credentials(hass: HomeAssistant) -> None:
    """The OAuth client the user creates in Google Cloud and gives to HA."""
    from homeassistant.components.application_credentials import (
        ClientCredential,
        async_import_client_credential,
    )

    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET))


def calendar_entry(**overrides: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Google Calendar",
        entry_id="entry-calendar",
        data={
            CONF_ENTRY_KIND: ENTRY_KIND_CALENDAR,
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "tok",
                "refresh_token": "refresh",
                "expires_at": 9999999999,
                "type": "Bearer",
            },
            CONF_CALENDAR_ID: "denys@example.com",
            CONF_EVENT_TITLE: "Tennis 🎾",
            CONF_LOCATION: "Kyiv, Some Street 12",
            CONF_ATTENDEES: ["oleh@example.com"],
            **overrides,
        },
    )


async def test_the_first_screen_offers_every_kind_of_entry(
    hass: HomeAssistant, credentials: None
) -> None:
    """One integration, two things to add: an account, and the calendar."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"account", "calendar", "watch"}


async def test_linking_a_calendar_asks_google_then_asks_which_one(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    current_request_with_host: Any,
    credentials: None,
) -> None:
    """The whole point of going direct: an event that can invite people."""
    from homeassistant.helpers import config_entry_oauth2_flow

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "calendar"}
    )

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert "calendar.events" in result["url"], "creating events is the point"
    assert "access_type=offline" in result["url"], "without it the link dies in an hour"

    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": result["flow_id"], "redirect_uri": REDIRECT}
    )
    client = await hass_client_no_auth()
    response = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert response.status == 200
    aioclient_mock.post(
        "https://oauth2.googleapis.com/token",
        json={
            "refresh_token": "refresh",
            "access_token": "tok",
            "type": "Bearer",
            "expires_in": 3600,
        },
    )

    with patch(LIST_CALENDARS, return_value=CALENDARS):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "calendar_settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CALENDAR_ID: "family@example.com",
            CONF_EVENT_TITLE: "Tennis 🎾",
            CONF_LOCATION: "Kyiv, Some Street 12",
            CONF_ATTENDEES: ["oleh@example.com"],
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTRY_KIND] == ENTRY_KIND_CALENDAR
    assert result["data"][CONF_CALENDAR_ID] == "family@example.com"
    assert result["data"]["token"]["refresh_token"] == "refresh"


async def test_a_booking_that_landed_becomes_an_event_with_its_guests(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    from custom_components.skedda_scheduler.core.result import (
        AttemptStatus,
        BookingAttempt,
        BookingOutcome,
    )
    from tests.test_sinks import JOB

    start = datetime(2026, 9, 29, 20, 0, tzinfo=KYIV)
    outcome = BookingOutcome(
        job_id="job-1",
        succeeded=True,
        booking_id="bk-1",
        space_id="2000001",
        slot_start=start,
        slot_end=start + timedelta(hours=1),
        attempts=(BookingAttempt(1, start, AttemptStatus.SUCCESS, 9.0),),
        finished_at=start,
    )
    client = AsyncMock()
    sink = GoogleCalendarSink(
        hass,
        client,
        calendar_id="denys@example.com",
        title="Tennis 🎾",
        location="Kyiv, Some Street 12",
        attendees=("oleh@example.com",),
    )

    await sink.async_handle(outcome, JOB)

    client.create_event.assert_awaited_once()
    call = client.create_event.await_args
    assert call.args[0] == "denys@example.com"
    assert call.kwargs["summary"] == "Tennis 🎾"
    assert call.kwargs["location"] == "Kyiv, Some Street 12"
    assert call.kwargs["attendees"] == ("oleh@example.com",)
    assert call.kwargs["timezone"] == "Europe/Kyiv"
    assert "bk-1" in call.kwargs["description"]


async def test_a_run_that_booked_nothing_writes_no_event(
    hass: HomeAssistant,
) -> None:
    """An event for a booking that does not exist is worse than none."""
    from tests.test_sinks import JOB, outcome

    client = AsyncMock()
    sink = GoogleCalendarSink(
        hass, client, calendar_id="c", title="Tennis 🎾", location=None, attendees=()
    )

    await sink.async_handle(outcome(succeeded=False), JOB)

    client.create_event.assert_not_awaited()


async def test_losing_the_calendar_entry_does_not_look_like_losing_the_court(
    hass: HomeAssistant, caplog: Any
) -> None:
    from tests.test_sinks import JOB, outcome

    client = AsyncMock()
    client.create_event.side_effect = SkeddaConnectionError("google is down")
    sink = GoogleCalendarSink(
        hass, client, calendar_id="c", title="Tennis 🎾", location=None, attendees=()
    )

    await sink.async_handle(outcome(succeeded=True), JOB)

    assert "could not add it to the calendar" in caplog.text


async def test_a_job_writes_to_the_calendar_when_one_is_linked(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The job does not know about Google; it just has one more sink."""
    from custom_components.skedda_scheduler.scheduler import async_build_job_sinks

    linked = calendar_entry()
    linked.add_to_hass(hass)
    with patch(
        "custom_components.skedda_scheduler.google_calendar.async_build_client",
        return_value=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(linked.entry_id)
        await hass.async_block_till_done()
        sinks = await async_build_job_sinks(hass)

    assert any(isinstance(sink, GoogleCalendarSink) for sink in sinks)


async def test_no_calendar_linked_means_no_calendar_sink(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """ "If there is none, nothing happens" - and nothing has to be told so."""
    from custom_components.skedda_scheduler.scheduler import async_build_job_sinks

    await setup_with_job(hass, mock_entry)

    sinks = await async_build_job_sinks(hass)

    assert not any(isinstance(sink, GoogleCalendarSink) for sink in sinks)


async def test_a_calendar_link_that_google_no_longer_honours_asks_for_reauth(
    hass: HomeAssistant, credentials: None
) -> None:
    """A dead link is the user's to fix; retrying forever would not."""
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.skedda_scheduler.api.errors import SkeddaAuthError

    linked = calendar_entry()
    linked.add_to_hass(hass)
    with patch(
        "custom_components.skedda_scheduler.google_calendar.async_build_client",
        side_effect=SkeddaAuthError("token revoked"),
    ):
        await hass.config_entries.async_setup(linked.entry_id)
        await hass.async_block_till_done()

    assert linked.state is ConfigEntryState.SETUP_ERROR


async def test_a_google_outage_leaves_the_link_to_retry(
    hass: HomeAssistant, credentials: None
) -> None:
    from homeassistant.config_entries import ConfigEntryState

    linked = calendar_entry()
    linked.add_to_hass(hass)
    with patch(
        "custom_components.skedda_scheduler.google_calendar.async_build_client",
        side_effect=SkeddaConnectionError("down"),
    ):
        await hass.config_entries.async_setup(linked.entry_id)
        await hass.async_block_till_done()

    assert linked.state is ConfigEntryState.SETUP_RETRY


async def test_linking_a_calendar_reloads_the_accounts_already_running(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """An account set up first would otherwise write nothing until it reloaded."""
    await setup_with_job(hass, mock_entry)
    linked = calendar_entry()
    linked.add_to_hass(hass)

    with patch(
        "custom_components.skedda_scheduler.google_calendar.async_build_client",
        return_value=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(linked.entry_id)
        await hass.async_block_till_done()
        # The reload is scheduled, so it lands after setup returns.
        await hass.async_block_till_done()
        sinks = mock_entry.runtime_data.scheduler.runners["sub-1"].sinks

    assert any(isinstance(sink, GoogleCalendarSink) for sink in sinks)


async def test_removing_the_calendar_link_leaves_no_history_behind(
    hass: HomeAssistant, credentials: None
) -> None:
    """It never had any: the store belongs to an account, not to a calendar."""
    from custom_components.skedda_scheduler import async_remove_entry

    linked = calendar_entry()
    linked.add_to_hass(hass)

    await async_remove_entry(hass, linked)


async def test_a_venue_we_cannot_list_calendars_for_says_so_rather_than_showing_none(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    current_request_with_host: Any,
    credentials: None,
) -> None:
    """An empty dropdown is a dead end with no explanation."""
    from homeassistant.helpers import config_entry_oauth2_flow

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "calendar"}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": result["flow_id"], "redirect_uri": REDIRECT}
    )
    client = await hass_client_no_auth()
    await client.get(f"/auth/external/callback?code=abcd&state={state}")
    aioclient_mock.post(
        "https://oauth2.googleapis.com/token",
        json={"refresh_token": "r", "access_token": "t", "type": "Bearer", "expires_in": 60},
    )

    with patch(LIST_CALENDARS, side_effect=SkeddaConnectionError("down")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "calendar_list_failed"


async def test_an_account_with_no_writable_calendar_is_told_so(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    current_request_with_host: Any,
    credentials: None,
) -> None:
    from homeassistant.helpers import config_entry_oauth2_flow

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "calendar"}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": result["flow_id"], "redirect_uri": REDIRECT}
    )
    client = await hass_client_no_auth()
    await client.get(f"/auth/external/callback?code=abcd&state={state}")
    aioclient_mock.post(
        "https://oauth2.googleapis.com/token",
        json={"refresh_token": "r", "access_token": "t", "type": "Bearer", "expires_in": 60},
    )

    with patch(LIST_CALENDARS, return_value=[]):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["reason"] == "no_writable_calendar"


async def test_the_credentials_dialog_points_at_the_pages_that_matter(
    hass: HomeAssistant,
) -> None:
    """Creating an OAuth client is the one part nobody can automate."""
    from custom_components.skedda_scheduler.application_credentials import (
        async_get_authorization_server,
        async_get_description_placeholders,
    )

    server = await async_get_authorization_server(hass)
    placeholders = await async_get_description_placeholders(hass)

    assert "accounts.google.com" in server.authorize_url
    assert "oauth2.googleapis.com" in server.token_url
    assert "credentials" in placeholders["oauth_creds_url"]
    assert "calendar" in placeholders["api_library_url"], "the API has to be enabled too"
    assert placeholders["redirect_url"].startswith("https://")


async def test_the_credentials_dialog_names_the_redirect_the_client_needs(
    hass: HomeAssistant,
) -> None:
    """Google refuses the sign-in unless this exact URI is on the client."""
    from homeassistant.helpers import config_entry_oauth2_flow

    from custom_components.skedda_scheduler.application_credentials import (
        async_get_description_placeholders,
    )

    hass.config.components.add("my")
    placeholders = await async_get_description_placeholders(hass)

    assert placeholders["redirect_url"] == config_entry_oauth2_flow.MY_AUTH_CALLBACK_PATH


async def test_every_link_the_dialog_mentions_is_supplied(hass: HomeAssistant) -> None:
    """A placeholder with nothing behind it renders as a broken instruction."""
    import json
    from pathlib import Path
    from string import Formatter

    from custom_components.skedda_scheduler.application_credentials import (
        async_get_description_placeholders,
    )

    path = Path("custom_components/skedda_scheduler/strings.json")
    strings = json.loads(await hass.async_add_executor_job(path.read_text, "utf-8"))
    description = strings["application_credentials"]["description"]
    wanted = {name for _, name, _, _ in Formatter().parse(description) if name}

    assert wanted <= set(await async_get_description_placeholders(hass))


async def test_the_token_is_refreshed_before_every_use(
    hass: HomeAssistant, credentials: None, aioclient_mock: Any
) -> None:
    """An hour-old link has to keep working without anybody noticing."""
    stale = calendar_entry()
    linked = MockConfigEntry(
        domain=DOMAIN,
        title=stale.title,
        entry_id=stale.entry_id,
        data={**stale.data, "token": {**stale.data["token"], "expires_at": 0}},
    )
    linked.add_to_hass(hass)
    aioclient_mock.post(
        "https://oauth2.googleapis.com/token",
        json={"access_token": "fresh", "refresh_token": "r", "expires_in": 3600},
    )

    from custom_components.skedda_scheduler.google_calendar import async_build_client

    client = await async_build_client(hass, linked)
    token = await client._token()

    assert token == "fresh"


async def test_a_calendar_that_cannot_be_reached_does_not_stop_the_booking(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Without the credential the link is unusable; the court is not."""
    from custom_components.skedda_scheduler.google_calendar import async_build_sink

    linked = calendar_entry()
    linked.add_to_hass(hass)

    assert await async_build_sink(hass) is None


async def test_the_calendar_list_is_fetched_with_the_token_just_issued(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: Any,
    current_request_with_host: Any,
    credentials: None,
) -> None:
    """There is no config entry yet to read a token from, only the reply."""
    from homeassistant.helpers import config_entry_oauth2_flow

    seen: list[str] = []

    class Recording:
        def __init__(self, _session: Any, token: Any, **_kwargs: Any) -> None:
            self._token = token

        async def list_calendars(self) -> list[GoogleCalendar]:
            seen.append(await self._token())
            return CALENDARS

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "calendar"}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": result["flow_id"], "redirect_uri": REDIRECT}
    )
    client = await hass_client_no_auth()
    await client.get(f"/auth/external/callback?code=abcd&state={state}")
    aioclient_mock.post(
        "https://oauth2.googleapis.com/token",
        json={
            "refresh_token": "r",
            "access_token": "brand-new",
            "type": "Bearer",
            "expires_in": 60,
        },
    )

    with patch("custom_components.skedda_scheduler.flows.calendar.GoogleCalendarClient", Recording):
        await hass.config_entries.flow.async_configure(result["flow_id"])

    assert seen == ["brand-new"]
    # Home Assistant owns this session; closing it would take every other
    # integration's requests down with it.
    assert not async_get_clientsession(hass).closed


async def test_the_calendar_settings_can_be_changed_afterwards(
    hass: HomeAssistant, credentials: None
) -> None:
    """Who comes to tennis changes more often than the Google account does."""
    linked = calendar_entry()
    linked.add_to_hass(hass)

    with patch(LIST_CALENDARS, return_value=CALENDARS):
        result = await linked.start_reconfigure_flow(hass)

        assert result["step_id"] == "calendar_settings"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_CALENDAR_ID: "family@example.com",
                CONF_EVENT_TITLE: "Теніс 🎾",
                CONF_LOCATION: "Kyiv",
                CONF_ATTENDEES: ["vika@example.com"],
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert linked.data[CONF_CALENDAR_ID] == "family@example.com"
    assert linked.data[CONF_ATTENDEES] == ["vika@example.com"]
    # The token is not re-issued by an edit.
    assert linked.data["token"]["refresh_token"] == "refresh"


async def test_editing_a_link_made_days_ago_refreshes_the_token_first(
    hass: HomeAssistant, credentials: None, aioclient_mock: Any
) -> None:
    """The stored access token expired within an hour of being issued."""
    stale = calendar_entry()
    linked = MockConfigEntry(
        domain=DOMAIN,
        title=stale.title,
        entry_id=stale.entry_id,
        data={**stale.data, "token": {**stale.data["token"], "expires_at": 0}},
    )
    linked.add_to_hass(hass)
    aioclient_mock.post(
        "https://oauth2.googleapis.com/token",
        json={
            "refresh_token": "r",
            "access_token": "renewed",
            "type": "Bearer",
            "expires_in": 3600,
        },
    )
    seen: list[str] = []

    class Recording:
        def __init__(self, _session: Any, token: Any, **_kwargs: Any) -> None:
            self._token = token

        async def list_calendars(self) -> list[GoogleCalendar]:
            seen.append(await self._token())
            return CALENDARS

    with patch(
        "custom_components.skedda_scheduler.google_calendar.GoogleCalendarClient", Recording
    ):
        result = await linked.start_reconfigure_flow(hass)

    assert result["step_id"] == "calendar_settings"
    assert seen == ["renewed"]

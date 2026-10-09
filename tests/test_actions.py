"""The panel's own actions: take a free neighbour, move ours, run a watch now."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.actions import PanelSubject
from custom_components.skedda_scheduler.api.errors import SlotTakenError
from custom_components.skedda_scheduler.core.provider import Booking
from tests.conftest import VENUE_RULES
from tests.helpers import setup_account, watch_entry_with_rule

KYIV = ZoneInfo("Europe/Kyiv")
ADD_EVENT = "custom_components.skedda_scheduler.google_calendar.async_add_event"
RELEASE_EVENT = "custom_components.skedda_scheduler.google_calendar.async_release_event"


def target(command: str) -> dict[str, str]:
    """What names the slot or the booking, per command."""
    return {"space_id": "2000001"} if command == "take_slot" else {"booking_id": "ours-19"}


def evening(hour: int, days_ahead: int = 3) -> datetime:
    return (dt_util.utcnow().astimezone(KYIV) + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def ours(hour: int, *, booking_id: str = "ours-19") -> Booking:
    return Booking(
        id=booking_id,
        space_ids=("2000001",),
        start=evening(hour),
        end=evening(hour) + timedelta(hours=1),
        title="",
        is_mine=True,
    )


async def send(client: Any, **message: Any) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    response: dict[str, Any] = await client.receive_json()
    return response


async def overview_offers(client: Any) -> list[dict[str, Any]]:
    response = await send(client, type="skedda_scheduler/overview")
    assert response["success"], response
    offers: list[dict[str, Any]] = response["result"]["offers"]
    return offers


@pytest.fixture
async def one_booking(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> Booking:
    """Our Tuesday 19:00, the request's own example."""
    held = ours(19)
    mock_provider.list_bookings.return_value = [held]
    await setup_account(hass, mock_entry)
    return held


async def test_a_lone_booking_shows_its_free_neighbours_to_move_onto(
    hass: HomeAssistant, hass_ws_client: Any, mock_entry: MockConfigEntry, one_booking: Booking
) -> None:
    """The one account has spent its hour, so the offer is to swap."""
    offers = await overview_offers(await hass_ws_client(hass))

    assert [(o["kind"], o["start"]) for o in offers] == [
        ("move", evening(18).isoformat()),
        ("move", evening(20).isoformat()),
    ]
    assert all(o["booking_id"] == "ours-19" for o in offers)
    assert all(o["entry_id"] == mock_entry.entry_id for o in offers)
    assert offers[0]["account"] == mock_entry.title


async def test_an_account_with_an_hour_left_is_offered_to_take_it(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    mock_provider.venue_settings.return_value = replace(VENUE_RULES, weekly_quota_minutes=None)
    mock_provider.list_bookings.return_value = [ours(19)]
    await setup_account(hass, mock_entry)

    offers = await overview_offers(await hass_ws_client(hass))

    assert {o["kind"] for o in offers} == {"take"}


async def test_an_account_not_yet_polled_offers_nothing(
    hass: HomeAssistant, mock_entry: MockConfigEntry, one_booking: Booking
) -> None:
    from custom_components.skedda_scheduler import actions

    mock_entry.runtime_data.coordinator.data = None

    assert actions.offers(hass) == []


async def test_taking_a_slot_books_it_and_writes_the_calendar(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
    one_booking: Booking,
) -> None:
    taken = Booking(
        id="new-20",
        space_ids=("2000001",),
        start=evening(20),
        end=evening(21),
        title="",
        is_mine=True,
    )
    mock_provider.book.return_value = taken
    client = await hass_ws_client(hass)

    with patch(ADD_EVENT) as add_event:
        response = await send(
            client,
            type="skedda_scheduler/take_slot",
            entry_id=mock_entry.entry_id,
            space_id="2000001",
            start=evening(20).isoformat(),
            end=evening(21).isoformat(),
        )

    assert response["success"], response
    assert response["result"] == {"booking_id": "new-20"}
    request = mock_provider.book.await_args.args[0]
    assert (request.space_id, request.start, request.end) == ("2000001", evening(20), evening(21))
    outcome = add_event.await_args.args[1]
    assert (outcome.booking_id, outcome.slot_start, outcome.account) == (
        "new-20",
        evening(20),
        mock_entry.title,
    )


async def test_a_slot_somebody_was_quicker_to_is_reported_not_raised(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
    one_booking: Booking,
) -> None:
    mock_provider.book.side_effect = SlotTakenError("conflicts with one already scheduled")
    client = await hass_ws_client(hass)

    with patch(ADD_EVENT) as add_event:
        response = await send(
            client,
            type="skedda_scheduler/take_slot",
            entry_id=mock_entry.entry_id,
            space_id="2000001",
            start=evening(20).isoformat(),
            end=evening(21).isoformat(),
        )

    assert response["error"]["code"] == "take_failed"
    assert "conflicts" in response["error"]["message"]
    add_event.assert_not_awaited()


async def test_moving_a_booking_gives_the_old_hour_up_and_moves_its_event(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
    one_booking: Booking,
) -> None:
    """The hour left behind must not be caught back by a watch."""
    mock_provider.move.return_value = replace(one_booking, start=evening(20), end=evening(21))
    client = await hass_ws_client(hass)

    with patch(ADD_EVENT) as add_event, patch(RELEASE_EVENT) as release_event:
        response = await send(
            client,
            type="skedda_scheduler/move_booking",
            entry_id=mock_entry.entry_id,
            booking_id="ours-19",
            start=evening(20).isoformat(),
            end=evening(21).isoformat(),
        )

    assert response["success"], response
    assert response["result"] == {"booking_id": "ours-19", "start": evening(20).isoformat()}
    assert mock_provider.move.await_args.args == (one_booking, evening(20), evening(21))
    assert ("2000001", evening(19), evening(20)) in (
        mock_entry.runtime_data.store.released_intervals()
    )
    release_event.assert_awaited_once_with(hass, "2000001", evening(19), evening(20), "ours-19")
    assert add_event.await_args.args[1].slot_start == evening(20)


async def test_moving_a_booking_this_account_no_longer_holds_says_so(
    hass: HomeAssistant, hass_ws_client: Any, mock_entry: MockConfigEntry, one_booking: Booking
) -> None:
    response = await send(
        await hass_ws_client(hass),
        type="skedda_scheduler/move_booking",
        entry_id=mock_entry.entry_id,
        booking_id="gone",
        start=evening(20).isoformat(),
        end=evening(21).isoformat(),
    )

    assert response["error"]["code"] == "move_failed"


async def test_a_move_the_venue_refuses_leaves_everything_as_it_was(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
    one_booking: Booking,
) -> None:
    mock_provider.move.side_effect = SlotTakenError("conflicts with one already scheduled")

    with patch(RELEASE_EVENT) as release_event:
        response = await send(
            await hass_ws_client(hass),
            type="skedda_scheduler/move_booking",
            entry_id=mock_entry.entry_id,
            booking_id="ours-19",
            start=evening(20).isoformat(),
            end=evening(21).isoformat(),
        )

    assert response["error"]["code"] == "move_failed"
    assert mock_entry.runtime_data.store.released_intervals() == set()
    release_event.assert_not_awaited()


@pytest.mark.parametrize("command", ["take_slot", "move_booking"])
async def test_an_account_that_is_not_set_up_is_refused(
    hass: HomeAssistant, hass_ws_client: Any, one_booking: Booking, command: str
) -> None:
    response = await send(
        await hass_ws_client(hass),
        type=f"skedda_scheduler/{command}",
        entry_id="nope",
        **target(command),
        start=evening(20).isoformat(),
        end=evening(21).isoformat(),
    )

    assert response["error"]["code"] == "not_loaded"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("not a time", "2026-10-13T21:00:00+03:00"),
        ("2026-10-13T20:00:00", "2026-10-13T21:00:00"),
        ("2026-10-13T21:00:00+03:00", "2026-10-13T20:00:00+03:00"),
    ],
    ids=["garbage", "no-zone", "backwards"],
)
@pytest.mark.parametrize("command", ["take_slot", "move_booking"])
async def test_a_time_that_is_not_a_slot_is_refused(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    one_booking: Booking,
    command: str,
    start: str,
    end: str,
) -> None:
    response = await send(
        await hass_ws_client(hass),
        type=f"skedda_scheduler/{command}",
        entry_id=mock_entry.entry_id,
        **target(command),
        start=start,
        end=end,
    )

    assert response["error"]["code"] == "invalid_time"


async def test_running_a_watch_rule_now_reads_the_venue_and_takes_what_it_finds(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    rule_id = next(iter(watch.subentries))
    polls = mock_provider.list_bookings.await_count

    response = await send(
        await hass_ws_client(hass),
        type="skedda_scheduler/run_watch_rule",
        entry_id=watch.entry_id,
        rule_id=rule_id,
    )

    assert response["success"], response
    assert response["result"]["caught"] is not None
    assert mock_provider.list_bookings.await_count > polls


async def test_running_a_watch_rule_with_nothing_free_says_so(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, book=False, enabled=False)
    rule_id = next(iter(watch.subentries))

    response = await send(
        await hass_ws_client(hass),
        type="skedda_scheduler/run_watch_rule",
        entry_id=watch.entry_id,
        rule_id=rule_id,
    )

    assert response["result"] == {"caught": None}


async def test_running_only_the_rule_asked_for(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Another rule's slot is not taken because this rule's button was pressed."""
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, book=False)

    caught = await watch.runtime_data.watcher.async_refresh_and_scan("some-other-rule")

    assert caught is None


async def test_running_a_rule_that_does_not_exist_is_refused(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    response = await send(
        await hass_ws_client(hass),
        type="skedda_scheduler/run_watch_rule",
        entry_id=watch.entry_id,
        rule_id="nope",
    )

    assert response["error"]["code"] == "unknown_rule"


async def test_running_a_rule_on_something_that_is_not_the_watch_is_refused(
    hass: HomeAssistant, hass_ws_client: Any, mock_entry: MockConfigEntry, one_booking: Booking
) -> None:
    response = await send(
        await hass_ws_client(hass),
        type="skedda_scheduler/run_watch_rule",
        entry_id=mock_entry.entry_id,
        rule_id="anything",
    )

    assert response["error"]["code"] == "not_loaded"


async def test_a_venue_that_cannot_be_read_is_reported_when_running_a_rule(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    rule_id = next(iter(watch.subentries))

    with patch.object(
        watch.runtime_data.watcher,
        "async_refresh_and_scan",
        side_effect=SlotTakenError("venue said no"),
    ):
        response = await send(
            await hass_ws_client(hass),
            type="skedda_scheduler/run_watch_rule",
            entry_id=watch.entry_id,
            rule_id=rule_id,
        )

    assert response["error"]["code"] == "run_failed"


def test_the_panel_reports_in_the_venues_zone() -> None:
    assert PanelSubject("Europe/Kyiv").tz == KYIV

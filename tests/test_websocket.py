"""The snapshot the panel reads."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.core.provider import Booking
from tests.helpers import setup_with_job
from tests.test_google_calendar import calendar_entry

KYIV = ZoneInfo("Europe/Kyiv")


async def overview(client: Any) -> dict[str, Any]:
    await client.send_json_auto_id({"type": "skedda_scheduler/overview"})
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


async def test_the_overview_lists_accounts_with_their_state(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    await setup_with_job(hass, mock_entry)
    client = await hass_ws_client(hass)

    result = await overview(client)

    assert len(result["accounts"]) == 1
    account = result["accounts"][0]
    assert account["title"] == "Main account (Oleh)"
    assert account["venue"] == "myclub"
    assert account["authenticated"] is True
    assert account["entry_id"] == mock_entry.entry_id


async def test_an_account_that_cannot_sign_in_says_so(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Green or red is the whole point of the accounts table."""
    from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError

    await setup_with_job(hass, mock_entry)
    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")
    await mock_entry.runtime_data.coordinator.async_refresh()
    client = await hass_ws_client(hass)

    account = (await overview(client))["accounts"][0]

    assert account["authenticated"] is False


async def test_jobs_carry_the_account_that_will_book_them(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """With several accounts, whose hour is being spent is the first question."""
    subentry_id = await setup_with_job(hass, mock_entry)
    client = await hass_ws_client(hass)

    jobs = (await overview(client))["jobs"]

    assert len(jobs) == 1
    assert jobs[0]["job_id"] == subentry_id
    assert jobs[0]["account"] == "Main account (Oleh)"
    assert jobs[0]["court"] == "Court 1"
    assert jobs[0]["status"] in {"armed", "out_of_season", "disabled"}
    assert jobs[0]["next_slot"] is not None


async def test_bookings_are_ours_only_and_sorted_by_when_they_start(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    now = dt_util.utcnow().astimezone(KYIV)

    def booking(days: int, identifier: str, *, mine: bool = True) -> Booking:
        start = now + timedelta(days=days)
        return Booking(
            id=identifier,
            space_ids=("2000001",),
            start=start,
            end=start + timedelta(hours=1),
            title="Tennis",
            is_mine=mine,
        )

    mock_provider.list_bookings.return_value = [
        booking(5, "later"),
        booking(1, "sooner"),
        booking(2, "theirs", mine=False),
    ]
    await setup_with_job(hass, mock_entry)
    client = await hass_ws_client(hass)

    bookings = (await overview(client))["bookings"]

    assert [item["booking_id"] for item in bookings] == ["sooner", "later"]
    assert bookings[0]["court"] == "Court 1"
    assert bookings[0]["account"] == "Main account (Oleh)"


async def test_an_account_that_never_loaded_does_not_break_the_overview(
    hass: HomeAssistant, hass_ws_client: Any, mock_entry: MockConfigEntry
) -> None:
    """A panel that goes blank when one account is down is worse than useless."""
    from homeassistant.setup import async_setup_component

    from custom_components.skedda_scheduler.const import DOMAIN

    mock_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)

    result = await overview(client)

    # Listed, but plainly not working: a missing row would look like a
    # deleted account rather than a broken one.
    assert [account["authenticated"] for account in result["accounts"]] == [False]
    assert result["jobs"] == []
    assert result["bookings"] == []


async def test_a_disabled_job_and_a_broken_one_are_both_reported(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """A job the panel cannot describe must not take the panel down with it."""
    from homeassistant.config_entries import ConfigSubentry

    from custom_components.skedda_scheduler.const import SUBENTRY_TYPE_JOB
    from tests.helpers import JOB_DATA, add_job_subentry

    mock_entry.add_to_hass(hass)
    add_job_subentry(hass, mock_entry, "sub-off", enabled=False)
    hass.config_entries.async_add_subentry(
        mock_entry,
        ConfigSubentry(
            data={**JOB_DATA, "start_date": "not-a-date"},
            subentry_id="sub-broken",
            subentry_type=SUBENTRY_TYPE_JOB,
            title="Broken",
            unique_id=None,
        ),
    )
    hass.config_entries.async_add_subentry(
        mock_entry,
        ConfigSubentry(
            data={},
            subentry_id="sub-other",
            subentry_type="something_else",
            title="Not a job",
            unique_id=None,
        ),
    )
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    jobs = (await overview(client))["jobs"]

    assert [job["job_id"] for job in jobs] == ["sub-off"]
    assert jobs[0]["status"] == "disabled"
    assert jobs[0]["enabled"] is False


async def test_a_job_whose_season_has_ended_reads_as_out_of_season(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Not disabled and not armed: shut for the winter is its own answer."""
    await setup_with_job(hass, mock_entry, season_end="2026-09-02")
    client = await hass_ws_client(hass)

    jobs = (await overview(client))["jobs"]

    assert jobs[0]["status"] == "out_of_season"
    assert jobs[0]["armed_for"] is None


async def test_a_job_reports_the_slot_it_is_actually_aiming_at(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Reported 2026-09-16: a window shown that had already opened.

    After catching up, a runner aims at the next slot whose window has yet to
    open. Reading the nearest occurrence instead described a target the runner
    had already passed.
    """
    subentry_id = await setup_with_job(hass, mock_entry)
    runner = mock_entry.runtime_data.scheduler.runner_for(subentry_id)
    client = await hass_ws_client(hass)

    job = (await overview(client))["jobs"][0]

    assert job["next_slot"] == runner.armed_slot.isoformat()
    assert job["armed_for"] == runner.armed_for.isoformat()
    assert job["opens_at"] == runner.job.window.opens_at(runner.armed_slot).isoformat()


async def test_a_booking_can_be_cancelled_from_the_panel(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Skedda has no form of ours to borrow for this, so it is ours to do."""
    await setup_with_job(hass, mock_entry)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "skedda_scheduler/cancel_booking",
            "entry_id": mock_entry.entry_id,
            "booking_id": "bk-1",
        }
    )
    response = await client.receive_json()

    assert response["success"], response
    mock_provider.cancel.assert_awaited_once_with("bk-1")


async def test_cancelling_refreshes_so_the_panel_stops_showing_it(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    await setup_with_job(hass, mock_entry)
    mock_provider.list_bookings.reset_mock()
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "skedda_scheduler/cancel_booking",
            "entry_id": mock_entry.entry_id,
            "booking_id": "bk-1",
        }
    )
    await client.receive_json()

    mock_provider.list_bookings.assert_awaited()


async def test_a_cancellation_the_venue_refuses_is_reported_not_swallowed(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Silence would leave the booking on screen and the user guessing."""
    from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError

    await setup_with_job(hass, mock_entry)
    mock_provider.cancel.side_effect = SkeddaConnectionError("down")
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "skedda_scheduler/cancel_booking",
            "entry_id": mock_entry.entry_id,
            "booking_id": "bk-1",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert "down" in response["error"]["message"]


async def test_cancelling_on_an_account_that_is_not_loaded_says_so(
    hass: HomeAssistant, hass_ws_client: Any, mock_entry: MockConfigEntry
) -> None:
    from homeassistant.setup import async_setup_component

    from custom_components.skedda_scheduler.const import DOMAIN

    mock_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "skedda_scheduler/cancel_booking",
            "entry_id": mock_entry.entry_id,
            "booking_id": "bk-1",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "not_loaded"


async def test_two_accounts_are_both_listed_with_their_own_jobs(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Two people booking from one Home Assistant is the point of accounts.

    Each has an hour a week of their own, so the panel has to keep them apart.
    """
    from custom_components.skedda_scheduler.const import CONF_VENUE, DOMAIN
    from tests.conftest import ENTRY_DATA
    from tests.helpers import add_job_subentry

    await setup_with_job(hass, mock_entry)

    second = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_VENUE: "galaktyka"},
        title="Vika",
        unique_id="galaktyka:vika@example.com",
        entry_id="entry-vika",
    )
    second.add_to_hass(hass)
    add_job_subentry(hass, second, "sub-vika", name="Vika Thursdays")
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    result = await overview(client)

    both = ["Main account (Oleh)", "Vika"]
    assert sorted(account["title"] for account in result["accounts"]) == both
    assert sorted(job["account"] for job in result["jobs"]) == both
    assert {job["entry_id"] for job in result["jobs"]} == {mock_entry.entry_id, "entry-vika"}


async def test_a_linked_calendar_is_not_mistaken_for_an_account(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """It holds a Google link, not a venue, and has no runtime data to read."""
    await setup_with_job(hass, mock_entry)
    linked = calendar_entry()
    linked.add_to_hass(hass)
    with patch(
        "custom_components.skedda_scheduler.google_calendar.async_build_client",
        return_value=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(linked.entry_id)
        await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    result = await overview(client)

    assert [account["entry_id"] for account in result["accounts"]] == [mock_entry.entry_id]


async def test_the_calendar_link_holds_no_bookings_to_cancel(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    await setup_with_job(hass, mock_entry)
    linked = calendar_entry()
    linked.add_to_hass(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "skedda_scheduler/cancel_booking",
            "entry_id": linked.entry_id,
            "booking_id": "b1",
        }
    )
    response = await client.receive_json()

    assert response["error"]["code"] == "not_loaded"


async def test_the_overview_lists_watch_rules(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    from tests.helpers import watch_entry_with_rule

    await setup_with_job(hass, mock_entry)
    await watch_entry_with_rule(hass)
    client = await hass_ws_client(hass)

    result = await overview(client)

    assert [watch["name"] for watch in result["watches"]] == ["Our evening"]
    watching = result["watches"][0]
    assert watching["enabled"] is True
    assert watching["hours"] == "19:00-21:00"
    assert watching["entry_id"] == "entry-watch"


async def test_the_overview_says_when_a_watch_has_nothing_left_to_spend(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    from tests.helpers import watch_entry_with_rule
    from tests.test_watcher import mine

    mock_provider.list_bookings.return_value = [mine(day, 20) for day in range(0, 21, 7)]
    await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    await watch.runtime_data.watcher.async_scan()
    client = await hass_ws_client(hass)

    assert (await overview(client))["watches"][0]["gate_open"] is False


async def test_cancelling_from_the_panel_marks_the_slot_as_given_up(
    hass: HomeAssistant,
    hass_ws_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Otherwise a watch rule would take the court straight back."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    start = (dt_util.utcnow() + timedelta(days=3)).replace(microsecond=0)
    booking = Booking(
        id="b-cancel-me",
        space_ids=("2000001",),
        start=start,
        end=start + timedelta(hours=1),
        title="",
        is_mine=True,
    )
    mock_provider.list_bookings.return_value = [booking]
    await setup_with_job(hass, mock_entry)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "skedda_scheduler/cancel_booking",
            "entry_id": mock_entry.entry_id,
            "booking_id": "b-cancel-me",
        }
    )
    assert (await client.receive_json())["success"]

    assert ("2000001", start.isoformat()) in mock_entry.runtime_data.store.released_slots()

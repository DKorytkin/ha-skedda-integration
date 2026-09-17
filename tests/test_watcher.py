"""The watcher against a fake venue: the gate, the catch, and what it refuses."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.core.provider import Booking
from tests.helpers import setup_account, setup_with_job, watch_entry_with_rule

KYIV = ZoneInfo("Europe/Kyiv")


@pytest.fixture(autouse=True)
def venue_remembers(mock_provider: AsyncMock) -> None:
    """A venue that keeps what we booked.

    Without this the fake hands back an empty diary after every catch, and the
    watch - correctly, given what it was told - takes the same hour again.
    """

    async def book(request: Any) -> Booking:
        booking = Booking(
            id=f"caught-{len(mock_provider.list_bookings.return_value)}",
            space_ids=(request.space_id,),
            start=request.start,
            end=request.end,
            title=request.title,
            is_mine=True,
        )
        mock_provider.list_bookings.return_value = [
            *mock_provider.list_bookings.return_value,
            booking,
        ]
        return booking

    mock_provider.book.side_effect = book


def mine(days_ahead: int, hour: int, *, space: str = "2000001") -> Booking:
    """One of our own bookings, relative to now so the horizon includes it."""
    start = (dt_util.utcnow().astimezone(KYIV) + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return Booking(
        id=f"mine-{days_ahead}-{hour}",
        space_ids=(space,),
        start=start,
        end=start + timedelta(hours=1),
        title="",
        is_mine=True,
    )


async def test_a_free_slot_inside_the_rule_is_booked(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_provider.book.reset_mock()

    caught = await watch.runtime_data.watcher.async_scan()

    assert caught is not None
    assert mock_provider.book.await_count == 1


async def test_nothing_is_booked_when_no_account_has_quota_left(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The gate is the economy: shut, the watch costs nothing at all."""
    mock_provider.list_bookings.return_value = [mine(day, 20) for day in range(0, 21, 7)]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_provider.book.reset_mock()

    assert await watch.runtime_data.watcher.async_scan() is None
    assert mock_provider.book.await_count == 0
    assert watch.runtime_data.watcher.gate_open is False


async def test_a_rule_set_to_notify_only_never_books(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, book=False)
    mock_provider.book.reset_mock()

    caught = await watch.runtime_data.watcher.async_scan()

    assert caught is not None
    assert mock_provider.book.await_count == 0


async def test_a_disabled_rule_leaves_the_venue_alone(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, enabled=False)
    mock_provider.book.reset_mock()

    assert await watch.runtime_data.watcher.async_scan() is None
    assert mock_provider.book.await_count == 0


async def test_losing_the_race_is_not_treated_as_a_fault(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The slot was free a moment ago and now is not. That is ordinary."""
    from custom_components.skedda_scheduler.api.errors import SlotTakenError

    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_provider.book.side_effect = SlotTakenError("gone")

    assert await watch.runtime_data.watcher.async_scan() is None


async def test_a_catch_is_announced_like_any_other_booking(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    from pytest_homeassistant_custom_component.common import async_capture_events

    from custom_components.skedda_scheduler.const import EVENT_BOOKING_SUCCEEDED

    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    events = async_capture_events(hass, EVENT_BOOKING_SUCCEEDED)

    await watch.runtime_data.watcher.async_scan()
    await hass.async_block_till_done()

    assert [event.data["job_name"] for event in events] == ["Our evening"]


async def test_a_watch_with_no_account_for_its_venue_does_nothing(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Nothing to pay with, and no venue snapshot to read."""
    watch = await watch_entry_with_rule(hass)

    assert await watch.runtime_data.watcher.async_scan() is None
    assert watch.runtime_data.watcher.gate_open is False


async def test_a_rule_the_form_let_through_broken_is_skipped(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """One unusable rule must not stop the others watching."""
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, weekdays=[])

    assert watch.runtime_data.watcher.rules == []
    assert await watch.runtime_data.watcher.async_scan() is None


async def test_the_catch_is_paid_for_by_an_account_with_quota_left(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """This week is spent, so the catch has to land in a later one."""
    mock_provider.list_bookings.return_value = [mine(0, 20)]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    caught = await watch.runtime_data.watcher.async_scan()

    assert caught is not None
    assert caught.account_id == mock_entry.entry_id
    assert caught.start.isocalendar()[1] != datetime.now(KYIV).isocalendar()[1]


async def test_the_venue_is_re_read_after_a_catch(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The next decision must not be made against a snapshot we just changed."""
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_provider.list_bookings.return_value = []

    with patch.object(mock_entry.runtime_data.coordinator, "async_request_refresh") as refresh:
        caught = await watch.runtime_data.watcher.async_scan()

    assert caught is not None
    assert refresh.await_count == 1


async def test_an_external_push_makes_the_watch_look_now(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A Telegram automation can beat the poll by minutes."""
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    # The venue forgot everything, so there is something to find again.
    mock_provider.list_bookings.return_value = []
    mock_provider.book.reset_mock()

    caught = await watch.runtime_data.watcher.async_refresh_and_scan()

    assert caught is not None
    assert mock_provider.book.await_count >= 1


async def test_unloading_the_watch_leaves_nothing_behind(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    assert await hass.config_entries.async_unload(watch.entry_id)
    await hass.async_block_till_done()


async def test_the_watch_reads_the_venue_through_one_account_only(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, extra_account: Any
) -> None:
    """Every account sees the same venue-wide list; three polls would be waste."""
    await setup_account(hass, mock_entry)
    second = await extra_account()
    watch = await watch_entry_with_rule(hass)

    await watch.runtime_data.watcher.async_scan()

    accounts = watch.runtime_data.watcher.accounts()
    assert [entry.entry_id for entry in accounts] == [mock_entry.entry_id, second.entry_id]


async def test_a_subentry_that_is_not_a_rule_is_passed_over(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Home Assistant allows several kinds under one entry."""
    from homeassistant.config_entries import ConfigSubentry

    from custom_components.skedda_scheduler.const import SUBENTRY_TYPE_JOB
    from tests.helpers import JOB_DATA

    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    hass.config_entries.async_add_subentry(
        watch,
        ConfigSubentry(
            data=JOB_DATA,
            subentry_id="not-a-rule",
            subentry_type=SUBENTRY_TYPE_JOB,
            title="A job",
            unique_id=None,
        ),
    )

    assert [rule.rule_id for rule in watch.runtime_data.watcher.rules] != []
    assert "not-a-rule" not in {rule.rule_id for rule in watch.runtime_data.watcher.rules}


async def test_a_venue_not_yet_read_is_waited_for(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A poll that has not landed is not a reason to guess."""
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_entry.runtime_data.coordinator.data = None

    assert await watch.runtime_data.watcher.async_scan() is None


async def test_a_venue_with_nothing_free_is_simply_quiet(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Quota to spend, but every evening taken: nothing to report."""
    taken = [
        Booking(
            id=f"theirs-{day}-{hour}-{space}",
            space_ids=(space,),
            start=(dt_util.utcnow().astimezone(KYIV) + timedelta(days=day)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            ),
            end=(dt_util.utcnow().astimezone(KYIV) + timedelta(days=day)).replace(
                hour=hour + 1, minute=0, second=0, microsecond=0
            ),
            title="",
            is_mine=False,
        )
        for day in range(0, 16)
        for hour in (19, 20)
        for space in ("2000001", "2000002")
    ]
    mock_provider.list_bookings.return_value = taken
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    assert await watch.runtime_data.watcher.async_scan() is None
    assert watch.runtime_data.watcher.gate_open is True


async def test_only_the_reader_is_asked_to_poll_faster(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, extra_account: Any
) -> None:
    """One venue-wide list: a second account polling it is pure waste."""
    await setup_account(hass, mock_entry)
    second = await extra_account()
    watch = await watch_entry_with_rule(hass)

    await watch.runtime_data.watcher.async_scan()

    assert watch.runtime_data.watcher.interval is not None
    assert mock_entry.runtime_data.coordinator.update_interval <= timedelta(minutes=15)
    assert second.runtime_data.coordinator.update_interval > timedelta(minutes=15)


async def test_a_shut_gate_gives_the_poll_rate_back(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Nothing left to spend means nothing left to look for."""
    mock_provider.list_bookings.return_value = [mine(day, 20) for day in range(0, 21, 7)]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    await watch.runtime_data.watcher.async_scan()

    assert watch.runtime_data.watcher.interval is None


async def test_a_rule_can_be_turned_off_without_deleting_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    entity_id = "switch.our_evening_rule_enabled"
    mock_provider.book.reset_mock()

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "off"
    assert await watch.runtime_data.watcher.async_scan() is None
    assert mock_provider.book.await_count == 0


async def test_the_rule_sensor_says_why_it_is_idle(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A shut gate is a reason, not an absence."""
    mock_provider.list_bookings.return_value = [mine(day, 20) for day in range(0, 21, 7)]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    await watch.runtime_data.watcher.async_scan()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.our_evening_watch")
    assert state.state == "no_quota"
    assert state.attributes["gate_open"] is False


async def test_the_rule_sensor_reports_a_catch(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    caught = await watch.runtime_data.watcher.async_scan()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.our_evening_watch")
    assert state.state == "watching"
    assert state.attributes["last_catch"] == caught.start.isoformat()
    assert state.attributes["poll_interval_minutes"] is not None


async def test_a_disabled_rule_reads_as_disabled(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    await watch_entry_with_rule(hass, enabled=False)

    assert hass.states.get("sensor.our_evening_watch").state == "disabled"


async def test_a_rule_turned_off_can_be_turned_back_on(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, enabled=False)
    entity_id = "switch.our_evening_rule_enabled"

    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "on"
    assert [rule.enabled for rule in watch.runtime_data.watcher.rules] == [True]


async def test_a_poll_landing_is_what_makes_the_watch_look(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The watch has no clock: without this it would never run at all."""
    await setup_account(hass, mock_entry)
    await watch_entry_with_rule(hass)
    mock_provider.list_bookings.return_value = []
    mock_provider.book.reset_mock()

    await mock_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert mock_provider.book.await_count >= 1


async def test_setting_the_watch_up_looks_once_immediately(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Waiting a quarter of an hour for the first look is a slot lost."""
    await setup_account(hass, mock_entry)
    mock_provider.book.reset_mock()

    await watch_entry_with_rule(hass)

    assert mock_provider.book.await_count >= 1


async def test_an_account_that_reloads_is_followed_to_its_new_coordinator(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A reload builds a new coordinator; a listener on the old one is deaf."""
    await setup_account(hass, mock_entry)
    await watch_entry_with_rule(hass)

    await hass.config_entries.async_reload(mock_entry.entry_id)
    await hass.async_block_till_done()
    mock_provider.list_bookings.return_value = []
    mock_provider.book.reset_mock()
    await mock_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert mock_provider.book.await_count >= 1


async def test_a_venue_failure_during_a_background_scan_is_only_logged(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """An exception in a background task would otherwise vanish."""
    from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError

    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_provider.book.side_effect = SkeddaConnectionError("down")

    await watch.runtime_data.watcher._async_scan_quietly()


async def test_a_week_a_job_is_aiming_at_is_left_alone(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Spending the job's hour would make it fail on quota at its own window."""
    await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_provider.book.reset_mock()

    assert await watch.runtime_data.watcher.async_scan() is None
    assert mock_provider.book.await_count == 0


async def test_a_week_the_job_already_lost_is_the_watchs_business(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """That is the whole point: the race was lost, the court may come back."""
    job_id = await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    runner = mock_entry.runtime_data.scheduler.runner_for(job_id)
    # The job fired at its slot and did not get it.
    runner.attempted_slot = runner.job.next_slot(dt_util.utcnow())[0]
    mock_provider.book.reset_mock()

    caught = await watch.runtime_data.watcher.async_scan()

    assert caught is not None
    assert caught.start.isocalendar()[:2] == runner.attempted_slot.isocalendar()[:2]


async def test_a_scan_that_raises_in_the_background_says_so_in_the_log(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, caplog: Any
) -> None:
    """A background task's exception would otherwise vanish without a trace."""
    from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError

    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_provider.list_bookings.return_value = []
    mock_provider.book.side_effect = SkeddaConnectionError("down")

    with patch.object(
        watch.runtime_data.watcher, "async_scan", side_effect=SkeddaConnectionError("down")
    ):
        await watch.runtime_data.watcher._async_scan_quietly()

    assert "Watch scan failed" in caplog.text


async def test_a_disabled_job_does_not_reserve_its_week(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A job switched off has no claim on the hour it would have spent."""
    job_id = await setup_with_job(hass, mock_entry, enabled=False)
    watch = await watch_entry_with_rule(hass)
    assert mock_entry.runtime_data.scheduler.runner_for(job_id) is not None
    mock_provider.book.reset_mock()
    mock_provider.list_bookings.return_value = []

    assert await watch.runtime_data.watcher.async_scan() is not None


async def test_a_watch_whose_account_has_no_scheduler_yet_reserves_nothing(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Setup order is not something the watch may depend on."""
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    mock_entry.runtime_data.scheduler = None

    assert (
        watch.runtime_data.watcher._aimed_weeks(mock_entry, dt_util.utcnow(), dt_util.utcnow())
        == set()
    )

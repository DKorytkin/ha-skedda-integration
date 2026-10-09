"""The watcher against a fake venue: the gate, the catch, and what it refuses."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise
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


def started_an_hour_ago(*, space: str = "2000001") -> Booking:
    """One of ours that is already under way, whatever time the test runs."""
    start = (dt_util.utcnow().astimezone(KYIV) - timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    )
    return Booking(
        id="mine-started",
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
    """This week is spent, so the catch has to land in a later one.

    Setting the watch up already scans, and may already have caught it: early
    on a Monday the horizon holds only one later week. Either scan will do.
    """
    mock_provider.list_bookings.return_value = [mine(0, 20)]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    caught = await watch.runtime_data.watcher.async_scan() or watch.runtime_data.watcher.last_catch

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


async def test_only_one_account_is_asked_to_poll_faster(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, extra_account: Any
) -> None:
    """One venue-wide list: a second account polling it is pure waste."""
    await setup_account(hass, mock_entry)
    second = await extra_account()
    watch = await watch_entry_with_rule(hass)

    await watch.runtime_data.watcher.async_scan()

    intervals = [
        mock_entry.runtime_data.coordinator.update_interval,
        second.runtime_data.coordinator.update_interval,
    ]
    assert watch.runtime_data.watcher.interval is not None
    assert sum(interval <= timedelta(minutes=15) for interval in intervals) == 1


async def test_the_reading_is_passed_around_the_accounts(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, extra_account: Any
) -> None:
    """Always the same reader is one member polling all day for everyone."""
    await setup_account(hass, mock_entry)
    second = await extra_account()
    # Notify only: a catch would refresh an account, and the scan that refresh
    # starts in the background would take a turn of its own.
    watch = await watch_entry_with_rule(hass, book=False)
    runner = watch.runtime_data.watcher
    accounts = runner.accounts()

    readers = []
    for _ in range(4):
        readers.append(runner.reader(accounts).entry_id)
        await runner.async_scan()

    assert set(readers) == {mock_entry.entry_id, second.entry_id}
    assert all(one != two for one, two in pairwise(readers))


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


async def caught_weeks(
    watch: MockConfigEntry, account: MockConfigEntry, mock_provider: AsyncMock
) -> set[int]:
    """Every ISO week the watch books in, scanning until it has had its fill.

    The account is re-read between scans by hand: with the clock frozen, the
    refresh a catch requests is debounced for ever.
    """
    for _ in range(10):
        await account.runtime_data.coordinator.async_refresh()
        if await watch.runtime_data.watcher.async_scan() is None:
            break
    return {call.args[0].start.isocalendar()[1] for call in mock_provider.book.await_args_list}


async def test_a_week_a_job_is_aiming_at_is_left_alone(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, freezer: Any
) -> None:
    """Spending the job's hour would make it fail on quota at its own window.

    Sunday 11.10: the job is armed for Tuesday 13.10, in week 42.
    """
    freezer.move_to("2026-10-11T09:00:00Z")
    job_id = await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    armed = mock_entry.runtime_data.scheduler.runner_for(job_id).armed_slot

    assert armed is not None and armed.isocalendar()[1] == 42
    assert 42 not in await caught_weeks(watch, mock_entry, mock_provider)


async def test_a_week_whose_window_the_job_let_pass_is_the_watchs_business(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock, freezer: Any
) -> None:
    """Thursday 15.10: the job is armed for Tuesday 20.10 (week 43), and the
    window for 27.10 (week 44) has already opened. The job catches up on one
    slot only and will never fire at 27.10, so leaving week 44 reserved would
    leave it to nobody.
    """
    freezer.move_to("2026-10-15T09:00:00Z")
    job_id = await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    armed = mock_entry.runtime_data.scheduler.runner_for(job_id).armed_slot

    weeks = await caught_weeks(watch, mock_entry, mock_provider)

    assert armed is not None and armed.isocalendar()[1] == 43
    assert 43 not in weeks
    assert 44 in weeks


async def test_a_week_the_job_already_lost_is_the_watchs_business(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """That is the whole point: the race was lost, the court may come back."""
    job_id = await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    runner = mock_entry.runtime_data.scheduler.runner_for(job_id)
    # The job fired at its slot, did not get it, and moved on to the next.
    runner.attempted_slot = runner.job.next_slot(dt_util.utcnow())[0]
    runner.async_schedule()
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


async def test_a_booking_that_disappears_is_remembered_as_given_up(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The court we cancelled must not be taken back minutes later."""
    held = mine(3, 20)
    mock_provider.list_bookings.return_value = [held]
    await setup_account(hass, mock_entry)
    # Notify-only: the only booking that can disappear is the one cancelled here.
    watch = await watch_entry_with_rule(hass, book=False)
    runner = watch.runtime_data.watcher
    await runner.async_scan()

    mock_provider.list_bookings.return_value = []
    await mock_entry.runtime_data.coordinator.async_refresh()
    await runner.async_scan()

    assert (held.space_ids[0], held.start, held.end) in (
        mock_entry.runtime_data.store.released_intervals()
    )


async def test_a_slot_given_up_is_never_caught_again(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    held = mine(3, 20)
    mock_provider.list_bookings.return_value = [held]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    runner = watch.runtime_data.watcher
    await runner.async_scan()
    mock_provider.list_bookings.return_value = []
    await mock_entry.runtime_data.coordinator.async_refresh()
    await runner.async_scan()
    mock_provider.book.reset_mock()

    caught = await runner.async_scan()

    assert caught is None or caught.start != held.start
    assert all(call.args[0].start != held.start for call in mock_provider.book.await_args_list)


async def test_a_booking_that_merely_aged_out_is_not_a_release(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """A slot that has started leaves the window on its own."""
    past = started_an_hour_ago()
    mock_provider.list_bookings.return_value = [past]
    await setup_account(hass, mock_entry)
    # Notify-only: nothing else appears and disappears to muddy the test.
    watch = await watch_entry_with_rule(hass, book=False)
    await watch.runtime_data.watcher.async_scan()

    mock_provider.list_bookings.return_value = []
    await mock_entry.runtime_data.coordinator.async_refresh()
    await watch.runtime_data.watcher.async_scan()

    assert mock_entry.runtime_data.store.released_intervals() == set()


async def test_the_first_scan_after_a_restart_releases_nothing(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Nothing to compare against is not the same as everything cancelled."""
    mock_provider.list_bookings.return_value = [mine(3, 20)]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    await watch.runtime_data.watcher.async_scan()

    assert mock_entry.runtime_data.store.released_intervals() == set()


async def test_a_week_whose_window_opened_without_the_job_is_not_reserved(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Observed 2026-10-01: a job skipped its week, and the watch kept out too.

    The job never fired at that slot, so "the last slot it attempted" did not
    release it - yet a slot whose window opened and that the job is not armed
    for is one it will never try again.
    """
    from custom_components.skedda_scheduler.core.watch import week_of

    job_id = await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    runner = mock_entry.runtime_data.scheduler.runner_for(job_id)
    now = dt_util.utcnow()
    nearest = runner.job.next_slot(now)[0]
    later = runner.job.next_slot(nearest)[0]
    assert runner.job.window.opens_at(nearest) <= now
    runner.attempted_slot = None
    runner.armed_slot = later

    weeks = watch.runtime_data.watcher._aimed_weeks(mock_entry, now, now + timedelta(days=14))

    assert week_of(nearest) not in weeks
    assert week_of(later) in weeks


async def test_a_booking_cancelled_while_home_assistant_was_down_is_a_release(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """What we held is kept on disk, so a restart does not forget it."""
    held = mine(3, 20)
    mock_provider.list_bookings.return_value = [held]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, book=False)
    await watch.runtime_data.watcher.async_scan()

    mock_provider.list_bookings.return_value = []
    await mock_entry.runtime_data.coordinator.async_refresh()
    # A fresh runner, with nothing in memory - the restart.
    assert await hass.config_entries.async_reload(watch.entry_id)
    await hass.async_block_till_done()

    assert (held.space_ids[0], held.start, held.end) in (
        mock_entry.runtime_data.store.released_intervals()
    )


async def test_a_release_takes_its_calendar_event_with_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Cancelled in the Skedda app: the court is gone, so is the diary entry."""
    held = mine(3, 20)
    mock_provider.list_bookings.return_value = [held]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, book=False)
    runner = watch.runtime_data.watcher
    await runner.async_scan()

    mock_provider.list_bookings.return_value = []
    with patch("custom_components.skedda_scheduler.google_calendar.async_release_event") as release:
        # The refresh starts a scan of its own; either may see the release.
        await mock_entry.runtime_data.coordinator.async_refresh()
        await runner.async_scan()
        await hass.async_block_till_done()

    release.assert_awaited_once()
    assert release.await_args.args[1:] == (held.space_ids[0], held.start, held.end)


async def test_a_slot_the_panel_already_released_is_not_released_twice(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The panel removed the event itself; a second delete would be noise."""
    held = mine(3, 20)
    mock_provider.list_bookings.return_value = [held]
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass, book=False)
    runner = watch.runtime_data.watcher
    await runner.async_scan()

    await mock_entry.runtime_data.store.async_note_released(held.space_ids[0], held.start, held.end)
    mock_provider.list_bookings.return_value = []
    with patch("custom_components.skedda_scheduler.google_calendar.async_release_event") as release:
        # The refresh starts a scan of its own; either may see the release.
        await mock_entry.runtime_data.coordinator.async_refresh()
        await runner.async_scan()
        await hass.async_block_till_done()

    release.assert_not_awaited()


async def test_a_catch_carries_the_id_of_the_booking_it_made(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """The calendar event names it, so the booking can be found again."""
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    with patch("custom_components.skedda_scheduler.watcher.async_dispatch") as dispatch:
        caught = await watch.runtime_data.watcher.async_scan()

    assert caught is not None
    assert dispatch.await_args.args[1].booking_id.startswith("caught-")


async def test_the_watch_never_asks_for_an_hour_the_venue_is_shut(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Seen 2026-10-08: 22:00 asked for on every scan, refused every time."""
    from dataclasses import replace

    from custom_components.skedda_scheduler.core.provider import OpenHours
    from tests.conftest import VENUE_RULES

    mock_provider.venue_settings.return_value = replace(
        VENUE_RULES,
        hours=(OpenHours(weekdays=frozenset(range(7)), start_minute=480, end_minute=1140),),
    )
    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    caught = await watch.runtime_data.watcher.async_scan()

    assert caught is None
    mock_provider.book.assert_not_awaited()


def test_a_rule_asks_only_while_the_venue_still_takes_the_slot() -> None:
    """Seen 2026-10-09: 18:00 asked for at 15:42 at a venue wanting three hours."""
    from dataclasses import replace

    from custom_components.skedda_scheduler.core.watch import WatchRule
    from custom_components.skedda_scheduler.watcher import _within_venue
    from tests.conftest import VENUE_RULES

    rule = WatchRule(
        rule_id="r",
        name="Friday",
        weekdays=frozenset({4}),
        not_before=datetime(2026, 1, 1, 18).time(),
        not_after=datetime(2026, 1, 1, 21).time(),
        space_ids=(),
        duration_minutes=60,
        venue_timezone="Europe/Kyiv",
    )

    assert _within_venue(rule, replace(VENUE_RULES, min_minutes_ahead=180)).min_lead_minutes == 180
    assert _within_venue(rule, VENUE_RULES).min_lead_minutes == 0


async def test_a_slot_the_venue_refused_outright_gives_way_to_the_next(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """One catch per scan: a refused best slot must not hide the others."""
    from custom_components.skedda_scheduler.api.errors import ApiContractError

    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    runner = watch.runtime_data.watcher
    mock_provider.book.side_effect = ApiContractError("must book 3 hour(s) in advance")
    assert await runner.async_scan() is None
    refused = mock_provider.book.await_args.args[0]
    mock_provider.book.reset_mock()

    await runner.async_scan()

    asked = mock_provider.book.await_args.args[0]
    assert (asked.space_id, asked.start) != (refused.space_id, refused.start)


async def test_a_slot_lost_in_a_race_is_still_worth_watching(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Taken now may be given up again later."""
    from custom_components.skedda_scheduler.api.errors import SlotTakenError

    await setup_account(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)
    runner = watch.runtime_data.watcher
    mock_provider.book.side_effect = SlotTakenError("conflicts with")
    await runner.async_scan()
    first = mock_provider.book.await_args.args[0]
    mock_provider.book.reset_mock()

    await runner.async_scan()

    again = mock_provider.book.await_args.args[0]
    assert (again.space_id, again.start) == (first.space_id, first.start)

"""Persisted booking-attempt history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.core.result import (
    AttemptStatus,
    BookingAttempt,
    BookingOutcome,
)
from custom_components.skedda_scheduler.store import (
    MAX_HISTORY_PER_JOB,
    AttemptStore,
    storage_key,
)

WHEN = datetime(2026, 9, 1, 21, 0, tzinfo=UTC)


def outcome(job_id: str, *, succeeded: bool = True) -> BookingOutcome:
    return BookingOutcome(
        job_id=job_id,
        succeeded=succeeded,
        booking_id="bk-1" if succeeded else None,
        space_id="2000001" if succeeded else None,
        slot_start=WHEN,
        slot_end=WHEN + timedelta(hours=1),
        attempts=(
            BookingAttempt(1, WHEN, AttemptStatus.TOO_EARLY, 12.5, "not yet"),
            BookingAttempt(2, WHEN, AttemptStatus.SUCCESS, 10.0),
        ),
        finished_at=WHEN,
    )


async def loaded_store(hass: HomeAssistant, entry: MockConfigEntry) -> AttemptStore:
    store = AttemptStore(hass, entry)
    await store.async_load()
    return store


async def test_a_recorded_outcome_is_readable_back(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    store = await loaded_store(hass, mock_entry)
    await store.async_record(outcome("job-1"))

    last = store.last_outcome("job-1")
    assert last is not None
    assert last["succeeded"] is True
    assert last["space_id"] == "2000001"


async def test_every_attempt_is_kept_not_just_the_count(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """A lost race is only explainable from the individual attempts.

    The event payload collapses them to a number; here the timings and the
    server's own words are what answer "why did this one not land?".
    """
    store = await loaded_store(hass, mock_entry)
    await store.async_record(outcome("job-1"))

    log = store.last_outcome("job-1")["attempt_log"]
    assert [item["status"] for item in log] == ["too_early", "success"]
    assert log[0]["detail"] == "not yet"
    assert log[0]["latency_ms"] == 12.5


async def test_history_is_newest_first(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    store = await loaded_store(hass, mock_entry)
    await store.async_record(outcome("job-1", succeeded=False))
    await store.async_record(outcome("job-1", succeeded=True))

    assert [item["succeeded"] for item in store.history_for("job-1")] == [True, False]


async def test_history_is_capped_per_job(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    store = await loaded_store(hass, mock_entry)
    for _ in range(MAX_HISTORY_PER_JOB + 10):
        await store.async_record(outcome("job-1"))

    assert len(store.history_for("job-1")) == MAX_HISTORY_PER_JOB


async def test_jobs_do_not_share_history(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    store = await loaded_store(hass, mock_entry)
    await store.async_record(outcome("job-1"))

    assert store.history_for("job-2") == []
    assert store.last_outcome("job-2") is None


async def test_history_survives_a_restart(
    hass: HomeAssistant, mock_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    store = await loaded_store(hass, mock_entry)
    await store.async_record(outcome("job-1"))

    assert await loaded_store(hass, mock_entry) is not store
    assert (await loaded_store(hass, mock_entry)).last_outcome("job-1") is not None
    assert storage_key(mock_entry) in hass_storage


async def test_two_accounts_do_not_overwrite_each_other(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """One file per account.

    A shared file would be read into two separate dictionaries and written
    back whole, so whichever account recorded last would erase the other.
    """
    other = MockConfigEntry(domain=mock_entry.domain, entry_id="entry-2")
    first = await loaded_store(hass, mock_entry)
    second = await loaded_store(hass, other)

    await first.async_record(outcome("job-1"))
    await second.async_record(outcome("job-2"))

    assert (await loaded_store(hass, mock_entry)).last_outcome("job-1") is not None
    assert (await loaded_store(hass, other)).last_outcome("job-2") is not None


async def test_removing_the_account_takes_its_history_with_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    store = await loaded_store(hass, mock_entry)
    await store.async_record(outcome("job-1"))

    await store.async_remove()

    assert hass_storage.get(storage_key(mock_entry), {}).get("data") in (None, {})

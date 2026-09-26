"""Diagnostics must be useful and must not leak credentials."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.skedda_scheduler.api.errors import ApiContractError
from custom_components.skedda_scheduler.const import DOMAIN
from custom_components.skedda_scheduler.repairs import CONTRACT_ISSUE
from tests.helpers import WATCH_RULE_DATA, setup_with_job, watch_entry_with_rule
from tests.test_google_calendar import calendar_entry


async def diagnostics(hass: HomeAssistant, client: Any, entry: MockConfigEntry) -> dict[str, Any]:
    return await get_diagnostics_for_config_entry(hass, client, entry)


async def test_diagnostics_never_carry_the_credentials(
    hass: HomeAssistant,
    hass_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Users attach these to public bug reports."""
    await setup_with_job(hass, mock_entry)

    dumped = json.dumps(await diagnostics(hass, hass_client, mock_entry))

    assert "secret" not in dumped
    assert "user@example.com" not in dumped
    # The venue stays: which venue's rules are in play is the first thing a
    # bug report needs.
    assert "myclub" in dumped


async def test_diagnostics_explain_a_lost_race(
    hass: HomeAssistant,
    hass_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    subentry_id = await setup_with_job(hass, mock_entry)
    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        await mock_entry.runtime_data.scheduler.async_run_now(subentry_id)

    result = await diagnostics(hass, hass_client, mock_entry)

    job = result["jobs"][0]
    assert job["job_id"] == subentry_id
    assert job["history"][0]["attempt_log"][0]["status"] == "success"
    assert job["armed_for"] is not None
    assert result["clock_offset_seconds"] is not None
    assert result["venue_rules"]["weekly_quota_minutes"] == 60
    assert result["spaces"][0]["name"] == "Court 1"


async def test_an_unrecognised_response_becomes_a_repair_issue(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Nobody documented this API; the day it changes the user must be told."""
    subentry_id = await setup_with_job(hass, mock_entry)
    mock_provider.book.side_effect = ApiContractError("unknown field 'foo'")

    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        await mock_entry.runtime_data.scheduler.async_run_now(subentry_id)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{CONTRACT_ISSUE}_{mock_entry.entry_id}")
    assert issue is not None
    assert "foo" in issue.translation_placeholders["detail"]


async def test_the_issue_clears_once_booking_works_again(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    subentry_id = await setup_with_job(hass, mock_entry)
    mock_provider.book.side_effect = ApiContractError("unknown field 'foo'")
    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        await mock_entry.runtime_data.scheduler.async_run_now(subentry_id)

    mock_provider.book.side_effect = None
    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        await mock_entry.runtime_data.scheduler.async_run_now(subentry_id)

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"{CONTRACT_ISSUE}_{mock_entry.entry_id}")
        is None
    )


async def test_diagnostics_ignore_subentries_that_are_not_jobs(
    hass: HomeAssistant,
    hass_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Nothing else uses subentries yet, but the dump must not assume so."""
    from homeassistant.config_entries import ConfigSubentry

    mock_entry.add_to_hass(hass)
    hass.config_entries.async_add_subentry(
        mock_entry,
        ConfigSubentry(
            data={},
            subentry_id="sub-x",
            subentry_type="something_else",
            title="Not a job",
            unique_id=None,
        ),
    )
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert (await diagnostics(hass, hass_client, mock_entry))["jobs"] == []


async def test_the_calendar_entry_has_diagnostics_without_its_secrets(
    hass: HomeAssistant,
    hass_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """Home Assistant offers the download on every entry, so it must not fail
    on the one that is not an account, nor hand out a Google token."""
    await setup_with_job(hass, mock_entry)
    calendar = calendar_entry()
    calendar.add_to_hass(hass)

    dumped = json.dumps(await diagnostics(hass, hass_client, calendar))

    # Quoted, so the key "token" does not count as the token "tok".
    for secret in ('"tok"', '"refresh"', "oleh@example.com", "denys@example.com", "Some Street"):
        assert secret not in dumped, secret
    assert "Tennis" in dumped


async def test_the_watch_entry_has_diagnostics(
    hass: HomeAssistant,
    hass_client: Any,
    mock_entry: MockConfigEntry,
    mock_provider: AsyncMock,
) -> None:
    """What a rule wants and whether the gate is open answer "why no catch?"."""
    await setup_with_job(hass, mock_entry)
    watch = await watch_entry_with_rule(hass)

    result = await diagnostics(hass, hass_client, watch)

    assert result["entry"]["venue"] == "myclub"
    assert [rule["config"]["name"] for rule in result["rules"]] == [WATCH_RULE_DATA["name"]]
    assert "gate_open" in result

"""Adding, re-authenticating and reconfiguring a Skedda account."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.errors import (
    ApiContractError,
    SkeddaAuthError,
    SkeddaConnectionError,
    SkeddaError,
)
from custom_components.skedda_scheduler.const import (
    CONF_ALIAS,
    CONF_VENUE,
    CONF_VENUE_TIMEZONE,
    DOMAIN,
)
from custom_components.skedda_scheduler.core.provider import VenueRules
from custom_components.skedda_scheduler.flows.account import validate_credentials
from tests.api.test_client import stub_login
from tests.conftest import FakeSkedda

FIXTURES = Path("tests/fixtures/skedda")
VALIDATE = "custom_components.skedda_scheduler.flows.account.validate_credentials"

USER_INPUT = {
    CONF_VENUE: "  MyClub ",
    CONF_EMAIL: " User@Example.com ",
    CONF_PASSWORD: "secret",
    CONF_ALIAS: " Main account (Oleh) ",
}

RULES = VenueRules(
    timezone="Europe/Kyiv",
    slot_minutes=60,
    max_days_ahead=14,
    weekly_quota_minutes=60,
)


async def start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_the_entry_titled_with_the_alias(
    hass: HomeAssistant,
) -> None:
    result = await start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM

    with patch(VALIDATE, return_value=RULES):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Main account (Oleh)"


async def test_user_input_is_trimmed_and_the_unique_id_normalised(
    hass: HomeAssistant,
) -> None:
    """Stray whitespace and casing must not create a second copy of an account."""
    result = await start_user_flow(hass)
    with patch(VALIDATE, return_value=RULES):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["result"].unique_id == "myclub:user@example.com"
    assert result["data"][CONF_VENUE] == "myclub"
    assert result["data"][CONF_EMAIL] == "User@Example.com"


async def test_the_venue_timezone_is_discovered_not_asked_for(
    hass: HomeAssistant,
) -> None:
    """Every booking time is venue-local, so guessing this wrongly shifts slots."""
    result = await start_user_flow(hass)
    with patch(VALIDATE, return_value=RULES):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["data"][CONF_VENUE_TIMEZONE] == "Europe/Kyiv"


@pytest.mark.parametrize(
    ("raised", "expected_error"),
    [
        (SkeddaAuthError("no"), "invalid_auth"),
        (SkeddaConnectionError("no"), "cannot_connect"),
    ],
)
async def test_user_flow_surfaces_the_failure_reason(
    hass: HomeAssistant, raised: Exception, expected_error: str
) -> None:
    result = await start_user_flow(hass)
    with patch(VALIDATE, side_effect=raised):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


async def test_a_corrected_password_still_creates_the_entry(
    hass: HomeAssistant,
) -> None:
    """The form must stay usable after a rejection, not dead-end."""
    result = await start_user_flow(hass)
    with patch(VALIDATE, side_effect=SkeddaAuthError("no")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    with patch(VALIDATE, return_value=RULES):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_PASSWORD: "right"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_account_cannot_be_added_twice(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await start_user_flow(hass)
    with patch(VALIDATE, return_value=RULES):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_password_in_place(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch(VALIDATE, return_value=RULES):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-secret"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_entry.data[CONF_PASSWORD] == "new-secret"


async def test_reauth_keeps_the_form_open_when_the_new_password_is_wrong_too(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reauth_flow(hass)
    with patch(VALIDATE, side_effect=SkeddaAuthError("no")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still-wrong"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_entry.data[CONF_PASSWORD] == "secret"


async def test_reconfigure_updates_the_account_in_place(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    with patch(VALIDATE, return_value=RULES):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**USER_INPUT, CONF_ALIAS: "Renamed", CONF_PASSWORD: "rotated"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.data[CONF_ALIAS] == "Renamed"
    assert mock_entry.data[CONF_PASSWORD] == "rotated"


async def test_reconfigure_reports_a_rejected_change(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reconfigure_flow(hass)
    with patch(VALIDATE, side_effect=SkeddaConnectionError("down")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_unrecognised_venue_response_is_reported_as_unknown(
    hass: HomeAssistant,
) -> None:
    """A venue that answers in an unfamiliar shape is not a wrong password.

    Saying "invalid auth" would send the user rotating a password that works.
    """
    result = await start_user_flow(hass)
    with patch(VALIDATE, side_effect=ApiContractError("/webs carried no 'venue' entry")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_validate_credentials_checks_the_venue_not_only_the_login(
    hass: HomeAssistant, skedda: FakeSkedda
) -> None:
    """Sign-in is centralised on app.skedda.com, so it proves nothing about the venue.

    A typo in the subdomain would otherwise authenticate happily and then fail
    on every booking, days later and far from the cause.
    """
    stub_login(skedda)
    skedda.stub(
        "GET",
        endpoints.SPACES.path,
        json=json.loads((FIXTURES / "webs.json").read_text()),
    )
    rules = await validate_credentials(hass, "myclub", "user@example.com", "secret")
    assert rules.timezone == "Europe/Kyiv"
    assert skedda.requests_for("GET", endpoints.SPACES.path)


async def test_validate_credentials_rejects_an_unreachable_venue(
    hass: HomeAssistant, skedda: FakeSkedda
) -> None:
    stub_login(skedda)
    skedda.stub("GET", endpoints.SPACES.path, status=404, json={})
    # SkeddaError is the boundary the flow catches; anything narrower would let
    # the failure escape as an unhandled exception in the middle of setup.
    with pytest.raises(SkeddaError):
        await validate_credentials(hass, "typo", "user@example.com", "secret")


async def test_validate_credentials_retries_once_when_the_venue_session_lags(
    hass: HomeAssistant, skedda: FakeSkedda
) -> None:
    """Observed 2026-09-15: /webs answered 422 right after a successful login.

    Reporting that as a wrong password would be both wrong and infuriating,
    since signing in again is what the person would have done by hand anyway.
    """
    stub_login(skedda)
    skedda.stub_once(
        endpoints.SPACES.method, endpoints.SPACES.path, status=422, json={"errors": []}
    )
    skedda.stub(
        endpoints.SPACES.method,
        endpoints.SPACES.path,
        json=json.loads((FIXTURES / "webs.json").read_text()),
    )

    rules = await validate_credentials(hass, "myclub", "user@example.com", "secret")

    assert rules.timezone == "Europe/Kyiv"
    assert len(skedda.requests_for(endpoints.SPACES.method, endpoints.SPACES.path)) == 2
    # The retry has to sign in again, not merely re-ask: the stale session is
    # exactly what the first call rejected.
    assert len(skedda.requests_for(endpoints.LOGIN.method, endpoints.LOGIN.path)) == 2

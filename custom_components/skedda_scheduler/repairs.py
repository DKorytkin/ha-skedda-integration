"""Tell the user, in their own language, that Skedda changed its API."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

CONTRACT_ISSUE = "api_contract_changed"


def _issue_id(entry_id: str) -> str:
    return f"{CONTRACT_ISSUE}_{entry_id}"


def async_raise_contract_issue(hass: HomeAssistant, entry_id: str, detail: str) -> None:
    """Surface an unrecognised response as a repair.

    This integration speaks an API nobody documented, so the day Skedda changes
    it, the failure has to arrive as a sentence the user can act on rather than
    as a job that quietly stops booking.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=CONTRACT_ISSUE,
        translation_placeholders={"detail": detail[:200]},
    )


def async_clear_contract_issue(hass: HomeAssistant, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))

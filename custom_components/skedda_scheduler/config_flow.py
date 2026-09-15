"""Config flow for Skedda Scheduler.

Home Assistant refuses to set up an entry for an integration whose manifest
declares `config_flow: true` unless this module exists, so the class is
introduced here alongside the entry point. The steps that actually collect and
verify credentials arrive with the account flow.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class SkeddaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collects one Skedda account per config entry."""

    VERSION = 1

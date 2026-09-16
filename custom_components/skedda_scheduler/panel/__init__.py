"""The sidebar panel.

Read-only. Adding and editing go through Home Assistant's own dialogs so that
the forms, their validation and their translations have one implementation
rather than two.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.core import HomeAssistant

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "skedda"
PANEL_NAME = "skedda-panel"
SCRIPT_URL = f"/{DOMAIN}/skedda-panel.js"
SCRIPT_PATH = Path(__file__).parent / "skedda-panel.js"


def script_version() -> str:
    """A short fingerprint of the panel script.

    The module url is what a browser caches against, so without this an
    updated panel keeps rendering the previous one however many times Home
    Assistant restarts.
    """
    return hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest()[:12]


async def async_register_panel(hass: HomeAssistant) -> None:
    """Put the integration in the sidebar, once.

    Admin only: the overview it renders names the venue and every account.
    """
    if PANEL_URL in hass.data.get("frontend_panels", {}):
        # A second account must not fail to set up because the first one
        # already registered the panel.
        return

    await hass.http.async_register_static_paths(
        [StaticPathConfig(SCRIPT_URL, str(SCRIPT_PATH), cache_headers=False)]
    )
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_NAME,
        frontend_url_path=PANEL_URL,
        module_url=f"{SCRIPT_URL}?v={await hass.async_add_executor_job(script_version)}",
        sidebar_title="Skedda",
        sidebar_icon="mdi:tennis",
        require_admin=True,
    )
    _LOGGER.debug("Registered the Skedda panel at /%s", PANEL_URL)

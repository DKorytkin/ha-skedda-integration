"""The sidebar panel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import DOMAIN
from custom_components.skedda_scheduler.panel import PANEL_URL
from tests.helpers import setup_with_job

PANEL_JS = Path("custom_components/skedda_scheduler/panel/skedda-panel.js")


async def test_the_integration_puts_itself_in_the_sidebar(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Jobs live under Settings, which is where you configure them and not
    where you would glance at them."""
    await setup_with_job(hass, mock_entry)

    panel = hass.data[frontend.DATA_PANELS].get(PANEL_URL)
    assert panel is not None
    assert panel.sidebar_title
    assert panel.require_admin is True


async def test_the_panel_is_registered_once_for_any_number_of_accounts(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Registering it twice raises; a second account must not break setup."""
    await setup_with_job(hass, mock_entry)
    other = MockConfigEntry(domain=DOMAIN, data=dict(mock_entry.data), entry_id="entry-2")
    other.add_to_hass(hass)

    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    assert PANEL_URL in hass.data[frontend.DATA_PANELS]


def test_the_panel_script_is_shipped_and_self_contained() -> None:
    """No build step: what ships is what was written, and it loads nothing."""
    source = PANEL_JS.read_text(encoding="utf-8")

    assert "customElements.define" in source
    assert "skedda_scheduler/overview" in source
    assert "import(" not in source
    assert "http://" not in source and "https://" not in source


def test_the_panel_speaks_every_language_the_integration_ships() -> None:
    """A panel in English under a Ukrainian interface is half-translated."""
    source = PANEL_JS.read_text(encoding="utf-8")

    for language in ("en", "uk"):
        assert f"  {language}: {{" in source, f"no strings for {language}"
    assert "Завдання бронювання" in source
    assert "hass?.language" in source or "hass.language" in source


def test_the_panel_escapes_what_the_venue_sends() -> None:
    """Court and booking titles come from Skedda and land in HTML."""
    source = PANEL_JS.read_text(encoding="utf-8")

    assert "function esc(" in source
    assert "innerHTML" in source
    # Every interpolation of remote data goes through esc(); spot-check the
    # ones that carry venue text.
    for field in ("booking.court", "job.court", "account.title"):
        assert f"esc({field})" in source


def test_the_panel_can_release_a_booking_and_nothing_else() -> None:
    """One action of its own; everything else opens Home Assistant's pages."""
    source = PANEL_JS.read_text(encoding="utf-8")

    assert "skedda_scheduler/cancel_booking" in source
    assert "confirmCancel" in source
    assert "/config/integrations/integration/skedda_scheduler" in source

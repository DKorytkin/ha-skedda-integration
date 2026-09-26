"""The sidebar panel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import DOMAIN
from custom_components.skedda_scheduler.panel import (
    PANEL_URL,
    SCRIPT_URL,
    async_register_panel,
    script_version,
)
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


async def test_accounts_set_up_together_register_the_panel_once(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """At startup Home Assistant sets every account up at once, so the second
    one reaches the registration while the first is still inside it."""
    mock_entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, data=dict(mock_entry.data), entry_id="entry-2")
    other.add_to_hass(hass)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED
    assert other.state is ConfigEntryState.LOADED
    assert PANEL_URL in hass.data[frontend.DATA_PANELS]


async def test_a_failed_registration_is_tried_again(hass: HomeAssistant) -> None:
    """Otherwise one bad start leaves the sidebar empty until a restart."""
    await async_setup_component(hass, "http", {})
    with (
        patch.object(
            hass.http, "async_register_static_paths", side_effect=OSError("disk"), autospec=True
        ),
        pytest.raises(OSError),
    ):
        await async_register_panel(hass)

    await async_register_panel(hass)

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


async def test_the_script_url_changes_when_the_script_does(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider: AsyncMock
) -> None:
    """Otherwise an update ships a panel nobody's browser will fetch.

    The module url is what the frontend caches against; holding it still means
    yesterday's panel keeps rendering however many times Home Assistant
    restarts.
    """
    await setup_with_job(hass, mock_entry)

    panel = hass.data[frontend.DATA_PANELS][PANEL_URL]
    module_url = panel.config["_panel_custom"]["module_url"]

    assert module_url.startswith(SCRIPT_URL)
    assert "?v=" in module_url
    assert module_url.split("?v=")[1] == script_version()


def test_the_panel_lists_every_account_rather_than_the_first() -> None:
    """With two people booking from one Home Assistant, whose hour is spent is
    the first question, and each account needs its own way in."""
    source = PANEL_JS.read_text(encoding="utf-8")

    assert (
        "accounts\n        .map(" in source
        or "accounts\n    .map(" in source
        or "accounts.map(" in source
    )
    assert "flex-direction: column" in source
    assert "t.manage" in source


def test_the_jobs_card_offers_one_way_to_manage_them() -> None:
    """Offered next to the bookings it read as "book a court by hand", which
    Skedda's own site does better. And a link per row led to the same page as
    the button above it - two ways to one place read as two places."""
    source = PANEL_JS.read_text(encoding="utf-8")

    jobs_card = source.split("t.bookingJobs,")[1]
    assert "manageJobs" in jobs_card
    bookings_card = source.split("t.existingBookings,")[1].split("t.bookingJobs,")[0]
    assert "manageJobs" not in bookings_card
    # One per account row, one for the jobs card, one for the empty state.
    assert source.count('href="${SETTINGS_URL}"') == 3
    assert "t.edit" not in source


def test_the_panel_shows_what_is_being_watched_for() -> None:
    """The third block: rules, their state, and what they last caught."""
    source = PANEL_JS.read_text(encoding="utf-8")

    assert "function watchRow(" in source
    assert "esc(watch.name)" in source
    assert "Полювання за слотами" in source
    assert "watches" in source


def test_the_panel_survives_an_instance_with_no_watch() -> None:
    """The key is absent until a watch exists; a card must not explode."""
    source = PANEL_JS.read_text(encoding="utf-8")

    assert "(watches || [])" in source


def test_each_job_row_says_which_job_it_is() -> None:
    """Five rows of "Tennis (weekly)" do not say which is Sunday's and which is
    Tuesday's; the name the job was given does."""
    source = PANEL_JS.read_text(encoding="utf-8")

    job_row = source.split("function jobRow(")[1].split("\nfunction ")[0]
    assert "esc(job.name)" in job_row
    jobs_card = source.split("t.bookingJobs,")[1].split("t.noJobs")[0]
    assert "t.job," in jobs_card
    for language in ("en", "uk"):
        strings = source.split(f"  {language}: {{")[1].split("\n  },")[0]
        assert "\n    job: " in strings, f"no heading for the job in {language}"


def test_bookings_are_grouped_by_day() -> None:
    """Twelve rows that each repeat a date and a court read as a wall; a day
    heading says the date once, and today and tomorrow say so."""
    source = PANEL_JS.read_text(encoding="utf-8")

    bookings_card = source.split("t.existingBookings,")[1].split("t.bookingJobs,")[0]
    assert "bookingRows(" in bookings_card
    assert "t.when" not in bookings_card, "a day heading replaces the column headings"
    rows = source.split("function bookingRows(")[1].split("\nfunction ")[0]
    assert "dayRow(" in rows
    day = source.split("function dayRow(")[1].split("\nfunction ")[0]
    assert "<strong>" in day, "today and tomorrow stand out"
    for language in ("en", "uk"):
        strings = source.split(f"  {language}: {{")[1].split("\n  },")[0]
        assert "\n    today: " in strings and "\n    tomorrow: " in strings, language


def test_the_court_is_shown_only_when_it_tells_bookings_apart() -> None:
    """At a venue with one court the column is the same word on every row."""
    source = PANEL_JS.read_text(encoding="utf-8")

    rows = source.split("function bookingRows(")[1].split("\nfunction ")[0]
    assert "new Set(" in rows and ".court" in rows
    row = source.split("function bookingRow(")[1].split("\nfunction ")[0]
    assert "showCourt ?" in row


def test_releasing_a_booking_is_an_icon_that_still_says_what_it_does() -> None:
    """A dozen blue "Cancel" links outshone the bookings themselves."""
    source = PANEL_JS.read_text(encoding="utf-8")

    row = source.split("function bookingRow(")[1].split("\nfunction ")[0]
    assert 'aria-label="${esc(t.cancel)}"' in row
    assert 'title="${esc(t.cancel)}"' in row
    assert ">${esc(t.cancel)}</button>" not in row
    # button.link comes later in the stylesheet; one class alone lost to it and
    # the quiet icon came out in the link colour.
    assert "button.link.icon {" in source

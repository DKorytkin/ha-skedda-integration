"""Validating and normalising one Skedda account."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..api.client import SkeddaClient
from ..api.errors import AuthExpiredError
from ..api.models import SkeddaCredentials
from ..const import CONF_ALIAS, CONF_VENUE
from ..core.provider import VenueRules
from ..skedda_provider import SkeddaProvider

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_VENUE): str,
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_ALIAS, default=""): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


def unique_id_for(venue: str, email: str) -> str:
    """Identify an account the way a person would: this login, at this venue.

    Case-folded because Skedda treats neither as case sensitive, and because
    "MyClub" and "myclub" added twice would fight over the same quota.
    """
    return f"{venue.strip().lower()}:{email.strip().lower()}"


def normalise(user_input: dict[str, str]) -> dict[str, str]:
    """Trim what the user typed and lowercase the subdomain.

    The venue becomes a hostname, so its case is not ours to keep. The email is
    only trimmed: it is shown back to the user, and some mail systems do care.
    """
    data = {key: value.strip() for key, value in user_input.items()}
    data[CONF_VENUE] = data[CONF_VENUE].lower()
    return data


async def validate_credentials(
    hass: HomeAssistant, venue: str, email: str, password: str
) -> VenueRules:
    """Prove the account can actually reach this venue, and read its rules.

    Signing in is not enough on its own. Skedda authenticates centrally on
    app.skedda.com, so a typo in the subdomain would log in perfectly happily
    and only surface days later as a booking that never fires. Asking the venue
    host for its own settings is what proves the pairing, and it returns the
    timezone and limits the scheduler needs anyway.
    """
    client = SkeddaClient(
        async_get_clientsession(hass),
        SkeddaCredentials(venue=venue, email=email, password=password),
    )
    provider = SkeddaProvider(client)
    await provider.authenticate()
    try:
        return await provider.venue_settings()
    except AuthExpiredError:
        # Observed 2026-09-15: /webs answered 422 immediately after a
        # successful login, and succeeded on a second attempt - the venue
        # session had not taken effect yet. Telling someone their password is
        # wrong when signing in again is the fix would be both incorrect and
        # maddening, so do by hand what they would have done themselves.
        await provider.authenticate()
        return await provider.venue_settings()

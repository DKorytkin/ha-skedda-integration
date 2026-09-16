"""Where Google's OAuth lives.

Home Assistant keeps the client id and secret itself, in its own credentials
store; this only tells it which endpoints to talk to and where the user goes to
create the credential.
"""

from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

AUTHORIZATION_SERVER = AuthorizationServer(
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
)


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    return AUTHORIZATION_SERVER


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Links for the dialog that asks for the credential.

    Creating an OAuth client is the one part of this nobody can automate, so
    the least we can do is point at the exact pages.
    """
    try:
        redirect_url = config_entry_oauth2_flow.async_get_redirect_uri(hass)
    except RuntimeError:
        # Only knowable from inside a request when My Home Assistant is not
        # set up. Naming the usual one beats naming none.
        redirect_url = config_entry_oauth2_flow.MY_AUTH_CALLBACK_PATH
    return {
        "oauth_consent_url": "https://console.cloud.google.com/apis/credentials/consent",
        "oauth_creds_url": "https://console.cloud.google.com/apis/credentials",
        "api_library_url": "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
        "redirect_url": redirect_url,
    }

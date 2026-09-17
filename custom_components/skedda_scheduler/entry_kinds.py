"""Which kind of entry this is.

One domain holds more than one: an account with credentials, and the Google
calendar link. Only an account carries runtime data, and code that forgets
that crashes the moment a second kind is set up.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry

from .const import CONF_ENTRY_KIND, ENTRY_KIND_ACCOUNT


def entry_kind(entry: ConfigEntry) -> str:
    """The kind an entry declares, defaulting to the one that predates the key."""
    return str(entry.data.get(CONF_ENTRY_KIND, ENTRY_KIND_ACCOUNT))


def is_account_entry(entry: ConfigEntry) -> bool:
    return entry_kind(entry) == ENTRY_KIND_ACCOUNT

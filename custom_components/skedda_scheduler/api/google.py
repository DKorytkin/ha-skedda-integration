"""Transport for the Google Calendar API.

Here rather than in the Home Assistant layer for the same reason the Skedda
client is: this file knows URLs and wire shapes, and nothing else. It is given
a way to get an access token and never learns where that came from.

Home Assistant's own calendar service cannot invite anyone - neither
`calendar.create_event` nor `google.add_event` accepts attendees - which is why
this exists at all.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from .errors import ApiContractError, SkeddaAuthError, SkeddaConnectionError, SkeddaError

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/calendar/v3"

#: Creating events, and reading the calendar list so the user can pick one.
SCOPES = (
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/calendar.readonly"
)

#: Ask Google to email every attendee. Without it the event is created and
#: nobody is told, which is the whole reason for going direct.
SEND_UPDATES = "all"


@dataclass(frozen=True, slots=True)
class GoogleCalendar:
    """One calendar the account can write to."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class GoogleEvent:
    """An event as Google echoed it back."""

    id: str
    html_link: str | None


class GoogleCalendarClient:
    """Creates events, with attendees, in a Google calendar."""

    def __init__(
        self,
        http: aiohttp.ClientSession,
        token_provider: Callable[[], Awaitable[str]],
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http
        self._token = token_provider
        self._base_url = base_url

    async def list_calendars(self) -> list[GoogleCalendar]:
        """Calendars this account may write to.

        Read-only ones are dropped: offering a calendar that will refuse every
        event is worse than not offering it.
        """
        body = await self._request("GET", "/users/me/calendarList")
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ApiContractError("calendarList carried no items")
        return [
            GoogleCalendar(id=str(item["id"]), name=str(item.get("summary", item["id"])))
            for item in items
            if isinstance(item, dict)
            and "id" in item
            and item.get("accessRole") in ("owner", "writer")
        ]

    async def create_event(
        self,
        calendar_id: str,
        *,
        summary: str,
        start: datetime,
        end: datetime,
        timezone: str,
        location: str | None = None,
        description: str | None = None,
        attendees: tuple[str, ...] = (),
        color_id: str | None = None,
    ) -> GoogleEvent:
        """Put the booking in the calendar and invite whoever should come."""
        payload: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone},
        }
        if location:
            payload["location"] = location
        if description:
            payload["description"] = description
        if attendees:
            payload["attendees"] = [{"email": email} for email in attendees]
        if color_id:
            # Google's own palette, by number: the API takes no colour names
            # and no hex values.
            payload["colorId"] = color_id

        body = await self._request(
            "POST",
            f"/calendars/{calendar_id}/events",
            params={"sendUpdates": SEND_UPDATES} if attendees else None,
            json_body=payload,
        )
        if not isinstance(body, dict) or "id" not in body:
            raise ApiContractError("the created event carried no id")
        return GoogleEvent(id=str(body["id"]), html_link=body.get("htmlLink"))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        token = await self._token()
        try:
            async with self._http.request(
                method,
                self._base_url + path,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json_body,
            ) as response:
                body = await self._read(response)
                if response.status in (401, 403):
                    # The token is the only credential here; nothing else can
                    # be wrong in a way a retry would fix.
                    raise SkeddaAuthError(_detail(body) or "Google rejected the token")
                if response.status >= 400:
                    raise ApiContractError(
                        _detail(body) or f"{method} {path} failed with status {response.status}"
                    )
                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SkeddaConnectionError(f"{method} {path} failed: {err}") from err

    @staticmethod
    async def _read(response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except ValueError, aiohttp.ClientError:
            _LOGGER.debug("Google answered %s with something other than JSON", response.url)
            return None


def _detail(body: Any) -> str | None:
    """Google's error shape: {"error": {"message": ...}}."""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
    return None


__all__ = [
    "BASE_URL",
    "SCOPES",
    "GoogleCalendar",
    "GoogleCalendarClient",
    "GoogleEvent",
    "SkeddaError",
]

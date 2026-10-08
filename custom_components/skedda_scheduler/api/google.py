"""Transport for the Google Calendar API.

Here rather than in the Home Assistant layer for the same reason the Skedda
client is: this file knows URLs and wire shapes, and nothing else. It is given
a way to get an access token and never learns where that came from.

Home Assistant's own calendar service cannot invite anyone - neither
`calendar.create_event` nor `google.add_event` accepts attendees - which is why
this exists at all.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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


#: Google answers this when an event id is already in use, deleted or not.
_STATUS_CONFLICT = 409
#: Gone for good, or never there: either way there is nothing left to delete.
_STATUS_ABSENT = frozenset({404, 410})


@dataclass(frozen=True, slots=True)
class GoogleEvent:
    """An event as Google echoed it back."""

    id: str
    html_link: str | None


@dataclass(frozen=True, slots=True)
class GoogleListedEvent:
    """An event found by time, for when its id was never recorded."""

    id: str
    summary: str
    start: datetime | None
    description: str


def event_id_for(calendar_id: str, space_id: str, start: datetime) -> str:
    """The id a booking's event gets, worked out rather than remembered.

    Google lets the caller choose the id (base32hex, 5-1024 characters; hex
    digits are a subset). Choosing it from the booking means a retried insert
    that had in fact landed is refused instead of duplicated, and a cancelled
    booking can find its event again without anything having been stored.
    """
    key = f"{calendar_id}|{space_id}|{start.astimezone(UTC).isoformat()}"
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()


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
        event_id: str | None = None,
    ) -> GoogleEvent:
        """Put the booking in the calendar and invite whoever should come.

        With an `event_id`, an id already taken - by this same event from an
        earlier try, or by one cancelled since - is overwritten rather than
        refused: the booking exists now, so its event should too.
        """
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

        params = {"sendUpdates": SEND_UPDATES} if attendees else None
        path = f"/calendars/{calendar_id}/events"
        if event_id is None:
            body = await self._request("POST", path, params=params, json_body=payload)
        else:
            status, body = await self._send(
                "POST", path, params=params, json_body={**payload, "id": event_id}
            )
            if status == _STATUS_CONFLICT:
                # A cancelled event keeps its id; "confirmed" brings it back.
                body = await self._request(
                    "PUT",
                    f"{path}/{event_id}",
                    params=params,
                    json_body={**payload, "status": "confirmed"},
                )
            else:
                self._raise_for(status, body, "POST", path)
        if not isinstance(body, dict) or "id" not in body:
            raise ApiContractError("the created event carried no id")
        return GoogleEvent(id=str(body["id"]), html_link=body.get("htmlLink"))

    async def delete_event(self, calendar_id: str, event_id: str, *, notify: bool) -> bool:
        """Remove an event; False when there was none to remove.

        `notify` tells the guests it is off - the same email that told them
        it was on.
        """
        path = f"/calendars/{calendar_id}/events/{event_id}"
        status, body = await self._send(
            "DELETE", path, params={"sendUpdates": SEND_UPDATES} if notify else None
        )
        if status in _STATUS_ABSENT:
            return False
        self._raise_for(status, body, "DELETE", path)
        return True

    async def events_between(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[GoogleListedEvent]:
        """Events overlapping this interval, cancelled ones left out."""
        body = await self._request(
            "GET",
            f"/calendars/{calendar_id}/events",
            params={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
            },
        )
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ApiContractError("the event list carried no items")
        return [
            GoogleListedEvent(
                id=str(item["id"]),
                summary=str(item.get("summary") or ""),
                start=_event_start(item),
                description=str(item.get("description") or ""),
            )
            for item in items
            if isinstance(item, dict) and "id" in item
        ]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        status, body = await self._send(method, path, params=params, json_body=json_body)
        self._raise_for(status, body, method, path)
        return body

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """The status and body, whatever they are. Only the network raises."""
        token = await self._token()
        try:
            async with self._http.request(
                method,
                self._base_url + path,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json_body,
            ) as response:
                return response.status, await self._read(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SkeddaConnectionError(f"{method} {path} failed: {err}") from err

    @staticmethod
    def _raise_for(status: int, body: Any, method: str, path: str) -> None:
        if status in (401, 403):
            # The token is the only credential here; nothing else can be
            # wrong in a way a retry would fix.
            raise SkeddaAuthError(_detail(body) or "Google rejected the token")
        detail = _detail(body) or f"{method} {path} failed with status {status}"
        if status == 429 or status >= 500:
            # Google's side, and passing: the same as a network that is down.
            raise SkeddaConnectionError(detail)
        if status >= 400:
            raise ApiContractError(detail)

    @staticmethod
    async def _read(response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except ValueError, aiohttp.ClientError:
            _LOGGER.debug("Google answered %s with something other than JSON", response.url)
            return None


def _event_start(item: dict[str, Any]) -> datetime | None:
    start = item.get("start")
    raw = start.get("dateTime") if isinstance(start, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
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
    "GoogleListedEvent",
    "SkeddaError",
    "event_id_for",
]

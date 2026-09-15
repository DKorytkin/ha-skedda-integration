"""Shared pytest fixtures.

The FakeSkedda server below replaces `aioresponses`, whose current release
(0.7.9) is incompatible with the aiohttp 3.14 that Home Assistant pins - it
fails with "ClientResponse.__init__() missing 1 required keyword-only
argument". Driving a real local server is a better trade anyway: status codes,
204 bodies, Date headers and JSON decoding all behave exactly as they will in
production, instead of as a mock library imagines.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.const import (
    CONF_ALIAS,
    CONF_VENUE,
    CONF_VENUE_TIMEZONE,
    DOMAIN,
)
from custom_components.skedda_scheduler.core.provider import Booking, Space, VenueRules


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    query: dict[str, str]
    json: Any


@dataclass
class Stub:
    status: int = 200
    json: Any = None
    text: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    exception: bool = False


class FakeSkedda:
    """Routes requests by (method, path) to canned responses."""

    def __init__(self) -> None:
        self.stubs: dict[tuple[str, str], Stub] = {}
        self.queued: dict[tuple[str, str], deque[Stub]] = defaultdict(deque)
        self.requests: list[Recorded] = []

    def stub(self, method: str, path: str, **kwargs: Any) -> None:
        self.stubs[(method.upper(), path)] = Stub(**kwargs)

    def stub_once(self, method: str, path: str, **kwargs: Any) -> None:
        """Answer the next call to this path this way, then fall back.

        For testing retries: the first response differs from the ones after it.
        """
        self.queued[(method.upper(), path)].append(Stub(**kwargs))

    def requests_for(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method.upper() and r.path == path]

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        body: Any = None
        if request.can_read_body:
            try:
                body = await request.json()
            except ValueError:
                body = await request.text()
        self.requests.append(
            Recorded(
                method=request.method,
                path=request.path,
                headers=dict(request.headers),
                query=dict(request.query),
                json=body,
            )
        )
        key = (request.method, request.path)
        queue = self.queued.get(key)
        stub = queue.popleft() if queue else self.stubs.get(key)
        if stub is None:
            return web.json_response({"unstubbed": request.path}, status=404)
        if stub.exception:
            raise web.HTTPInternalServerError
        if stub.status == 204:
            return web.Response(status=204, headers=stub.headers)
        if stub.text is not None:
            return web.Response(
                status=stub.status,
                text=stub.text,
                content_type="text/html",
                headers=stub.headers,
            )
        return web.json_response(stub.json, status=stub.status, headers=stub.headers)


@pytest.fixture
async def skedda(
    monkeypatch: pytest.MonkeyPatch, socket_enabled: None
) -> AsyncIterator[FakeSkedda]:
    """Start a local server and point the endpoint module at it.

    `socket_enabled` lifts pytest-homeassistant-custom-component's socket ban,
    which exists to stop tests reaching the internet. These sockets are
    loopback-only, to a server this fixture starts and stops.
    """
    fake = FakeSkedda()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake._handle)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    monkeypatch.setattr(endpoints, "LOGIN_HOST", base)
    monkeypatch.setattr(endpoints, "base_url", lambda venue: base)
    try:
        yield fake
    finally:
        await server.close()


@pytest.fixture
async def http(socket_enabled: None) -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: object) -> None:
    """Let Home Assistant load custom_components during tests."""
    return None


VENUE_RULES = VenueRules(
    timezone="Europe/Kyiv",
    slot_minutes=60,
    max_days_ahead=14,
    weekly_quota_minutes=60,
)

SPACES = (Space(id="2000001", name="Court 1"), Space(id="2000002", name="Court 2"))

#: What a successful booking looks like coming back from the venue. The fake
#: provider returns a real one because arming a job fires for real: a mock's
#: attribute would reach the history store and fail to serialise.
BOOKED = Booking(
    id="bk-fixture",
    space_ids=("2000001",),
    start=datetime(2026, 9, 29, 18, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
    end=datetime(2026, 9, 29, 19, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
    title="Tennis (auto)",
)

ENTRY_DATA = {
    CONF_VENUE: "myclub",
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_ALIAS: "Main account (Oleh)",
    CONF_VENUE_TIMEZONE: "Europe/Kyiv",
}


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        title="Main account (Oleh)",
        unique_id="myclub:user@example.com",
        entry_id="entry-1",
    )


@pytest.fixture
def mock_provider() -> Iterator[AsyncMock]:
    """Patch SkeddaProvider everywhere the integration constructs one."""
    provider = AsyncMock()
    provider.is_authenticated = True
    # Mirrors the captured venue: hour-long slots, a fortnight's horizon and an
    # hour a week. Defaults that match reality catch the mistakes that matter.
    provider.venue_settings.return_value = VENUE_RULES
    provider.list_spaces.return_value = list(SPACES)
    provider.list_bookings.return_value = []
    provider.book.return_value = BOOKED
    # The real provider exposes a synchronous client carrying the clock
    # estimate; an AsyncMock here would hand back coroutines instead of times.
    provider.client = MagicMock()
    provider.client.clock.local_instant_for.side_effect = lambda instant: instant
    with patch("custom_components.skedda_scheduler.SkeddaProvider", return_value=provider):
        yield provider

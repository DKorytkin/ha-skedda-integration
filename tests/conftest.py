"""Shared pytest fixtures.

The FakeSkedda server below replaces `aioresponses`, whose current release
(0.7.9) is incompatible with the aiohttp 3.14 that Home Assistant pins - it
fails with "ClientResponse.__init__() missing 1 required keyword-only
argument". Driving a real local server is a better trade anyway: status codes,
204 bodies, Date headers and JSON decoding all behave exactly as they will in
production, instead of as a mock library imagines.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.skedda_scheduler.api import endpoints


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
        self.requests: list[Recorded] = []

    def stub(self, method: str, path: str, **kwargs: Any) -> None:
        self.stubs[(method.upper(), path)] = Stub(**kwargs)

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
        stub = self.stubs.get((request.method, request.path))
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

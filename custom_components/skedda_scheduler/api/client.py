"""Async HTTP client for Skedda's private web API.

Responsibilities, deliberately narrow:

* follow Skedda's authentication dance (scrape token -> post credentials);
* attach the antiforgery header to every request;
* classify failures into the taxonomy in errors.py;
* feed every response's Date header to the clock estimator.

It does not know what a booking job is, when a window opens, or how to retry.
Those decisions belong to core/ and to the scheduler.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import aiohttp

from . import endpoints
from .clock import ClockSync, parse_date_header
from .errors import (
    ApiContractError,
    AuthExpiredError,
    SkeddaAuthError,
    SkeddaConnectionError,
)
from .models import SkeddaCredentials, SkeddaSession

_LOGGER = logging.getLogger(__name__)

# The token sits in a hidden input in the page HTML of whichever host is being
# called. Parsing it with a regex rather than an HTML parser keeps the transport
# layer dependency-free; the markup is a single well-formed input tag, and a
# miss raises ApiContractError rather than silently proceeding.
_TOKEN_INPUT = re.compile(rf'name="{endpoints.ANTIFORGERY_INPUT}"[^>]*\bvalue="([^"]+)"')

# Endpoints that only read. A 422 from one of these cannot be a booking-rule
# violation - see the contract doc, "422 is not only a booking-rule status".
_READ_PATHS = frozenset({endpoints.SPACES.path, endpoints.BOOKINGS_LIST.path})


class SkeddaClient:
    """One authenticated conversation with one Skedda venue."""

    def __init__(self, http: aiohttp.ClientSession, credentials: SkeddaCredentials) -> None:
        self._http = http
        self._credentials = credentials
        self._session: SkeddaSession | None = None
        self.clock = ClockSync()

    @property
    def is_authenticated(self) -> bool:
        return self._session is not None

    @property
    def session(self) -> SkeddaSession | None:
        return self._session

    async def authenticate(self) -> SkeddaSession:
        """Sign in and remember the session.

        The token is scraped from the login page before the credentials are
        posted: Skedda issues it with the page, not with a successful login.
        """
        token = await self._fetch_antiforgery_token(
            endpoints.LOGIN_HOST + endpoints.LOGIN_PAGE.path
        )
        payload = endpoints.login_payload(self._credentials.email, self._credentials.password)
        status, body, _ = await self._send(
            endpoints.LOGIN,
            host=endpoints.LOGIN_HOST,
            token=token,
            json_body=payload,
        )
        if status in (401, 403):
            self._session = None
            raise SkeddaAuthError("Skedda rejected the credentials")
        failure = endpoints.classify_error(status, body)
        if failure is not None:
            raise failure(endpoints.error_detail(body) or f"login failed with status {status}")
        self._session = SkeddaSession(
            # The auth cookie is HttpOnly and handled by aiohttp's jar; this
            # mapping exists for diagnostics, not for sending.
            cookies={c.key: "***" for c in self._http.cookie_jar},
            # NOT from a response header: scraped above, host-scoped, and
            # re-issued on every page load.
            antiforgery_token=token,
            expires_at=None,
        )
        return self._session

    async def request(
        self,
        endpoint: endpoints.Endpoint,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        """Issue an authenticated request against the venue host."""
        if self._session is None:
            raise AuthExpiredError("not authenticated; call authenticate() first")
        status, body, headers = await self._send(
            endpoint,
            host=endpoints.base_url(self._credentials.venue),
            token=self._session.antiforgery_token,
            params=params,
            json_body=json_body,
        )
        failure = self._classify(endpoint, status, body)
        if failure is not None:
            self._forget_session_if_stale(failure)
            raise failure(
                endpoints.error_detail(body)
                or f"{endpoint.method} {endpoint.path} failed with status {status}"
            )
        return status, body, headers

    def _classify(
        self, endpoint: endpoints.Endpoint, status: int, body: Any
    ) -> type[Exception] | None:
        failure = endpoints.classify_error(status, body)
        if failure is ApiContractError and status == 422 and endpoint.path in _READ_PATHS:
            # A read cannot violate a booking rule. Observed 2026-09-15: /webs
            # answers 422 when the venue session has not taken effect. Calling
            # that a contract error would send the user hunting for an API
            # change instead of signing in again.
            return AuthExpiredError
        return failure

    def _forget_session_if_stale(self, failure: type[Exception]) -> None:
        if issubclass(failure, AuthExpiredError):
            self._session = None

    async def _fetch_antiforgery_token(self, url: str) -> str:
        status, text, _ = await self._get_text(url)
        match = _TOKEN_INPUT.search(text)
        if match is None:
            raise ApiContractError(
                f"no antiforgery token in the page at {url} "
                f"(status {status}); Skedda's login page has changed"
            )
        return match.group(1)

    async def _get_text(self, url: str) -> tuple[int, str, dict[str, str]]:
        sent = datetime.now(UTC)
        try:
            async with self._http.get(url) as response:
                text = await response.text()
                headers = dict(response.headers)
                self._observe_clock(headers, sent)
                return response.status, text, headers
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SkeddaConnectionError(f"GET {url} failed: {err}") from err

    async def _send(
        self,
        endpoint: endpoints.Endpoint,
        *,
        host: str,
        token: str | None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        url = host + endpoint.path
        headers = {endpoints.ANTIFORGERY_HEADER: token} if token else {}
        sent = datetime.now(UTC)
        try:
            async with self._http.request(
                endpoint.method, url, headers=headers, params=params, json=json_body
            ) as response:
                body = await self._read_json(response)
                response_headers = dict(response.headers)
                self._observe_clock(response_headers, sent)
                return response.status, body, response_headers
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SkeddaConnectionError(f"{endpoint.method} {url} failed: {err}") from err

    @staticmethod
    async def _read_json(response: aiohttp.ClientResponse) -> Any:
        """Return the decoded body, or None when there is not one.

        204 carries no body, and a proxy may answer with HTML. Neither is worth
        raising over here: the caller classifies on status first.
        """
        if response.status == 204:
            return None
        try:
            return await response.json(content_type=None)
        except ValueError, aiohttp.ClientError:
            _LOGGER.debug("Response from %s was not JSON", response.url)
            return None

    def _observe_clock(self, headers: dict[str, str], sent: datetime) -> None:
        raw = headers.get("Date")
        if raw is None:
            return
        try:
            server_time = parse_date_header(raw)
        except TypeError, ValueError:
            _LOGGER.debug("Unparseable Date header: %r", raw)
            return
        self.clock.observe(server_time, sent, datetime.now(UTC))

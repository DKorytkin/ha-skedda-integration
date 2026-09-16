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
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp

from . import endpoints
from .clock import ClockSync, parse_date_header
from .errors import (
    ApiContractError,
    AuthExpiredError,
    SignInBlockedError,
    SkeddaAuthError,
    SkeddaConnectionError,
)
from .models import (
    SkeddaBooking,
    SkeddaBookingRequest,
    SkeddaCredentials,
    SkeddaIdentity,
    SkeddaSession,
    SkeddaSpace,
    SkeddaVenue,
)

_LOGGER = logging.getLogger(__name__)

# The token sits in a hidden input in the page HTML of whichever host is being
# called. Parsing it with a regex rather than an HTML parser keeps the transport
# layer dependency-free; the markup is a single well-formed input tag, and a
# miss raises ApiContractError rather than silently proceeding.
_TOKEN_INPUT = re.compile(rf'name="{endpoints.ANTIFORGERY_INPUT}"[^>]*\bvalue="([^"]+)"')

# Endpoints that only read. A 422 from one of these cannot be a booking-rule
# violation - see the contract doc, "422 is not only a booking-rule status".
_READ_PATHS = frozenset({endpoints.SPACES.path, endpoints.BOOKINGS_LIST.path})

#: Skedda's own words when it declines to process a sign-in at all. Matching
#: prose is unpleasant, but the alternative is reporting a working password as
#: wrong, and this response carries nothing else to go on.
_SECURITY_REFUSAL = "potential security problem"

#: How long one fetch of /webs stands in for the next. The venue's rules,
#: courts and our own ids all arrive in that one payload and are read
#: separately, so without this every poll asked for the same page twice.
WEBS_CACHE_SECONDS = 30.0


class SkeddaClient:
    """One authenticated conversation with one Skedda venue."""

    def __init__(self, http: aiohttp.ClientSession, credentials: SkeddaCredentials) -> None:
        self._http = http
        self._credentials = credentials
        self._session: SkeddaSession | None = None
        self._identity: SkeddaIdentity | None = None
        #: The venue host issues its own antiforgery token; the sign-in host's
        #: is not accepted there. Cached for the session: fetching a page
        #: before every request would double the cost of a burst.
        self._venue_token: str | None = None
        self._webs: dict[str, Any] | None = None
        self._webs_fetched_at = 0.0
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
        # Skedda refuses a sign-in attempted while an old session is still
        # held - "our super detectives found a potential security problem".
        # A reloaded entry builds a new client over the same cookie jar, so
        # whatever the last one left behind has to go first.
        self._http.cookie_jar.clear()
        self._session = None
        self._identity = None
        self._venue_token = None
        self._webs = None
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
            self._session = None
            detail = endpoints.error_detail(body) or f"login failed with status {status}"
            if failure is ApiContractError and _SECURITY_REFUSAL in detail.lower():
                # Not the credentials: Skedda declined to process the attempt.
                # Asking for a new password would send the user to change one
                # that works.
                raise SignInBlockedError(detail)
            if failure is ApiContractError:
                # Captured live 2026-09-15: a wrong password comes back as 422
                # carrying the same error envelope as a booking-rule violation.
                # A sign-in request cannot violate a booking rule, so the only
                # thing an unrecognised rejection here can mean is that the
                # credentials were not accepted - and telling the user their
                # API changed would send them hunting for someone else's bug.
                raise SkeddaAuthError(detail)
            raise failure(detail)
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
            token=await self._venue_antiforgery_token(),
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

    async def list_spaces(self) -> list[SkeddaSpace]:
        """Return the venue's bookable spaces. Skedda calls these "assets"."""
        payload = await self._fetch_webs()
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise ApiContractError(f"/webs carried no 'assets' list; keys {sorted(payload)}")
        return [SkeddaSpace.from_payload(item) for item in assets]

    async def venue_settings(self) -> SkeddaVenue:
        """Return the venue rules the scheduler needs: timezone, window, quota."""
        payload = await self._fetch_webs()
        venues = payload.get("venue")
        if not isinstance(venues, list) or not venues:
            raise ApiContractError(f"/webs carried no 'venue' entry; keys {sorted(payload)}")
        return SkeddaVenue.from_payload(venues[0])

    async def identity(self) -> SkeddaIdentity:
        """Our venue and membership ids, read once per session from /webs.

        Cached because every booking needs them and the burst loop cannot
        afford a round trip at the instant a window opens.
        """
        if self._identity is None:
            self._identity = SkeddaIdentity.from_payload(await self._fetch_webs())
        return self._identity

    async def create_booking(self, request: SkeddaBookingRequest) -> SkeddaBooking:
        identity = await self.identity()
        _, body, _ = await self.request(
            endpoints.BOOKINGS,
            json_body=endpoints.booking_payload(request, identity),
        )
        return self._unwrap_booking(body)

    async def list_bookings(self, start: datetime, end: datetime) -> list[SkeddaBooking]:
        """List bookings in a window. Times are venue-local and timezone-aware."""
        _, body, _ = await self.request(
            endpoints.BOOKINGS_LIST, params=endpoints.list_bookings_params(start, end)
        )
        bookings = body.get("bookings") if isinstance(body, dict) else None
        if not isinstance(bookings, list):
            raise ApiContractError(
                "/bookingslists carried no 'bookings' list; "
                f"keys {sorted(body) if isinstance(body, dict) else type(body)}"
            )
        return [SkeddaBooking.from_payload(item) for item in bookings]

    async def cancel_booking(self, booking_id: str) -> None:
        """Cancel a booking. Confirmed: DELETE answers 204 with no body."""
        await self.request(
            endpoints.Endpoint(endpoints.BOOKING_CANCEL.method, endpoints.booking_path(booking_id))
        )

    async def _fetch_webs(self) -> dict[str, Any]:
        """The venue page, fetched at most once every WEBS_CACHE_SECONDS.

        Rules, courts and our own ids all come from it, and a refresh reads
        all three. Asking three times for one answer is rude to a service that
        never invited us.
        """
        now = time.monotonic()
        if self._webs is not None and now - self._webs_fetched_at < WEBS_CACHE_SECONDS:
            return self._webs
        _, body, _ = await self.request(endpoints.SPACES)
        if not isinstance(body, dict):
            raise ApiContractError(f"/webs returned {type(body).__name__}, not an object")
        self._webs = body
        self._webs_fetched_at = now
        return body

    @staticmethod
    def _unwrap_booking(body: Any) -> SkeddaBooking:
        """Read a booking from a create/update response.

        The response envelope was never captured - the successful POST predated
        instrumentation - so both the enveloped and the bare shape are accepted.
        Guessing one would fail at the worst moment: the instant a window opens.
        """
        if not isinstance(body, dict):
            raise ApiContractError(f"booking response was {type(body).__name__}, not an object")
        inner = body.get("booking")
        return SkeddaBooking.from_payload(inner if isinstance(inner, dict) else body)

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
            self._identity = None
            self._venue_token = None

    async def _venue_antiforgery_token(self) -> str | None:
        """The token this venue will accept, read from one of its own pages.

        The contract is explicit that the token is per page load and
        host-scoped. Offering the sign-in host's token to the venue asks one
        server to accept another's credential, and Skedda answers that with a
        page about a "potential security problem" rather than anything a
        client can act on.
        """
        if self._venue_token is not None:
            return self._venue_token
        venue_home = endpoints.base_url(self._credentials.venue) + "/"
        try:
            self._venue_token = await self._fetch_antiforgery_token(venue_home)
        except ApiContractError:
            # A venue that serves no token must not cost the user every
            # booking; the sign-in token is the best remaining guess.
            _LOGGER.debug("No antiforgery token on %s; using the sign-in token", venue_home)
            self._venue_token = self._session.antiforgery_token if self._session else None
        return self._venue_token

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

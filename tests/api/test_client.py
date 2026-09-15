"""The HTTP client: token scraping, authentication, error classification."""

from __future__ import annotations

from datetime import UTC, datetime

import aiohttp
import pytest

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.client import SkeddaClient
from custom_components.skedda_scheduler.api.errors import (
    ApiContractError,
    AuthExpiredError,
    QuotaExceededError,
    RateLimitedError,
    SkeddaAuthError,
    SkeddaConnectionError,
)
from custom_components.skedda_scheduler.api.models import SkeddaCredentials
from tests.conftest import FakeSkedda

CREDS = SkeddaCredentials(venue="myclub", email="user@example.com", password="secret")
TOKEN = "CfDJ8-test-token"
LOGIN_PAGE_HTML = (
    "<html><body><form>"
    '<input name="ReturnUrl" type="hidden" value="https://myclub.skedda.com/">'
    f'<input name="{endpoints.ANTIFORGERY_INPUT}" type="hidden" value="{TOKEN}">'
    "</form></body></html>"
)
LOGIN_OK = {"login": {"id": "7cdf5129d8064d788811a5b15a10955c", "redirectUrl": None}}
DATE = {"Date": "Tue, 15 Sep 2026 11:54:59 GMT"}


#: Skedda answers a successful sign-in with an HttpOnly session cookie; the
#: fake sets one so that anything reading the jar is reading something real.
SESSION_COOKIE = ".AspNet.ApplicationCookie=abc123; Path=/; HttpOnly"


def stub_login(skedda: FakeSkedda, *, status: int = 200) -> None:
    skedda.stub("GET", endpoints.LOGIN_PAGE.path, text=LOGIN_PAGE_HTML, headers=DATE)
    skedda.stub(
        "POST",
        endpoints.LOGIN.path,
        status=status,
        json=LOGIN_OK,
        headers={**DATE, "Set-Cookie": SESSION_COOKIE},
    )


async def authenticated(http: aiohttp.ClientSession, skedda: FakeSkedda) -> SkeddaClient:
    client = SkeddaClient(http, CREDS)
    stub_login(skedda)
    await client.authenticate()
    return client


async def test_authenticate_scrapes_the_token_from_the_login_page(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """The token lives in the page HTML, not in a response header."""
    client = SkeddaClient(http, CREDS)
    stub_login(skedda)
    session = await client.authenticate()
    assert session.antiforgery_token == TOKEN
    assert client.is_authenticated


async def test_authenticate_sends_the_token_as_a_header(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """The input is named __RequestVerificationToken; the header is not."""
    await authenticated(http, skedda)
    sent = skedda.requests_for("POST", endpoints.LOGIN.path)[0]
    assert sent.headers[endpoints.ANTIFORGERY_HEADER] == TOKEN


async def test_authenticate_posts_the_login_envelope(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    await authenticated(http, skedda)
    sent = skedda.requests_for("POST", endpoints.LOGIN.path)[0]
    assert sent.json["login"]["username"] == "user@example.com"
    assert sent.json["login"]["rememberMe"] is True


async def test_authenticate_records_a_clock_sample_per_response(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    assert client.clock.samples == 2


async def test_a_login_page_without_the_token_is_a_contract_error(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """No token means the login page changed; guessing would fail obscurely."""
    client = SkeddaClient(http, CREDS)
    skedda.stub("GET", endpoints.LOGIN_PAGE.path, text="<html>nothing</html>")
    with pytest.raises(ApiContractError, match="antiforgery"):
        await client.authenticate()
    assert not client.is_authenticated


async def test_rejected_credentials_raise_auth_error(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = SkeddaClient(http, CREDS)
    stub_login(skedda, status=403)
    with pytest.raises(SkeddaAuthError):
        await client.authenticate()
    assert not client.is_authenticated


async def test_network_failure_raises_connection_error(
    http: aiohttp.ClientSession,
    skedda: FakeSkedda,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable host must not leak aiohttp's own exception type.

    The scheduler branches on SkeddaConnectionError to decide whether a retry
    is worth attempting, so the transport must never let ClientError through.
    """
    # Port 1 on loopback is never listening, and stays within the socket ban.
    monkeypatch.setattr(endpoints, "LOGIN_HOST", "http://127.0.0.1:1")
    client = SkeddaClient(http, CREDS)
    with pytest.raises(SkeddaConnectionError):
        await client.authenticate()


async def test_request_before_authenticating_is_rejected(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = SkeddaClient(http, CREDS)
    with pytest.raises(AuthExpiredError):
        await client.request(endpoints.SPACES)


async def test_authenticated_request_carries_the_venue_token(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, json={"assets": []}, headers=DATE)
    status, body, _ = await client.request(endpoints.SPACES)
    assert status == 200
    assert body == {"assets": []}
    sent = skedda.requests_for("GET", endpoints.SPACES.path)[0]
    assert sent.headers[endpoints.ANTIFORGERY_HEADER] == TOKEN


async def test_query_parameters_are_passed_through(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.BOOKINGS_LIST.path, json={"bookings": []})
    await client.request(
        endpoints.BOOKINGS_LIST,
        params={"start": "2026-09-28T00:00:00", "end": "2026-09-28T23:59:59.999"},
    )
    sent = skedda.requests_for("GET", endpoints.BOOKINGS_LIST.path)[0]
    assert sent.query["start"] == "2026-09-28T00:00:00"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Date": "soon"}, {"Date": ""}],
    ids=["absent", "unparseable", "empty"],
)
async def test_a_bad_date_header_is_ignored_rather_than_fatal(
    http: aiohttp.ClientSession, headers: dict[str, str]
) -> None:
    """Clock sync is an optimisation; a missing Date must not fail a booking.

    Exercised against the private hook because aiohttp's test server always
    stamps a valid Date of its own, so these branches are unreachable through
    a real response.
    """
    client = SkeddaClient(http, CREDS)
    client._observe_clock(headers, datetime.now(UTC))
    assert client.clock.samples == 0


async def test_a_422_on_a_read_endpoint_is_treated_as_an_expired_session(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Observed 2026-09-15: /webs answered 422 when the venue session was stale.

    Reporting that as a contract error would tell the user Skedda changed its
    API, when the real fix is to sign in again.
    """
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, status=422, json={"errors": [{}]})
    with pytest.raises(AuthExpiredError):
        await client.request(endpoints.SPACES)


async def test_an_expired_session_is_forgotten_so_the_caller_re_authenticates(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, status=401, json={})
    with pytest.raises(AuthExpiredError):
        await client.request(endpoints.SPACES)
    assert not client.is_authenticated


async def test_a_quota_violation_keeps_the_session(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Quota is a rule, not an auth problem; dropping the session would cause
    a pointless re-login on every attempt."""
    client = await authenticated(http, skedda)
    skedda.stub(
        "POST",
        endpoints.BOOKINGS.path,
        status=422,
        json={
            "errors": [
                {
                    "detail": '<span>quota for the week <var data-var="period-start">'
                    '28.09.26</var> to <var data-var="period-end">04.10.26</var>, max '
                    '<var data-var="time-value">1h</var></span>'
                }
            ]
        },
    )
    with pytest.raises(QuotaExceededError, match="quota"):
        await client.request(endpoints.BOOKINGS, json_body={"booking": {}})
    assert client.is_authenticated


async def test_the_error_message_is_stripped_of_markup(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = await authenticated(http, skedda)
    skedda.stub(
        "POST",
        endpoints.BOOKINGS.path,
        status=422,
        json={
            "errors": [
                {
                    "detail": '<span>You can only book at <var data-var="venue">X'
                    '</var> up to <var data-var="duration-days">14</var> day(s) ahead.'
                    "</span>"
                }
            ]
        },
    )
    with pytest.raises(Exception, match="up to 14 day") as caught:
        await client.request(endpoints.BOOKINGS, json_body={"booking": {}})
    assert "<span>" not in str(caught.value)


async def test_a_204_yields_no_body(http: aiohttp.ClientSession, skedda: FakeSkedda) -> None:
    """Cancellation answers 204 with an empty body; json() would explode."""
    client = await authenticated(http, skedda)
    cancel = endpoints.Endpoint("DELETE", endpoints.booking_path("1"))
    skedda.stub("DELETE", cancel.path, status=204)
    status, body, _ = await client.request(cancel)
    assert status == 204
    assert body is None


async def test_a_non_json_body_does_not_crash_the_client(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """An HTML error page from a proxy must not raise a decoding error."""
    client = await authenticated(http, skedda)
    skedda.stub("GET", endpoints.SPACES.path, text="<html>502 Bad Gateway</html>")
    status, body, _ = await client.request(endpoints.SPACES)
    assert status == 200
    assert body is None


async def test_session_without_expiry_never_expires(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    client = SkeddaClient(http, CREDS)
    stub_login(skedda)
    session = await client.authenticate()
    assert not session.is_expired(datetime(2030, 1, 1, tzinfo=UTC))


async def test_stored_cookies_are_masked_for_diagnostics(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Session cookies must never reach a diagnostics dump verbatim."""
    client = await authenticated(http, skedda)
    assert client.session is not None
    assert client.session.cookies, "the fake must hand out a session cookie"
    assert all(value == "***" for value in client.session.cookies.values())


async def test_a_rate_limited_login_is_not_mistaken_for_bad_credentials(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """429 must not trigger a reauth flow; the password is fine, we are early."""
    client = SkeddaClient(http, CREDS)
    stub_login(skedda, status=429)
    with pytest.raises(RateLimitedError):
        await client.authenticate()
    assert not client.is_authenticated


async def test_a_connection_drop_mid_session_is_a_connection_error(
    http: aiohttp.ClientSession, skedda: FakeSkedda, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The venue host can vanish after login; the burst loop needs to retry."""
    client = await authenticated(http, skedda)
    monkeypatch.setattr(endpoints, "base_url", lambda venue: "http://127.0.0.1:1")
    with pytest.raises(SkeddaConnectionError):
        await client.request(endpoints.SPACES)


async def test_a_rejected_login_is_reported_as_bad_credentials_not_a_contract_error(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Captured live 2026-09-15: Skedda answers a wrong password with 422.

    It reuses the same error envelope as a booking-rule violation, so without
    this the user is told the API changed and goes hunting for a bug, when the
    real answer is that they mistyped their password.
    """
    client = SkeddaClient(http, CREDS)
    skedda.stub("GET", endpoints.LOGIN_PAGE.path, text=LOGIN_PAGE_HTML, headers=DATE)
    skedda.stub(
        "POST",
        endpoints.LOGIN.path,
        status=422,
        json={
            "errors": [
                {
                    "source": {"pointer": "/data/attributes/arbitraryerrors"},
                    "detail": "Sorry, your login credentials are not correct. Please "
                    "double-check your email and password. You can use the login-reset "
                    "feature if you have forgotten your password.",
                }
            ]
        },
    )

    with pytest.raises(SkeddaAuthError, match="not correct"):
        await client.authenticate()
    assert not client.is_authenticated


async def test_signing_in_again_starts_from_a_clean_slate(
    http: aiohttp.ClientSession, skedda: FakeSkedda
) -> None:
    """Skedda refuses a login attempted while an old session is still held.

    Observed live 2026-09-16: "our super detectives found a potential security
    problem ... this can happen if you've logged in/out on another tab". A
    reloaded config entry builds a new client over the same cookie jar, so the
    second sign-in must drop what the first one left behind.
    """
    client = SkeddaClient(http, CREDS)
    stub_login(skedda)
    await client.authenticate()
    assert len(http.cookie_jar) > 0

    skedda.stub(
        "POST",
        endpoints.LOGIN.path,
        status=400,
        json={"errors": [{"detail": "a potential security problem"}]},
    )
    seen: list[int] = []
    original = client._fetch_antiforgery_token

    async def watch(url: str) -> str:
        seen.append(len(http.cookie_jar))
        return await original(url)

    client._fetch_antiforgery_token = watch  # type: ignore[method-assign]
    with pytest.raises(SkeddaAuthError):
        await client.authenticate()

    assert seen == [0], "the login page must be fetched without a stale session"

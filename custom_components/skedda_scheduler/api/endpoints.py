"""The Skedda HTTP contract, in one file.

Every URL, header name and wire field name for Skedda lives here. When Skedda
changes its private API, this is the only module that should need editing.
Values are transcribed from .claude/specs/skedda-api-contract.md - do not guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import (
    ApiContractError,
    AuthExpiredError,
    BookingWindowClosedError,
    QuotaExceededError,
    RateLimitedError,
    SkeddaAuthError,
    SkeddaError,
    SlotTakenError,
)
from .models import SkeddaBookingRequest


@dataclass(frozen=True, slots=True)
class Endpoint:
    method: str
    path: str


# Confirmed 2026-09-15 from live traffic. Note none of these sit under /api/.
SPACES = Endpoint("GET", "/webs")  # venue config + assets, ~14 KB JSON
BOOKINGS_LIST = Endpoint("GET", "/bookingslists")  # params: start, end (naive local)
BOOKINGS = Endpoint("POST", "/bookings")  # create  -> 200 + booking
BOOKING_UPDATE = Endpoint("PUT", "/bookings")  # + /{id} -> 200 + booking
BOOKING_CANCEL = Endpoint("DELETE", "/bookings")  # + /{id} -> 204, empty body

# Sign-in is centralised on app.skedda.com; the venue host is reached only
# through a redirect chain the client must follow.
LOGIN_HOST = "https://app.skedda.com"
LOGIN_PAGE = Endpoint("GET", "/account/login")  # issues the antiforgery token
LOGIN = Endpoint("POST", "/logins")  # NB: /logins, not /account/login

# The token is sent under this header but read from an input named
# __RequestVerificationToken in the page HTML. The names differ; both are real.
ANTIFORGERY_HEADER = "x-skedda-requestverificationtoken"
ANTIFORGERY_INPUT = "__RequestVerificationToken"

# Status alone cannot classify a failure: every business-rule violation comes
# back as 422. The discriminator is the set of data-var names in the HTML error
# detail - see classify_error below.
STATUS_MAP: dict[int, type[SkeddaError]] = {
    401: AuthExpiredError,
    403: SkeddaAuthError,
    429: RateLimitedError,
}

# data-var names observed in 422 bodies, mapped to the error they identify.
# Both are permanent for the run: no retry and no reserve space will help.
# SlotTakenError has no marker yet - that response was not capturable while the
# test account's weekly quota was spent. Leave it absent rather than guess.
# Phrases that identify errors Skedda sends as bare prose, with no markup and
# no placeholders to key off. Confirmed 2026-09-15 for the conflict case.
#
# This is deliberately weaker than ERROR_MARKERS and it is language-fragile:
# the prose arrived in English at a venue whose dates render in Ukrainian, so
# it follows some venue language setting rather than the request's
# Accept-Language. A venue configured in another language would not match, and
# the failure then degrades to ApiContractError - noisy, but never a wrong
# retry. If that proves a problem, the scheduler can confirm a suspected
# conflict by re-reading /bookingslists, which is language-independent.
ERROR_PHRASES: tuple[tuple[str, type[SkeddaError]], ...] = (
    ("conflicts with", SlotTakenError),
    ("conflicting bookings are not allowed", SlotTakenError),
)

ERROR_MARKERS: tuple[tuple[frozenset[str], type[SkeddaError]], ...] = (
    (frozenset({"duration-days"}), BookingWindowClosedError),
    (frozenset({"period-start", "period-end", "time-value"}), QuotaExceededError),
)

_DATA_VAR = re.compile(r'data-var="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")


def base_url(venue: str) -> str:
    return f"https://{venue}.skedda.com"


def booking_path(booking_id: str) -> str:
    # Confirmed 2026-09-15 from a live DELETE (204) and PUT (200). No /api/ prefix.
    return f"/bookings/{booking_id}"


def _local_iso(value: datetime) -> str:
    """Render a venue-local instant the way Skedda's own client does.

    Confirmed from live traffic: every booking datetime on the wire is naive
    venue-local with no offset and no trailing Z, e.g. "2026-09-16T15:00:00".
    Callers pass timezone-aware venue-local datetimes; the offset is stripped
    here, never converted to UTC.
    """
    if value.tzinfo is None:
        raise ValueError("booking times must be timezone-aware venue-local datetimes")
    naive = value.replace(tzinfo=None)
    # Skedda writes whole seconds for a start and milliseconds for an end-of-day
    # bound. Preserving sub-second precision only when it exists reproduces both.
    spec = "milliseconds" if naive.microsecond else "seconds"
    return naive.isoformat(timespec=spec)


def list_bookings_params(start: datetime, end: datetime) -> dict[str, str]:
    """Build the /bookingslists query the way Skedda's own client does.

    Naive venue-local, and the end of a day is stamped to the millisecond
    (`2026-09-28T23:59:59.999`) rather than rounded up to the next midnight.
    """
    return {"start": _local_iso(start), "end": _local_iso(end)}


def login_payload(email: str, password: str) -> dict[str, Any]:
    """Build the sign-in body exactly as Skedda's own client sends it.

    The payload is wrapped in a "login" envelope and the credential field is
    "username", not "email" as the original brief assumed.
    """
    return {
        "login": {
            "username": email,
            "password": password,
            "rememberMe": True,
            "redirectUrl": None,
            "arbitraryerrors": None,
        }
    }


def booking_payload(request: SkeddaBookingRequest) -> dict[str, Any]:
    """Build the create-booking body.

    Skedda's own client posts all 40 fields of its booking model; the server
    accepted a request carrying exactly the non-default subset below. Note
    "spaces" (not "spaceIds"), string ids, and that the brief's "lockState"
    does not exist.
    """
    return {
        "booking": {
            "spaces": list(request.space_ids),
            "start": _local_iso(request.start),
            "end": _local_iso(request.end),
            "venue": request.venue_id,
            "venueuser": request.venueuser_id,
            "title": request.title or None,
            "price": 0,
            "type": 1,
            "paymentStatus": 0,
            "lockInMargin": request.lock_in_margin,
            "availabilityStatus": 1,
            "hideAttendees": True,
            "conferenceLinkType": 0,
            "attendees": [],
            "addOns": [],
            "customFields": [],
            "recurrenceRule": None,
            # The sink the server attaches rule violations to; always sent null.
            "arbitraryerrors": None,
        }
    }


def _error_markers(body: Any) -> frozenset[str]:
    """Collect the data-var names Skedda embedded in an error detail."""
    if not isinstance(body, dict):
        return frozenset()
    errors = body.get("errors")
    if not isinstance(errors, list):
        return frozenset()
    names: set[str] = set()
    for item in errors:
        if isinstance(item, dict) and isinstance(item.get("detail"), str):
            names.update(_DATA_VAR.findall(item["detail"]))
    return frozenset(names)


def classify_error(status: int, body: Any) -> type[SkeddaError] | None:
    """Map a response to the error it represents, or None if it succeeded.

    Matching is on data-var names rather than on the detail text, because the
    text is localised to the venue's language and carries HTML.
    """
    if 200 <= status < 300:
        return None
    if status in STATUS_MAP:
        return STATUS_MAP[status]
    if status == 422:
        markers = _error_markers(body)
        for expected, error in ERROR_MARKERS:
            if expected <= markers:
                return error
        detail = error_detail(body).casefold()
        for phrase, error in ERROR_PHRASES:
            if phrase in detail:
                return error
    return ApiContractError


def error_detail(body: Any) -> str:
    """Extract a human-readable message from an error body.

    detail is localised HTML with <var> placeholders; tags are stripped so the
    text can go into a log line or a repairs issue.
    """
    if not isinstance(body, dict):
        return ""
    errors = body.get("errors")
    if not isinstance(errors, list):
        return ""
    parts = [
        _TAG.sub("", item["detail"]).strip()
        for item in errors
        if isinstance(item, dict) and isinstance(item.get("detail"), str)
    ]
    return " ".join(part for part in parts if part)

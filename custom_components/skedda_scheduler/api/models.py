"""Transport DTOs.

Field names and types here mirror live traffic captured 2026-09-15; see
.claude/specs/skedda-api-contract.md. Two details are easy to get wrong:

* **Ids are strings.** `"1011034"`, not `1011034`. The server echoes them back
  as strings, so coercing to int makes later comparisons fail silently.
* **Booking datetimes are naive venue-local**, with no offset and no trailing
  Z. Only server audit stamps such as `createdDate` are UTC. Attaching a
  timezone here would be a guess; the venue timezone lives in `/webs` and is
  applied by the core layer, which knows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import ApiContractError


def _require(payload: dict[str, Any], key: str) -> Any:
    """Read a key the contract says is always present."""
    if key not in payload:
        raise ApiContractError(f"expected key {key!r} in payload, got keys {sorted(payload)}")
    return payload[key]


def _parse_local(payload: dict[str, Any], key: str) -> datetime:
    """Parse a naive venue-local datetime, e.g. '2026-09-28T08:00:00'."""
    raw = _require(payload, key)
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as err:
        raise ApiContractError(f"key {key!r} is not an ISO datetime: {raw!r}") from err
    if parsed.tzinfo is not None:
        raise ApiContractError(
            f"key {key!r} carried a timezone ({raw!r}); booking times are "
            "expected to be naive venue-local"
        )
    return parsed


@dataclass(frozen=True, slots=True)
class SkeddaCredentials:
    """One Skedda account, scoped to one venue subdomain."""

    venue: str
    email: str
    password: str

    def __repr__(self) -> str:
        """Never let the password reach a log or a traceback."""
        return f"SkeddaCredentials(venue={self.venue!r}, email={self.email!r})"


@dataclass(frozen=True, slots=True)
class SkeddaSession:
    """An authenticated session.

    `antiforgery_token` is scraped from the page HTML of the host being called
    and is per page load, not per session.
    """

    cookies: dict[str, str]
    antiforgery_token: str | None
    expires_at: datetime | None

    def is_expired(self, now: datetime) -> bool:
        if self.expires_at is None:
            return False
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class SkeddaSpace:
    """A bookable space. Skedda calls these 'assets' in /webs."""

    id: str
    name: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkeddaSpace:
        return cls(id=str(_require(payload, "id")), name=str(_require(payload, "name")))


@dataclass(frozen=True, slots=True)
class SkeddaBooking:
    """An existing booking, as returned by /bookingslists and /bookings."""

    id: str
    space_ids: tuple[str, ...]
    start: datetime
    end: datetime
    title: str
    #: Whose booking this is. /bookingslists returns the whole venue's, so
    #: telling ours apart is the difference between a calendar of our court
    #: times and a calendar of everybody's.
    venueuser_id: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkeddaBooking:
        owner = payload.get("venueuser")
        return cls(
            id=str(_require(payload, "id")),
            space_ids=tuple(str(x) for x in _require(payload, "spaces")),
            venueuser_id=str(owner) if owner is not None else None,
            start=_parse_local(payload, "start"),
            end=_parse_local(payload, "end"),
            # The venue does not require titles, so null is normal, not a
            # contract violation.
            title=payload.get("title") or "",
        )


@dataclass(frozen=True, slots=True)
class SkeddaVenue:
    """The venue settings that drive scheduling, read from /webs.

    `max_days_ahead` and `weekly_quota_minutes` are None when the venue sets no
    such rule. None means "unlimited" - it must never be conflated with 0,
    which would read as "nothing may be booked".
    """

    id: str
    name: str
    timezone: str
    slot_minutes: int
    max_days_ahead: int | None
    weekly_quota_minutes: int | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkeddaVenue:
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            timezone=str(_require(payload, "timeZoneId")),
            slot_minutes=int(payload.get("timeGranularityMinutes") or 0),
            max_days_ahead=_max_days_ahead(payload.get("bookingWindow")),
            weekly_quota_minutes=_weekly_quota(payload.get("quotaRules")),
        )


# bookingWindow.rules[].predicate: 1 means "at most N days ahead" - confirmed
# 2026-09-15, the server rejected a later slot quoting the same value.
_PREDICATE_MAX_DAYS_AHEAD = 1
# quotaRules.rules[].period: 2 means "per week"; aggregationMetric 1 means the
# value counts minutes.
_PERIOD_WEEK = 2
_METRIC_MINUTES = 1


def _rules(block: Any) -> list[dict[str, Any]]:
    if not isinstance(block, dict):
        return []
    rules = block.get("rules")
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []


def _max_days_ahead(block: Any) -> int | None:
    values = [
        int(r["value"])
        for r in _rules(block)
        if r.get("predicate") == _PREDICATE_MAX_DAYS_AHEAD and r.get("value") is not None
    ]
    # Several rules can apply at once; the tightest one is what the server
    # enforces, so anything looser would let us schedule an attempt that fails.
    return min(values) if values else None


def _weekly_quota(block: Any) -> int | None:
    values = [
        int(r["value"])
        for r in _rules(block)
        if r.get("period") == _PERIOD_WEEK
        and r.get("aggregationMetric") == _METRIC_MINUTES
        # A rule scoped to member tags may not apply to this account; without
        # knowing our own tags we cannot evaluate it, so only untagged
        # venue-wide rules are read here.
        and not r.get("tagIds")
        and r.get("value") is not None
    ]
    return min(values) if values else None


@dataclass(frozen=True, slots=True)
class SkeddaIdentity:
    """Who we are at this venue, as Skedda numbers it.

    Both ids go into every booking payload and the server does not infer
    either. `venueuser_id` is the id of the *membership*, not of the account:
    /webs also carries `web.userId`, a different number, and using it would
    make every booking fail.
    """

    venue_id: str
    venueuser_id: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkeddaIdentity:
        web = payload.get("web")
        if not isinstance(web, dict):
            raise ApiContractError("/webs carried no 'web' block to read ids from")
        return cls(
            venue_id=str(_require(web, "venue")),
            venueuser_id=str(_require(web, "venueuser")),
        )


@dataclass(frozen=True, slots=True)
class SkeddaBookingRequest:
    """A booking we intend to create.

    Describes *what* to book. Who is booking it is the client's business, and
    is merged in from SkeddaIdentity when the payload is built.

    `start` and `end` are timezone-aware venue-local; endpoints.py strips the
    offset when serialising, so the conversion happens in exactly one place.
    """

    space_ids: tuple[str, ...]
    start: datetime
    end: datetime
    title: str
    #: Mirrors the venue's lockInConfig.lockInHours; 1 at every venue seen so far.
    lock_in_margin: int = 1

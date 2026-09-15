"""Exception taxonomy for the Skedda transport layer.

The burst loop in scheduler.py branches on these types, so each one means a
specific recovery action. Do not collapse them.
"""

from __future__ import annotations


class SkeddaError(Exception):
    """Base for every error raised by the transport layer."""


class SkeddaConnectionError(SkeddaError):
    """Network-level failure: DNS, TCP, TLS or timeout. Retry."""


class SkeddaAuthError(SkeddaError):
    """Credentials were rejected. Not retryable without user action."""


class AuthExpiredError(SkeddaAuthError):
    """The session lapsed mid-run. Re-authenticate, then retry once."""


class TooEarlyError(SkeddaError):
    """The booking window has not opened yet. Retry after a short pause."""


class SlotTakenError(SkeddaError):
    """Someone else holds the slot. Retrying the same space is pointless;
    fall back to a reserve space if the job has one."""


class BookingWindowClosedError(SkeddaError):
    """The slot lies beyond the venue's booking horizon. Permanent for this
    run - the window rule is a venue setting, not a race."""


class QuotaExceededError(SkeddaError):
    """The account has used its allowance for the period. Permanent for this
    run; no other space or retry will help."""


class RateLimitedError(SkeddaError):
    """Skedda is throttling us. Back off before retrying."""


class ApiContractError(SkeddaError):
    """A response did not match the recorded contract. Means Skedda changed
    something; surfaced as a repairs issue rather than retried."""

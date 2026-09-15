# Skedda Scheduler — Implementation Plan (Phase 0 + v0.1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a HACS-installable Home Assistant integration that wins the race for a Skedda court slot the instant its booking window opens, unattended, for multiple accounts, on a recurring weekly schedule.

**Architecture:** Three layers with downward-only dependencies — `api/` (aiohttp transport for Skedda's private API, the volatile part), `core/` (pure-Python domain: recurrence, booking-window maths, burst timing; zero `homeassistant` imports), and the Home Assistant layer (config/subentry flows, coordinator, scheduler, sinks, entities). One config entry per Skedda account; one config subentry per booking job.

**Tech Stack:** Python 3.13, `aiohttp`, Home Assistant 2025.9+, `pytest` + `pytest-asyncio` + `pytest-homeassistant-custom-component`, `aioresponses`, `freezegun`, `syrupy`, `ruff`, `mypy`, `uv`, GitHub Actions (hassfest + HACS validate).

**Spec:** `.claude/specs/2026-09-15-skedda-scheduler-design.md` — read it before Task 1. The original requirements brief is `.claude/specs/Init.md`; where the two disagree, the design spec wins and explains why.

## Global Constraints

- Domain is `skedda_scheduler`. Repo is `DKorytkin/ha-skedda-integration`. Integration code lives under `custom_components/skedda_scheduler/`.
- Minimum Home Assistant version is **2025.9.0** (`ConfigSubentryFlow.async_update_reload_and_abort` is only stable from there). Never use an API newer than that.
- Python **3.13**. All I/O is `async`. No blocking calls inside the event loop.
- `core/` must not import `homeassistant` or `aiohttp`. `api/` must not import `core/` or `homeassistant`. Enforced by `tests/test_layering.py`, which runs in CI.
- Every URL, HTTP method, header name and request/response field name for Skedda lives in `custom_components/skedda_scheduler/api/endpoints.py` and **nowhere else**.
- Exception taxonomy lives in `api/errors.py`. `core/` never imports it; the HA layer may.
- All booking-time arithmetic happens in the **venue's** timezone, never the HA host timezone.
- Hard ceiling of **8** booking HTTP attempts per job run. This is a third-party service; a bug must not become a flood.
- Credentials are stored only in the config entry (HA encrypted storage) and are redacted in diagnostics and logs.
- Coverage target: 90% overall, **100% on `core/`**.
- Commit after every task. Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Out of scope for this plan: fallback spaces, calendar entity, Google invites, second provider. Those are v0.2+ and get their own plans.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | deps, ruff, mypy, pytest config |
| `hacs.json` | HACS metadata |
| `custom_components/skedda_scheduler/manifest.json` | HA integration metadata |
| `custom_components/skedda_scheduler/const.py` | `DOMAIN`, config keys, defaults, event names |
| `custom_components/skedda_scheduler/__init__.py` | entry setup/unload, `runtime_data`, platform forwarding |
| `api/errors.py` | exception taxonomy |
| `api/models.py` | transport DTOs (`Skedda*`) |
| `api/endpoints.py` | the single source of truth for the Skedda HTTP contract |
| `api/clock.py` | server clock-offset estimation |
| `api/client.py` | session, auth, request wrapper, error classification, attempt ceiling |
| `core/result.py` | `AttemptStatus`, `BookingAttempt`, `BookingOutcome` |
| `core/recurrence.py` | `Frequency`, `RecurrenceRule` |
| `core/window.py` | `BookingWindow` — slot → window-open instant |
| `core/job.py` | `BookingJob` — the unit the user configures |
| `core/strategy.py` | `AttemptPlan`, `BookingStrategy`, `SniperStrategy`, `ImmediateStrategy` |
| `core/provider.py` | `BookingProvider` protocol + domain value objects |
| `skedda_provider.py` | adapter: implements `BookingProvider` over `api.client` |
| `config_flow.py` | flow handler registration + subentry type registration |
| `flows/account.py` | user / reauth / reconfigure steps |
| `flows/job.py` | `ConfigSubentryFlow` for booking jobs |
| `coordinator.py` | `DataUpdateCoordinator` — account health + upcoming bookings |
| `store.py` | persisted attempt history |
| `scheduler.py` | arming, sub-second firing, burst loop, per-account semaphore |
| `sinks/base.py`, `sinks/ha_event.py`, `sinks/notify.py` | outcome fan-out |
| `sensor.py`, `binary_sensor.py`, `switch.py`, `button.py` | entities |
| `diagnostics.py`, `repairs.py` | support surfaces |
| `services.yaml` | `trigger_job_now`, `refresh_spaces` |
| `translations/en.json`, `translations/uk.json` | UI strings |

**Deviation from spec §4.2, deliberate:** the Skedda adapter lives at `skedda_provider.py` (HA layer), not inside `api/`. `api/` must not import `core/`, and the adapter needs both. Keeping `api/` free of domain imports also leaves it extractable as a standalone PyPI SDK later.

---

### Task 1: Repository scaffolding, tooling, CI, layer-boundary tests

**Files:**
- Create: `pyproject.toml`, `hacs.json`, `custom_components/skedda_scheduler/manifest.json`, `custom_components/skedda_scheduler/__init__.py`, `custom_components/skedda_scheduler/const.py`, `custom_components/skedda_scheduler/api/__init__.py`, `custom_components/skedda_scheduler/core/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_layering.py`, `.github/workflows/validate.yml`, `.github/workflows/tests.yml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `DOMAIN = "skedda_scheduler"` in `const.py`; a working `uv run pytest`; CI that runs hassfest, HACS validate, ruff, mypy, pytest.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "ha-skedda-integration"
version = "0.1.0"
description = "Skedda Scheduler integration for Home Assistant"
requires-python = ">=3.13"
dependencies = []

[dependency-groups]
dev = [
  "homeassistant>=2025.9.0",
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=6.0",
  "pytest-homeassistant-custom-component>=0.13.200",
  "aioresponses>=0.7.7",
  "freezegun>=1.5",
  "syrupy>=4.8",
  "ruff>=0.8",
  "mypy>=1.13",
]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC", "RUF", "SIM", "TID"]

[tool.mypy]
python_version = "3.13"
strict = true
warn_unused_ignores = true

[[tool.mypy.overrides]]
module = "tests.*"
strict = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q --cov=custom_components/skedda_scheduler --cov-report=term-missing"
```

- [ ] **Step 2: Create HACS and HA metadata**

`hacs.json`:

```json
{
  "name": "Skedda Scheduler",
  "homeassistant": "2025.9.0",
  "render_readme": true
}
```

`custom_components/skedda_scheduler/manifest.json`:

```json
{
  "domain": "skedda_scheduler",
  "name": "Skedda Scheduler",
  "codeowners": ["@DKorytkin"],
  "config_flow": true,
  "documentation": "https://github.com/DKorytkin/ha-skedda-integration",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/DKorytkin/ha-skedda-integration/issues",
  "requirements": [],
  "version": "0.1.0"
}
```

Do **not** add `quality_scale` — hassfest requires a matching `quality_scale.yaml` that core-only tooling generates, and it fails custom integrations.

- [ ] **Step 3: Create the minimal package so the integration imports**

`custom_components/skedda_scheduler/const.py`:

```python
"""Constants for the Skedda Scheduler integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "skedda_scheduler"

CONF_VENUE: Final = "venue"
CONF_ALIAS: Final = "alias"

SUBENTRY_TYPE_JOB: Final = "job"

EVENT_BOOKING_SUCCEEDED: Final = f"{DOMAIN}_booking_succeeded"
EVENT_BOOKING_FAILED: Final = f"{DOMAIN}_booking_failed"

DEFAULT_PREWARM_SECONDS: Final = 120
DEFAULT_LEAD_MS: Final = 150
DEFAULT_BURST_COUNT: Final = 5
DEFAULT_BURST_SPACING_MS: Final = 250
MAX_ATTEMPTS_PER_RUN: Final = 8
```

`custom_components/skedda_scheduler/__init__.py`:

```python
"""The Skedda Scheduler integration."""

from __future__ import annotations
```

Create empty `custom_components/skedda_scheduler/api/__init__.py` and `custom_components/skedda_scheduler/core/__init__.py`.

- [ ] **Step 4: Write the layer-boundary test (this is the failing test)**

`tests/test_layering.py`:

```python
"""The layering rules from the design spec, enforced rather than trusted."""

from __future__ import annotations

import ast
from pathlib import Path

COMPONENT = Path("custom_components/skedda_scheduler")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _offenders(package: str, forbidden: tuple[str, ...]) -> list[str]:
    bad: list[str] = []
    for path in sorted((COMPONENT / package).rglob("*.py")):
        for name in _imported_modules(path):
            if any(name == f or name.startswith(f"{f}.") for f in forbidden):
                bad.append(f"{path}: {name}")
    return bad


def test_core_imports_neither_home_assistant_nor_aiohttp() -> None:
    assert _offenders("core", ("homeassistant", "aiohttp")) == []


def test_api_imports_neither_core_nor_home_assistant() -> None:
    forbidden = ("homeassistant", "custom_components.skedda_scheduler.core")
    assert _offenders("api", forbidden) == []
```

`tests/conftest.py`:

```python
"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001, ANN201
    """Let Home Assistant load custom_components during tests."""
    yield
```

Create an empty `tests/__init__.py`.

- [ ] **Step 5: Run the tests to verify the harness works**

Run: `uv sync && uv run pytest tests/test_layering.py -v`
Expected: both tests PASS (the directories exist and are empty, so there is nothing to offend yet). If `uv sync` fails on `pytest-homeassistant-custom-component`, pin it to the release matching HA 2025.9 and retry.

- [ ] **Step 6: Create CI workflows**

`.github/workflows/validate.yml`:

```yaml
name: Validate

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: "0 3 * * 1"

jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

`.github/workflows/tests.yml`:

```yaml
name: Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.13"
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy custom_components
      - run: uv run pytest
```

- [ ] **Step 7: Add tooling artefacts to `.gitignore`**

Append:

```
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
*.har
```

`*.har` matters: Task 2 captures real credentials in transit and those files must never be committed.

- [ ] **Step 8: Run everything and commit**

Run: `uv run ruff check . && uv run mypy custom_components && uv run pytest`
Expected: clean.

```bash
git add pyproject.toml hacs.json custom_components tests .github .gitignore
git commit -m "chore: scaffold integration, tooling, CI and layer-boundary tests"
```

---

### Task 2: Phase 0 — discover the real Skedda API contract

This task produces **knowledge**, not features. Everything after it depends on the artefacts it writes. Do not skip it and do not guess.

**Files:**
- Create: `docs/skedda-api-contract.md`, `tests/fixtures/skedda/login_success.json`, `tests/fixtures/skedda/spaces.json`, `tests/fixtures/skedda/booking_created.json`, `tests/fixtures/skedda/booking_conflict.json`, `tests/fixtures/skedda/bookings_list.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `docs/skedda-api-contract.md`, which Tasks 3–7 transcribe into code; JSON fixtures that `aioresponses` replays in every `api/` test.

- [ ] **Step 1: Capture live traffic**

Use the `claude-in-chrome` skill. Open the user's Skedda venue in a new tab. **The user types their own password** — never handle it. With the Network panel recording, perform in order: log in, load the booking grid, open one bookable slot, create one real booking, then cancel it.

Record for each request: method, full URL, request headers that are not browser defaults (especially any antiforgery/XSRF header), the exact request body, the response status, and the response body.

- [ ] **Step 2: Capture the failure cases too**

The error contract matters more than the success contract, because the burst loop branches on it. Deliberately provoke and record:

1. Booking a slot that is already taken → note status code and body.
2. Booking a slot whose window has not opened yet → note status code and body.
3. Any request with an expired or absent session cookie → note status code and body.

If a case cannot be provoked safely, write "not observed" in the contract doc rather than inventing a value, and make the corresponding branch in Task 6 fall through to `ApiContractError`.

- [ ] **Step 3: Write `docs/skedda-api-contract.md`**

Use exactly this structure, one section per endpoint:

```markdown
# Skedda private API contract

Observed on <YYYY-MM-DD> against `<venue>.skedda.com`.
Undocumented and unversioned — re-verify after any Skedda release.

## Authentication

- Login URL: `<method> <url>`
- Antiforgery: <how the token is obtained and which header/field carries it>
- Session: <cookie name(s)>, observed lifetime
- Request body: ```json { ... } ```
- Success response: status `<n>`, body ```json { ... } ```
- Failure response: status `<n>`, body ```json { ... } ```

## List spaces
... same shape ...

## Create booking
... same shape ...

## List bookings
... same shape ...

## Cancel booking
... same shape ...

## Error status map

| Situation | Status | Distinguishing field in body |
|---|---|---|
| slot already taken | | |
| window not yet open | | |
| session expired | | |
| rate limited | | |
```

- [ ] **Step 4: Save redacted fixtures**

Copy each observed response body into `tests/fixtures/skedda/`. Replace every real email, name, account id and venue name with placeholders (`user@example.com`, `Example Venue`). Keep the **structure and types** exactly as observed — that is the whole value of the fixture. Verify no `.har` file is staged.

- [ ] **Step 5: Commit**

```bash
git add docs/skedda-api-contract.md tests/fixtures
git status --short   # confirm no .har file is staged
git commit -m "docs: record observed Skedda private API contract and fixtures"
```

---

### Task 3: Transport DTOs and exception taxonomy

**Files:**
- Create: `custom_components/skedda_scheduler/api/models.py`, `custom_components/skedda_scheduler/api/errors.py`
- Test: `tests/api/test_models.py`

**Interfaces:**
- Consumes: fixtures from Task 2.
- Produces:
  - `SkeddaCredentials(venue: str, email: str, password: str)`
  - `SkeddaSession(cookies: dict[str, str], antiforgery_token: str | None, expires_at: datetime | None)` with `is_expired(now: datetime) -> bool`
  - `SkeddaSpace(id: int, name: str)` with `from_payload(payload: dict) -> SkeddaSpace`
  - `SkeddaBooking(id: str, space_ids: tuple[int, ...], start: datetime, end: datetime, title: str)` with `from_payload`
  - `SkeddaBookingRequest(space_ids: tuple[int, ...], start: datetime, end: datetime, title: str, lock_state: str)`
  - `SkeddaError`, `SkeddaConnectionError`, `SkeddaAuthError`, `AuthExpiredError`, `TooEarlyError`, `SlotTakenError`, `RateLimitedError`, `ApiContractError`

- [ ] **Step 1: Write the failing test**

`tests/api/test_models.py` (create `tests/api/__init__.py` too):

```python
"""Transport DTO parsing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.skedda_scheduler.api.errors import ApiContractError
from custom_components.skedda_scheduler.api.models import (
    SkeddaSession,
    SkeddaSpace,
)

FIXTURES = Path("tests/fixtures/skedda")


def test_space_from_payload_uses_contract_field_names() -> None:
    payload = json.loads((FIXTURES / "spaces.json").read_text())
    spaces = [SkeddaSpace.from_payload(item) for item in payload["spaces"]]
    assert spaces
    assert all(isinstance(space.id, int) for space in spaces)
    assert all(space.name for space in spaces)


def test_space_from_payload_raises_contract_error_on_unknown_shape() -> None:
    with pytest.raises(ApiContractError):
        SkeddaSpace.from_payload({"unexpected": "shape"})


def test_session_is_expired_compares_against_given_now() -> None:
    session = SkeddaSession(
        cookies={"x": "y"},
        antiforgery_token=None,
        expires_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert session.is_expired(datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC))
    assert not session.is_expired(datetime(2026, 1, 1, 11, 59, tzinfo=UTC))


def test_session_without_expiry_is_never_expired() -> None:
    session = SkeddaSession(cookies={"x": "y"}, antiforgery_token=None, expires_at=None)
    assert not session.is_expired(datetime(2030, 1, 1, tzinfo=UTC))
```

Adjust `payload["spaces"]` to the actual top-level key recorded in `docs/skedda-api-contract.md`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: ...api.models`.

- [ ] **Step 3: Write `api/errors.py`**

```python
"""Exception taxonomy for the Skedda transport layer.

The burst loop in scheduler.py branches on these types, so each one means a
specific recovery action. Do not collapse them.
"""

from __future__ import annotations


class SkeddaError(Exception):
    """Base for every error raised by the transport layer."""


class SkeddaConnectionError(SkeddaError):
    """Network-level failure: DNS, TCP, TLS or timeout."""


class SkeddaAuthError(SkeddaError):
    """Credentials were rejected. Not retryable without user action."""


class AuthExpiredError(SkeddaAuthError):
    """The session lapsed. Retryable exactly once after re-authenticating."""


class TooEarlyError(SkeddaError):
    """The booking window has not opened yet. Retry immediately."""


class SlotTakenError(SkeddaError):
    """Someone else holds the slot. Retrying the same space is pointless."""


class RateLimitedError(SkeddaError):
    """Skedda asked us to slow down. Back off and abandon this run."""


class ApiContractError(SkeddaError):
    """The response did not match the recorded contract — Skedda changed."""
```

- [ ] **Step 4: Write `api/models.py`**

```python
"""Transport DTOs mirroring the shapes in docs/skedda-api-contract.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .errors import ApiContractError


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ApiContractError(f"missing field {key!r}; got keys {sorted(payload)}")
    return payload[key]


@dataclass(frozen=True, slots=True)
class SkeddaCredentials:
    venue: str
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SkeddaSession:
    cookies: dict[str, str] = field(repr=False)
    antiforgery_token: str | None = field(repr=False)
    expires_at: datetime | None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at


@dataclass(frozen=True, slots=True)
class SkeddaSpace:
    id: int
    name: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkeddaSpace:
        return cls(id=int(_require(payload, "id")), name=str(_require(payload, "name")))


@dataclass(frozen=True, slots=True)
class SkeddaBooking:
    id: str
    space_ids: tuple[int, ...]
    start: datetime
    end: datetime
    title: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkeddaBooking:
        return cls(
            id=str(_require(payload, "id")),
            space_ids=tuple(int(x) for x in _require(payload, "spaceIds")),
            start=datetime.fromisoformat(str(_require(payload, "start"))),
            end=datetime.fromisoformat(str(_require(payload, "end"))),
            title=str(payload.get("title", "")),
        )


@dataclass(frozen=True, slots=True)
class SkeddaBookingRequest:
    space_ids: tuple[int, ...]
    start: datetime
    end: datetime
    title: str
    lock_state: str = "Locked"
```

Rename every field-name string literal (`"id"`, `"name"`, `"spaceIds"`, `"start"`, `"end"`, `"title"`) to whatever `docs/skedda-api-contract.md` actually records.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/skedda_scheduler/api tests/api
git commit -m "feat(api): add transport DTOs and exception taxonomy"
```

---

### Task 4: The single-source-of-truth endpoint module

**Files:**
- Create: `custom_components/skedda_scheduler/api/endpoints.py`
- Test: `tests/api/test_endpoints.py`

**Interfaces:**
- Consumes: `docs/skedda-api-contract.md`.
- Produces:
  - `base_url(venue: str) -> str`
  - `LOGIN`, `SPACES`, `BOOKINGS`, `BOOKING(booking_id)` — `Endpoint(method: str, path: str)`
  - `login_payload(email: str, password: str) -> dict[str, Any]`
  - `booking_payload(request: SkeddaBookingRequest) -> dict[str, Any]`
  - `ANTIFORGERY_HEADER: str`
  - `STATUS_MAP: dict[int, type[SkeddaError]]`

- [ ] **Step 1: Write the failing test**

`tests/api/test_endpoints.py`:

```python
"""The contract module is the only place URLs and payload shapes may live."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.models import SkeddaBookingRequest


def test_base_url_is_built_from_the_venue_subdomain() -> None:
    assert endpoints.base_url("myclub") == "https://myclub.skedda.com"


def test_booking_payload_serialises_times_as_utc_iso8601_with_z() -> None:
    request = SkeddaBookingRequest(
        space_ids=(10293,),
        start=datetime(2026, 5, 19, 18, 0, tzinfo=UTC),
        end=datetime(2026, 5, 19, 19, 30, tzinfo=UTC),
        title="Tennis",
    )
    payload = endpoints.booking_payload(request)
    assert payload["start"] == "2026-05-19T18:00:00.000Z"
    assert payload["end"] == "2026-05-19T19:30:00.000Z"
    assert payload["spaceIds"] == [10293]


def test_booking_payload_rejects_naive_datetimes() -> None:
    request = SkeddaBookingRequest(
        space_ids=(1,),
        start=datetime(2026, 5, 19, 18, 0),
        end=datetime(2026, 5, 19, 19, 0),
        title="x",
    )
    try:
        endpoints.booking_payload(request)
    except ValueError as err:
        assert "timezone-aware" in str(err)
    else:
        raise AssertionError("naive datetimes must be rejected")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_endpoints.py -v`
Expected: FAIL with `ImportError`/`AttributeError` on `endpoints`.

- [ ] **Step 3: Write `api/endpoints.py`**

```python
"""The Skedda HTTP contract, in one file.

Every URL, header name and wire field name for Skedda lives here. When Skedda
changes its private API, this is the only module that should need editing.
Values are transcribed from docs/skedda-api-contract.md — do not guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .errors import (
    AuthExpiredError,
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


LOGIN = Endpoint("POST", "/api/account/login")
SPACES = Endpoint("GET", "/api/spaces")
BOOKINGS = Endpoint("POST", "/api/bookings")
BOOKINGS_LIST = Endpoint("GET", "/api/bookings")

ANTIFORGERY_HEADER = "RequestVerificationToken"
SESSION_COOKIE = ".AspNetCore.Identity.Application"

STATUS_MAP: dict[int, type[SkeddaError]] = {
    401: AuthExpiredError,
    403: SkeddaAuthError,
    409: SlotTakenError,
    422: SlotTakenError,
    429: RateLimitedError,
}


def base_url(venue: str) -> str:
    return f"https://{venue}.skedda.com"


def booking_path(booking_id: str) -> str:
    return f"/api/bookings/{booking_id}"


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("booking times must be timezone-aware")
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def login_payload(email: str, password: str) -> dict[str, Any]:
    return {"email": email, "password": password}


def booking_payload(request: SkeddaBookingRequest) -> dict[str, Any]:
    return {
        "spaceIds": list(request.space_ids),
        "start": _iso_z(request.start),
        "end": _iso_z(request.end),
        "title": request.title,
        "lockState": request.lock_state,
    }
```

Now **replace every constant above with the values recorded in `docs/skedda-api-contract.md`**. The values shown are the guesses from the original brief and are almost certainly wrong. Add a line comment next to each entry in `STATUS_MAP` naming the observation it came from, and delete any entry the contract doc marks "not observed".

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_endpoints.py -v`
Expected: PASS. If the contract doc shows a different time format, change the test's expected strings to match the contract, not the other way round.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/api/endpoints.py tests/api/test_endpoints.py
git commit -m "feat(api): add endpoint contract module"
```

---

### Task 5: Server clock synchronisation

The sniper fires 150 ms before the window opens. If the HA host's clock is 400 ms fast, it fires into a closed window and loses. This module removes that error.

**Files:**
- Create: `custom_components/skedda_scheduler/api/clock.py`
- Test: `tests/api/test_clock.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_date_header(value: str) -> datetime` (UTC-aware)
  - `ClockSync(alpha: float = 0.3)` with `offset_seconds: float`, `samples: int`, `observe(server_time, request_sent, response_received) -> float`, `local_instant_for(server_instant: datetime) -> datetime`

- [ ] **Step 1: Write the failing test**

`tests/api/test_clock.py`:

```python
"""Server clock-offset estimation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.skedda_scheduler.api.clock import ClockSync, parse_date_header


def test_parse_date_header_returns_utc_aware_datetime() -> None:
    parsed = parse_date_header("Tue, 19 May 2026 18:00:00 GMT")
    assert parsed == datetime(2026, 5, 19, 18, 0, tzinfo=UTC)


def test_offset_is_zero_when_clocks_agree_and_latency_is_symmetric() -> None:
    sync = ClockSync()
    sent = datetime(2026, 5, 19, 18, 0, 0, tzinfo=UTC)
    received = sent + timedelta(milliseconds=100)
    server = sent + timedelta(milliseconds=50)
    assert sync.observe(server, sent, received) == 0.0


def test_positive_offset_when_server_clock_is_ahead() -> None:
    sync = ClockSync()
    sent = datetime(2026, 5, 19, 18, 0, 0, tzinfo=UTC)
    received = sent + timedelta(milliseconds=100)
    server = sent + timedelta(milliseconds=50) + timedelta(seconds=2)
    assert sync.observe(server, sent, received) == 2.0


def test_later_samples_are_smoothed_not_replaced() -> None:
    sync = ClockSync(alpha=0.5)
    sent = datetime(2026, 5, 19, 18, 0, 0, tzinfo=UTC)
    received = sent + timedelta(milliseconds=100)
    mid = timedelta(milliseconds=50)
    sync.observe(sent + mid + timedelta(seconds=2), sent, received)
    second = sync.observe(sent + mid + timedelta(seconds=4), sent, received)
    assert second == 3.0
    assert sync.samples == 2


def test_local_instant_for_subtracts_the_offset() -> None:
    sync = ClockSync()
    sent = datetime(2026, 5, 19, 18, 0, 0, tzinfo=UTC)
    sync.observe(sent + timedelta(milliseconds=50) + timedelta(seconds=2), sent,
                 sent + timedelta(milliseconds=100))
    target = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
    assert sync.local_instant_for(target) == target - timedelta(seconds=2)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_clock.py -v`
Expected: FAIL with `ModuleNotFoundError: ...api.clock`.

- [ ] **Step 3: Write `api/clock.py`**

```python
"""Estimate how far the Skedda server's clock is from ours.

offset = server_time - local_time, corrected for half the round trip and
smoothed with an exponentially weighted moving average so one slow response
cannot throw the estimate off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime


def parse_date_header(value: str) -> datetime:
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(slots=True)
class ClockSync:
    alpha: float = 0.3
    offset_seconds: float = 0.0
    samples: int = 0

    def observe(
        self,
        server_time: datetime,
        request_sent: datetime,
        response_received: datetime,
    ) -> float:
        round_trip = (response_received - request_sent).total_seconds()
        local_midpoint = request_sent + timedelta(seconds=round_trip / 2)
        sample = (server_time - local_midpoint).total_seconds()
        if self.samples == 0:
            self.offset_seconds = sample
        else:
            self.offset_seconds = self.alpha * sample + (1 - self.alpha) * self.offset_seconds
        self.samples += 1
        return self.offset_seconds

    def local_instant_for(self, server_instant: datetime) -> datetime:
        """Return the local instant at which the server's clock reads ``server_instant``."""
        return server_instant - timedelta(seconds=self.offset_seconds)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_clock.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/api/clock.py tests/api/test_clock.py
git commit -m "feat(api): add server clock-offset estimation"
```

---

### Task 6: HTTP client — session, authentication, error classification

**Files:**
- Create: `custom_components/skedda_scheduler/api/client.py`
- Test: `tests/api/test_client_auth.py`

**Interfaces:**
- Consumes: `endpoints`, `models`, `errors`, `clock` from Tasks 3–5.
- Produces:
  - `SkeddaClient(session: aiohttp.ClientSession, credentials: SkeddaCredentials, clock: ClockSync | None = None)`
  - `await client.authenticate() -> SkeddaSession`
  - `client.is_authenticated: bool`
  - `client.clock: ClockSync`
  - `await client.request(endpoint: Endpoint, *, json_body: dict | None = None, params: dict | None = None) -> dict[str, Any]`
  - `client.classify(status: int, body: Any) -> type[SkeddaError] | None`

- [ ] **Step 1: Write the failing test**

`tests/api/test_client_auth.py`:

```python
"""Authentication and error classification."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.client import SkeddaClient
from custom_components.skedda_scheduler.api.errors import (
    AuthExpiredError,
    RateLimitedError,
    SkeddaAuthError,
    SkeddaConnectionError,
    SlotTakenError,
)
from custom_components.skedda_scheduler.api.models import SkeddaCredentials

CREDS = SkeddaCredentials(venue="myclub", email="user@example.com", password="secret")
LOGIN_URL = endpoints.base_url("myclub") + endpoints.LOGIN.path


@pytest.fixture
async def http():
    async with aiohttp.ClientSession() as session:
        yield session


async def test_authenticate_stores_session_and_marks_client_authenticated(http) -> None:
    client = SkeddaClient(http, CREDS)
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=200, payload={"ok": True},
                    headers={"Date": "Tue, 19 May 2026 18:00:00 GMT"})
        await client.authenticate()
    assert client.is_authenticated


async def test_authenticate_records_a_clock_sample(http) -> None:
    client = SkeddaClient(http, CREDS)
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=200, payload={"ok": True},
                    headers={"Date": "Tue, 19 May 2026 18:00:00 GMT"})
        await client.authenticate()
    assert client.clock.samples == 1


async def test_rejected_credentials_raise_auth_error(http) -> None:
    client = SkeddaClient(http, CREDS)
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=403, payload={"error": "invalid"})
        with pytest.raises(SkeddaAuthError):
            await client.authenticate()
    assert not client.is_authenticated


async def test_network_failure_raises_connection_error(http) -> None:
    client = SkeddaClient(http, CREDS)
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, exception=aiohttp.ClientConnectorError(None, OSError()))
        with pytest.raises(SkeddaConnectionError):
            await client.authenticate()


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, AuthExpiredError), (409, SlotTakenError), (429, RateLimitedError), (200, None)],
)
async def test_classify_maps_status_codes_to_the_taxonomy(http, status, expected) -> None:
    client = SkeddaClient(http, CREDS)
    assert client.classify(status, {}) is expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_client_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: ...api.client`.

- [ ] **Step 3: Write `api/client.py`**

```python
"""Async HTTP client for Skedda's private API.

Owns the session, the antiforgery token and the translation of HTTP responses
into the exception taxonomy. It holds no domain knowledge — callers decide what
a SlotTakenError means for them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import aiohttp

from . import endpoints
from .clock import ClockSync, parse_date_header
from .errors import (
    ApiContractError,
    SkeddaAuthError,
    SkeddaConnectionError,
    SkeddaError,
)
from .models import SkeddaCredentials, SkeddaSession

_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)


class SkeddaClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        credentials: SkeddaCredentials,
        clock: ClockSync | None = None,
    ) -> None:
        self._http = session
        self._credentials = credentials
        self._session: SkeddaSession | None = None
        self.clock = clock or ClockSync()
        self._base = endpoints.base_url(credentials.venue)

    @property
    def is_authenticated(self) -> bool:
        return self._session is not None and not self._session.is_expired(
            datetime.now(UTC)
        )

    def classify(self, status: int, body: Any) -> type[SkeddaError] | None:
        if 200 <= status < 300:
            return None
        return endpoints.STATUS_MAP.get(status, ApiContractError)

    async def authenticate(self) -> SkeddaSession:
        payload = endpoints.login_payload(
            self._credentials.email, self._credentials.password
        )
        status, body, headers = await self._send(endpoints.LOGIN, json_body=payload)
        if status == 403 or status == 401:
            self._session = None
            raise SkeddaAuthError("Skedda rejected the credentials")
        failure = self.classify(status, body)
        if failure is not None:
            raise failure(f"login failed with status {status}")
        self._session = SkeddaSession(
            cookies={c.key: c.value for c in self._http.cookie_jar},
            antiforgery_token=headers.get(endpoints.ANTIFORGERY_HEADER),
            expires_at=None,
        )
        return self._session

    async def request(
        self,
        endpoint: endpoints.Endpoint,
        *,
        path: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status, body, _ = await self._send(
            endpoint, path=path, json_body=json_body, params=params
        )
        failure = self.classify(status, body)
        if failure is not None:
            raise failure(f"{endpoint.method} {path or endpoint.path} -> {status}: {body}")
        if not isinstance(body, dict):
            raise ApiContractError(f"expected a JSON object, got {type(body).__name__}")
        return body

    async def _send(
        self,
        endpoint: endpoints.Endpoint,
        *,
        path: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        url = self._base + (path or endpoint.path)
        headers: dict[str, str] = {}
        if self._session and self._session.antiforgery_token:
            headers[endpoints.ANTIFORGERY_HEADER] = self._session.antiforgery_token
        sent = datetime.now(UTC)
        try:
            async with self._http.request(
                endpoint.method,
                url,
                json=json_body,
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
            ) as response:
                body = await self._read_body(response)
                received = datetime.now(UTC)
                self._observe_clock(response.headers.get("Date"), sent, received)
                return response.status, body, dict(response.headers)
        except TimeoutError as err:
            raise SkeddaConnectionError(f"timeout calling {url}") from err
        except aiohttp.ClientError as err:
            raise SkeddaConnectionError(f"network error calling {url}: {err}") from err

    async def _read_body(self, response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            return await response.text()

    def _observe_clock(
        self, date_header: str | None, sent: datetime, received: datetime
    ) -> None:
        if not date_header:
            return
        try:
            self.clock.observe(parse_date_header(date_header), sent, received)
        except (TypeError, ValueError):
            return
```

Note `asyncio` is imported for Task 7's use; if ruff flags it as unused now, remove the import and re-add it in Task 7.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_client_auth.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/api/client.py tests/api/test_client_auth.py
git commit -m "feat(api): add authenticated HTTP client with error classification"
```

---

### Task 7: HTTP client — spaces, booking, listing, cancelling

**Files:**
- Modify: `custom_components/skedda_scheduler/api/client.py`
- Test: `tests/api/test_client_bookings.py`

**Interfaces:**
- Consumes: Task 6's `SkeddaClient`.
- Produces on `SkeddaClient`:
  - `await list_spaces() -> list[SkeddaSpace]`
  - `await create_booking(request: SkeddaBookingRequest) -> SkeddaBooking`
  - `await list_bookings(start: datetime, end: datetime) -> list[SkeddaBooking]`
  - `await cancel_booking(booking_id: str) -> None`

- [ ] **Step 1: Write the failing test**

`tests/api/test_client_bookings.py`:

```python
"""Booking operations against recorded fixtures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.client import SkeddaClient
from custom_components.skedda_scheduler.api.errors import SlotTakenError
from custom_components.skedda_scheduler.api.models import (
    SkeddaBookingRequest,
    SkeddaCredentials,
)

FIXTURES = Path("tests/fixtures/skedda")
CREDS = SkeddaCredentials(venue="myclub", email="user@example.com", password="secret")
BASE = endpoints.base_url("myclub")

REQUEST = SkeddaBookingRequest(
    space_ids=(10293,),
    start=datetime(2026, 5, 19, 18, 0, tzinfo=UTC),
    end=datetime(2026, 5, 19, 19, 30, tzinfo=UTC),
    title="Tennis",
)


@pytest.fixture
async def http():
    async with aiohttp.ClientSession() as session:
        yield session


async def test_list_spaces_parses_the_recorded_fixture(http) -> None:
    client = SkeddaClient(http, CREDS)
    payload = json.loads((FIXTURES / "spaces.json").read_text())
    with aioresponses() as mocked:
        mocked.get(BASE + endpoints.SPACES.path, status=200, payload=payload)
        spaces = await client.list_spaces()
    assert spaces
    assert spaces[0].id


async def test_create_booking_returns_the_created_booking(http) -> None:
    client = SkeddaClient(http, CREDS)
    payload = json.loads((FIXTURES / "booking_created.json").read_text())
    with aioresponses() as mocked:
        mocked.post(BASE + endpoints.BOOKINGS.path, status=200, payload=payload)
        booking = await client.create_booking(REQUEST)
    assert booking.id


async def test_create_booking_raises_slot_taken_on_conflict(http) -> None:
    client = SkeddaClient(http, CREDS)
    payload = json.loads((FIXTURES / "booking_conflict.json").read_text())
    with aioresponses() as mocked:
        mocked.post(BASE + endpoints.BOOKINGS.path, status=409, payload=payload)
        with pytest.raises(SlotTakenError):
            await client.create_booking(REQUEST)


async def test_cancel_booking_issues_a_delete_to_the_booking_path(http) -> None:
    client = SkeddaClient(http, CREDS)
    url = BASE + endpoints.booking_path("abc123")
    with aioresponses() as mocked:
        mocked.delete(url, status=204, payload=None)
        await client.cancel_booking("abc123")
        mocked.assert_called_once()
```

Change the mocked statuses to the ones `docs/skedda-api-contract.md` actually records.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_client_bookings.py -v`
Expected: FAIL with `AttributeError: 'SkeddaClient' object has no attribute 'list_spaces'`.

- [ ] **Step 3: Add the methods to `api/client.py`**

Append inside `SkeddaClient`:

```python
    async def list_spaces(self) -> list[SkeddaSpace]:
        body = await self.request(endpoints.SPACES)
        items = body.get("spaces")
        if not isinstance(items, list):
            raise ApiContractError(f"spaces payload missing list; keys {sorted(body)}")
        return [SkeddaSpace.from_payload(item) for item in items]

    async def create_booking(self, request: SkeddaBookingRequest) -> SkeddaBooking:
        body = await self.request(
            endpoints.BOOKINGS, json_body=endpoints.booking_payload(request)
        )
        return SkeddaBooking.from_payload(body.get("booking", body))

    async def list_bookings(
        self, start: datetime, end: datetime
    ) -> list[SkeddaBooking]:
        body = await self.request(
            endpoints.BOOKINGS_LIST,
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        items = body.get("bookings", [])
        if not isinstance(items, list):
            raise ApiContractError(f"bookings payload missing list; keys {sorted(body)}")
        return [SkeddaBooking.from_payload(item) for item in items]

    async def cancel_booking(self, booking_id: str) -> None:
        status, body, _ = await self._send(
            endpoints.Endpoint("DELETE", endpoints.booking_path(booking_id)),
            path=endpoints.booking_path(booking_id),
        )
        failure = self.classify(status, body)
        if failure is not None:
            raise failure(f"cancel {booking_id} -> {status}")
```

Extend the imports at the top of the file with `SkeddaBooking`, `SkeddaBookingRequest`, `SkeddaSpace`. Replace the `"spaces"`, `"bookings"` and `"booking"` envelope keys with the ones in the contract doc.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api -v`
Expected: PASS, all `api/` tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/api/client.py tests/api/test_client_bookings.py
git commit -m "feat(api): add space, booking, listing and cancellation calls"
```

---

### Task 8: Domain results — `core/result.py`

**Files:**
- Create: `custom_components/skedda_scheduler/core/result.py`
- Test: `tests/core/test_result.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `AttemptStatus` — `SUCCESS`, `SLOT_TAKEN`, `TOO_EARLY`, `AUTH_FAILED`, `RATE_LIMITED`, `CONTRACT_ERROR`, `CONNECTION_ERROR`
  - `BookingAttempt(attempt_no: int, fired_at: datetime, status: AttemptStatus, latency_ms: float, detail: str | None = None)`
  - `BookingOutcome(job_id, succeeded, booking_id, space_id, slot_start, slot_end, attempts, finished_at)` with `as_dict() -> dict[str, Any]` and `failure_reason: str | None`

- [ ] **Step 1: Write the failing test**

`tests/core/test_result.py` (create `tests/core/__init__.py` too):

```python
"""Booking outcome value objects."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.skedda_scheduler.core.result import (
    AttemptStatus,
    BookingAttempt,
    BookingOutcome,
)

SLOT_START = datetime(2026, 5, 19, 18, 0, tzinfo=UTC)
SLOT_END = datetime(2026, 5, 19, 19, 30, tzinfo=UTC)


def _outcome(succeeded: bool, *statuses: AttemptStatus) -> BookingOutcome:
    attempts = tuple(
        BookingAttempt(i + 1, SLOT_START, status, 12.5)
        for i, status in enumerate(statuses)
    )
    return BookingOutcome(
        job_id="job-1",
        succeeded=succeeded,
        booking_id="bk-1" if succeeded else None,
        space_id=10293 if succeeded else None,
        slot_start=SLOT_START,
        slot_end=SLOT_END,
        attempts=attempts,
        finished_at=SLOT_START,
    )


def test_failure_reason_is_none_when_the_booking_succeeded() -> None:
    assert _outcome(True, AttemptStatus.TOO_EARLY, AttemptStatus.SUCCESS).failure_reason is None


def test_failure_reason_reports_the_last_attempt_status() -> None:
    outcome = _outcome(False, AttemptStatus.TOO_EARLY, AttemptStatus.SLOT_TAKEN)
    assert outcome.failure_reason == "slot_taken"


def test_failure_reason_is_unknown_when_no_attempt_was_made() -> None:
    assert _outcome(False).failure_reason == "unknown"


def test_as_dict_is_json_serialisable_and_keeps_attempt_count() -> None:
    import json

    payload = _outcome(True, AttemptStatus.SUCCESS).as_dict()
    assert json.dumps(payload)
    assert payload["attempts"] == 1
    assert payload["booking_id"] == "bk-1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_result.py -v`
Expected: FAIL with `ModuleNotFoundError: ...core.result`.

- [ ] **Step 3: Write `core/result.py`**

```python
"""What happened when we tried to book. Pure data, no Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class AttemptStatus(StrEnum):
    SUCCESS = "success"
    SLOT_TAKEN = "slot_taken"
    TOO_EARLY = "too_early"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    CONTRACT_ERROR = "contract_error"
    CONNECTION_ERROR = "connection_error"


@dataclass(frozen=True, slots=True)
class BookingAttempt:
    attempt_no: int
    fired_at: datetime
    status: AttemptStatus
    latency_ms: float
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class BookingOutcome:
    job_id: str
    succeeded: bool
    booking_id: str | None
    space_id: int | None
    slot_start: datetime
    slot_end: datetime
    attempts: tuple[BookingAttempt, ...]
    finished_at: datetime

    @property
    def failure_reason(self) -> str | None:
        if self.succeeded:
            return None
        if not self.attempts:
            return "unknown"
        return str(self.attempts[-1].status)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "succeeded": self.succeeded,
            "booking_id": self.booking_id,
            "space_id": self.space_id,
            "slot_start": self.slot_start.isoformat(),
            "slot_end": self.slot_end.isoformat(),
            "attempts": len(self.attempts),
            "failure_reason": self.failure_reason,
            "finished_at": self.finished_at.isoformat(),
        }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_result.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/core/result.py tests/core
git commit -m "feat(core): add booking attempt and outcome value objects"
```

---

### Task 9: Recurrence rules — `core/recurrence.py`

**Files:**
- Create: `custom_components/skedda_scheduler/core/recurrence.py`
- Test: `tests/core/test_recurrence.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Frequency` — `WEEKLY = "weekly"`, `BIWEEKLY = "biweekly"`
  - `RecurrenceRule(frequency: Frequency, weekday: int, season_start: date, season_end: date | None = None)` where `weekday` is 0 = Monday, matching `date.weekday()`
  - `rule.occurrences(after: date, limit: int) -> list[date]` — dates on or after `after`, ascending
  - `rule.next_occurrence(after: date) -> date | None`

- [ ] **Step 1: Write the failing test**

`tests/core/test_recurrence.py`:

```python
"""Recurrence maths. Off-by-one here silently skips a whole week of booking."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule

# 2026-09-01 is a Tuesday. Tuesday is weekday 1.
SEASON_START = date(2026, 9, 1)


def _weekly(**kwargs) -> RecurrenceRule:
    return RecurrenceRule(
        frequency=Frequency.WEEKLY, weekday=1, season_start=SEASON_START, **kwargs
    )


def test_weekly_occurrences_are_seven_days_apart_starting_at_the_season() -> None:
    assert _weekly().occurrences(after=SEASON_START, limit=3) == [
        date(2026, 9, 1),
        date(2026, 9, 8),
        date(2026, 9, 15),
    ]


def test_first_occurrence_rolls_forward_when_the_season_starts_off_weekday() -> None:
    # 2026-09-02 is a Wednesday; the first Tuesday on or after it is the 8th.
    rule = RecurrenceRule(
        frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 2)
    )
    assert rule.occurrences(after=date(2026, 9, 2), limit=1) == [date(2026, 9, 8)]


def test_biweekly_skips_every_other_week_anchored_on_the_season_start() -> None:
    rule = RecurrenceRule(
        frequency=Frequency.BIWEEKLY, weekday=1, season_start=SEASON_START
    )
    assert rule.occurrences(after=SEASON_START, limit=3) == [
        date(2026, 9, 1),
        date(2026, 9, 15),
        date(2026, 9, 29),
    ]


def test_biweekly_phase_is_preserved_when_asking_from_the_middle_of_the_season() -> None:
    rule = RecurrenceRule(
        frequency=Frequency.BIWEEKLY, weekday=1, season_start=SEASON_START
    )
    assert rule.occurrences(after=date(2026, 9, 20), limit=2) == [
        date(2026, 9, 29),
        date(2026, 10, 13),
    ]


def test_season_end_truncates_the_series_and_is_inclusive() -> None:
    rule = _weekly(season_end=date(2026, 9, 15))
    assert rule.occurrences(after=SEASON_START, limit=10) == [
        date(2026, 9, 1),
        date(2026, 9, 8),
        date(2026, 9, 15),
    ]


def test_next_occurrence_returns_none_after_the_season_ends() -> None:
    rule = _weekly(season_end=date(2026, 9, 15))
    assert rule.next_occurrence(after=date(2026, 9, 16)) is None


def test_occurrences_before_the_season_start_are_never_returned() -> None:
    assert _weekly().occurrences(after=date(2026, 8, 1), limit=1) == [date(2026, 9, 1)]


@pytest.mark.parametrize("weekday", [-1, 7])
def test_weekday_outside_zero_to_six_is_rejected(weekday: int) -> None:
    with pytest.raises(ValueError, match="weekday"):
        RecurrenceRule(
            frequency=Frequency.WEEKLY, weekday=weekday, season_start=SEASON_START
        )


def test_season_end_before_season_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="season_end"):
        RecurrenceRule(
            frequency=Frequency.WEEKLY,
            weekday=1,
            season_start=SEASON_START,
            season_end=date(2026, 8, 1),
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_recurrence.py -v`
Expected: FAIL with `ModuleNotFoundError: ...core.recurrence`.

- [ ] **Step 3: Write `core/recurrence.py`**

```python
"""When does this job recur?

Occurrences are anchored on the season start, not on "today", so a biweekly
job keeps its phase no matter when Home Assistant restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

_STEP_DAYS = {"weekly": 7, "biweekly": 14}


class Frequency(StrEnum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"


@dataclass(frozen=True, slots=True)
class RecurrenceRule:
    frequency: Frequency
    weekday: int
    season_start: date
    season_end: date | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError(f"weekday must be 0..6 (Monday..Sunday), got {self.weekday}")
        if self.season_end is not None and self.season_end < self.season_start:
            raise ValueError("season_end must not precede season_start")

    @property
    def step(self) -> timedelta:
        return timedelta(days=_STEP_DAYS[str(self.frequency)])

    @property
    def first_occurrence(self) -> date:
        shift = (self.weekday - self.season_start.weekday()) % 7
        return self.season_start + timedelta(days=shift)

    def occurrences(self, after: date, limit: int) -> list[date]:
        step_days = self.step.days
        current = self.first_occurrence
        if current < after:
            missed = (after - current).days
            whole_steps = -(-missed // step_days)  # ceiling division
            current = current + timedelta(days=whole_steps * step_days)
        found: list[date] = []
        while len(found) < limit:
            if self.season_end is not None and current > self.season_end:
                break
            found.append(current)
            current += self.step
        return found

    def next_occurrence(self, after: date) -> date | None:
        found = self.occurrences(after=after, limit=1)
        return found[0] if found else None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_recurrence.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/core/recurrence.py tests/core/test_recurrence.py
git commit -m "feat(core): add weekly and biweekly recurrence rules"
```

---

### Task 10: Booking window — `core/window.py`

The venue's timezone, not the host's. A venue two timezones away opens its window at an instant the host clock knows nothing about.

**Files:**
- Create: `custom_components/skedda_scheduler/core/window.py`
- Test: `tests/core/test_window.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `BookingWindow(window_days: int, open_time: time)` — `open_time` is venue-local wall-clock
  - `window.opens_at(slot_start_local: datetime) -> datetime` — returns a UTC-aware instant; raises `ValueError` if `slot_start_local` is naive

- [ ] **Step 1: Write the failing test**

`tests/core/test_window.py`:

```python
"""Booking-window arithmetic, including the DST edge that loses an hour."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.core.window import BookingWindow

KYIV = ZoneInfo("Europe/Kyiv")
WINDOW = BookingWindow(window_days=7, open_time=time(0, 0, 0))


def test_window_opens_seven_days_before_the_slot_at_venue_midnight() -> None:
    slot = datetime(2026, 5, 19, 18, 0, tzinfo=KYIV)
    # Kyiv is UTC+3 in May, so local midnight on the 12th is 21:00 UTC on the 11th.
    assert WINDOW.opens_at(slot) == datetime(2026, 5, 11, 21, 0, tzinfo=UTC)


def test_window_open_time_other_than_midnight_is_respected() -> None:
    window = BookingWindow(window_days=2, open_time=time(9, 30))
    slot = datetime(2026, 5, 19, 18, 0, tzinfo=KYIV)
    assert window.opens_at(slot) == datetime(2026, 5, 17, 6, 30, tzinfo=UTC)


def test_window_uses_winter_offset_when_the_open_date_is_before_the_dst_switch() -> None:
    # Kyiv moves to UTC+3 on 2026-03-29. A slot on 2026-03-31 opens on 2026-03-24,
    # which is still UTC+2.
    window = BookingWindow(window_days=7, open_time=time(0, 0))
    slot = datetime(2026, 3, 31, 18, 0, tzinfo=KYIV)
    assert window.opens_at(slot) == datetime(2026, 3, 23, 22, 0, tzinfo=UTC)


def test_zero_day_window_opens_on_the_slot_date_itself() -> None:
    window = BookingWindow(window_days=0, open_time=time(0, 0))
    slot = datetime(2026, 5, 19, 18, 0, tzinfo=KYIV)
    assert window.opens_at(slot) == datetime(2026, 5, 18, 21, 0, tzinfo=UTC)


def test_naive_slot_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        WINDOW.opens_at(datetime(2026, 5, 19, 18, 0))


def test_negative_window_days_is_rejected() -> None:
    with pytest.raises(ValueError, match="window_days"):
        BookingWindow(window_days=-1, open_time=time(0, 0))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_window.py -v`
Expected: FAIL with `ModuleNotFoundError: ...core.window`.

- [ ] **Step 3: Write `core/window.py`**

```python
"""When does the booking window for a given slot open?

All arithmetic happens in the venue's timezone, carried on ``slot_start_local``.
The result is UTC because everything downstream schedules in UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta


@dataclass(frozen=True, slots=True)
class BookingWindow:
    window_days: int
    open_time: time

    def __post_init__(self) -> None:
        if self.window_days < 0:
            raise ValueError(f"window_days must not be negative, got {self.window_days}")

    def opens_at(self, slot_start_local: datetime) -> datetime:
        if slot_start_local.tzinfo is None:
            raise ValueError("slot_start_local must be timezone-aware")
        venue_tz = slot_start_local.tzinfo
        open_date = slot_start_local.date() - timedelta(days=self.window_days)
        naive = datetime.combine(open_date, self.open_time)
        return naive.replace(tzinfo=venue_tz).astimezone(UTC)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_window.py -v`
Expected: PASS, 6 tests. If the DST test fails, the bug is real — `replace(tzinfo=...)` must resolve the offset for the *open* date, not the slot date. Do not weaken the test.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/core/window.py tests/core/test_window.py
git commit -m "feat(core): add venue-timezone booking window arithmetic"
```

---

### Task 11: The booking job — `core/job.py`

**Files:**
- Create: `custom_components/skedda_scheduler/core/job.py`
- Test: `tests/core/test_job.py`

**Interfaces:**
- Consumes: `RecurrenceRule`, `BookingWindow`.
- Produces:
  - `BookingJob(job_id, name, space_ids, start_time, duration_minutes, recurrence, window, venue_timezone, title, lock_state="Locked", strategy="sniper", notify_targets=(), enabled=True)`
  - `job.slot_for(occurrence: date) -> tuple[datetime, datetime]` — venue-local aware
  - `job.next_slot(now_utc: datetime) -> tuple[datetime, datetime] | None`
  - `job.next_window_open(now_utc: datetime) -> datetime | None` — UTC-aware
  - `job.primary_space_id: int`

- [ ] **Step 1: Write the failing test**

`tests/core/test_job.py`:

```python
"""The booking job ties recurrence, slot and window together."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.skedda_scheduler.core.job import BookingJob
from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule
from custom_components.skedda_scheduler.core.window import BookingWindow

KYIV = ZoneInfo("Europe/Kyiv")


def make_job(**overrides) -> BookingJob:
    defaults = dict(
        job_id="job-1",
        name="Tuesday 18:00",
        space_ids=(10293,),
        start_time=time(18, 0),
        duration_minutes=90,
        recurrence=RecurrenceRule(
            frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 1)
        ),
        window=BookingWindow(window_days=7, open_time=time(0, 0)),
        venue_timezone="Europe/Kyiv",
        title="Tennis (auto)",
    )
    return BookingJob(**{**defaults, **overrides})


def test_slot_for_builds_a_venue_local_interval_of_the_configured_duration() -> None:
    start, end = make_job().slot_for(date(2026, 9, 8))
    assert start == datetime(2026, 9, 8, 18, 0, tzinfo=KYIV)
    assert end == datetime(2026, 9, 8, 19, 30, tzinfo=KYIV)


def test_next_slot_picks_the_first_occurrence_after_now() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    start, _ = make_job().next_slot(now)
    assert start.date() == date(2026, 9, 8)


def test_next_window_open_is_seven_days_before_the_next_slot_in_utc() -> None:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    # Kyiv is UTC+3 in September: local midnight on 2026-09-01 is 21:00 UTC on 08-31.
    assert make_job().next_window_open(now) == datetime(2026, 9, 1, 21, 0, tzinfo=UTC)


def test_next_slot_is_none_once_the_season_is_over() -> None:
    job = make_job(
        recurrence=RecurrenceRule(
            frequency=Frequency.WEEKLY,
            weekday=1,
            season_start=date(2026, 9, 1),
            season_end=date(2026, 9, 8),
        )
    )
    assert job.next_slot(datetime(2026, 9, 9, tzinfo=UTC)) is None
    assert job.next_window_open(datetime(2026, 9, 9, tzinfo=UTC)) is None


def test_primary_space_id_is_the_first_configured_space() -> None:
    assert make_job(space_ids=(7, 8, 9)).primary_space_id == 7


def test_empty_space_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="space_ids"):
        make_job(space_ids=())


def test_non_positive_duration_is_rejected() -> None:
    with pytest.raises(ValueError, match="duration_minutes"):
        make_job(duration_minutes=0)


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="venue_timezone"):
        make_job(venue_timezone="Mars/Olympus")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_job.py -v`
Expected: FAIL with `ModuleNotFoundError: ...core.job`.

- [ ] **Step 3: Write `core/job.py`**

```python
"""One configured booking job: what to book, when, and how often."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .recurrence import RecurrenceRule
from .window import BookingWindow

# How far ahead next_slot is willing to look before giving up.
_SEARCH_LIMIT = 8


@dataclass(frozen=True, slots=True)
class BookingJob:
    job_id: str
    name: str
    space_ids: tuple[int, ...]
    start_time: time
    duration_minutes: int
    recurrence: RecurrenceRule
    window: BookingWindow
    venue_timezone: str
    title: str
    lock_state: str = "Locked"
    strategy: str = "sniper"
    notify_targets: tuple[str, ...] = field(default=())
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.space_ids:
            raise ValueError("space_ids must contain at least one space")
        if self.duration_minutes <= 0:
            raise ValueError(
                f"duration_minutes must be positive, got {self.duration_minutes}"
            )
        try:
            ZoneInfo(self.venue_timezone)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ValueError(f"venue_timezone {self.venue_timezone!r} is unknown") from err

    @property
    def tz(self) -> tzinfo:
        return ZoneInfo(self.venue_timezone)

    @property
    def primary_space_id(self) -> int:
        return self.space_ids[0]

    def slot_for(self, occurrence: date) -> tuple[datetime, datetime]:
        start = datetime.combine(occurrence, self.start_time).replace(tzinfo=self.tz)
        return start, start + timedelta(minutes=self.duration_minutes)

    def next_slot(self, now_utc: datetime) -> tuple[datetime, datetime] | None:
        today_local = now_utc.astimezone(self.tz).date()
        for occurrence in self.recurrence.occurrences(today_local, _SEARCH_LIMIT):
            start, end = self.slot_for(occurrence)
            if start > now_utc:
                return start, end
        return None

    def next_window_open(self, now_utc: datetime) -> datetime | None:
        slot = self.next_slot(now_utc)
        if slot is None:
            return None
        return self.window.opens_at(slot[0])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core -v`
Expected: PASS, all `core/` tests.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/core/job.py tests/core/test_job.py
git commit -m "feat(core): add booking job model"
```

---

### Task 12: Attempt strategies — `core/strategy.py`

This is the sniper, expressed as pure arithmetic: given the instant the window opens, produce the instants to act. No I/O, no sleeping — that belongs to `scheduler.py`. Making the timing a value makes it testable.

**Files:**
- Create: `custom_components/skedda_scheduler/core/strategy.py`
- Test: `tests/core/test_strategy.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `AttemptPlan(arm_at: datetime, fire_times: tuple[datetime, ...])` with `first_fire_at` and `last_fire_at`
  - `BookingStrategy` Protocol with `plan(opens_at: datetime) -> AttemptPlan`
  - `SniperStrategy(prewarm_seconds=120, lead_ms=150, burst_count=5, burst_spacing_ms=250)`
  - `ImmediateStrategy(prewarm_seconds=30, retry_delays_ms=(0, 1000, 3000))`
  - `build_strategy(name: str) -> BookingStrategy` — accepts `"sniper"` and `"immediate"`, raises `ValueError` otherwise

- [ ] **Step 1: Write the failing test**

`tests/core/test_strategy.py`:

```python
"""Attempt timing, as pure arithmetic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.skedda_scheduler.core.strategy import (
    ImmediateStrategy,
    SniperStrategy,
    build_strategy,
)

OPENS_AT = datetime(2026, 9, 1, 21, 0, 0, tzinfo=UTC)


def test_sniper_arms_two_minutes_before_the_window_opens() -> None:
    assert SniperStrategy().plan(OPENS_AT).arm_at == OPENS_AT - timedelta(seconds=120)


def test_sniper_first_shot_lands_just_before_the_window_opens() -> None:
    plan = SniperStrategy().plan(OPENS_AT)
    assert plan.first_fire_at == OPENS_AT - timedelta(milliseconds=150)


def test_sniper_fires_a_burst_at_the_configured_spacing() -> None:
    plan = SniperStrategy(burst_count=3, burst_spacing_ms=250, lead_ms=150).plan(OPENS_AT)
    assert plan.fire_times == (
        OPENS_AT - timedelta(milliseconds=150),
        OPENS_AT + timedelta(milliseconds=100),
        OPENS_AT + timedelta(milliseconds=350),
    )


def test_sniper_burst_stays_within_the_per_run_attempt_ceiling() -> None:
    assert len(SniperStrategy().plan(OPENS_AT).fire_times) <= 8


def test_immediate_fires_at_the_open_instant_then_backs_off() -> None:
    plan = ImmediateStrategy().plan(OPENS_AT)
    assert plan.fire_times == (
        OPENS_AT,
        OPENS_AT + timedelta(seconds=1),
        OPENS_AT + timedelta(seconds=3),
    )


def test_last_fire_at_reports_the_end_of_the_burst() -> None:
    plan = SniperStrategy(burst_count=2, burst_spacing_ms=250, lead_ms=150).plan(OPENS_AT)
    assert plan.last_fire_at == OPENS_AT + timedelta(milliseconds=100)


def test_build_strategy_resolves_the_configured_names() -> None:
    assert isinstance(build_strategy("sniper"), SniperStrategy)
    assert isinstance(build_strategy("immediate"), ImmediateStrategy)


def test_build_strategy_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("shotgun")


def test_burst_count_above_the_ceiling_is_rejected() -> None:
    with pytest.raises(ValueError, match="burst_count"):
        SniperStrategy(burst_count=99)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: ...core.strategy`.

- [ ] **Step 3: Write `core/strategy.py`**

```python
"""How hard do we try, and exactly when?

A strategy turns "the window opens at T" into a schedule of instants. Keeping
it free of I/O is what makes the millisecond behaviour testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

# Mirrors MAX_ATTEMPTS_PER_RUN in const.py. Duplicated rather than imported
# because core/ must stay free of Home Assistant package layout.
MAX_ATTEMPTS = 8


@dataclass(frozen=True, slots=True)
class AttemptPlan:
    arm_at: datetime
    fire_times: tuple[datetime, ...]

    @property
    def first_fire_at(self) -> datetime:
        return self.fire_times[0]

    @property
    def last_fire_at(self) -> datetime:
        return self.fire_times[-1]


class BookingStrategy(Protocol):
    def plan(self, opens_at: datetime) -> AttemptPlan: ...


@dataclass(frozen=True, slots=True)
class SniperStrategy:
    prewarm_seconds: int = 120
    lead_ms: int = 150
    burst_count: int = 5
    burst_spacing_ms: int = 250

    def __post_init__(self) -> None:
        if not 1 <= self.burst_count <= MAX_ATTEMPTS:
            raise ValueError(f"burst_count must be 1..{MAX_ATTEMPTS}, got {self.burst_count}")

    def plan(self, opens_at: datetime) -> AttemptPlan:
        first = opens_at - timedelta(milliseconds=self.lead_ms)
        fire_times = tuple(
            first + timedelta(milliseconds=self.burst_spacing_ms * index)
            for index in range(self.burst_count)
        )
        return AttemptPlan(
            arm_at=opens_at - timedelta(seconds=self.prewarm_seconds),
            fire_times=fire_times,
        )


@dataclass(frozen=True, slots=True)
class ImmediateStrategy:
    prewarm_seconds: int = 30
    retry_delays_ms: tuple[int, ...] = (0, 1000, 3000)

    def __post_init__(self) -> None:
        if not 1 <= len(self.retry_delays_ms) <= MAX_ATTEMPTS:
            raise ValueError(f"retry_delays_ms must hold 1..{MAX_ATTEMPTS} entries")

    def plan(self, opens_at: datetime) -> AttemptPlan:
        return AttemptPlan(
            arm_at=opens_at - timedelta(seconds=self.prewarm_seconds),
            fire_times=tuple(
                opens_at + timedelta(milliseconds=delay) for delay in self.retry_delays_ms
            ),
        )


_STRATEGIES: dict[str, type[SniperStrategy] | type[ImmediateStrategy]] = {
    "sniper": SniperStrategy,
    "immediate": ImmediateStrategy,
}


def build_strategy(name: str) -> BookingStrategy:
    try:
        return _STRATEGIES[name]()
    except KeyError as err:
        raise ValueError(f"unknown strategy {name!r}") from err
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core -v --cov=custom_components/skedda_scheduler/core --cov-report=term-missing`
Expected: PASS, and `core/` coverage at 100%. If any line is uncovered, add the missing test now — this is the module where untested branches cost real bookings.

- [ ] **Step 5: Commit**

```bash
git add custom_components/skedda_scheduler/core/strategy.py tests/core/test_strategy.py
git commit -m "feat(core): add sniper and immediate attempt strategies"
```

---

### Task 13: Provider protocol and the Skedda adapter

**Files:**
- Create: `custom_components/skedda_scheduler/core/provider.py`, `custom_components/skedda_scheduler/skedda_provider.py`
- Test: `tests/core/test_provider_protocol.py`, `tests/test_skedda_provider.py`

**Interfaces:**
- Consumes: `api.client.SkeddaClient`, `api.models.*`.
- Produces:
  - `core.provider.Space(id: int, name: str)`
  - `core.provider.Booking(id: str, space_ids: tuple[int, ...], start: datetime, end: datetime, title: str)`
  - `core.provider.BookingRequest(space_id: int, start: datetime, end: datetime, title: str, lock_state: str)`
  - `core.provider.DateRange(start: datetime, end: datetime)`
  - `core.provider.BookingProvider` Protocol: `authenticate()`, `is_authenticated`, `list_spaces()`, `book(request) -> Booking`, `list_bookings(window) -> list[Booking]`, `cancel(booking_id)`
  - `skedda_provider.SkeddaProvider(client: SkeddaClient)` implementing it

- [ ] **Step 1: Write the failing test**

`tests/test_skedda_provider.py`:

```python
"""The adapter maps transport DTOs onto domain types and nothing more."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.skedda_scheduler.api import endpoints
from custom_components.skedda_scheduler.api.client import SkeddaClient
from custom_components.skedda_scheduler.api.models import SkeddaCredentials
from custom_components.skedda_scheduler.core.provider import BookingRequest, Space
from custom_components.skedda_scheduler.skedda_provider import SkeddaProvider

FIXTURES = Path("tests/fixtures/skedda")
CREDS = SkeddaCredentials(venue="myclub", email="user@example.com", password="secret")
BASE = endpoints.base_url("myclub")


@pytest.fixture
async def provider():
    async with aiohttp.ClientSession() as http:
        yield SkeddaProvider(SkeddaClient(http, CREDS))


async def test_list_spaces_returns_domain_spaces_not_transport_dtos(provider) -> None:
    payload = json.loads((FIXTURES / "spaces.json").read_text())
    with aioresponses() as mocked:
        mocked.get(BASE + endpoints.SPACES.path, status=200, payload=payload)
        spaces = await provider.list_spaces()
    assert spaces and isinstance(spaces[0], Space)


async def test_book_maps_a_single_space_request_onto_the_transport_shape(provider) -> None:
    payload = json.loads((FIXTURES / "booking_created.json").read_text())
    request = BookingRequest(
        space_id=10293,
        start=datetime(2026, 5, 19, 18, 0, tzinfo=UTC),
        end=datetime(2026, 5, 19, 19, 30, tzinfo=UTC),
        title="Tennis",
        lock_state="Locked",
    )
    with aioresponses() as mocked:
        mocked.post(BASE + endpoints.BOOKINGS.path, status=200, payload=payload)
        booking = await provider.book(request)
    assert booking.id
    assert booking.space_ids


async def test_provider_reports_authentication_state_from_the_client(provider) -> None:
    assert provider.is_authenticated is False
```

`tests/core/test_provider_protocol.py`:

```python
"""The protocol must stay structural so a second provider can be dropped in."""

from __future__ import annotations

from custom_components.skedda_scheduler.core.provider import BookingProvider
from custom_components.skedda_scheduler.skedda_provider import SkeddaProvider


def test_skedda_provider_satisfies_the_protocol() -> None:
    assert isinstance(SkeddaProvider, type)
    # Structural check: every protocol member exists on the implementation.
    for member in BookingProvider.__protocol_attrs__:  # type: ignore[attr-defined]
        assert hasattr(SkeddaProvider, member), member
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skedda_provider.py tests/core/test_provider_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: ...core.provider`.

- [ ] **Step 3: Write `core/provider.py`**

```python
"""The seam that lets a second booking platform be added later.

Implementations raise the transport exception taxonomy from api/errors.py.
core/ does not import those types — the exception contract is documented here
and honoured by adapters; the burst loop in scheduler.py catches them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Space:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Booking:
    id: str
    space_ids: tuple[int, ...]
    start: datetime
    end: datetime
    title: str


@dataclass(frozen=True, slots=True)
class BookingRequest:
    space_id: int
    start: datetime
    end: datetime
    title: str
    lock_state: str = "Locked"


@dataclass(frozen=True, slots=True)
class DateRange:
    start: datetime
    end: datetime


@runtime_checkable
class BookingProvider(Protocol):
    """A booking platform.

    ``book`` raises SlotTakenError, TooEarlyError, AuthExpiredError,
    RateLimitedError, ApiContractError or SkeddaConnectionError.
    """

    @property
    def is_authenticated(self) -> bool: ...

    async def authenticate(self) -> None: ...

    async def list_spaces(self) -> list[Space]: ...

    async def book(self, request: BookingRequest) -> Booking: ...

    async def list_bookings(self, window: DateRange) -> list[Booking]: ...

    async def cancel(self, booking_id: str) -> None: ...
```

- [ ] **Step 4: Write `skedda_provider.py`**

```python
"""Adapter: Skedda's transport layer expressed as a BookingProvider.

Lives outside api/ because api/ must not import core/, and outside core/
because it speaks HTTP. Mapping is the entire job — no logic belongs here.
"""

from __future__ import annotations

from .api.client import SkeddaClient
from .api.models import SkeddaBooking, SkeddaBookingRequest
from .core.provider import Booking, BookingRequest, DateRange, Space


class SkeddaProvider:
    def __init__(self, client: SkeddaClient) -> None:
        self._client = client

    @property
    def is_authenticated(self) -> bool:
        return self._client.is_authenticated

    @property
    def client(self) -> SkeddaClient:
        return self._client

    async def authenticate(self) -> None:
        await self._client.authenticate()

    async def list_spaces(self) -> list[Space]:
        return [Space(id=item.id, name=item.name) for item in await self._client.list_spaces()]

    async def book(self, request: BookingRequest) -> Booking:
        created = await self._client.create_booking(
            SkeddaBookingRequest(
                space_ids=(request.space_id,),
                start=request.start,
                end=request.end,
                title=request.title,
                lock_state=request.lock_state,
            )
        )
        return self._to_booking(created)

    async def list_bookings(self, window: DateRange) -> list[Booking]:
        found = await self._client.list_bookings(window.start, window.end)
        return [self._to_booking(item) for item in found]

    async def cancel(self, booking_id: str) -> None:
        await self._client.cancel_booking(booking_id)

    @staticmethod
    def _to_booking(item: SkeddaBooking) -> Booking:
        return Booking(
            id=item.id,
            space_ids=item.space_ids,
            start=item.start,
            end=item.end,
            title=item.title,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests -v`
Expected: PASS, including `tests/test_layering.py` — `core/provider.py` imports nothing forbidden.

- [ ] **Step 6: Commit**

```bash
git add custom_components/skedda_scheduler/core/provider.py \
        custom_components/skedda_scheduler/skedda_provider.py \
        tests/core/test_provider_protocol.py tests/test_skedda_provider.py
git commit -m "feat: add booking provider protocol and Skedda adapter"
```

---

### Task 14: Integration entry point and runtime data

**Files:**
- Modify: `custom_components/skedda_scheduler/__init__.py`, `custom_components/skedda_scheduler/const.py`
- Test: `tests/test_init.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `SkeddaProvider`, `SkeddaClient`, `SkeddaCredentials`.
- Produces:
  - `SkeddaRuntimeData` dataclass with `provider: SkeddaProvider` and `semaphore: asyncio.Semaphore`
  - `SkeddaConfigEntry = ConfigEntry[SkeddaRuntimeData]`
  - `async_setup_entry`, `async_unload_entry`, `async_reload_entry`
  - `PLATFORMS: list[Platform]` (empty for now; later tasks append)

- [ ] **Step 1: Add the shared test fixtures**

Append to `tests/conftest.py`:

```python
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import CONF_ALIAS, CONF_VENUE, DOMAIN

ENTRY_DATA = {
    CONF_VENUE: "myclub",
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_ALIAS: "Main account (Oleh)",
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
def mock_provider():
    """Patch SkeddaProvider everywhere the integration constructs one."""
    provider = AsyncMock()
    provider.is_authenticated = True
    provider.list_spaces.return_value = []
    provider.list_bookings.return_value = []
    with patch(
        "custom_components.skedda_scheduler.SkeddaProvider", return_value=provider
    ):
        yield provider
```

- [ ] **Step 2: Write the failing test**

`tests/test_init.py`:

```python
"""Config entry lifecycle."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


async def test_entry_sets_up_and_exposes_runtime_data(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED
    assert mock_entry.runtime_data.provider is mock_provider


async def test_entry_unloads_cleanly(hass: HomeAssistant, mock_entry, mock_provider) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.NOT_LOADED


async def test_runtime_data_carries_a_single_flight_semaphore(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.runtime_data.semaphore._value == 1
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_init.py -v`
Expected: FAIL — `async_setup_entry` does not exist.

- [ ] **Step 4: Write `__init__.py`**

```python
"""The Skedda Scheduler integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.client import SkeddaClient
from .api.models import SkeddaCredentials
from .const import CONF_VENUE
from .skedda_provider import SkeddaProvider

PLATFORMS: list[Platform] = []


@dataclass
class SkeddaRuntimeData:
    """Everything an entry's platforms and scheduler need at runtime."""

    provider: SkeddaProvider
    semaphore: asyncio.Semaphore


type SkeddaConfigEntry = ConfigEntry[SkeddaRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    credentials = SkeddaCredentials(
        venue=entry.data[CONF_VENUE],
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
    )
    client = SkeddaClient(async_get_clientsession(hass), credentials)
    entry.runtime_data = SkeddaRuntimeData(
        provider=SkeddaProvider(client),
        # One booking in flight per account: two jobs must never race each other.
        semaphore=asyncio.Semaphore(1),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: SkeddaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add custom_components/skedda_scheduler/__init__.py tests/test_init.py tests/conftest.py
git commit -m "feat: set up config entry with typed runtime data"
```

---

### Task 15: Account config flow — add, reauth, reconfigure

**Files:**
- Create: `custom_components/skedda_scheduler/config_flow.py`, `custom_components/skedda_scheduler/flows/__init__.py`, `custom_components/skedda_scheduler/flows/account.py`, `custom_components/skedda_scheduler/strings.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `SkeddaClient`, `SkeddaAuthError`, `SkeddaConnectionError`.
- Produces:
  - `SkeddaConfigFlow` with `async_step_user`, `async_step_reauth`, `async_step_reauth_confirm`, `async_step_reconfigure`
  - `flows.account.validate_credentials(hass, venue, email, password) -> None` raising `SkeddaAuthError` / `SkeddaConnectionError`
  - unique id format: `f"{venue.lower()}:{email.lower()}"`

- [ ] **Step 1: Write the failing test**

`tests/test_config_flow.py`:

```python
"""Adding, re-authenticating and reconfiguring a Skedda account."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.skedda_scheduler.api.errors import (
    SkeddaAuthError,
    SkeddaConnectionError,
)
from custom_components.skedda_scheduler.const import CONF_ALIAS, CONF_VENUE, DOMAIN

USER_INPUT = {
    CONF_VENUE: "myclub",
    CONF_EMAIL: "User@Example.com",
    CONF_PASSWORD: "secret",
    CONF_ALIAS: "Main account (Oleh)",
}

VALIDATE = "custom_components.skedda_scheduler.flows.account.validate_credentials"


async def test_user_flow_creates_the_entry_titled_with_the_alias(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(VALIDATE, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Main account (Oleh)"
    assert result["result"].unique_id == "myclub:user@example.com"


@pytest.mark.parametrize(
    ("raised", "expected_error"),
    [(SkeddaAuthError("no"), "invalid_auth"), (SkeddaConnectionError("no"), "cannot_connect")],
)
async def test_user_flow_surfaces_the_failure_reason(
    hass: HomeAssistant, raised, expected_error
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, side_effect=raised):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


async def test_the_same_account_cannot_be_added_twice(
    hass: HomeAssistant, mock_entry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_password_in_place(
    hass: HomeAssistant, mock_entry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch(VALIDATE, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-secret"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_entry.data[CONF_PASSWORD] == "new-secret"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_config_flow.py -v`
Expected: FAIL — no config flow handler registered for the domain.

- [ ] **Step 3: Add the new config keys to `const.py`**

```python
CONF_VENUE_TIMEZONE: Final = "venue_timezone"
```

- [ ] **Step 4: Write `flows/account.py`**

```python
"""Account steps of the config flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..api.client import SkeddaClient
from ..api.models import SkeddaCredentials
from ..const import CONF_ALIAS, CONF_VENUE

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_VENUE): str,
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_ALIAS): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


def unique_id_for(venue: str, email: str) -> str:
    return f"{venue.strip().lower()}:{email.strip().lower()}"


async def validate_credentials(
    hass: HomeAssistant, venue: str, email: str, password: str
) -> None:
    """Raise SkeddaAuthError or SkeddaConnectionError if the account is unusable."""
    client = SkeddaClient(
        async_get_clientsession(hass),
        SkeddaCredentials(venue=venue.strip(), email=email.strip(), password=password),
    )
    await client.authenticate()


def normalise(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_VENUE: user_input[CONF_VENUE].strip().lower(),
        CONF_EMAIL: user_input[CONF_EMAIL].strip(),
        CONF_PASSWORD: user_input[CONF_PASSWORD],
        CONF_ALIAS: user_input[CONF_ALIAS].strip(),
    }
```

- [ ] **Step 5: Write `config_flow.py`**

```python
"""Config flow for Skedda Scheduler."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .api.errors import SkeddaAuthError, SkeddaConnectionError
from .const import CONF_ALIAS, CONF_VENUE, DOMAIN
from .flows.account import (
    STEP_REAUTH_SCHEMA,
    STEP_USER_SCHEMA,
    normalise,
    unique_id_for,
    validate_credentials,
)


class SkeddaConfigFlow(ConfigFlow, domain=DOMAIN):
    """One config entry per Skedda account."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = normalise(user_input)
            await self.async_set_unique_id(
                unique_id_for(data[CONF_VENUE], data[CONF_EMAIL])
            )
            self._abort_if_unique_id_configured()
            try:
                await validate_credentials(
                    self.hass, data[CONF_VENUE], data[CONF_EMAIL], data[CONF_PASSWORD]
                )
            except SkeddaAuthError:
                errors["base"] = "invalid_auth"
            except SkeddaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=data[CONF_ALIAS], data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await validate_credentials(
                    self.hass,
                    entry.data[CONF_VENUE],
                    entry.data[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                )
            except SkeddaAuthError:
                errors["base"] = "invalid_auth"
            except SkeddaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"alias": entry.data[CONF_ALIAS]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = normalise(user_input)
            try:
                await validate_credentials(
                    self.hass, data[CONF_VENUE], data[CONF_EMAIL], data[CONF_PASSWORD]
                )
            except SkeddaAuthError:
                errors["base"] = "invalid_auth"
            except SkeddaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, entry.data
            ),
            errors=errors,
        )
```

- [ ] **Step 6: Write `strings.json`**

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Add a Skedda account",
        "description": "Find your venue subdomain in the address of your Skedda site: for https://myclub.skedda.com it is myclub.",
        "data": {
          "venue": "Venue subdomain",
          "email": "Email",
          "password": "Password",
          "alias": "Account name"
        }
      },
      "reauth_confirm": {
        "title": "Re-enter the password",
        "description": "Skedda rejected the stored password for {alias}.",
        "data": { "password": "Password" }
      },
      "reconfigure": {
        "title": "Edit the Skedda account",
        "data": {
          "venue": "Venue subdomain",
          "email": "Email",
          "password": "Password",
          "alias": "Account name"
        }
      }
    },
    "error": {
      "cannot_connect": "Could not reach Skedda. Check the venue subdomain and your network.",
      "invalid_auth": "Skedda rejected these credentials."
    },
    "abort": {
      "already_configured": "This Skedda account is already set up.",
      "reauth_successful": "Re-authentication succeeded.",
      "reconfigure_successful": "The account was updated."
    }
  }
}
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/test_config_flow.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 8: Commit**

```bash
git add custom_components/skedda_scheduler/config_flow.py \
        custom_components/skedda_scheduler/flows \
        custom_components/skedda_scheduler/strings.json \
        custom_components/skedda_scheduler/const.py tests/test_config_flow.py
git commit -m "feat: add account config flow with reauth and reconfigure"
```

---

### Task 16: Booking job subentry flow

**Files:**
- Create: `custom_components/skedda_scheduler/flows/job.py`, `custom_components/skedda_scheduler/job_factory.py`
- Modify: `custom_components/skedda_scheduler/config_flow.py`, `custom_components/skedda_scheduler/const.py`, `custom_components/skedda_scheduler/strings.json`
- Test: `tests/test_job_subentry_flow.py`, `tests/test_job_factory.py`

**Interfaces:**
- Consumes: `BookingJob`, `RecurrenceRule`, `BookingWindow`.
- Produces:
  - config keys in `const.py`: `CONF_SPACE_ID`, `CONF_WEEKDAY`, `CONF_START_TIME`, `CONF_DURATION`, `CONF_WINDOW_DAYS`, `CONF_WINDOW_OPEN_TIME`, `CONF_FREQUENCY`, `CONF_SEASON_START`, `CONF_SEASON_END`, `CONF_TITLE`, `CONF_LOCK_STATE`, `CONF_STRATEGY`, `CONF_NOTIFY_TARGETS`, `CONF_ENABLED`
  - `job_factory.build_job(subentry_id: str, data: Mapping[str, Any], venue_timezone: str) -> BookingJob`
  - `flows.job.JobSubentryFlowHandler`
  - `SkeddaConfigFlow.async_get_supported_subentry_types` returning `{"job": JobSubentryFlowHandler}`

- [ ] **Step 1: Write the failing test for the factory**

`tests/test_job_factory.py`:

```python
"""Turning stored subentry config into a domain BookingJob."""

from __future__ import annotations

from datetime import date, time

import pytest

from custom_components.skedda_scheduler.core.recurrence import Frequency
from custom_components.skedda_scheduler.job_factory import build_job

DATA = {
    "name": "Tuesday 18:00",
    "space_id": 10293,
    "weekday": 1,
    "start_time": "18:00:00",
    "duration_minutes": 90,
    "window_days": 7,
    "window_open_time": "00:00:00",
    "frequency": "weekly",
    "season_start": "2026-09-01",
    "season_end": "2026-12-20",
    "title": "Tennis (auto)",
    "lock_state": "Locked",
    "strategy": "sniper",
    "notify_targets": ["notify.telegram"],
    "enabled": True,
}


def test_build_job_maps_every_stored_field() -> None:
    job = build_job("sub-1", DATA, "Europe/Kyiv")

    assert job.job_id == "sub-1"
    assert job.space_ids == (10293,)
    assert job.start_time == time(18, 0)
    assert job.duration_minutes == 90
    assert job.window.window_days == 7
    assert job.window.open_time == time(0, 0)
    assert job.recurrence.frequency is Frequency.WEEKLY
    assert job.recurrence.weekday == 1
    assert job.recurrence.season_start == date(2026, 9, 1)
    assert job.recurrence.season_end == date(2026, 12, 20)
    assert job.notify_targets == ("notify.telegram",)
    assert job.enabled is True


def test_season_end_is_optional() -> None:
    job = build_job("sub-1", {**DATA, "season_end": None}, "Europe/Kyiv")
    assert job.recurrence.season_end is None


def test_missing_season_end_key_is_also_accepted() -> None:
    data = {key: value for key, value in DATA.items() if key != "season_end"}
    assert build_job("sub-1", data, "Europe/Kyiv").recurrence.season_end is None


def test_invalid_stored_config_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_job("sub-1", {**DATA, "duration_minutes": 0}, "Europe/Kyiv")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_job_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: ...job_factory`.

- [ ] **Step 3: Add the job config keys to `const.py`**

```python
CONF_SPACE_ID: Final = "space_id"
CONF_WEEKDAY: Final = "weekday"
CONF_START_TIME: Final = "start_time"
CONF_DURATION: Final = "duration_minutes"
CONF_WINDOW_DAYS: Final = "window_days"
CONF_WINDOW_OPEN_TIME: Final = "window_open_time"
CONF_FREQUENCY: Final = "frequency"
CONF_SEASON_START: Final = "season_start"
CONF_SEASON_END: Final = "season_end"
CONF_TITLE: Final = "title"
CONF_LOCK_STATE: Final = "lock_state"
CONF_STRATEGY: Final = "strategy"
CONF_NOTIFY_TARGETS: Final = "notify_targets"
CONF_ENABLED: Final = "enabled"

DEFAULT_DURATION_MINUTES: Final = 90
DEFAULT_WINDOW_DAYS: Final = 7
DEFAULT_LOCK_STATE: Final = "Locked"
```

- [ ] **Step 4: Write `job_factory.py`**

```python
"""Bridge between stored Home Assistant config and the domain model.

Subentry data is JSON: times and dates arrive as strings. This is the only
place that parses them, so core/ can stay strongly typed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from typing import Any

from .const import (
    CONF_DURATION,
    CONF_ENABLED,
    CONF_FREQUENCY,
    CONF_LOCK_STATE,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SEASON_START,
    CONF_SPACE_ID,
    CONF_START_TIME,
    CONF_STRATEGY,
    CONF_TITLE,
    CONF_WEEKDAY,
    CONF_WINDOW_DAYS,
    CONF_WINDOW_OPEN_TIME,
    DEFAULT_LOCK_STATE,
)
from .core.job import BookingJob
from .core.recurrence import Frequency, RecurrenceRule
from .core.window import BookingWindow


def _time(value: str | time) -> time:
    return value if isinstance(value, time) else time.fromisoformat(value)


def _date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def build_job(
    subentry_id: str, data: Mapping[str, Any], venue_timezone: str
) -> BookingJob:
    season_end = data.get(CONF_SEASON_END)
    return BookingJob(
        job_id=subentry_id,
        name=str(data["name"]),
        space_ids=(int(data[CONF_SPACE_ID]),),
        start_time=_time(data[CONF_START_TIME]),
        duration_minutes=int(data[CONF_DURATION]),
        recurrence=RecurrenceRule(
            frequency=Frequency(data[CONF_FREQUENCY]),
            weekday=int(data[CONF_WEEKDAY]),
            season_start=_date(data[CONF_SEASON_START]),
            season_end=_date(season_end) if season_end else None,
        ),
        window=BookingWindow(
            window_days=int(data[CONF_WINDOW_DAYS]),
            open_time=_time(data[CONF_WINDOW_OPEN_TIME]),
        ),
        venue_timezone=venue_timezone,
        title=str(data[CONF_TITLE]),
        lock_state=str(data.get(CONF_LOCK_STATE, DEFAULT_LOCK_STATE)),
        strategy=str(data.get(CONF_STRATEGY, "sniper")),
        notify_targets=tuple(data.get(CONF_NOTIFY_TARGETS) or ()),
        enabled=bool(data.get(CONF_ENABLED, True)),
    )
```

- [ ] **Step 5: Run the factory test — it should pass**

Run: `uv run pytest tests/test_job_factory.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 6: Write the failing subentry-flow test**

`tests/test_job_subentry_flow.py`:

```python
"""Creating and editing a booking job through the subentry UI."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.skedda_scheduler.const import DOMAIN, SUBENTRY_TYPE_JOB

JOB_INPUT = {
    "name": "Tuesday 18:00",
    "space_id": "10293",
    "weekday": "1",
    "start_time": "18:00:00",
    "duration_minutes": 90,
    "window_days": 7,
    "window_open_time": "00:00:00",
    "frequency": "weekly",
    "season_start": "2026-09-01",
    "title": "Tennis (auto)",
    "strategy": "sniper",
}


async def test_job_subentry_is_created_and_titled_with_the_job_name(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_TYPE_JOB), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], JOB_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tuesday 18:00"

    subentries = list(mock_entry.subentries.values())
    assert len(subentries) == 1
    assert subentries[0].data["space_id"] == "10293"


async def test_the_job_subentry_type_is_advertised(
    hass: HomeAssistant, mock_entry
) -> None:
    from custom_components.skedda_scheduler.config_flow import SkeddaConfigFlow

    assert SUBENTRY_TYPE_JOB in SkeddaConfigFlow.async_get_supported_subentry_types(
        mock_entry
    )
    assert DOMAIN == "skedda_scheduler"
```

- [ ] **Step 7: Run it to verify it fails**

Run: `uv run pytest tests/test_job_subentry_flow.py -v`
Expected: FAIL — `async_get_supported_subentry_types` is not defined.

- [ ] **Step 8: Write `flows/job.py`**

```python
"""Subentry flow: one booking job per subentry."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import selector

from ..const import (
    CONF_DURATION,
    CONF_FREQUENCY,
    CONF_NOTIFY_TARGETS,
    CONF_SEASON_END,
    CONF_SEASON_START,
    CONF_SPACE_ID,
    CONF_START_TIME,
    CONF_STRATEGY,
    CONF_TITLE,
    CONF_WEEKDAY,
    CONF_WINDOW_DAYS,
    CONF_WINDOW_OPEN_TIME,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_WINDOW_DAYS,
)

WEEKDAYS = [
    {"value": "0", "label": "Monday"},
    {"value": "1", "label": "Tuesday"},
    {"value": "2", "label": "Wednesday"},
    {"value": "3", "label": "Thursday"},
    {"value": "4", "label": "Friday"},
    {"value": "5", "label": "Saturday"},
    {"value": "6", "label": "Sunday"},
]


def job_schema(space_options: list[dict[str, str]]) -> vol.Schema:
    space_selector: Any
    if space_options:
        space_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(options=space_options)
        )
    else:
        # Spaces could not be fetched; fall back to a free-text space id so the
        # user is never blocked by a transient API failure.
        space_selector = selector.TextSelector()

    return vol.Schema(
        {
            vol.Required("name"): selector.TextSelector(),
            vol.Required(CONF_SPACE_ID): space_selector,
            vol.Required(CONF_WEEKDAY): selector.SelectSelector(
                selector.SelectSelectorConfig(options=WEEKDAYS)
            ),
            vol.Required(CONF_START_TIME): selector.TimeSelector(),
            vol.Required(
                CONF_DURATION, default=DEFAULT_DURATION_MINUTES
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15, max=480, step=15, unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_WINDOW_DAYS, default=DEFAULT_WINDOW_DAYS
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=60, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_WINDOW_OPEN_TIME, default="00:00:00"
            ): selector.TimeSelector(),
            vol.Required(CONF_FREQUENCY, default="weekly"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "weekly", "label": "Every week"},
                        {"value": "biweekly", "label": "Every other week"},
                    ]
                )
            ),
            vol.Required(CONF_SEASON_START): selector.DateSelector(),
            vol.Optional(CONF_SEASON_END): selector.DateSelector(),
            vol.Required(CONF_TITLE): selector.TextSelector(),
            vol.Required(CONF_STRATEGY, default="sniper"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "sniper", "label": "Sniper (race the window open)"},
                        {"value": "immediate", "label": "Immediate (fire and retry)"},
                    ]
                )
            ),
            vol.Optional(CONF_NOTIFY_TARGETS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", multiple=True)
            ),
        }
    )


class JobSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit one booking job."""

    def _space_options(self) -> list[dict[str, str]]:
        entry = self._get_entry()
        runtime = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime, "coordinator", None)
        data = getattr(coordinator, "data", None)
        spaces = getattr(data, "spaces", None) or []
        return [{"value": str(space.id), "label": space.name} for space in spaces]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=job_schema(self._space_options())
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=user_input["name"], data=user_input
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                job_schema(self._space_options()), subentry.data
            ),
        )
```

- [ ] **Step 9: Register the subentry type in `config_flow.py`**

Add to `SkeddaConfigFlow`:

```python
    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_JOB: JobSubentryFlowHandler}
```

with imports `from homeassistant.config_entries import ConfigEntry, ConfigSubentryFlow`, `from homeassistant.core import callback`, `from .const import SUBENTRY_TYPE_JOB`, `from .flows.job import JobSubentryFlowHandler`.

- [ ] **Step 10: Add the subentry strings**

Add to `strings.json` at the top level:

```json
  "config_subentries": {
    "job": {
      "title": "Booking job",
      "initiate_flow": { "user": "Add a booking job", "reconfigure": "Edit the booking job" },
      "entry_type": "Booking job",
      "step": {
        "user": {
          "title": "Add a booking job",
          "description": "The job books the same slot every week. Sniper mode fires the request the instant the booking window opens.",
          "data": {
            "name": "Job name",
            "space_id": "Court",
            "weekday": "Day of week",
            "start_time": "Start time",
            "duration_minutes": "Duration",
            "window_days": "Booking opens this many days before",
            "window_open_time": "Booking opens at (venue time)",
            "frequency": "Repeat",
            "season_start": "Season starts",
            "season_end": "Season ends (optional)",
            "title": "Booking title shown in Skedda",
            "strategy": "Strategy",
            "notify_targets": "Notify these services with the result"
          }
        },
        "reconfigure": {
          "title": "Edit the booking job",
          "data": {
            "name": "Job name",
            "space_id": "Court",
            "weekday": "Day of week",
            "start_time": "Start time",
            "duration_minutes": "Duration",
            "window_days": "Booking opens this many days before",
            "window_open_time": "Booking opens at (venue time)",
            "frequency": "Repeat",
            "season_start": "Season starts",
            "season_end": "Season ends (optional)",
            "title": "Booking title shown in Skedda",
            "strategy": "Strategy",
            "notify_targets": "Notify these services with the result"
          }
        }
      }
    }
  }
```

- [ ] **Step 11: Run the tests and verify they pass**

Run: `uv run pytest tests/test_job_subentry_flow.py tests/test_job_factory.py -v`
Expected: PASS. If `async_update_and_abort` is missing, check your installed HA version's `ConfigSubentryFlow` in `homeassistant/config_entries.py` and use the method it provides — do not downgrade the minimum HA version below 2025.9.

- [ ] **Step 12: Commit**

```bash
git add custom_components/skedda_scheduler tests/test_job_subentry_flow.py tests/test_job_factory.py
git commit -m "feat: add booking job subentry flow"
```

---

### Task 17: Coordinator

**Files:**
- Create: `custom_components/skedda_scheduler/coordinator.py`
- Modify: `custom_components/skedda_scheduler/__init__.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `SkeddaProvider`, `core.provider.Space`, `core.provider.Booking`, `DateRange`.
- Produces:
  - `SkeddaData(spaces: list[Space], bookings: list[Booking])`
  - `SkeddaCoordinator(hass, entry, provider)` — `DataUpdateCoordinator[SkeddaData]`, 15-minute interval, `authenticated: bool`
  - `SkeddaRuntimeData.coordinator`

- [ ] **Step 1: Write the failing test**

`tests/test_coordinator.py`:

```python
"""Polling account health and upcoming bookings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.skedda_scheduler.api.errors import (
    SkeddaAuthError,
    SkeddaConnectionError,
)
from custom_components.skedda_scheduler.core.provider import Booking, Space


async def test_coordinator_exposes_spaces_and_bookings(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_provider.list_spaces.return_value = [Space(id=1, name="Court 1")]
    mock_provider.list_bookings.return_value = [
        Booking(
            id="bk-1",
            space_ids=(1,),
            start=datetime(2026, 9, 8, 15, 0, tzinfo=UTC),
            end=datetime(2026, 9, 8, 16, 30, tzinfo=UTC),
            title="Tennis",
        )
    ]
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    data = mock_entry.runtime_data.coordinator.data
    assert [space.name for space in data.spaces] == ["Court 1"]
    assert [booking.id for booking in data.bookings] == ["bk-1"]


async def test_bad_credentials_put_the_entry_into_reauth(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_provider.is_authenticated = False
    mock_provider.authenticate.side_effect = SkeddaAuthError("rejected")
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(flow["context"]["source"] == "reauth"
               for flow in hass.config_entries.flow.async_progress())


async def test_network_failure_is_reported_as_update_failed(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    from custom_components.skedda_scheduler.coordinator import SkeddaCoordinator

    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")
    coordinator = SkeddaCoordinator(hass, mock_entry, mock_provider)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_interval_is_fifteen_minutes(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    from custom_components.skedda_scheduler.coordinator import SkeddaCoordinator

    coordinator = SkeddaCoordinator(hass, mock_entry, mock_provider)
    assert coordinator.update_interval == timedelta(minutes=15)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_coordinator.py -v`
Expected: FAIL with `ModuleNotFoundError: ...coordinator`.

- [ ] **Step 3: Write `coordinator.py`**

```python
"""Periodic refresh of account health and upcoming bookings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.errors import SkeddaAuthError, SkeddaError
from .const import DOMAIN
from .core.provider import Booking, DateRange, Space

if TYPE_CHECKING:
    from .skedda_provider import SkeddaProvider

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=15)
LOOKAHEAD = timedelta(days=30)


@dataclass(slots=True)
class SkeddaData:
    spaces: list[Space] = field(default_factory=list)
    bookings: list[Booking] = field(default_factory=list)


class SkeddaCoordinator(DataUpdateCoordinator[SkeddaData]):
    """Keeps one account's view of Skedda fresh."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, provider: SkeddaProvider
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.provider = provider
        self.authenticated = False

    async def _async_update_data(self) -> SkeddaData:
        try:
            if not self.provider.is_authenticated:
                await self.provider.authenticate()
            now = dt_util.utcnow()
            spaces = await self.provider.list_spaces()
            bookings = await self.provider.list_bookings(
                DateRange(start=now, end=now + LOOKAHEAD)
            )
        except SkeddaAuthError as err:
            self.authenticated = False
            raise ConfigEntryAuthFailed(str(err)) from err
        except SkeddaError as err:
            self.authenticated = False
            raise UpdateFailed(str(err)) from err

        self.authenticated = True
        return SkeddaData(spaces=spaces, bookings=bookings)
```

- [ ] **Step 4: Wire the coordinator into `__init__.py`**

Add `coordinator: SkeddaCoordinator` to `SkeddaRuntimeData`, and inside `async_setup_entry`, before forwarding platforms:

```python
    coordinator = SkeddaCoordinator(hass, entry, provider)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = SkeddaRuntimeData(
        provider=provider, coordinator=coordinator, semaphore=asyncio.Semaphore(1)
    )
```

Construct `provider = SkeddaProvider(client)` into a local variable first so both the coordinator and the runtime data use the same instance.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_coordinator.py tests/test_init.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/skedda_scheduler/coordinator.py \
        custom_components/skedda_scheduler/__init__.py tests/test_coordinator.py
git commit -m "feat: add data update coordinator with reauth handling"
```

---

### Task 18: Persisted attempt history

**Files:**
- Create: `custom_components/skedda_scheduler/store.py`
- Modify: `custom_components/skedda_scheduler/__init__.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `BookingOutcome`.
- Produces:
  - `AttemptStore(hass)` with `async_load()`, `async_record(outcome)`, `history_for(job_id) -> list[dict]`, `last_outcome(job_id) -> dict | None`
  - `MAX_HISTORY_PER_JOB = 50`
  - `SkeddaRuntimeData.store`

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:

```python
"""Persisted booking-attempt history."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.core import HomeAssistant

from custom_components.skedda_scheduler.core.result import (
    AttemptStatus,
    BookingAttempt,
    BookingOutcome,
)
from custom_components.skedda_scheduler.store import MAX_HISTORY_PER_JOB, AttemptStore

WHEN = datetime(2026, 9, 1, 21, 0, tzinfo=UTC)


def _outcome(job_id: str, succeeded: bool) -> BookingOutcome:
    return BookingOutcome(
        job_id=job_id,
        succeeded=succeeded,
        booking_id="bk-1" if succeeded else None,
        space_id=1 if succeeded else None,
        slot_start=WHEN,
        slot_end=WHEN,
        attempts=(BookingAttempt(1, WHEN, AttemptStatus.SUCCESS, 10.0),),
        finished_at=WHEN,
    )


async def test_recorded_outcome_is_readable_back(hass: HomeAssistant) -> None:
    store = AttemptStore(hass)
    await store.async_load()
    await store.async_record(_outcome("job-1", True))

    assert store.last_outcome("job-1")["succeeded"] is True


async def test_history_is_newest_first(hass: HomeAssistant) -> None:
    store = AttemptStore(hass)
    await store.async_load()
    await store.async_record(_outcome("job-1", False))
    await store.async_record(_outcome("job-1", True))

    history = store.history_for("job-1")
    assert [item["succeeded"] for item in history] == [True, False]


async def test_history_is_capped_per_job(hass: HomeAssistant) -> None:
    store = AttemptStore(hass)
    await store.async_load()
    for _ in range(MAX_HISTORY_PER_JOB + 10):
        await store.async_record(_outcome("job-1", True))

    assert len(store.history_for("job-1")) == MAX_HISTORY_PER_JOB


async def test_jobs_do_not_share_history(hass: HomeAssistant) -> None:
    store = AttemptStore(hass)
    await store.async_load()
    await store.async_record(_outcome("job-1", True))

    assert store.history_for("job-2") == []
    assert store.last_outcome("job-2") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: ...store`.

- [ ] **Step 3: Write `store.py`**

```python
"""Persisted history of booking attempts.

A lost race must be explainable afterwards, so every outcome is kept, not just
the last one.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .core.result import BookingOutcome

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.history"
MAX_HISTORY_PER_JOB = 50


class AttemptStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, list[dict[str, Any]]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._data: dict[str, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        self._data = await self._store.async_load() or {}

    async def async_record(self, outcome: BookingOutcome) -> None:
        history = self._data.setdefault(outcome.job_id, [])
        history.insert(0, outcome.as_dict())
        del history[MAX_HISTORY_PER_JOB:]
        await self._store.async_save(self._data)

    def history_for(self, job_id: str) -> list[dict[str, Any]]:
        return list(self._data.get(job_id, []))

    def last_outcome(self, job_id: str) -> dict[str, Any] | None:
        history = self._data.get(job_id)
        return history[0] if history else None
```

- [ ] **Step 4: Wire it into `__init__.py`**

Add `store: AttemptStore` to `SkeddaRuntimeData`, and in `async_setup_entry`:

```python
    store = AttemptStore(hass)
    await store.async_load()
```

then pass `store=store` when building `SkeddaRuntimeData`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py tests/test_init.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/skedda_scheduler/store.py \
        custom_components/skedda_scheduler/__init__.py tests/test_store.py
git commit -m "feat: persist booking attempt history"
```

---

### Task 19: Outcome sinks

**Files:**
- Create: `custom_components/skedda_scheduler/sinks/__init__.py`, `sinks/base.py`, `sinks/ha_event.py`, `sinks/notify.py`
- Test: `tests/test_sinks.py`

**Interfaces:**
- Consumes: `BookingOutcome`, `BookingJob`, `EVENT_BOOKING_SUCCEEDED`, `EVENT_BOOKING_FAILED`.
- Produces:
  - `ResultSink` Protocol with `async_handle(outcome: BookingOutcome, job: BookingJob) -> None`
  - `HaEventSink(hass)`
  - `NotifySink(hass)`
  - `build_default_sinks(hass) -> list[ResultSink]`
  - `async_dispatch(sinks, outcome, job) -> None` — never raises; logs and continues

- [ ] **Step 1: Write the failing test**

`tests/test_sinks.py`:

```python
"""Fan-out of booking outcomes."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.skedda_scheduler.const import (
    EVENT_BOOKING_FAILED,
    EVENT_BOOKING_SUCCEEDED,
)
from custom_components.skedda_scheduler.core.job import BookingJob
from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule
from custom_components.skedda_scheduler.core.result import BookingOutcome
from custom_components.skedda_scheduler.core.window import BookingWindow
from custom_components.skedda_scheduler.sinks import async_dispatch
from custom_components.skedda_scheduler.sinks.ha_event import HaEventSink
from custom_components.skedda_scheduler.sinks.notify import NotifySink

WHEN = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)

JOB = BookingJob(
    job_id="job-1",
    name="Tuesday 18:00",
    space_ids=(1,),
    start_time=time(18, 0),
    duration_minutes=90,
    recurrence=RecurrenceRule(
        frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 1)
    ),
    window=BookingWindow(window_days=7, open_time=time(0, 0)),
    venue_timezone="Europe/Kyiv",
    title="Tennis",
    notify_targets=("notify.telegram",),
)


def _outcome(succeeded: bool) -> BookingOutcome:
    return BookingOutcome(
        job_id="job-1",
        succeeded=succeeded,
        booking_id="bk-1" if succeeded else None,
        space_id=1 if succeeded else None,
        slot_start=WHEN,
        slot_end=WHEN,
        attempts=(),
        finished_at=WHEN,
    )


async def test_success_fires_the_success_event(hass: HomeAssistant) -> None:
    events = async_capture_events(hass, EVENT_BOOKING_SUCCEEDED)
    await HaEventSink(hass).async_handle(_outcome(True), JOB)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["job_name"] == "Tuesday 18:00"
    assert events[0].data["booking_id"] == "bk-1"


async def test_failure_fires_the_failure_event(hass: HomeAssistant) -> None:
    events = async_capture_events(hass, EVENT_BOOKING_FAILED)
    await HaEventSink(hass).async_handle(_outcome(False), JOB)
    await hass.async_block_till_done()

    assert len(events) == 1


async def test_notify_sink_calls_each_configured_target(hass: HomeAssistant) -> None:
    calls: list[tuple[str, dict]] = []

    async def record(call):
        calls.append((call.service, dict(call.data)))

    hass.services.async_register("notify", "telegram", record)
    await NotifySink(hass).async_handle(_outcome(True), JOB)
    await hass.async_block_till_done()

    assert calls and calls[0][0] == "telegram"
    assert "Tuesday 18:00" in calls[0][1]["message"]


async def test_notify_sink_does_nothing_without_targets(hass: HomeAssistant) -> None:
    job = BookingJob(**{**JOB.__dict__, "notify_targets": ()})  # type: ignore[arg-type]
    await NotifySink(hass).async_handle(_outcome(True), job)


async def test_dispatch_continues_after_a_failing_sink(hass: HomeAssistant) -> None:
    broken = AsyncMock()
    broken.async_handle.side_effect = RuntimeError("boom")
    good = AsyncMock()

    await async_dispatch([broken, good], _outcome(True), JOB)

    good.async_handle.assert_awaited_once()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_sinks.py -v`
Expected: FAIL with `ModuleNotFoundError: ...sinks`.

- [ ] **Step 3: Write `sinks/base.py`**

```python
"""What to do with a booking outcome."""

from __future__ import annotations

from typing import Protocol

from ..core.job import BookingJob
from ..core.result import BookingOutcome


class ResultSink(Protocol):
    async def async_handle(self, outcome: BookingOutcome, job: BookingJob) -> None: ...
```

- [ ] **Step 4: Write `sinks/ha_event.py`**

```python
"""Publish the outcome on the Home Assistant event bus.

This is the seam users automate against, so the payload is part of the public
contract — add fields, never rename them.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..const import EVENT_BOOKING_FAILED, EVENT_BOOKING_SUCCEEDED
from ..core.job import BookingJob
from ..core.result import BookingOutcome


class HaEventSink:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_handle(self, outcome: BookingOutcome, job: BookingJob) -> None:
        event = EVENT_BOOKING_SUCCEEDED if outcome.succeeded else EVENT_BOOKING_FAILED
        self._hass.bus.async_fire(event, {"job_name": job.name, **outcome.as_dict()})
```

- [ ] **Step 5: Write `sinks/notify.py`**

```python
"""Tell the user what happened, through whichever notify services they picked."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..core.job import BookingJob
from ..core.result import BookingOutcome

_LOGGER = logging.getLogger(__name__)


def build_message(outcome: BookingOutcome, job: BookingJob) -> str:
    local_start = dt_util.as_local(outcome.slot_start)
    when = local_start.strftime("%a %d %b %H:%M")
    if outcome.succeeded:
        return f"Booked: {job.name} — {when} (booking {outcome.booking_id})"
    return f"Booking failed: {job.name} — {when} ({outcome.failure_reason})"


class NotifySink:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_handle(self, outcome: BookingOutcome, job: BookingJob) -> None:
        message = build_message(outcome, job)
        for target in job.notify_targets:
            domain, _, service = target.partition(".")
            if not service:
                _LOGGER.warning("Ignoring malformed notify target %s", target)
                continue
            await self._hass.services.async_call(
                domain, service, {"message": message}, blocking=False
            )
```

- [ ] **Step 6: Write `sinks/__init__.py`**

```python
"""Outcome fan-out."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from homeassistant.core import HomeAssistant

from ..core.job import BookingJob
from ..core.result import BookingOutcome
from .base import ResultSink
from .ha_event import HaEventSink
from .notify import NotifySink

_LOGGER = logging.getLogger(__name__)

__all__ = ["ResultSink", "async_dispatch", "build_default_sinks"]


def build_default_sinks(hass: HomeAssistant) -> list[ResultSink]:
    return [HaEventSink(hass), NotifySink(hass)]


async def async_dispatch(
    sinks: Sequence[ResultSink], outcome: BookingOutcome, job: BookingJob
) -> None:
    """Run every sink. A broken sink must never lose the other sinks' output."""
    for sink in sinks:
        try:
            await sink.async_handle(outcome, job)
        except Exception:  # noqa: BLE001 - a sink failure must not abort the rest
            _LOGGER.exception("Sink %s failed for job %s", type(sink).__name__, job.job_id)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sinks.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 8: Commit**

```bash
git add custom_components/skedda_scheduler/sinks tests/test_sinks.py
git commit -m "feat: add event and notification outcome sinks"
```

---

### Task 20: The scheduler — arming, firing, the burst loop

This is where the product lives. Everything before it was preparation.

**Files:**
- Create: `custom_components/skedda_scheduler/scheduler.py`
- Modify: `custom_components/skedda_scheduler/__init__.py`, `custom_components/skedda_scheduler/job_factory.py`, `custom_components/skedda_scheduler/flows/account.py`, `custom_components/skedda_scheduler/strings.json`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `build_strategy`, `BookingJob`, `BookingProvider`, `AttemptStore`, sinks, `MAX_ATTEMPTS_PER_RUN`.
- Produces:
  - `job_factory.venue_timezone_for(hass, entry) -> str`
  - `JobRunner(hass, entry, provider, job, sinks, store, semaphore)` with `async_schedule()`, `async_cancel()`, `await async_run_now() -> BookingOutcome`, `next_run: datetime | None`
  - `JobScheduler(hass, entry)` with `async_sync_jobs()`, `async_shutdown()`, `await async_run_now(job_id)`, `runner_for(job_id) -> JobRunner | None`
  - `SkeddaRuntimeData.scheduler`

- [ ] **Step 1: Let the user declare the venue timezone**

The venue's clock decides when the window opens. Add to `flows/account.py`:

```python
from homeassistant.helpers import selector

from ..const import CONF_VENUE_TIMEZONE
```

Add to `STEP_USER_SCHEMA` (and therefore to the reconfigure form, which reuses it):

```python
        vol.Optional(CONF_VENUE_TIMEZONE): selector.TextSelector(),
```

Extend `normalise` to carry it through:

```python
    result = {...}                       # the existing four keys
    if timezone := user_input.get(CONF_VENUE_TIMEZONE):
        result[CONF_VENUE_TIMEZONE] = timezone.strip()
    return result
```

Add to `strings.json` under both `user` and `reconfigure` data blocks:

```json
"venue_timezone": "Venue timezone (leave empty to use Home Assistant's)"
```

Add to `job_factory.py`:

```python
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_VENUE_TIMEZONE


def venue_timezone_for(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """The venue's timezone, falling back to Home Assistant's own."""
    return str(entry.data.get(CONF_VENUE_TIMEZONE) or hass.config.time_zone)
```

- [ ] **Step 2: Write the failing test**

`tests/test_scheduler.py`:

```python
"""The burst loop: how hard we try, and when we stop."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.skedda_scheduler.api.errors import (
    ApiContractError,
    AuthExpiredError,
    RateLimitedError,
    SlotTakenError,
    TooEarlyError,
)
from custom_components.skedda_scheduler.const import MAX_ATTEMPTS_PER_RUN
from custom_components.skedda_scheduler.core.job import BookingJob
from custom_components.skedda_scheduler.core.provider import Booking
from custom_components.skedda_scheduler.core.recurrence import Frequency, RecurrenceRule
from custom_components.skedda_scheduler.core.result import AttemptStatus
from custom_components.skedda_scheduler.core.window import BookingWindow
from custom_components.skedda_scheduler.scheduler import JobRunner
from custom_components.skedda_scheduler.store import AttemptStore

JOB = BookingJob(
    job_id="job-1",
    name="Tuesday 18:00",
    space_ids=(10293,),
    start_time=time(18, 0),
    duration_minutes=90,
    recurrence=RecurrenceRule(
        frequency=Frequency.WEEKLY, weekday=1, season_start=date(2026, 9, 1)
    ),
    window=BookingWindow(window_days=7, open_time=time(0, 0)),
    venue_timezone="Europe/Kyiv",
    title="Tennis (auto)",
)

BOOKING = Booking(
    id="bk-1",
    space_ids=(10293,),
    start=datetime(2026, 9, 8, 15, 0, tzinfo=UTC),
    end=datetime(2026, 9, 8, 16, 30, tzinfo=UTC),
    title="Tennis (auto)",
)


@pytest.fixture
def no_sleep():
    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        yield


@pytest.fixture
async def runner(hass: HomeAssistant, mock_entry, mock_provider):
    mock_entry.add_to_hass(hass)
    store = AttemptStore(hass)
    await store.async_load()
    import asyncio

    return JobRunner(
        hass=hass,
        entry=mock_entry,
        provider=mock_provider,
        job=JOB,
        sinks=[],
        store=store,
        semaphore=asyncio.Semaphore(1),
    )


async def test_a_first_shot_that_lands_stops_the_burst(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.return_value = BOOKING
    outcome = await runner.async_run_now()

    assert outcome.succeeded
    assert outcome.booking_id == "bk-1"
    assert len(outcome.attempts) == 1


async def test_too_early_retries_until_the_window_opens(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.side_effect = [TooEarlyError("nope"), TooEarlyError("nope"), BOOKING]
    outcome = await runner.async_run_now()

    assert outcome.succeeded
    assert [attempt.status for attempt in outcome.attempts] == [
        AttemptStatus.TOO_EARLY,
        AttemptStatus.TOO_EARLY,
        AttemptStatus.SUCCESS,
    ]


async def test_a_taken_slot_ends_the_run_immediately(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.side_effect = SlotTakenError("gone")
    outcome = await runner.async_run_now()

    assert not outcome.succeeded
    assert outcome.failure_reason == "slot_taken"
    assert len(outcome.attempts) == 1


async def test_rate_limiting_ends_the_run_immediately(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.side_effect = RateLimitedError("slow down")
    outcome = await runner.async_run_now()

    assert outcome.failure_reason == "rate_limited"
    assert len(outcome.attempts) == 1


async def test_a_contract_error_ends_the_run_immediately(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.side_effect = ApiContractError("shape changed")
    outcome = await runner.async_run_now()

    assert outcome.failure_reason == "contract_error"
    assert len(outcome.attempts) == 1


async def test_an_expired_session_is_re_authenticated_once_then_retried(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.side_effect = [AuthExpiredError("expired"), BOOKING]
    outcome = await runner.async_run_now()

    assert outcome.succeeded
    assert mock_provider.authenticate.await_count >= 1


async def test_a_second_expiry_is_not_retried_again(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.side_effect = AuthExpiredError("expired")
    outcome = await runner.async_run_now()

    assert not outcome.succeeded
    assert len(outcome.attempts) == 2


async def test_attempts_never_exceed_the_per_run_ceiling(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.side_effect = TooEarlyError("nope")
    outcome = await runner.async_run_now()

    assert len(outcome.attempts) <= MAX_ATTEMPTS_PER_RUN


async def test_the_outcome_is_written_to_the_history_store(
    runner, mock_provider, no_sleep
) -> None:
    mock_provider.book.return_value = BOOKING
    await runner.async_run_now()

    assert runner.store.last_outcome("job-1")["succeeded"] is True


async def test_next_run_reports_the_next_window_open_instant(runner) -> None:
    with patch(
        "custom_components.skedda_scheduler.scheduler.dt_util.utcnow",
        return_value=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
    ):
        assert runner.next_run == datetime(2026, 9, 1, 21, 0, tzinfo=UTC)
```

Note `test_next_run_reports_the_next_window_open_instant` asserts the window for the 2026-09-08 slot, which opened on 2026-09-01 — already in the past relative to the frozen now. That is correct and deliberate: `next_run` reports the computed instant, and `async_schedule` is responsible for skipping windows that have already passed. Assert that too:

```python
async def test_scheduling_skips_a_window_that_has_already_closed(runner) -> None:
    with patch(
        "custom_components.skedda_scheduler.scheduler.dt_util.utcnow",
        return_value=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
    ):
        runner.async_schedule()
    assert runner.armed_for is None or runner.armed_for > datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: ...scheduler`.

- [ ] **Step 4: Write `scheduler.py`**

```python
"""Arming, firing and retrying booking attempts.

The timing arithmetic lives in core/strategy.py. This module is the part that
touches the clock and the network: it wakes up early, warms the session,
sleeps to the millisecond, and classifies what comes back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .api.errors import (
    ApiContractError,
    AuthExpiredError,
    RateLimitedError,
    SkeddaAuthError,
    SkeddaConnectionError,
    SkeddaError,
    SlotTakenError,
    TooEarlyError,
)
from .const import MAX_ATTEMPTS_PER_RUN, SUBENTRY_TYPE_JOB
from .core.job import BookingJob
from .core.provider import BookingProvider, BookingRequest
from .core.result import AttemptStatus, BookingAttempt, BookingOutcome
from .core.strategy import build_strategy
from .job_factory import build_job, venue_timezone_for
from .sinks import ResultSink, async_dispatch, build_default_sinks
from .store import AttemptStore

_LOGGER = logging.getLogger(__name__)

# Checked in order, because AuthExpiredError is a SkeddaAuthError.
_ERROR_STATUS: tuple[tuple[type[SkeddaError], AttemptStatus], ...] = (
    (TooEarlyError, AttemptStatus.TOO_EARLY),
    (SlotTakenError, AttemptStatus.SLOT_TAKEN),
    (RateLimitedError, AttemptStatus.RATE_LIMITED),
    (ApiContractError, AttemptStatus.CONTRACT_ERROR),
    (SkeddaConnectionError, AttemptStatus.CONNECTION_ERROR),
    (AuthExpiredError, AttemptStatus.AUTH_FAILED),
    (SkeddaAuthError, AttemptStatus.AUTH_FAILED),
)

# Anything else means the slot is gone, we are being throttled, or Skedda
# changed — keep trying only where trying again can plausibly succeed.
_RETRYABLE = frozenset({AttemptStatus.TOO_EARLY, AttemptStatus.CONNECTION_ERROR})


def _status_for(error: SkeddaError) -> AttemptStatus:
    for error_type, status in _ERROR_STATUS:
        if isinstance(error, error_type):
            return status
    return AttemptStatus.CONTRACT_ERROR


class JobRunner:
    """Owns one booking job's schedule and its attempt loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        provider: BookingProvider,
        job: BookingJob,
        sinks: Sequence[ResultSink],
        store: AttemptStore,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.provider = provider
        self.job = job
        self.sinks = list(sinks)
        self.store = store
        self.semaphore = semaphore
        self.armed_for: datetime | None = None
        self._unsub: callable | None = None  # type: ignore[valid-type]

    @property
    def next_run(self) -> datetime | None:
        return self.job.next_window_open(dt_util.utcnow())

    @callback
    def async_schedule(self) -> None:
        """Arm the next run, skipping any window that has already closed."""
        self.async_cancel()
        if not self.job.enabled:
            return

        now = dt_util.utcnow()
        opens_at = self.job.next_window_open(now)
        # Walk forward over already-closed windows.
        probe = now
        while opens_at is not None and opens_at <= now:
            probe = probe + timedelta(days=1)
            opens_at = self.job.next_window_open(probe)
        if opens_at is None:
            _LOGGER.debug("Job %s has no future window; season is over", self.job.job_id)
            return

        arm_at = build_strategy(self.job.strategy).plan(opens_at).arm_at
        self.armed_for = max(arm_at, now)
        self._unsub = async_track_point_in_utc_time(
            self.hass, self._async_armed, self.armed_for
        )
        _LOGGER.debug("Job %s armed for %s (window opens %s)",
                      self.job.job_id, self.armed_for, opens_at)

    @callback
    def async_cancel(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self.armed_for = None

    async def _async_armed(self, _now: datetime) -> None:
        self._unsub = None
        opens_at = self.job.next_window_open(dt_util.utcnow())
        if opens_at is None:
            return
        try:
            await self._async_warm_up()
            await self._async_execute(opens_at)
        finally:
            self.async_schedule()

    async def _async_warm_up(self) -> None:
        """Authenticate and open a connection so the first shot pays no setup cost.

        Each call also feeds the clock-offset estimator via the Date header.
        """
        try:
            if not self.provider.is_authenticated:
                await self.provider.authenticate()
            await self.provider.list_spaces()
        except SkeddaError as err:
            _LOGGER.warning("Warm-up for job %s failed: %s", self.job.job_id, err)

    async def async_run_now(self) -> BookingOutcome:
        await self._async_warm_up()
        return await self._async_execute(dt_util.utcnow())

    async def _async_execute(self, opens_at: datetime) -> BookingOutcome:
        slot = self.job.next_slot(dt_util.utcnow())
        if slot is None:
            outcome = BookingOutcome(
                job_id=self.job.job_id,
                succeeded=False,
                booking_id=None,
                space_id=None,
                slot_start=opens_at,
                slot_end=opens_at,
                attempts=(),
                finished_at=dt_util.utcnow(),
            )
            await self._async_finish(outcome)
            return outcome

        slot_start, slot_end = slot
        request = BookingRequest(
            space_id=self.job.primary_space_id,
            start=slot_start,
            end=slot_end,
            title=self.job.title,
            lock_state=self.job.lock_state,
        )
        plan = build_strategy(self.job.strategy).plan(opens_at)

        attempts: list[BookingAttempt] = []
        booking_id: str | None = None
        reauth_used = False

        async with self.semaphore:
            for number, fire_at in enumerate(plan.fire_times, start=1):
                if number > MAX_ATTEMPTS_PER_RUN:
                    break
                await self._async_sleep_until(fire_at)

                started = dt_util.utcnow()
                try:
                    booking = await self.provider.book(request)
                except SkeddaError as err:
                    status = _status_for(err)
                    detail = str(err)
                else:
                    status, detail = AttemptStatus.SUCCESS, None
                    booking_id = booking.id

                attempts.append(
                    BookingAttempt(
                        attempt_no=number,
                        fired_at=started,
                        status=status,
                        latency_ms=(dt_util.utcnow() - started).total_seconds() * 1000,
                        detail=detail,
                    )
                )

                if status is AttemptStatus.SUCCESS:
                    break
                if status is AttemptStatus.AUTH_FAILED and not reauth_used:
                    reauth_used = True
                    try:
                        await self.provider.authenticate()
                    except SkeddaError:
                        break
                    continue
                if status not in _RETRYABLE:
                    break

        outcome = BookingOutcome(
            job_id=self.job.job_id,
            succeeded=booking_id is not None,
            booking_id=booking_id,
            space_id=self.job.primary_space_id if booking_id else None,
            slot_start=slot_start,
            slot_end=slot_end,
            attempts=tuple(attempts),
            finished_at=dt_util.utcnow(),
        )
        await self._async_finish(outcome)
        return outcome

    async def _async_finish(self, outcome: BookingOutcome) -> None:
        await self.store.async_record(outcome)
        await async_dispatch(self.sinks, outcome, self.job)

    async def _async_sleep_until(self, server_instant: datetime) -> None:
        """Sleep until our clock reads the moment Skedda's clock reads ``server_instant``.

        asyncio.sleep schedules through loop.call_later, so this keeps the
        sub-second resolution that async_track_point_in_time cannot give.
        """
        clock = getattr(getattr(self.provider, "client", None), "clock", None)
        target = clock.local_instant_for(server_instant) if clock else server_instant
        delay = (target - dt_util.utcnow()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)


class JobScheduler:
    """Keeps one runner per enabled job subentry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._runners: dict[str, JobRunner] = {}

    def runner_for(self, job_id: str) -> JobRunner | None:
        return self._runners.get(job_id)

    @callback
    def async_sync_jobs(self) -> None:
        runtime = self.entry.runtime_data
        timezone = venue_timezone_for(self.hass, self.entry)
        sinks = build_default_sinks(self.hass)

        for runner in self._runners.values():
            runner.async_cancel()
        self._runners.clear()

        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_JOB:
                continue
            try:
                job = build_job(subentry_id, subentry.data, timezone)
            except (ValueError, KeyError):
                _LOGGER.exception("Ignoring misconfigured job %s", subentry_id)
                continue
            runner = JobRunner(
                hass=self.hass,
                entry=self.entry,
                provider=runtime.provider,
                job=job,
                sinks=sinks,
                store=runtime.store,
                semaphore=runtime.semaphore,
            )
            self._runners[subentry_id] = runner
            runner.async_schedule()

    @callback
    def async_shutdown(self) -> None:
        for runner in self._runners.values():
            runner.async_cancel()
        self._runners.clear()

    async def async_run_now(self, job_id: str) -> BookingOutcome | None:
        runner = self._runners.get(job_id)
        if runner is None:
            _LOGGER.warning("No runner for job %s", job_id)
            return None
        return await runner.async_run_now()
```

- [ ] **Step 5: Wire the scheduler into `__init__.py`**

Add `scheduler: JobScheduler` to `SkeddaRuntimeData`. After building the runtime data and forwarding platforms:

```python
    scheduler = JobScheduler(hass, entry)
    entry.runtime_data.scheduler = scheduler
    scheduler.async_sync_jobs()
    entry.async_on_unload(scheduler.async_shutdown)
```

Set `scheduler=None` initially in the dataclass by typing the field `JobScheduler | None = None`, because `JobScheduler` reads `entry.runtime_data` and must be created after it exists.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, everything green.

- [ ] **Step 8: Commit**

```bash
git add custom_components/skedda_scheduler tests/test_scheduler.py
git commit -m "feat: add booking scheduler with sniper burst loop"
```

---

### Task 21: Sensor and binary sensor entities

**Files:**
- Create: `custom_components/skedda_scheduler/entity.py`, `custom_components/skedda_scheduler/sensor.py`, `custom_components/skedda_scheduler/binary_sensor.py`
- Modify: `custom_components/skedda_scheduler/__init__.py`, `custom_components/skedda_scheduler/strings.json`
- Test: `tests/test_sensor.py`, `tests/test_binary_sensor.py`

**Interfaces:**
- Consumes: `SkeddaCoordinator`, `JobScheduler`, `AttemptStore`, `build_job`, `venue_timezone_for`.
- Produces:
  - `entity.SkeddaAccountEntity`, `entity.SkeddaJobEntity` — `CoordinatorEntity` bases with device info
  - `sensor.NextRunSensor` (`device_class: timestamp`), `sensor.LastOutcomeSensor`
  - `binary_sensor.AccountAuthenticatedBinarySensor` (`device_class: problem`, inverted)
  - `PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]`

- [ ] **Step 1: Write the failing tests**

`tests/test_sensor.py`:

```python
"""Per-job sensors."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.helpers import add_job_subentry


async def test_each_job_gets_a_next_run_and_last_outcome_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_provider
) -> None:
    add_job_subentry(mock_entry)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.tuesday_18_00_next_run") is not None
    assert hass.states.get("sensor.tuesday_18_00_last_outcome") is not None


async def test_next_run_sensor_is_a_timestamp(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    add_job_subentry(mock_entry)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.tuesday_18_00_next_run")
    assert state.attributes["device_class"] == "timestamp"


async def test_last_outcome_is_unknown_before_the_first_run(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    add_job_subentry(mock_entry)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.tuesday_18_00_last_outcome").state == "unknown"
```

`tests/helpers.py`:

```python
"""Test helpers shared across entity tests."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skedda_scheduler.const import SUBENTRY_TYPE_JOB

JOB_DATA = {
    "name": "Tuesday 18:00",
    "space_id": 10293,
    "weekday": 1,
    "start_time": "18:00:00",
    "duration_minutes": 90,
    "window_days": 7,
    "window_open_time": "00:00:00",
    "frequency": "weekly",
    "season_start": "2026-09-01",
    "season_end": None,
    "title": "Tennis (auto)",
    "lock_state": "Locked",
    "strategy": "sniper",
    "notify_targets": [],
    "enabled": True,
}


def add_job_subentry(entry: MockConfigEntry, subentry_id: str = "sub-1") -> None:
    entry.subentries = {
        subentry_id: ConfigSubentryData(
            data=JOB_DATA,
            subentry_id=subentry_id,
            subentry_type=SUBENTRY_TYPE_JOB,
            title="Tuesday 18:00",
            unique_id=None,
        )
    }
```

If `MockConfigEntry` in your installed `pytest-homeassistant-custom-component` takes subentries as a constructor argument instead, pass them there — check its signature rather than forcing the assignment.

`tests/test_binary_sensor.py`:

```python
"""Per-account authentication sensor."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.skedda_scheduler.api.errors import SkeddaConnectionError


async def test_authenticated_account_reports_no_problem(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.main_account_oleh_authentication")
    assert state.state == "off"
    assert state.attributes["device_class"] == "problem"


async def test_a_failing_account_reports_a_problem(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    mock_provider.list_spaces.side_effect = SkeddaConnectionError("down")
    await mock_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.main_account_oleh_authentication").state == "on"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_sensor.py tests/test_binary_sensor.py -v`
Expected: FAIL — the entities do not exist.

- [ ] **Step 3: Write `entity.py`**

```python
"""Shared entity bases and device wiring.

An account is a device; each booking job is a device attached to it, so the
Home Assistant UI groups a job's entities together and lets a user rename or
disable a whole job at once.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_VENUE, DOMAIN
from .coordinator import SkeddaCoordinator
from .core.job import BookingJob


class SkeddaAccountEntity(CoordinatorEntity[SkeddaCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SkeddaCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        venue = entry.data[CONF_VENUE]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Skedda",
            model="Account",
            configuration_url=f"https://{venue}.skedda.com",
        )


class SkeddaJobEntity(CoordinatorEntity[SkeddaCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SkeddaCoordinator, job: BookingJob) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.job = job
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}:{job.job_id}")},
            name=job.name,
            manufacturer="Skedda",
            model="Booking job",
            via_device=(DOMAIN, entry.entry_id),
        )
```

- [ ] **Step 4: Write `sensor.py`**

```python
"""Per-job state sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .const import SUBENTRY_TYPE_JOB
from .entity import SkeddaJobEntity
from .job_factory import build_job, venue_timezone_for

NEXT_RUN = SensorEntityDescription(
    key="next_run", translation_key="next_run", device_class=SensorDeviceClass.TIMESTAMP
)
LAST_OUTCOME = SensorEntityDescription(key="last_outcome", translation_key="last_outcome")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    timezone = venue_timezone_for(hass, entry)

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        job = build_job(subentry_id, subentry.data, timezone)
        async_add_entities(
            [
                NextRunSensor(runtime.coordinator, job, entry),
                LastOutcomeSensor(runtime.coordinator, job, entry),
            ],
            config_subentry_id=subentry_id,
        )


class NextRunSensor(SkeddaJobEntity, SensorEntity):
    entity_description = NEXT_RUN

    def __init__(self, coordinator, job, entry) -> None:  # noqa: ANN001
        super().__init__(coordinator, job)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:next_run"

    @property
    def native_value(self) -> datetime | None:
        scheduler = self._entry.runtime_data.scheduler
        runner = scheduler.runner_for(self.job.job_id) if scheduler else None
        return runner.armed_for if runner else None


class LastOutcomeSensor(SkeddaJobEntity, SensorEntity):
    entity_description = LAST_OUTCOME

    def __init__(self, coordinator, job, entry) -> None:  # noqa: ANN001
        super().__init__(coordinator, job)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:last_outcome"

    @property
    def native_value(self) -> str | None:
        last = self._entry.runtime_data.store.last_outcome(self.job.job_id)
        if last is None:
            return None
        return "success" if last["succeeded"] else str(last["failure_reason"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self._entry.runtime_data.store.last_outcome(self.job.job_id) or {}
        return {
            "booking_id": last.get("booking_id"),
            "slot_start": last.get("slot_start"),
            "attempts": last.get("attempts"),
            "finished_at": last.get("finished_at"),
        }
```

- [ ] **Step 5: Write `binary_sensor.py`**

```python
"""Per-account authentication health."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .entity import SkeddaAccountEntity

AUTHENTICATION = BinarySensorEntityDescription(
    key="authentication",
    translation_key="authentication",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AccountAuthenticatedBinarySensor(entry.runtime_data.coordinator)])


class AccountAuthenticatedBinarySensor(SkeddaAccountEntity, BinarySensorEntity):
    entity_description = AUTHENTICATION

    def __init__(self, coordinator) -> None:  # noqa: ANN001
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:authentication"

    @property
    def is_on(self) -> bool:
        """True means there is a problem — the account is not usable."""
        return not self.coordinator.authenticated
```

- [ ] **Step 6: Register the platforms and add entity strings**

In `__init__.py`: `PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]`.

In `strings.json`, add a top-level `entity` block:

```json
  "entity": {
    "sensor": {
      "next_run": { "name": "Next run" },
      "last_outcome": { "name": "Last outcome" }
    },
    "binary_sensor": {
      "authentication": { "name": "Authentication" }
    }
  }
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sensor.py tests/test_binary_sensor.py -v`
Expected: PASS, 5 tests. If entity ids differ from the ones asserted, fix the assertions to match what Home Assistant actually generates — do not rename the entities to fit the test.

- [ ] **Step 8: Commit**

```bash
git add custom_components/skedda_scheduler tests/helpers.py \
        tests/test_sensor.py tests/test_binary_sensor.py
git commit -m "feat: add job and account sensor entities"
```

---

### Task 22: Switch, button and services

**Files:**
- Create: `custom_components/skedda_scheduler/switch.py`, `custom_components/skedda_scheduler/button.py`, `custom_components/skedda_scheduler/services.yaml`, `custom_components/skedda_scheduler/services.py`
- Modify: `custom_components/skedda_scheduler/__init__.py`, `custom_components/skedda_scheduler/strings.json`
- Test: `tests/test_switch.py`, `tests/test_services.py`

**Interfaces:**
- Consumes: `JobScheduler`, `SkeddaCoordinator`, `CONF_ENABLED`.
- Produces:
  - `switch.JobEnabledSwitch` — writes `enabled` back into the subentry and re-syncs the scheduler
  - `button.RunNowButton`
  - `services.async_setup_services(hass)` registering `trigger_job_now` and `refresh_spaces`
  - `PLATFORMS` extended with `Platform.BUTTON` and `Platform.SWITCH`

- [ ] **Step 1: Write the failing tests**

`tests/test_switch.py`:

```python
"""Enabling and disabling a booking job."""

from __future__ import annotations

from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant

from tests.helpers import add_job_subentry

ENTITY = "switch.tuesday_18_00_job_enabled"


async def _setup(hass: HomeAssistant, entry) -> None:
    add_job_subentry(entry)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_new_job_is_enabled(hass: HomeAssistant, mock_entry, mock_provider) -> None:
    await _setup(hass, mock_entry)
    assert hass.states.get(ENTITY).state == "on"


async def test_turning_the_switch_off_persists_into_the_subentry(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    await _setup(hass, mock_entry)
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {"entity_id": ENTITY}, blocking=True
    )
    await hass.async_block_till_done()

    assert mock_entry.subentries["sub-1"].data["enabled"] is False


async def test_turning_the_switch_back_on_re_arms_the_job(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    await _setup(hass, mock_entry)
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {"entity_id": ENTITY}, blocking=True
    )
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {"entity_id": ENTITY}, blocking=True
    )
    await hass.async_block_till_done()

    assert mock_entry.runtime_data.scheduler.runner_for("sub-1").job.enabled is True
```

`tests/test_services.py`:

```python
"""Custom services."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.skedda_scheduler.const import DOMAIN
from custom_components.skedda_scheduler.core.provider import Booking
from tests.helpers import add_job_subentry

BOOKING = Booking(
    id="bk-1",
    space_ids=(10293,),
    start=datetime(2026, 9, 8, 15, 0, tzinfo=UTC),
    end=datetime(2026, 9, 8, 16, 30, tzinfo=UTC),
    title="Tennis (auto)",
)


async def test_services_are_registered(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "trigger_job_now")
    assert hass.services.has_service(DOMAIN, "refresh_spaces")


async def test_trigger_job_now_runs_the_job(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    add_job_subentry(mock_entry)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    mock_provider.book.return_value = BOOKING
    with patch("custom_components.skedda_scheduler.scheduler.asyncio.sleep"):
        await hass.services.async_call(
            DOMAIN, "trigger_job_now", {"job_id": "sub-1"}, blocking=True
        )

    mock_provider.book.assert_awaited()


async def test_refresh_spaces_triggers_a_coordinator_refresh(
    hass: HomeAssistant, mock_entry, mock_provider
) -> None:
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    mock_provider.list_spaces.reset_mock()
    await hass.services.async_call(DOMAIN, "refresh_spaces", {}, blocking=True)
    await hass.async_block_till_done()

    mock_provider.list_spaces.assert_awaited()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_switch.py tests/test_services.py -v`
Expected: FAIL — neither the switch platform nor the services exist.

- [ ] **Step 3: Write `switch.py`**

```python
"""Enable or disable a booking job without deleting it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .const import CONF_ENABLED, SUBENTRY_TYPE_JOB
from .entity import SkeddaJobEntity
from .job_factory import build_job, venue_timezone_for

JOB_ENABLED = SwitchEntityDescription(key="job_enabled", translation_key="job_enabled")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    timezone = venue_timezone_for(hass, entry)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        job = build_job(subentry_id, subentry.data, timezone)
        async_add_entities(
            [JobEnabledSwitch(entry.runtime_data.coordinator, job, entry)],
            config_subentry_id=subentry_id,
        )


class JobEnabledSwitch(SkeddaJobEntity, SwitchEntity):
    entity_description = JOB_ENABLED

    def __init__(self, coordinator, job, entry) -> None:  # noqa: ANN001
        super().__init__(coordinator, job)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:job_enabled"

    @property
    def is_on(self) -> bool:
        subentry = self._entry.subentries.get(self.job.job_id)
        return bool(subentry.data.get(CONF_ENABLED, True)) if subentry else False

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        subentry = self._entry.subentries[self.job.job_id]
        self.hass.config_entries.async_update_subentry(
            self._entry, subentry, data={**subentry.data, CONF_ENABLED: enabled}
        )
        self._entry.runtime_data.scheduler.async_sync_jobs()
        self.async_write_ha_state()
```

- [ ] **Step 4: Write `button.py`**

```python
"""Run a booking job right now, without waiting for its window."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SkeddaConfigEntry
from .const import SUBENTRY_TYPE_JOB
from .entity import SkeddaJobEntity
from .job_factory import build_job, venue_timezone_for

RUN_NOW = ButtonEntityDescription(key="run_now", translation_key="run_now")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkeddaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    timezone = venue_timezone_for(hass, entry)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        job = build_job(subentry_id, subentry.data, timezone)
        async_add_entities(
            [RunNowButton(entry.runtime_data.coordinator, job, entry)],
            config_subentry_id=subentry_id,
        )


class RunNowButton(SkeddaJobEntity, ButtonEntity):
    entity_description = RUN_NOW

    def __init__(self, coordinator, job, entry) -> None:  # noqa: ANN001
        super().__init__(coordinator, job)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:{job.job_id}:run_now"

    async def async_press(self) -> None:
        await self._entry.runtime_data.scheduler.async_run_now(self.job.job_id)
```

- [ ] **Step 5: Write `services.py` and `services.yaml`**

```python
"""Custom services for Skedda Scheduler."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

SERVICE_TRIGGER_JOB_NOW = "trigger_job_now"
SERVICE_REFRESH_SPACES = "refresh_spaces"

TRIGGER_SCHEMA = vol.Schema({vol.Required("job_id"): cv.string})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def _trigger_job_now(call: ServiceCall) -> None:
        job_id = call.data["job_id"]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            scheduler = entry.runtime_data.scheduler
            if scheduler and scheduler.runner_for(job_id):
                await scheduler.async_run_now(job_id)
                return

    async def _refresh_spaces(_call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_TRIGGER_JOB_NOW, _trigger_job_now, schema=TRIGGER_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_SPACES, _refresh_spaces)
```

`services.yaml`:

```yaml
trigger_job_now:
  fields:
    job_id:
      required: true
      example: "01JABCDEF0123456789"
      selector:
        text:

refresh_spaces:
```

- [ ] **Step 6: Register everything**

In `__init__.py`: extend `PLATFORMS` to `[Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]`, and add

```python
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True
```

with `from homeassistant.helpers.typing import ConfigType` and `from .services import async_setup_services`.

Add to `strings.json`:

```json
  "services": {
    "trigger_job_now": {
      "name": "Trigger job now",
      "description": "Runs a booking job immediately instead of waiting for its window to open.",
      "fields": {
        "job_id": {
          "name": "Job",
          "description": "The subentry id of the booking job. It is shown in the job's diagnostics."
        }
      }
    },
    "refresh_spaces": {
      "name": "Refresh spaces",
      "description": "Re-reads courts and upcoming bookings from Skedda for every configured account."
    }
  }
```

and to the `entity` block:

```json
    "switch": { "job_enabled": { "name": "Job enabled" } },
    "button": { "run_now": { "name": "Run now" } }
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_switch.py tests/test_services.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 8: Commit**

```bash
git add custom_components/skedda_scheduler tests/test_switch.py tests/test_services.py
git commit -m "feat: add job switch, run-now button and custom services"
```

---

### Task 23: Diagnostics and repairs

**Files:**
- Create: `custom_components/skedda_scheduler/diagnostics.py`, `custom_components/skedda_scheduler/repairs.py`
- Modify: `custom_components/skedda_scheduler/scheduler.py`, `custom_components/skedda_scheduler/strings.json`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `AttemptStore`, `SkeddaCoordinator`, `ApiContractError`.
- Produces:
  - `async_get_config_entry_diagnostics(hass, entry) -> dict`
  - `repairs.async_raise_contract_issue(hass, entry_id, detail)` and `async_clear_contract_issue(hass, entry_id)`

- [ ] **Step 1: Write the failing test**

`tests/test_diagnostics.py`:

```python
"""Diagnostics must be useful and must not leak credentials."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from tests.helpers import add_job_subentry


async def test_diagnostics_redact_the_password_and_email(
    hass: HomeAssistant, hass_client, mock_entry, mock_provider
) -> None:
    add_job_subentry(mock_entry)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_entry)

    assert "secret" not in json.dumps(result)
    assert "user@example.com" not in json.dumps(result)


async def test_diagnostics_include_jobs_and_history(
    hass: HomeAssistant, hass_client, mock_entry, mock_provider
) -> None:
    add_job_subentry(mock_entry)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_entry)

    assert result["jobs"][0]["job_id"] == "sub-1"
    assert "history" in result["jobs"][0]
    assert "clock_offset_seconds" in result
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_diagnostics.py -v`
Expected: FAIL with `ModuleNotFoundError: ...diagnostics`.

- [ ] **Step 3: Write `diagnostics.py`**

```python
"""Diagnostics: enough to explain a lost race, never enough to log in."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import SkeddaConfigEntry
from .const import SUBENTRY_TYPE_JOB

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SkeddaConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    clock = getattr(getattr(runtime.provider, "client", None), "clock", None)

    jobs: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_JOB:
            continue
        runner = runtime.scheduler.runner_for(subentry_id) if runtime.scheduler else None
        jobs.append(
            {
                "job_id": subentry_id,
                "config": dict(subentry.data),
                "armed_for": runner.armed_for.isoformat()
                if runner and runner.armed_for
                else None,
                "history": runtime.store.history_for(subentry_id),
            }
        )

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "authenticated": runtime.coordinator.authenticated,
        "clock_offset_seconds": getattr(clock, "offset_seconds", None),
        "clock_samples": getattr(clock, "samples", None),
        "spaces": [
            {"id": space.id, "name": space.name}
            for space in (runtime.coordinator.data.spaces if runtime.coordinator.data else [])
        ],
        "jobs": jobs,
    }
```

- [ ] **Step 4: Write `repairs.py`**

```python
"""Tell the user, in their own language, that Skedda changed its API."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

CONTRACT_ISSUE = "api_contract_changed"


def _issue_id(entry_id: str) -> str:
    return f"{CONTRACT_ISSUE}_{entry_id}"


def async_raise_contract_issue(hass: HomeAssistant, entry_id: str, detail: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=CONTRACT_ISSUE,
        translation_placeholders={"detail": detail[:200]},
    )


def async_clear_contract_issue(hass: HomeAssistant, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))
```

- [ ] **Step 5: Raise the issue from the burst loop**

In `scheduler.py`, inside `_async_execute`, after computing `status`:

```python
                if status is AttemptStatus.CONTRACT_ERROR:
                    async_raise_contract_issue(self.hass, self.entry.entry_id, detail or "")
                elif status is AttemptStatus.SUCCESS:
                    async_clear_contract_issue(self.hass, self.entry.entry_id)
```

with `from .repairs import async_clear_contract_issue, async_raise_contract_issue`.

Add to `strings.json`:

```json
  "issues": {
    "api_contract_changed": {
      "title": "Skedda changed its API",
      "description": "Skedda returned something this integration does not recognise, so booking was abandoned. Please open an issue with the diagnostics attached.\n\nDetail: {detail}"
    }
  }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_diagnostics.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 7: Commit**

```bash
git add custom_components/skedda_scheduler tests/test_diagnostics.py
git commit -m "feat: add redacted diagnostics and API-change repair issue"
```

---

### Task 24: Translations, documentation and the v0.1.0 release

**Files:**
- Create: `custom_components/skedda_scheduler/translations/en.json`, `custom_components/skedda_scheduler/translations/uk.json`, `.github/workflows/release.yml`, `CONTRIBUTING.md`
- Modify: `README.md`
- Test: `tests/test_translations.py`

**Interfaces:**
- Consumes: `strings.json`.
- Produces: an installable, documented, released v0.1.0.

- [ ] **Step 1: Write the failing test**

`tests/test_translations.py`:

```python
"""Translations must stay in step with strings.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

COMPONENT = Path("custom_components/skedda_scheduler")
LANGUAGES = ["en", "uk"]


def _keys(node, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    found: set[str] = set()
    for key, value in node.items():
        found |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return found


@pytest.mark.parametrize("language", LANGUAGES)
def test_translation_has_every_key_from_strings_json(language: str) -> None:
    source = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    translated = json.loads(
        (COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    missing = _keys(source) - _keys(translated)
    assert not missing, f"{language}.json is missing: {sorted(missing)}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_translation_has_no_keys_strings_json_lacks(language: str) -> None:
    source = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    translated = json.loads(
        (COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    extra = _keys(translated) - _keys(source)
    assert not extra, f"{language}.json has stale keys: {sorted(extra)}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_translations.py -v`
Expected: FAIL — the `translations/` directory does not exist.

- [ ] **Step 3: Create the translations**

`translations/en.json` is a byte-for-byte copy of `strings.json`:

```bash
cp custom_components/skedda_scheduler/strings.json \
   custom_components/skedda_scheduler/translations/en.json
```

`translations/uk.json` is the same structure with Ukrainian values. Translate every leaf string. Keep the JSON keys identical. Sample of the tone to match:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Додати акаунт Skedda",
        "description": "Субдомен закладу видно в адресі вашого сайту Skedda: для https://myclub.skedda.com це myclub.",
        "data": {
          "venue": "Субдомен закладу",
          "email": "Email",
          "password": "Пароль",
          "alias": "Назва акаунту",
          "venue_timezone": "Часовий пояс закладу (порожньо — узяти з Home Assistant)"
        }
      }
    }
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_translations.py -v`
Expected: PASS, 4 tests. If a key is reported missing, add it — do not delete it from `strings.json`.

- [ ] **Step 5: Write the README**

Replace `README.md` with sections in this order:

1. **What it does** — one paragraph: books the same court every week the instant the window opens.
2. **Warning** — Skedda has no public write API; this integration drives the private one used by Skedda's own web app, it can break without notice, and automating bookings may conflict with Skedda's Terms of Service. Use it on your own account, at your own discretion.
3. **Install via HACS** — HACS → three-dot menu → Custom repositories → `https://github.com/DKorytkin/ha-skedda-integration`, category *Integration* → Install → restart Home Assistant.
4. **Add an account** — Settings → Devices & Services → Add Integration → *Skedda Scheduler*; explain venue subdomain, and that the venue timezone is optional.
5. **Add a booking job** — on the account entry, *Add booking job*; explain each field, especially *Booking opens this many days before* and *Booking opens at*.
6. **What you get** — the table of entities from the spec, plus the two events `skedda_scheduler_booking_succeeded` and `skedda_scheduler_booking_failed` with their payload fields.
7. **Automation example** — an automation triggering on `skedda_scheduler_booking_failed` that sends a notification.
8. **Troubleshooting** — where to find diagnostics, what the *Skedda changed its API* repair means.
9. **Roadmap** — the milestone table from the spec.

- [ ] **Step 6: Write `CONTRIBUTING.md`**

Cover: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run mypy custom_components`; the layering rule and that `tests/test_layering.py` enforces it; that any Skedda API change goes in `api/endpoints.py` and `docs/skedda-api-contract.md` together.

- [ ] **Step 7: Add the release workflow**

`.github/workflows/release.yml`:

```yaml
name: Release

on:
  release:
    types: [published]

jobs:
  verify-version:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Manifest version must match the tag
        run: |
          manifest=$(jq -r .version custom_components/skedda_scheduler/manifest.json)
          tag="${GITHUB_REF_NAME#v}"
          test "$manifest" = "$tag" || {
            echo "manifest.json says $manifest but the tag says $tag"; exit 1;
          }
```

- [ ] **Step 8: Run the whole suite and the linters**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy custom_components && uv run pytest`
Expected: all green, coverage at or above 90% overall and 100% on `core/`.

- [ ] **Step 9: Commit and release**

```bash
git add README.md CONTRIBUTING.md custom_components/skedda_scheduler/translations \
        .github/workflows/release.yml tests/test_translations.py
git commit -m "docs: add README, contributing guide and translations"
git tag v0.1.0
git push origin main --tags
gh release create v0.1.0 --title "v0.1.0" --generate-notes
```

Then set the GitHub repository description and add the topics `home-assistant`, `hacs`, `skedda`, `integration` — HACS validation checks for them.

---

## Self-Review

Run against the spec after finishing the plan, before handing it to an executor.

**Spec coverage.** Every spec section maps to a task:

| Spec section | Tasks |
|---|---|
| §2.1 private API discovery | 2 |
| §4.1 layering | 1 (enforced), 3–13 |
| §4.2 directory layout | 1, 3–24 |
| §4.3 six extension seams | 13 (provider), 4 (contract), 12 (strategy), 9 (recurrence), 19 (sinks), 21–22 (entities) |
| §4.4 data model, subentries, venue timezone | 15, 16, 20 |
| §4.5 sniper engine, error classification, guards | 5, 12, 20 |
| §4.6 outcome flow | 18, 19 |
| §4.7 entities and services | 21, 22 |
| §5 error handling, reauth, repairs | 15, 17, 20, 23 |
| §6 testing | every task; layering in 1 |
| §7 HACS distribution bar | 1, 24 |
| §8 roadmap v0.1 | all |

**Known gaps, deliberate.** Spec §4.7 lists a `calendar` platform and §2.2 the calendar sink — both are v0.2 and excluded here by decision D6. Fallback spaces are modelled (`space_ids` is a tuple, `SlotTakenError` is a distinct branch) but not yet used; v0.2 changes one branch in `scheduler.py`.

**Type consistency check.** Names that cross task boundaries: `SkeddaCredentials`, `SkeddaSession`, `SkeddaSpace`, `SkeddaBooking`, `SkeddaBookingRequest` (Task 3) are consumed unchanged in 4, 6, 7, 13. `AttemptStatus`, `BookingAttempt`, `BookingOutcome` (Task 8) are consumed in 18, 19, 20. `RecurrenceRule`, `BookingWindow`, `BookingJob` (9–11) are consumed in 16, 20, 21, 22. `AttemptPlan.arm_at` / `.fire_times` (Task 12) are consumed in 20. `build_job` / `venue_timezone_for` (16, 20) are consumed in 20, 21, 22, 23. `SkeddaRuntimeData` grows across 14, 17, 18, 20 — each task states the field it adds.

---

## Execution Handoff

**Plan complete and saved to `.claude/plans/2026-09-15-skedda-scheduler-v0.1.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

Note either way: **Task 2 must be done interactively with you** — it needs your live Skedda session in Chrome, and you type the password. Everything after it is unblocked once the contract doc and fixtures are committed.

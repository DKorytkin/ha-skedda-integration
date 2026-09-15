# Design: Skedda Scheduler — Home Assistant custom integration

- **Status:** approved
- **Date:** 2026-09-15
- **Domain:** `skedda_scheduler`
- **Repo:** `DKorytkin/ha-skedda-integration`
- **Supersedes:** `.claude/specs/Init.md` (kept as the original requirements brief)

---

## 1. Problem

Booking a tennis/padel court on Skedda is a race. The slot opens at a fixed
moment (typically 00:00:00 local, N days before play) and is gone within
seconds. A person has to be awake with a browser tab open. The integration
must win that race unattended, for several Skedda accounts, on a recurring
weekly schedule, and report the outcome into Home Assistant.

## 2. Constraints discovered during design

These three findings changed the design away from what `Init.md` assumed.
They are requirements, not opinions.

### 2.1 Skedda has no public write API

Skedda officially exposes only outgoing webhooks (booking create/update/
cancel), Zapier/Make connectors, and read-only iCal feeds. Inbound writes are
deliberately blocked. The endpoints in `Init.md`
(`/api/account/login`, `/api/spaces`, `/api/bookings`) are the **internal API
of Skedda's own SPA** — undocumented, unversioned, and free to change.
Skedda has also consolidated login onto `app.skedda.com/account/login` rather
than per-venue subdomains.

**Consequence:** the exact contract is unknown until observed. It is
discovered in Phase 0 from live network traffic, recorded as fixtures, and
confined to a single module so a breaking upstream change is a one-file fix.

### 2.2 `calendar.create_event` cannot invite attendees

Home Assistant's calendar service takes summary, description, start, end and
location — there is no `attendees` field. Item 6 of `Init.md` (participants
receive Google Calendar invitations) is therefore not implementable through
the standard service.

**Consequence:** v0.2 creates the event without invitations and notifies
participants with a prefilled "Add to Calendar" link. Real invitations need a
direct Google Calendar API integration via HA Application Credentials, which
is deferred to v0.4 behind the `CalendarSink` interface.

### 2.3 `addons.community` and HACS are different distribution channels

`addons.community` hosts Supervisor **add-ons** — Docker containers shown in
the Add-on Store. HACS distributes **custom integrations** installed into
`custom_components/`. A booking scheduler is an integration, not an add-on.

**Consequence:** the target is HACS — shipped immediately as a custom
repository, built from day one to the HACS **default store** bar, so
submission is a formality rather than a rewrite.

## 3. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Ship as HACS custom repository; hold the HACS-default bar from day one; submit at v0.3 | HA Core would reject a reverse-engineered, password-authenticated private API, and would demand a separate PyPI library. HACS default is achievable and gives one-click install. |
| D2 | Discover the API contract from live traffic (Claude in Chrome) before writing the client | Writing against `Init.md`'s guessed endpoints guarantees a non-working first release. |
| D3 | Sniper strategy: pre-warm, server clock sync, sub-second burst | This is the product. A plain "fire at T and retry" loses to a human with an open tab. |
| D4 | Config entry per account; booking job as a **config subentry** | Native multi-account, native per-job UI CRUD, per-job devices and entities. No hand-rolled list editor in an options flow. |
| D5 | `core/` has zero `homeassistant` imports | The hardest logic (recurrence, window computation, burst timing) becomes testable in milliseconds without booting HA. |
| D6 | v0.1 scope: multi-account, recurrence, notifications, sniper | Chosen by the user. Fallback spaces and the calendar entity move to v0.2. |
| D7 | Minimum Home Assistant 2025.9.0 | `ConfigSubentryFlow.async_update_reload_and_abort` is only stable from 2025.9. |

## 4. Architecture

### 4.1 Three layers, dependencies point downward only

```
  HA layer   │ config_flow, flows/, coordinator, scheduler, sinks/, entities
     │        │ imports homeassistant.*
     ↓
  core/      │ BookingJob, RecurrenceRule, BookingWindow, BookingStrategy,
     │        │ BookingOutcome — pure Python, NO homeassistant imports
     ↓
  api/       │ SkeddaClient, endpoints, DTOs, errors, clock — aiohttp only
```

`api/` must not import `core/`. `core/` must not import `homeassistant`.
This is enforced by a lint rule in CI, not by discipline.

### 4.2 Directory layout

```
custom_components/skedda_scheduler/
├── __init__.py              # async_setup_entry, runtime_data, platform forwarding
├── const.py                 # DOMAIN, config keys, defaults, event names
├── manifest.json
├── services.yaml
├── config_flow.py           # thin handler; delegates to flows/
├── flows/
│   ├── __init__.py
│   ├── account.py           # user / reauth / reconfigure steps
│   └── job.py               # ConfigSubentryFlow for booking jobs
├── api/                     # volatile layer — isolated
│   ├── client.py            # session, auth, antiforgery, retry, rate ceiling
│   ├── endpoints.py         # THE single place holding URLs and payload shapes
│   ├── models.py            # Venue, Space, Booking, UserProfile DTOs
│   ├── errors.py            # SkeddaAuthError, SlotTakenError, TooEarlyError, ...
│   └── clock.py             # server clock-offset estimation
├── core/                    # domain — no HA
│   ├── provider.py          # BookingProvider protocol
│   ├── job.py               # BookingJob dataclass + validation
│   ├── recurrence.py        # RecurrenceRule -> occurrences()
│   ├── window.py            # target slot -> booking-window open instant
│   ├── strategy.py          # BookingStrategy protocol; Immediate, Sniper, Poll
│   └── result.py            # BookingAttempt, BookingOutcome
├── scheduler.py             # arming, HA time tracking, sub-second firing
├── coordinator.py           # DataUpdateCoordinator: account health, bookings
├── store.py                 # persisted attempt history
├── sinks/
│   ├── base.py              # ResultSink protocol
│   ├── ha_event.py          # fires events on the HA bus
│   ├── notify.py            # calls notify.* services
│   └── calendar.py          # calls calendar.create_event        (v0.2)
├── calendar.py              # CalendarEntity                      (v0.2)
├── sensor.py, binary_sensor.py, switch.py, button.py
├── diagnostics.py           # redacted diagnostics
├── repairs.py               # issues on auth failure / API contract break
└── translations/ en.json, uk.json
```

### 4.3 Extension seams

Six named interfaces, each the answer to "what will change later".

| Seam | Interface | Extension without touching anything else |
|---|---|---|
| Provider | `core/provider.py: BookingProvider` | Playtomic, Matchi, another club platform |
| API contract | `api/endpoints.py` | Skedda changes its API → edit one file |
| Strategy | `core/strategy.py: BookingStrategy` | `Immediate`, `Sniper`, `Poll`, future `Adaptive` |
| Recurrence | `core/recurrence.py: RecurrenceRule` | weekly → biweekly → full RRULE → "first Tuesday" |
| Result handling | `sinks/base.py: ResultSink` | HA event → notify → calendar → Google invites → webhook |
| Entities | standard HA platforms | new platform = new file, nothing else changes |

`BookingProvider` is the load-bearing one:

```python
class BookingProvider(Protocol):
    async def authenticate(self, credentials: Credentials) -> Session: ...
    async def list_spaces(self, session: Session) -> list[Space]: ...
    async def book(self, session: Session, request: BookingRequest) -> BookingOutcome: ...
    async def list_bookings(self, session: Session, window: DateRange) -> list[Booking]: ...
    async def cancel(self, session: Session, booking_id: str) -> None: ...
```

### 4.4 Data model

- **Config entry** = one Skedda account. Data: venue subdomain, email,
  password (HA encrypted storage), alias. One HA device per account.
- **Config subentry** (`type: "job"`) = one booking job. One HA device per
  job, so a job carries its own entities and can be renamed/disabled in the UI.

`BookingJob` fields:

| Field | Meaning |
|---|---|
| `name` | "Tuesday 18:00 evening practice" |
| `space_ids` | ordered priority list (v0.1 uses the first only; v0.2 uses fallbacks) |
| `weekday`, `start_time`, `duration_minutes` | the recurring slot |
| `window_days`, `window_open_time` | when booking opens (e.g. 7 days before, 00:00:00) |
| `recurrence` | `weekly` \| `biweekly`, with `season_start` / `season_end` |
| `strategy` | `sniper` (default) \| `immediate` |
| `title`, `lock_state` | passed to Skedda |
| `notify_targets` | notify service entity ids |
| `enabled` | mirrored by a `switch` entity |

All times are stored and computed in the **venue's** timezone, not the HA
host timezone — a venue abroad would otherwise open its window at the wrong
instant.

### 4.5 Sniper engine

```
T-120s    ARM    authenticate / refresh session, resolve space ids,
                 open keep-alive connection
T-120s..T SYNC   sample server time from the Date response header,
                 correct by half-RTT, smooth with EWMA -> clock_offset
T-150ms   FIRE   first POST, scheduled with loop.call_at for sub-second accuracy
T..T+1.2s BURST  up to 5 attempts spaced 250ms, stop on first success
```

Home Assistant's `async_track_point_in_time` fires the coarse ARM and a
pre-fire handoff; the final sub-second timing uses `loop.call_at`, because
HA's time tracking is not sub-second accurate.

Error classification drives the whole loop:

| Error | Response |
|---|---|
| `TooEarlyError` | retry immediately, window has not opened yet |
| `SlotTakenError` | v0.1: stop and report lost. v0.2: next fallback space |
| `AuthExpiredError` | re-authenticate once, retry, then give up |
| `RateLimitedError` | back off and abandon this run — never escalate |
| `ApiContractError` | abandon, raise a repairs issue, include payload in diagnostics |

Guards: one semaphore per account so two jobs never race each other; a hard
ceiling on attempts per run so a bug cannot turn into a flood against a
third-party service.

### 4.6 Outcome flow

```
BookingOutcome ──▶ store.py (persisted history)
               ├─▶ HaEventSink   → skedda_scheduler_booking_succeeded / _failed
               ├─▶ NotifySink    → notify.* with result + link
               └─▶ CalendarSink  → calendar.create_event            (v0.2)
```

Sinks are resolved from a list, so v0.4 adds Google invites by appending one
implementation.

### 4.7 Entities

| Platform | Entity | Notes |
|---|---|---|
| `sensor` | `next_run` (timestamp), `last_outcome`, `consecutive_failures` | per job |
| `binary_sensor` | `account_authenticated` | per account; problem device class |
| `switch` | `job_enabled` | per job |
| `button` | `run_now` | per job; mirrors the `trigger_job_now` service |
| `calendar` | planned + confirmed bookings (v0.2) | per account |

Services in `services.yaml`: `trigger_job_now`, `cancel_booking`,
`refresh_spaces`.

## 5. Error handling

- Auth failure on an account → `binary_sensor` goes unavailable, a **reauth
  flow** is started, and a repairs issue is raised. Jobs on that account are
  skipped, not silently failed.
- Unexpected response shape → `ApiContractError`, repairs issue "Skedda API
  changed", diagnostics capture the offending payload (redacted).
- Network failure during ARM → retry ARM with backoff until T-10s, then
  attempt cold.
- Every attempt, successful or not, is written to `store.py` and surfaced in
  diagnostics. A lost race must be explainable after the fact.

## 6. Testing

| Layer | Approach |
|---|---|
| `core/` | plain pytest + `freezegun`; recurrence, window computation and burst scheduling are the highest-value tests in the repo |
| `api/` | `aioresponses` replaying fixtures recorded from real traffic in Phase 0 |
| HA layer | `pytest-homeassistant-custom-component`: config flow, subentry flow, reauth, entity snapshots |
| Boundaries | CI lint rule asserting `core/` imports no `homeassistant` and `api/` imports no `core/` |

Coverage target: 90% overall, 100% on `core/`.

## 7. Distribution requirements (HACS default bar)

- `hacs.json` at repo root: `name`, `render_readme`, `homeassistant: "2025.9.0"`
- `manifest.json`: `domain`, `name`, `codeowners`, `config_flow: true`,
  `documentation`, `issue_tracker`, `integration_type: "service"`,
  `iot_class: "cloud_polling"`, `requirements`, `version`, `quality_scale`
- GitHub repo description and topics (`home-assistant`, `hacs`, `skedda`)
- Versioned GitHub Releases; version in `manifest.json` matches the tag
- CI: `hassfest`, `HACS validate`, ruff, mypy, pytest
- `home-assistant/brands` PR with icon and logo (256px and 512px)
- README with installation, configuration and screenshots; translations
  `en` and `uk`

## 8. Roadmap

| Milestone | Contents |
|---|---|
| **Phase 0** | API discovery from live traffic; contract and fixtures committed |
| **v0.1** | multi-account, sniper booking, recurrence, notifications, sensors/switch/button, HACS custom repo |
| **v0.2** | fallback spaces, calendar entity, `calendar.create_event`, diagnostics, repairs |
| **v0.3** | HACS default submission, brands PR, documentation polish |
| **v0.4+** | Google OAuth invitations, second provider implementation |

## 9. Accepted risks

1. **The private API will break without warning.** Localised to
   `api/endpoints.py` plus a repairs issue that tells the user what happened,
   but it cannot be eliminated.
2. **Automating bookings may conflict with Skedda's Terms of Service.** The
   integration acts under the user's own credentials on their own account.
   This is the user's decision, recorded here so it is not implicit.
3. **Sub-second timing depends on network latency to Skedda.** Clock-offset
   estimation reduces but does not remove the variance.

## 10. Out of scope

Payment handling, booking on behalf of other users' accounts, scraping
rendered HTML as a fallback transport, and any add-on (Docker) packaging.

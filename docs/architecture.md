# Architecture

This page describes how the integration is put together. It is aimed at anyone
reading or extending the code.

## Design goals

1. **Contain the volatile part.** Skedda does not publish an API for creating
   reservations, so the integration speaks the same undocumented HTTP interface the
   Skedda web application uses. That interface can change at any time. The design
   assumes it will, and confines it so a change is a small, local repair.
2. **Keep the hard logic testable.** Recurrence arithmetic, booking-window
   calculation and sub-second request timing are where mistakes are expensive and
   invisible. They are written as pure functions so they can be tested exhaustively
   in milliseconds, without starting Home Assistant or touching a network.
3. **Leave room to grow.** Each axis along which the integration is likely to change
   is a named interface rather than a branch in a long function.

## Layers

Dependencies point in one direction only.

```
  Home Assistant layer  │ config flow, coordinator, scheduler, sinks, entities
          │             │ imports homeassistant.*
          ▼
  core/                 │ domain model and pure logic
          │             │ imports neither homeassistant nor aiohttp
          ▼
  api/                  │ HTTP transport for Skedda
                        │ imports aiohttp only
```

- `api/` knows how to talk to Skedda and nothing about booking jobs.
- `core/` knows what a booking job is and nothing about HTTP or Home Assistant.
- The Home Assistant layer wires the two together and owns everything with a clock,
  a network connection or a UI.

`core/` must not import `homeassistant`, and `api/` must not import `core/`. These
rules are enforced by a test that parses the import statements of every module, so
a violation fails CI rather than surviving review.

## Modules

```
custom_components/skedda_scheduler/
├── api/                  HTTP transport
│   ├── client.py         session, authentication, retries, request ceiling
│   ├── endpoints.py      every URL, header and wire field name
│   ├── models.py         transport data objects
│   ├── errors.py         exception taxonomy
│   └── clock.py          server clock-offset estimation
├── core/                 domain, pure Python
│   ├── provider.py       BookingProvider protocol and value objects
│   ├── job.py            BookingJob
│   ├── recurrence.py     RecurrenceRule
│   ├── window.py         BookingWindow
│   ├── strategy.py       attempt planning
│   └── result.py         BookingAttempt, BookingOutcome
├── skedda_provider.py    adapter mapping api/ onto core/
├── config_flow.py        account and job configuration
├── flows/                the individual flow steps
├── coordinator.py        periodic refresh of account state
├── scheduler.py          arming, timing and the attempt loop
├── store.py              persisted attempt history
├── sinks/                what happens to an outcome
├── entity.py             shared entity bases and device wiring
└── sensor.py, binary_sensor.py, switch.py, button.py
```

## Extension points

Six interfaces, each answering a question of the form "what is likely to change?"

| Interface | Where | What it allows |
|---|---|---|
| `BookingProvider` | `core/provider.py` | Supporting a booking platform other than Skedda. Skedda becomes one implementation among several. |
| Endpoint contract | `api/endpoints.py` | Reacting to a change in Skedda's interface by editing a single file. |
| `BookingStrategy` | `core/strategy.py` | New submission behaviour beyond `precise` and `immediate`. |
| `RecurrenceRule` | `core/recurrence.py` | Richer repetition than weekly and fortnightly. |
| `ResultSink` | `sinks/base.py` | New things to do with an outcome — a calendar entry, a webhook, a message. |
| Entity platforms | standard Home Assistant | A new platform is a new file; nothing else changes. |

`BookingProvider` is the load-bearing one:

```python
class BookingProvider(Protocol):
    @property
    def is_authenticated(self) -> bool: ...
    async def authenticate(self) -> None: ...
    async def list_spaces(self) -> list[Space]: ...
    async def book(self, request: BookingRequest) -> Booking: ...
    async def list_bookings(self, window: DateRange) -> list[Booking]: ...
    async def cancel(self, booking_id: str) -> None: ...
```

## Configuration model

The integration uses Home Assistant's config entries and subentries:

- A **config entry** holds one account: venue, credentials, label, timezone.
- A **config subentry** holds one booking job.

This gives multiple accounts and per-job editing without a bespoke settings screen,
and lets each job own a device with its own entities.

## Timing

A booking job declares a slot and a window policy. From those the integration derives
the exact instant the window opens, in the venue's timezone:

```
slot 2026-09-15 18:00 (venue local)
window: 7 days before, at 00:00:00
→ opens 2026-09-08 00:00:00 venue local → 2026-09-07 21:00:00 UTC
```

The `precise` strategy then works backwards from that instant:

| Offset | Step |
|---|---|
| −120 s | Authenticate if needed and open a connection, so no setup cost is paid later. |
| −120 s … 0 | Sample the venue server's clock from the `Date` response header, correct for half the round trip, and smooth the estimate. |
| −150 ms | Submit the first request, scheduled against the corrected clock. |
| 0 … +1.2 s | Submit up to four further requests, 250 ms apart, stopping on the first success. |

Home Assistant's time tracking is used for the coarse wake-up; the final hop uses the
event loop directly, because the coarse tracker is not accurate below one second.

## Error handling

The response to a failure depends on what the failure means, so the exception
taxonomy is deliberately fine-grained.

| Condition | Response |
|---|---|
| Window not open yet | Retry immediately — this is expected near the boundary. |
| Slot already taken | Stop. Retrying the same court cannot succeed. |
| Session expired | Re-authenticate once, retry once, then stop. |
| Asked to slow down | Back off and abandon the run. Never escalate. |
| Unrecognised response | Abandon the run, raise a repair issue, capture the payload in diagnostics. |
| Network failure | Retry within the run's remaining attempts. |

Two guards apply regardless: one submission at a time per account, and a hard
ceiling of eight requests per run.

## Outcomes

Every run produces a `BookingOutcome`, whether it succeeded or not. The outcome is
persisted, then handed to each configured sink:

```
BookingOutcome ─┬─▶ history store          (explains a failure after the fact)
                ├─▶ Home Assistant events  (the automation surface)
                └─▶ notify services        (tells you what happened)
```

## Testing

| Layer | Approach |
|---|---|
| `core/` | Plain pytest with frozen time. Target: full coverage. |
| `api/` | Fixtures recorded from real traffic, replayed with `aioresponses`. |
| Home Assistant layer | `pytest-homeassistant-custom-component`: flows, reauthentication, entity snapshots. |
| Boundaries | A test that parses imports and fails if a layer reaches upward. |

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the development setup. Two rules are
worth repeating here:

- If Skedda's interface changes, `api/endpoints.py` and the redacted fixtures under
  `tests/fixtures/skedda/` change together. A fixture that no longer reflects reality
  is worse than no fixture.
- Nothing outside `api/` may hold a URL, a header name or a wire field name.

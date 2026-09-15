# Skedda Scheduler

A Home Assistant integration for recurring bookings in [Skedda](https://www.skedda.com/).

Venues that use Skedda open reservations a fixed period ahead, and the horizon
rolls with the clock: at a venue that books two weeks out, the 18:00 slot two
weeks from today becomes available at 18:00 today, and not a moment sooner.
Holding a regular weekly slot therefore means being at a keyboard at an exact
minute, every week, for the length of a season.

This integration turns that into configuration. You describe the slot once — court,
weekday, time, how far ahead the venue opens bookings — and Home Assistant submits
the reservation when the booking window opens, then reports the result as entities,
events and notifications.

## Documentation

| Page | Contents |
|---|---|
| [Installation](installation.md) | Installing through HACS, requirements, updating |
| [Configuration](configuration.md) | Adding an account, creating a booking job, every field explained |
| [Usage](usage.md) | Entities, services, events, automation examples |
| [Architecture](architecture.md) | How the integration is put together and why |
| [Troubleshooting](troubleshooting.md) | Diagnostics, repair issues, common failures |

## Features

- **Recurring bookings** — weekly or fortnightly, bounded by a season start and end date.
- **Booking-window awareness** — you declare how far ahead the venue opens
  reservations; the integration derives the exact instant each slot unlocks, in the
  venue's own timezone, and refuses a job the venue's rules make impossible.
- **Accurate submission** — the session is prepared in advance and the request is
  timed against the venue server's own clock rather than the Home Assistant host's.
- **Several accounts** — add as many Skedda accounts as you need and assign each
  booking job to one of them.
- **Visible results** — every attempt is recorded, exposed as entities, published on
  the event bus, and optionally pushed to a notification service.

## What is not here yet

Deliberate omissions, in the order they are likely to be missed:

- **A sidebar panel.** Booking jobs live under Settings, which is where you
  configure them but not where you would glance at them. A panel of its own
  needs a frontend module shipped alongside the integration; until then, a
  dashboard with the job entities does the same job in two minutes. Worth
  building once somebody has enough jobs to want a page for them.
- **A calendar platform.** Upcoming bookings as a calendar entity, and a sink
  that writes each successful booking into a calendar of your choosing.
- **Fallback courts.** The model already carries a list of spaces per job and
  treats "slot taken" as its own outcome; what is missing is trying the next
  court when the first is gone.

## Compatibility

- Home Assistant **2026.3.0** or newer.
- A Skedda venue that you can sign in to with an email address and password.

## A note on how this works

Skedda does not publish a public API for creating reservations. This integration
uses the same HTTP interface that the Skedda web application uses, which means the
interface is undocumented and may change without notice. When that happens the
integration raises a repair issue in Home Assistant rather than failing quietly.

Review your venue's terms of use before automating reservations, and use the
integration only with accounts you own.

## Licence

MIT. See [LICENSE](../LICENSE).

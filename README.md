# Skedda Scheduler

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Release](https://img.shields.io/github/v/release/DKorytkin/ha-skedda-integration)](https://github.com/DKorytkin/ha-skedda-integration/releases)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/github/license/DKorytkin/ha-skedda-integration)](https://github.com/DKorytkin/ha-skedda-integration/blob/main/LICENSE)

A Home Assistant integration that holds your regular slot at a venue that books
through [Skedda](https://www.skedda.com/).

Venues open reservations a fixed period ahead, and the horizon rolls with the
clock: where bookings open two weeks out, the 18:00 court two weeks from today
becomes available at 18:00 today — not a minute earlier, and gone shortly after.
Keeping a weekly slot means being at a keyboard at that exact minute, every
week, all season.

This integration turns that into configuration. Describe the slot once, and Home
Assistant submits the reservation the instant the window opens, timed against the
venue server's own clock, then tells you what happened.

![The Skedda panel in Home Assistant](https://raw.githubusercontent.com/DKorytkin/ha-skedda-integration/main/docs/images/panel.png)

## Features

- **Booking jobs** — one court, one weekday, one time; booked the moment the
  venue's window opens, every week or every other week, until the season ends.
- **Slot watch** — rules for what you would take if somebody gave it up. The
  integration books it with whichever account still has quota that week.
- **Several accounts** — each with its own jobs; a watch spends whichever one
  still has hours left.
- **Sidebar panel** — bookings grouped by day, upcoming jobs and watch rules at a
  glance, and releasing a booking from the list.
- **Google Calendar** — every booking that lands becomes an event, with the
  people you name invited.
- **Automation surface** — sensors, switches, buttons, calendars, events and
  services for anything the panel does not cover.

## Requirements

- Home Assistant **2026.3.0** or newer.
- [HACS](https://hacs.xyz/), for the recommended installation.
- A Skedda account that signs in with an email address and password.

## Installation

### Through HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DKorytkin&repository=ha-skedda-integration&category=integration)

Or by hand:

1. Open **HACS**, then the ⋮ menu in the top right → **Custom repositories**.
2. Add `https://github.com/DKorytkin/ha-skedda-integration` with type
   **Integration**.
3. Find **Skedda Scheduler** in HACS and select **Download**.
4. Restart Home Assistant.

HACS then offers every new release as an update.

### Manually

Copy `custom_components/skedda_scheduler` from the
[latest release](https://github.com/DKorytkin/ha-skedda-integration/releases/latest)
into your Home Assistant `config/custom_components/` directory and restart.

More detail, including updating and uninstalling, in
[docs/installation.md](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/installation.md).

## Configuration

Everything is configured in the Home Assistant interface; there is nothing to add
to `configuration.yaml`.

[![Open your Home Assistant instance and start setting up Skedda Scheduler.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=skedda_scheduler)

1. **Add an account.** **Settings → Devices & Services → Add Integration →
   Skedda Scheduler**, then enter the venue subdomain (`myclub` for
   `https://myclub.skedda.com`), your email and password, and a name for the
   account. The venue's timezone, slot size, booking horizon and weekly allowance
   are read from the venue itself.
2. **Add a booking job.** On the integration's page choose **Book a court**, then
   pick the court, the date, the start time and the duration. Choose **Every
   week** to keep the slot; the job books the same weekday until the date in
   **Repeat until**.
3. **Optional: watch for freed slots.** **Add Integration → Skedda Scheduler →
   Slot watch**, then add a rule per thing you want caught — which days, which
   hours, which courts.
4. **Optional: link a Google calendar.** **Add Integration → Skedda Scheduler →
   Google Calendar.** Home Assistant cannot invite anyone to a calendar event, so
   this needs an OAuth client of your own — the same one Home Assistant's Google
   integration uses, if you already have it.

Every field is explained in [docs/configuration.md](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/configuration.md).

## Usage

Once an account and a job exist, the integration runs on its own. Each job
arms a single timer for the moment its window opens and sleeps until then.

### What you get

| Where | What |
|---|---|
| **Skedda** in the sidebar | Bookings by day, booking jobs, watch rules, and account status |
| Each booking job | `Next run`, `Last outcome` and `Status` sensors, a `Job enabled` switch, a `Run now` button |
| Each account | An `Authentication` problem sensor, and `bookings` / `pending` calendars |
| Event bus | `skedda_scheduler_booking_succeeded` and `skedda_scheduler_booking_failed` after every run |
| Services | `trigger_job_now`, `refresh_spaces`, `slot_freed` |

### Tell me when a booking fails

```yaml
automation:
  - alias: Skedda booking failed
    triggers:
      - trigger: event
        event_type: skedda_scheduler_booking_failed
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: Booking failed
          message: >-
            {{ trigger.event.data.job_name }}:
            {{ trigger.event.data.failure_reason }}
```

### Pause every job while away

```yaml
automation:
  - alias: Pause Skedda jobs while away
    triggers:
      - trigger: state
        entity_id: input_boolean.holiday_mode
        to: "on"
    actions:
      - action: switch.turn_off
        target:
          entity_id:
            - switch.court_1_tuesdays_20_00_job_enabled
            - switch.court_2_saturdays_18_00_job_enabled
```

### Look for a freed slot as soon as somebody cancels

If your venue announces cancellations somewhere Home Assistant can hear — a
Telegram channel, say — tell the slot watch to look now instead of at its next
poll:

```yaml
automation:
  - alias: Venue says a court is free
    triggers:
      - trigger: event
        event_type: telegram_text
    conditions:
      - condition: template
        value_template: "{{ 'cancelled' in trigger.event.data.text | lower }}"
    actions:
      - action: skedda_scheduler.slot_freed
```

More entities, the event payload and further examples in
[docs/usage.md](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/usage.md).

## How it works, and a caution

Skedda publishes no API for creating reservations. This integration speaks the
same undocumented HTTP interface the Skedda web application uses, which means that
interface may change without notice. When it does, the integration raises a repair
issue in Home Assistant rather than failing quietly.

Read your venue's terms of use before automating reservations, and use this only
with accounts you own. What the integration stores and who it talks to is in
[PRIVACY.md](https://github.com/DKorytkin/ha-skedda-integration/blob/main/PRIVACY.md).

## Documentation

| Page | Contents |
|---|---|
| [Installation](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/installation.md) | Installing through HACS, requirements, updating |
| [Configuration](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/configuration.md) | Accounts, booking jobs, slot watch, Google Calendar, every field explained |
| [Usage](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/usage.md) | Panel, entities, services, events, automation examples |
| [Troubleshooting](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/troubleshooting.md) | Diagnostics, repair issues, common failures |
| [Architecture](https://github.com/DKorytkin/ha-skedda-integration/blob/main/docs/architecture.md) | How the integration is put together and why |

## Contributing

Bug reports and pull requests are welcome at the
[issue tracker](https://github.com/DKorytkin/ha-skedda-integration/issues). See
[CONTRIBUTING.md](https://github.com/DKorytkin/ha-skedda-integration/blob/main/CONTRIBUTING.md) for the development setup, testing on a real
Home Assistant, and how releases are made.

## Trademark

Skedda is a trademark of its owner. The logo in this repository is used to
identify the service this integration talks to. This project is unofficial and
is not affiliated with, endorsed by, or supported by Skedda. See
[assets/README.md](https://github.com/DKorytkin/ha-skedda-integration/blob/main/assets/README.md).

## Licence

MIT. See [LICENSE](https://github.com/DKorytkin/ha-skedda-integration/blob/main/LICENSE).

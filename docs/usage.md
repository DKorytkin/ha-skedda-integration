# Usage

Once an account and at least one booking job exist, the integration runs on its own.
This page describes what it exposes so you can watch it, drive it and automate
around it.

## The Skedda panel

**Skedda** in the sidebar shows three tables, each sorted and each naming the
account responsible:

- **Bookings** — the court times this account holds.
- **Booking jobs** — what will be booked next, and when its window opens.
- **Accounts** — green when the account can sign in, red when it cannot.

It is a view, not an editor. Adding and editing open Home Assistant's own
dialogs, so there is one implementation of the forms rather than two.

## Calendars

Each account publishes two calendar entities, so Home Assistant's own calendar
view shows the week or the month with the two coloured apart:

| Entity | Shows |
|---|---|
| `calendar.<account>_bookings` | Court times the account holds |
| `calendar.<account>_pending` | Slots a job still means to book, with the date its window opens |

A slot leaves the pending calendar the moment it appears on the booked one, so
the same court time is never on both.

## Devices

Each booking job becomes a device: it groups five entities, so a whole job can be
renamed or hidden in one place.

An account does not. It is the config entry, and its one entity - the
authentication sensor - carries the account in its own entity id.

## Entities

### Per booking job

| Entity | Type | Meaning |
|---|---|---|
| `Next run` | sensor (timestamp) | When the integration will next wake up for this job. Unknown once the season has ended. |
| `Last outcome` | sensor | `success`, or the reason the last run failed: `slot_taken`, `quota_exceeded`, `window_closed`, `too_early`, `auth_failed`, `rate_limited`, `contract_error`, `connection_error`, or `already_booked` when the slot was already held and nothing was sent. |
| `Status` | sensor | `armed` while a booking attempt is scheduled, `disabled` when switched off, `out_of_season` once the season has ended. |
| `Job enabled` | switch | Pauses or resumes the job without deleting it. |
| `Run now` | button | Runs the job immediately instead of waiting for its window. |

`Last outcome` carries extra attributes: `booking_id`, `slot_start`, `attempts` and
`finished_at`.

### Per account

| Entity | Type | Meaning |
|---|---|---|
| `Authentication` | binary sensor (problem) | `on` means the account cannot currently be used — wrong password, or the venue is unreachable. |

## How often it talks to the venue

The integration is quiet by design. Each job arms a single timer for the moment
its booking window opens and sleeps until then - there is no polling in between.

The account itself is polled to keep courts, rules and bookings fresh, and that
poll follows what is due:

| Situation | Poll |
|---|---|
| Nothing due - out of season, or every job switched off | every 12 hours |
| Next attempt more than six hours away | every 12 hours |
| Next attempt within six hours | hourly |
| Next attempt within the hour | every 15 minutes |

The twelve-hourly floor is deliberate: a password that has stopped working is
better discovered in February than on the morning the season opens.

For one weekly job that is roughly **fifty requests a week**, most of them in
the hours around the booking window. Each poll is two requests, not three: the
venue's rules and its courts arrive in the same payload, and one fetch stands
in for the other.

A booking run adds the sign-in, a warm-up and up to five attempts, and only a
run that actually booked something asks for a refresh afterwards - the venue's
diary does not change unless we change it.

## Services

### `skedda_scheduler.trigger_job_now`

Runs one booking job straight away.

```yaml
action: skedda_scheduler.trigger_job_now
data:
  job_id: 01JABCDEF0123456789
```

The job id is shown in the integration's diagnostics. The `Run now` button does the
same thing for a single job and is usually easier.

### `skedda_scheduler.refresh_spaces`

Re-reads courts and upcoming reservations from Skedda for every configured account.
Useful after the venue adds or renames a court.

```yaml
action: skedda_scheduler.refresh_spaces
```

## Events

Every run publishes exactly one event on the Home Assistant event bus. These are the
stable automation surface: fields may be added over time, but existing fields keep
their names and meanings.

| Event | Fired when |
|---|---|
| `skedda_scheduler_booking_succeeded` | A reservation was created. |
| `skedda_scheduler_booking_failed` | The run finished without a reservation. |

Payload:

| Field | Type | Meaning |
|---|---|---|
| `job_name` | string | The job's name. |
| `job_id` | string | The job's internal id. |
| `succeeded` | boolean | Whether a reservation was created. |
| `booking_id` | string or null | The Skedda reservation id. |
| `space_id` | string or null | The booked court. Skedda's ids are strings. |
| `slot_start`, `slot_end` | ISO 8601 | The reserved interval. |
| `attempts` | integer | How many requests the run made. |
| `failure_reason` | string or null | Why the run failed, if it did. |
| `finished_at` | ISO 8601 | When the run ended. |

## Automation examples

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
            {{ trigger.event.data.job_name }} on
            {{ trigger.event.data.slot_start | as_datetime | as_local
               | as_timestamp | timestamp_custom('%a %d %b %H:%M') }}
            — {{ trigger.event.data.failure_reason }}
```

### Put the reservation in a calendar

```yaml
automation:
  - alias: Skedda booking to calendar
    triggers:
      - trigger: event
        event_type: skedda_scheduler_booking_succeeded
    actions:
      - action: calendar.create_event
        target:
          entity_id: calendar.personal
        data:
          summary: "{{ trigger.event.data.job_name }}"
          description: "Skedda reservation {{ trigger.event.data.booking_id }}"
          start_date_time: "{{ trigger.event.data.slot_start }}"
          end_date_time: "{{ trigger.event.data.slot_end }}"
```

`calendar.create_event` does not support attendees, so this creates the event
without sending invitations. Notify your participants separately if you need to.

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
            - switch.tuesday_18_00_job_enabled
            - switch.thursday_20_00_job_enabled
```

### Warn me when an account stops working

```yaml
automation:
  - alias: Skedda account problem
    triggers:
      - trigger: state
        entity_id: binary_sensor.main_account_authentication
        to: "on"
        for: "00:10:00"
    actions:
      - action: notify.mobile_app_my_phone
        data:
          message: Skedda account needs attention.
```

## Dashboard

There is no custom card. The job devices work with the standard entities card:

```yaml
type: entities
title: Court bookings
entities:
  - entity: sensor.tuesday_18_00_next_run
  - entity: sensor.tuesday_18_00_last_outcome
  - entity: switch.tuesday_18_00_job_enabled
  - entity: button.tuesday_18_00_run_now
```

## Next steps

If something is not behaving, see [Troubleshooting](troubleshooting.md).

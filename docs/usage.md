# Usage

Once an account and at least one booking job exist, the integration runs on its own.
This page describes what it exposes so you can watch it, drive it and automate
around it.

## Devices

Each account becomes a device, and each booking job becomes a device attached to it.
Job entities are therefore grouped by job in the interface, and a whole job can be
renamed or hidden in one place.

## Entities

### Per booking job

| Entity | Type | Meaning |
|---|---|---|
| `Next run` | sensor (timestamp) | When the integration will next wake up for this job. Unknown once the season has ended. |
| `Last outcome` | sensor | `success`, or the reason the last run failed: `slot_taken`, `too_early`, `auth_failed`, `rate_limited`, `contract_error`, `connection_error`. |
| `Job enabled` | switch | Pauses or resumes the job without deleting it. |
| `Run now` | button | Runs the job immediately instead of waiting for its window. |

`Last outcome` carries extra attributes: `booking_id`, `slot_start`, `attempts` and
`finished_at`.

### Per account

| Entity | Type | Meaning |
|---|---|---|
| `Authentication` | binary sensor (problem) | `on` means the account cannot currently be used — wrong password, or the venue is unreachable. |

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
| `space_id` | integer or null | The booked court. |
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

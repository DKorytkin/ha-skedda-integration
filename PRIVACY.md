# Privacy

Skedda Scheduler is a Home Assistant custom integration that books sports
courts on a [Skedda](https://www.skedda.com/) venue on your behalf, and can add
each booking to your Google Calendar.

It is not a service. There is no server, no account, and no operator: the code
runs inside your own Home Assistant installation, and the author has no access
to it or to anything it holds.

## What it stores, and where

Everything stays in your Home Assistant instance, in its own encrypted
configuration storage:

| Kept | Why |
|---|---|
| Your Skedda email and password | To sign in to your venue when a booking window opens. |
| Google OAuth tokens | To create calendar events. Issued to the OAuth client **you** created in your own Google Cloud project. |
| Your booking jobs, and what each attempt did | To book the right court at the right moment, and to show you what happened. |

Credentials are redacted from logs and from the diagnostics download.

## Who it talks to

Two hosts, both at your instruction:

- **Your Skedda venue** - to read its rules and your bookings, and to book.
- **Google Calendar API** - only if you link a calendar, and only to list your
  calendars and create events. If you name attendees, Google emails them the
  invitation; their addresses are stored in your Home Assistant and sent to
  Google as part of the event.

Nothing is sent anywhere else. No analytics, no telemetry, no third parties.

## Removing it

Deleting the Google Calendar entry removes the stored tokens; you can also
revoke access at [Google Account permissions](https://myaccount.google.com/permissions).
Deleting an account entry removes its credentials and its booking history.
Uninstalling the integration leaves nothing behind.

## Contact

Questions and reports: <https://github.com/DKorytkin/ha-skedda-integration/issues>

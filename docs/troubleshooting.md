# Troubleshooting

## Start with diagnostics

**Settings → Devices & Services → Skedda Scheduler → three-dot menu → Download
diagnostics**

The file contains the account configuration with the email address and password
removed, every job's configuration, the full history of recent attempts with their
timings and failure reasons, the measured clock offset, and the list of courts read
from the venue. It is the right attachment for a bug report.

## Repair issues

Home Assistant shows these under **Settings → System → Repairs**.

### "Skedda changed its API"

The venue returned something the integration does not recognise, so the run was
abandoned rather than guessed at. Reservations are never submitted on an unrecognised
response.

There is nothing to configure — this needs a code change. Please open an issue with
the diagnostics attached; the offending payload is included in them.

### Re-authentication requested

The stored password was rejected. Enter the current one when prompted. This also
appears after changing your password in Skedda, or after the venue changes its
sign-in method.

## Common situations

### `Last outcome` says `slot_taken`

The court was reserved by someone else before the request arrived. The run stops at
this point deliberately: the same court cannot become free by retrying.

Worth checking:

- **Is the booking window configured correctly?** If **Booking opens this many days
  before** is larger than the venue actually allows, the request goes out days late.
  Compare `Next run` against when you know the window opens.
- **Is the venue timezone right?** An hour's error here is enough. See
  [Configuration](configuration.md#why-the-venue-timezone-matters).
- **Is the strategy set to `Precise`?** `Immediate` submits without preparing the
  session first, which costs time.

### `Last outcome` says `too_early`

Every attempt in the run arrived before the venue considered the window open. The
integration corrects for the difference between its clock and the venue's, but a
large error in **Booking opens at** will defeat that. Confirm the exact local time
the venue opens reservations.

### `Last outcome` says `rate_limited`

The venue asked the client to slow down and the run was abandoned. This is intentional
— the integration never escalates against a rate limit.

If it recurs, reduce how many jobs share one account, or check whether something else
is using the same account.

### `Last outcome` says `connection_error`

The venue could not be reached. Check that Home Assistant has working internet access
and that `https://<venue>.skedda.com` loads in a browser.

### `Next run` is unknown

Either the job is disabled, or its season has ended. Check the `Job enabled` switch
and the **Season ends** date.

### Nothing happens at all

1. Confirm the `Job enabled` switch is on.
2. Confirm the account's `Authentication` binary sensor is `off` — `on` means the
   account cannot be used and all its jobs are skipped.
3. Press `Run now` and watch `Last outcome`. This runs the same code path without
   waiting for the window and usually identifies the problem immediately.

### Courts are missing from the job form

The list is read from the venue when the account is set up and refreshed periodically.
Call `skedda_scheduler.refresh_spaces` after the venue adds or renames a court.

If the list cannot be read at all, the field accepts a court id typed by hand. You can
find the id in the diagnostics or in the address bar while viewing the court in Skedda.

## Logging

Add this to `configuration.yaml` and restart to get detailed logs:

```yaml
logger:
  default: warning
  logs:
    custom_components.skedda_scheduler: debug
```

Debug logs include arming times, clock offsets and per-attempt outcomes. They do not
contain the password. Read a log through before attaching it to an issue regardless.

## Reporting a problem

Open an issue at
[github.com/DKorytkin/ha-skedda-integration/issues](https://github.com/DKorytkin/ha-skedda-integration/issues)
with:

- the downloaded diagnostics,
- your Home Assistant version,
- what you expected and what happened,
- the relevant debug log lines, if you have them.

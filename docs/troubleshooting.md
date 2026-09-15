# Troubleshooting

## Start with diagnostics

**Settings → Devices & Services → Skedda Scheduler → three-dot menu → Download
diagnostics**

The file contains the account configuration with the email address and password
removed, every job's configuration, the full history of recent runs with each
individual attempt — its timing, latency and the venue's own words — the measured
offset between your clock and the venue's, the venue's rules that were in force, and
the list of courts. It is the right attachment for a bug report.

The venue subdomain is deliberately left in: which venue's rules applied is the
first thing anyone reading the report needs to know.

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
  before** is smaller than the venue actually allows, the request goes out days after
  the slot became available to everyone else. Compare it against the value the form
  suggests, which comes from the venue itself.
- **Is the strategy set to `Precise`?** `Immediate` submits without preparing the
  session first, which costs time.
- **Was the court simply gone?** At a busy venue the first request after the window
  opens can still lose. The diagnostics show the exact instant each attempt was sent
  and how far your clock sat from the venue's.

### `Last outcome` says `too_early`

Every attempt in the run arrived before the venue considered the window open. A shot
or two of `too_early` is normal — the burst deliberately straddles the opening
instant — but a whole run of them means the window was calculated too early. Check
**Booking opens this many days before** against the value the job form suggests.

### `Last outcome` says `quota_exceeded`

The venue caps how much one member may book per period, and this booking would have
gone over it. No amount of retrying changes that: either shorten the job's duration
or release another reservation in the same period.

### `Last outcome` says `window_closed`

The slot is further ahead than the venue accepts reservations for, so the request was
refused outright. **Booking opens this many days before** is larger than the venue's
own horizon.

### `Last outcome` says `already_booked`

The run fired nothing because the last poll already showed that slot reserved on one
of the job's courts — normally because the job had already succeeded. This is the
guard that stops a Home Assistant restart from re-submitting a booking you hold.

### `Last outcome` says `rate_limited`

The venue asked the client to slow down. The run pauses for a couple of seconds
before trying again rather than answering at burst speed, and it never escalates.

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
   last poll could not use the account, which usually means its jobs will fail too.
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

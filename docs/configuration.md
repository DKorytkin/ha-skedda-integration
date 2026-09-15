# Configuration

Everything is configured through the Home Assistant interface. You never edit
`configuration.yaml`.

Configuration has two levels:

- An **account** is one Skedda login. Add one entry per account.
- A **booking job** describes one recurring reservation. Jobs belong to an account,
  and an account can hold as many jobs as you like.

## Adding an account

**Settings → Devices & Services → Add Integration → Skedda Scheduler**

| Field | Meaning |
|---|---|
| **Venue subdomain** | The venue part of your Skedda address. For `https://myclub.skedda.com` this is `myclub`. |
| **Email** | The email address you sign in with. |
| **Password** | The matching password. Stored in Home Assistant's encrypted storage. |
| **Account name** | A label you choose, shown throughout the interface — for example `Main account` or `Partner account`. |

The credentials are verified before the entry is created, and the check goes further
than signing in: Skedda authenticates centrally, so a typo in the venue subdomain
would be accepted by the login page and only surface days later as a booking that
never happened. The venue itself is asked for its settings, which proves the pairing.

If setup fails you are told whether the credentials were rejected or the venue could
not be reached.

### The venue timezone is not something you type

Booking windows open at a local time at the venue, not at yours, and an hour's error
is enough to lose every slot. The timezone is therefore read from the venue's own
settings during setup and refreshed on every poll, rather than being asked for.

### Adding more accounts

Repeat the same steps. Each account becomes a separate entry with its own device and
its own booking jobs. The same account cannot be added twice.

### Changing a password

If Skedda rejects the stored password, Home Assistant raises a repair notification
and asks for the new one. You can also use **Reconfigure** on the entry at any time.

## Adding a booking job

Open the account entry under **Settings → Devices & Services** and choose
**Add booking job**.

### The slot

| Field | Meaning | Example |
|---|---|---|
| **Job name** | A label for this job. Names the device and its entities. | `Tuesday 18:00` |
| **Court** | Which space to book. The list is read from your venue. | `Court 1` |
| **Day of week** | The weekday the slot falls on. | `Tuesday` |
| **Start time** | Local start time at the venue. | `18:00` |
| **Duration** | Length in minutes. The step follows the venue's own booking granularity — at a venue that books whole hours, 15 minutes is not offered, because the server would refuse it. | `60` |

### The booking window

This one field describes your venue's reservation policy, and everything about
timing follows from it. It is pre-filled from the venue's own settings, so in most
cases you should leave it alone.

| Field | Meaning | Example |
|---|---|---|
| **Booking opens this many days before** | How far ahead the venue accepts reservations. | `14` |

The horizon rolls with the clock rather than unlocking at midnight. With the example
value, a slot at 18:00 on Tuesday the 29th becomes bookable at 18:00 on Tuesday the
15th — the same time of day, exactly that many days earlier.

Match the venue exactly. Set it larger and every request goes out before the venue
will accept anything; set it smaller and the request arrives days after the slot
became available to everyone else.

### Limits the form will not let you past

Two settings describe a job that could never book anything, so they are refused
while you are still in the form rather than failing quietly every week:

- a **duration** longer than the venue's weekly allowance;
- a **window** wider than the venue's own horizon.

Both limits are read from the venue and shown in the form's description.

### Repetition

| Field | Meaning |
|---|---|
| **Repeat** | `Every week` or `Every other week`. Fortnightly repetition is anchored on the season start, so it keeps its rhythm across restarts. |
| **Season starts** | The first date the job is active. |
| **Season ends** | Optional. The last date the job is active. Leave empty to run indefinitely. |

### Submission and reporting

| Field | Meaning |
|---|---|
| **Booking title shown in Skedda** | The title written on the reservation itself. |
| **Strategy** | `Precise` or `Immediate`. See below. |
| **Notify these services with the result** | Optional. Any `notify` services that should receive the outcome. |

## Strategies

| Strategy | Behaviour | Use when |
|---|---|---|
| **Precise** | Prepares the session about two minutes ahead, measures the offset between the Home Assistant clock and the venue server's clock, then submits the request at the exact moment the window opens. Retries briefly if the server still considers the window closed. | The default. Use it whenever the slot is expected to be taken quickly after the window opens. |
| **Immediate** | Submits once when the window opens and retries a few times with a growing delay. | Quiet venues, or when you prefer the simplest possible behaviour. |

Both strategies stop as soon as a reservation succeeds, and both stop immediately
when the answer cannot change: the slot is already taken, the weekly allowance is
spent, the slot is beyond the venue's horizon, or the response is not something the
integration recognises. Being asked to slow down pauses the run for a couple of
seconds before the next attempt rather than answering at burst speed. A hard ceiling
of eight requests per run applies regardless of configuration.

## Editing and removing

- **Edit a job** — open the job under the account entry and choose **Reconfigure**.
- **Pause a job** — turn off its `Job enabled` switch. The configuration is kept and
  the job simply stops running.
- **Delete a job** — delete the subentry. Reservations already created in Skedda are
  not removed.

## Next steps

Continue with [Usage](usage.md).

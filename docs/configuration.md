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
| **Venue timezone** | Optional. An IANA timezone such as `Europe/Kyiv`. Leave empty to use Home Assistant's own timezone. |

The credentials are verified before the entry is created. If sign-in fails you are
told whether the credentials were rejected or the venue could not be reached.

### Why the venue timezone matters

Booking windows open at a local time at the venue, not at yours. If the venue is in
a different timezone from your Home Assistant host, set this field — otherwise the
window is calculated against the wrong clock and the reservation is submitted at the
wrong moment.

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
| **Duration** | Length in minutes, in 15-minute steps. | `90` |

### The booking window

These two fields describe your venue's reservation policy. They are the fields to
get right — everything about timing follows from them.

| Field | Meaning | Example |
|---|---|---|
| **Booking opens this many days before** | How far ahead the venue accepts reservations. | `7` |
| **Booking opens at (venue time)** | The local time of day the window opens. | `00:00:00` |

With the example values, a slot on Tuesday the 15th becomes bookable at midnight on
Tuesday the 8th, venue time. If you are unsure, the policy is usually stated on the
venue's Skedda page or in its house rules.

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

Both strategies stop as soon as a reservation succeeds, and both stop immediately if
the slot is already taken, if the venue asks the client to slow down, or if the
response is not something the integration recognises. A hard ceiling of eight
requests per run applies regardless of configuration.

## Editing and removing

- **Edit a job** — open the job under the account entry and choose **Reconfigure**.
- **Pause a job** — turn off its `Job enabled` switch. The configuration is kept and
  the job simply stops running.
- **Delete a job** — delete the subentry. Reservations already created in Skedda are
  not removed.

## Next steps

Continue with [Usage](usage.md).

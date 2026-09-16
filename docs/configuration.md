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

Open the account under **Settings → Devices & Services** and choose
**Book a court**. Home Assistant calls these subentries; the button is on the
integration's page, not on the account's device page.

The form asks four things, because they are the four only you can know:

| Field | Meaning | Example |
|---|---|---|
| **Court** | Which space to book. The list is read from your venue. | `Court 1` |
| **Date** | The date to book. Pre-filled with the furthest date the venue currently accepts, which is usually the one worth racing for. | `29/09/2026` |
| **Start time** | Local start time at the venue. | `20:00` |
| **Duration** | Length in minutes. Pre-filled from the bookings your account already holds, then held to the venue's slot size and weekly allowance. | `60` |

**Repeat** is `Once` unless you change it. A one-off books that single date and
then has nothing left to do. `Every week` or `Every other week` keeps booking
the same weekday - taken from the date you picked, so the two can never
disagree.

Choosing to repeat opens a second step, because a repeating job is the only
kind with anything left to decide. A one-off is finished in one screen:

| Field | Default |
|---|---|
| **Job name** and **Booking title** | Written from your answers: `Court 1 · Tuesdays 20:00` |
| **Repeat until** | Empty, meaning no end. This is where a season goes: a court paid for until the end of autumn stops there. |
| **Booking opens this many days before** | The venue's own horizon |
| **Strategy** | Precise |
| **Notify these services with the result** | None |

Editing a job shows every field at once. A job that already exists should not
be harder to change than an unknown one is to create.

### Seasons

A season is the stretch during which the job is allowed to run - typically the
months your venue subscription is paid for. Set **Repeat until** to the last
date; outside it the job's status reads *Out of season*, no timer is armed, and
the account stops being polled. Extend the date when the new season starts.

### Limits the form will not let you past

Two settings describe a job that could never book anything, so they are refused
while you are still in the form rather than failing quietly every week:

- a **duration** longer than the venue's weekly allowance;
- a **window** wider than the venue's own horizon.

Both limits are read from the venue and shown in the form's description.

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

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

## Putting bookings in Google Calendar

Optional, and separate from any one account: link it once and every booking
that lands becomes an event, with the people you name invited to it.

**Settings → Devices & Services → Add Integration → Skedda Scheduler → Google
Calendar.**

### Why this needs your own Google credential

Home Assistant can create a calendar event but cannot invite anybody to one —
neither `calendar.create_event` nor `google.add_event` accepts attendees. To
send invitations the integration has to talk to Google's Calendar API itself,
and Google requires the credential to be yours.

One-off setup, roughly ten minutes:

1. In the [Google Cloud console](https://console.cloud.google.com/), create a
   project (or reuse one).
2. Enable the
   [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com).
3. Configure the
   [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
   as **External**, then **publish** it. An app left in *Testing* is issued a
   refresh token that expires after seven days, and the link would need
   renegotiating every week. Published but unverified is fine here - it is your
   own app used by you - though Google shows an "unverified app" warning the
   first time, behind **Advanced**.
4. Create an **OAuth client ID** of type *Web application* at
   [Credentials](https://console.cloud.google.com/apis/credentials), with the
   redirect URI Home Assistant shows you - usually
   `https://my.home-assistant.io/redirect/oauth`.
5. Give the client id and secret to Home Assistant when it asks.

This is the same procedure Home Assistant's own Google integration requires,
and one client covers both: Home Assistant keeps credentials per integration,
so reuse means entering the same id and secret again, not returning to Google.

### What gets written

| Field | |
|---|---|
| **Calendar** | Only calendars you can write to are listed. |
| **Event title** | `Tennis 🎾` unless you change it. |
| **Address** | Shown on the event and used for directions. |
| **Invite by email** | Google emails each person an invitation. Leave empty to book quietly. |

Only bookings that succeeded are written. If Google cannot be reached the
booking still stands and the failure is logged — a missing diary entry must not
look like a lost court.

Change any of it later with **Reconfigure** on the Google Calendar entry; the
link itself is not renegotiated.

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

## Catching a slot somebody gives up

A booking job races for the moment a window opens. It either wins or it does
not, and at a venue allowing an hour a week a lost race costs the whole week.
But courts come back: people cancel. Nothing in Skedda tells a member when that
happens, so the only way to profit from it is to keep looking.

**Settings → Devices & Services → Add Integration → Skedda Scheduler → Slot
watch.** There is nothing to fill in: the venue comes from your accounts. Then
add one rule per thing you want caught.

### Why the watch is not part of an account

Accounts are interchangeable here. Each is one hour a week, and a rule does not
care which of them pays - so when a slot appears, the watch spends whichever
account still has an hour that week. Three accounts are what three hours in a
row costs.

### A rule

| Field | Default | Meaning |
|---|---|---|
| **Name** | — | What the rule is for: `Our evening` |
| **Days** | — | Only these weekdays are watched |
| **Not before / not after** | 19:00 / 21:00 | A slot must start at or after the first and end at or before the second |
| **Courts** | any | Which spaces, in preference order |
| **Duration** | 60 min | How long a slot to take |
| **What counts as a catch** | both | See below |
| **Most hours in a row** | 180 min | A catch is refused if it would build a longer block |
| **A neighbour may be on another court** | off | Whether a block may continue on a different space |
| **Ignore slots starting sooner than** | 180 min | A court starting in an hour cannot be filled with people |
| **How often to look** | Stepped | Calm 30/15/5, Stepped 15/5/2, Fast 5/2/1 minutes |
| **Book it** | on | Turn off to be told and take it yourself |
| **Watch until** | empty | End of season |

There is no horizon field: the horizon is always today to the venue's last open
day, because nothing beyond it can be booked at all.

### The two modes

**Next to ours** grows a block. When you already hold an hour that day, the
watch will take the hour immediately before or after it - on the same court
unless you allow another - as long as the whole run stays inside the cap. Two
accounts make two hours; three make three. If every hour of yours that day
already has neighbours, or the block is at its cap, the rule refuses and says
nothing: that is a normal outcome, not a failure.

**Any free slot** applies only on a day you hold nothing at all. This is the
case where every attempt was lost and the group would otherwise not play.

With both enabled, a neighbour wins: growing a block to three hours is worth
more than a lone hour elsewhere.

### What it costs the venue

Watching is the only expensive thing this integration does. One request covers
the whole horizon, so the cost is counted in looks:

| Interval | Requests a week |
|---|---|
| 15 min | ~670 |
| 5 min | ~2000 |
| 1 min | ~10000 |

Four things keep that defensible. **The gate:** when no account has quota left
in any week of the horizon, the watch stops entirely - no looking, no requests -
until the horizon rolls forward or something is cancelled. **One reader:** every
account sees the same venue-wide list, so exactly one polls. **The existing
poll:** the watch opens no loop of its own; it raises the rate of the poll the
account already makes. **Steps:** cancellations cluster near the day of play, so
the rate follows the nearest candidate day rather than running flat.

In practice a week with the gate open all the way through costs 700-1000
requests at the default speed - about what a member with the venue's page open
all day produces.

### Telling it sooner

If something else hears about cancellations before we do - a venue's Telegram
channel, say - an automation can call `skedda_scheduler.slot_freed` and the
watch looks immediately. See [Usage](usage.md#skedda_schedulerslot_freed).

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

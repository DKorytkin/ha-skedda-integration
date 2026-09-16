<img src="assets/skedda-icon-dark.svg#gh-light-mode-only" alt="" width="72" align="right">
<img src="assets/skedda-icon-light.svg#gh-dark-mode-only" alt="" width="72" align="right">

# Skedda Scheduler

A Home Assistant integration that holds your regular slot at a venue that books
through [Skedda](https://www.skedda.com/).

Venues open reservations a fixed period ahead, and the horizon rolls with the clock:
where bookings open two weeks out, the 18:00 court two weeks from today becomes
available at 18:00 today — not a minute earlier, and gone shortly after. Keeping a
weekly slot means being at a keyboard at that exact minute, every week, all season.

This integration turns that into configuration. Describe the slot once — court,
weekday, time, how far ahead the venue opens bookings — and Home Assistant submits
the reservation the instant the window opens, timed against the venue server's own
clock, then reports what happened as entities, events and notifications.

## Status

v0.0.1 — the first release. Home Assistant 2026.3.0 or newer.

The code is complete and tested, but no booking has yet been placed by the
integration running inside Home Assistant. Treat this release as one to try
rather than one to rely on for a slot you care about.

## How it works

Skedda publishes no API for creating reservations. This integration speaks the same
undocumented HTTP interface the Skedda web application uses, which means that
interface may change without notice. When it does, the integration raises a repair
issue in Home Assistant rather than failing quietly.

Read your venue's terms of use before automating reservations, and use this only
with an account you own.

## Install

Add this repository to HACS as a custom repository of type **Integration**:

```
https://github.com/DKorytkin/ha-skedda-integration
```

Install **Skedda Scheduler**, restart Home Assistant, then add the integration under
**Settings → Devices & Services**. Full steps in
[docs/installation.md](docs/installation.md).

## Configure

Add an account with your venue subdomain, email and password; the venue's timezone,
slot size, booking horizon and weekly allowance are read from the venue itself. Then
add one booking job per recurring slot. Every field is explained in
[docs/configuration.md](docs/configuration.md).

## Documentation

| Page | Contents |
|---|---|
| [Installation](docs/installation.md) | Installing through HACS, requirements, updating |
| [Configuration](docs/configuration.md) | Adding an account, creating a booking job, every field explained |
| [Usage](docs/usage.md) | Entities, services, events, automation examples |
| [Architecture](docs/architecture.md) | How the integration is put together and why |
| [Troubleshooting](docs/troubleshooting.md) | Diagnostics, repair issues, common failures |

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the development setup and the two rules that matter most.

## Trademark

Skedda is a trademark of its owner. The logo in this repository is used to
identify the service this integration talks to. This project is unofficial and
is not affiliated with, endorsed by, or supported by Skedda. See
[assets/README.md](assets/README.md).

## Licence

MIT. See [LICENSE](LICENSE).

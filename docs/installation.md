# Installation

## Requirements

- Home Assistant **2026.3.0** or newer.
- [HACS](https://www.hacs.xyz/) installed and set up.
- A Skedda account that signs in with an email address and password.

## Install through HACS

The integration is distributed as a HACS custom repository.

1. Open **HACS** in the Home Assistant sidebar.
2. Open the three-dot menu in the top right and choose **Custom repositories**.
3. Paste the repository URL:

   ```
   https://github.com/DKorytkin/ha-skedda-integration
   ```

4. Set **Type** to `Integration` and select **Add**.
5. Find **Skedda Scheduler** in the HACS list and select **Download**.
6. Restart Home Assistant.

## Install manually

Use this only if you do not run HACS.

1. Download the latest release from the
   [releases page](https://github.com/DKorytkin/ha-skedda-integration/releases).
2. Copy the `custom_components/skedda_scheduler` directory into your Home Assistant
   configuration directory, so that the result is:

   ```
   config/
   └── custom_components/
       └── skedda_scheduler/
           ├── manifest.json
           └── ...
   ```

3. Restart Home Assistant.

## Verify the installation

Go to **Settings → Devices & Services → Add Integration** and search for
`Skedda`. If **Skedda Scheduler** appears, the integration loaded correctly.

If it does not appear, the files are in the wrong place or Home Assistant has not
been restarted. Check **Settings → System → Logs** for entries from
`custom_components.skedda_scheduler`.

## Updating

HACS shows an update when a new release is published. Select **Update** and restart
Home Assistant. Your accounts and booking jobs are preserved across updates.

## Uninstalling

1. Remove every Skedda Scheduler entry under **Settings → Devices & Services**.
   This deletes the stored credentials, the booking jobs and the recorded attempt
   history for that account.
2. Remove the integration from HACS.
3. Restart Home Assistant.

Reservations already created in Skedda are not affected. Cancel those in Skedda
itself if you no longer want them.

## Next steps

Continue with [Configuration](configuration.md).

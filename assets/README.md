# Assets

## Provenance

The mark in these files is **Skedda's logo**, supplied by the repository owner
from Skedda's own site. It is Skedda's trademark, not this project's.

It is used here for one purpose: to identify which service the integration
talks to, in the README and in Home Assistant's brand listing. This project is
not affiliated with, endorsed by, or supported by Skedda. If Skedda would
rather it were not used this way, remove these files and the references to them
— nothing in the integration depends on them.

## Files

| File | Use |
|---|---|
| `skedda-icon.svg` | The mark on a square canvas, inheriting `currentColor`. Use this where the surrounding text colour is right. |
| `skedda-icon-dark.svg` | The same, in near-black. For light backgrounds. |
| `skedda-icon-light.svg` | The same, in white. For dark backgrounds. |
| `skedda-logo.svg` | The original path on its untrimmed canvas, as supplied. |
| `brands/` | What [home-assistant/brands](https://github.com/home-assistant/brands) expects. |

## Submitting to home-assistant/brands

The HACS check for brand assets is currently skipped in
`.github/workflows/validate.yml`. To close it, open a pull request against
[home-assistant/brands](https://github.com/home-assistant/brands) adding:

```
custom_integrations/skedda_scheduler/icon.png      256×256
custom_integrations/skedda_scheduler/icon@2x.png   512×512
custom_integrations/skedda_scheduler/logo.png
custom_integrations/skedda_scheduler/logo@2x.png
```

The files in `brands/` are those images, rendered from the SVG and trimmed to
the artwork with a small margin.

**One decision is still open:** they are rendered in near-black, which reads
well on Home Assistant's light theme and poorly on its dark one. Skedda's own
brand colour would be better than either. If you know it, re-render with it:

```bash
# edit the fill in assets/skedda-icon-dark.svg, then
cd assets
qlmanage -t -s 256 -o . skedda-icon-dark.svg && mv skedda-icon-dark.svg.png brands/icon.png
qlmanage -t -s 512 -o . skedda-icon-dark.svg && mv skedda-icon-dark.svg.png brands/icon@2x.png
```

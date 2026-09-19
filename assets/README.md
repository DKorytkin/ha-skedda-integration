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
| `brands/` | The same images in the shape [home-assistant/brands](https://github.com/home-assistant/brands) expects, for the HACS catalogue submission. |

## Where the icons are used

Since Home Assistant 2026.3.0 a custom integration carries its own brand
images, and they take priority over the brands CDN:

```
custom_components/skedda_scheduler/brand/icon.png      256×256
custom_components/skedda_scheduler/brand/icon@2x.png   512×512
```

`brands/` holds the same files, kept for the day this integration is submitted
to [home-assistant/brands](https://github.com/home-assistant/brands) - which
HACS still requires for inclusion in its default catalogue, though no longer
for the icon to appear. The logo is identical to the icon, so only the icon is
shipped: that is also what brands asks for when the two are the same.

**One decision is still open:** they are rendered in near-black, which reads
well on Home Assistant's light theme and poorly on its dark one. Skedda's own
brand colour would be better than either. If you know it, re-render with it:

```bash
# edit the fill in assets/skedda-icon-dark.svg, then
cd assets
qlmanage -t -s 256 -o . skedda-icon-dark.svg && mv skedda-icon-dark.svg.png brands/icon.png
qlmanage -t -s 512 -o . skedda-icon-dark.svg && mv skedda-icon-dark.svg.png brands/icon@2x.png
cp brands/icon.png brands/icon@2x.png ../custom_components/skedda_scheduler/brand/
```

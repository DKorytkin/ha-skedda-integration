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

## Where the icons are used

Since Home Assistant 2026.3.0 a custom integration carries its own brand
images, and they take priority over the brands CDN. HACS accepts them too, for
its default catalogue as well, so nothing is submitted to
[home-assistant/brands](https://github.com/home-assistant/brands):

```
custom_components/skedda_scheduler/brand/icon.png            256×256  near-black mark
custom_components/skedda_scheduler/brand/icon@2x.png         512×512
custom_components/skedda_scheduler/brand/dark_icon.png       256×256  white mark, for the dark theme
custom_components/skedda_scheduler/brand/dark_icon@2x.png    512×512
```

All four have transparent backgrounds. The logo is identical to the icon, so
only the icon is shipped.

**One decision is still open:** the mark is near-black on the light theme and
white on the dark one. Skedda's own brand colour would read better on both. If
you know it, re-render all four in that colour, keeping the backgrounds
transparent.

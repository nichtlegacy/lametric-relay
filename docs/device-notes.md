# Device notes

These findings were verified on a pre-2022 **LaMetric Time LM 37X8** running firmware
**2.3.9**. Newer `sa8` devices may behave differently.

## Verified capabilities

| Capability | Notification | DIY widget |
| --- | :---: | :---: |
| Multiple rotating frames | yes | yes |
| Base64 PNG and animated GIF icons | yes | yes |
| `goalData` progress bars | yes | yes |
| `text` plus `goalData` | yes, with an empty `unit` | yes |
| `chartData` spike charts | yes | yes |
| Scrolling text beyond 28 pixels | yes | yes |
| Built-in sound and MP3 by URL | yes | no |
| Persistent display | `cycles: 0` or `lifeTime` | yes |

The left 8×8 pixels are full RGB. The remaining 29×8 area renders text and data in
white.

## Display layout

Read off photographs of the panel, LED by LED. These are what
[`site/font.js`](../site/font.js) reproduces:

- **Column 8 stays dark** next to the icon. Text and progress bars use columns 9-36,
  so the usable width is 28 pixels, and text past that scrolls.
- **Text is centred** in those 28 columns, rounding the left margin down, and sits on
  rows 1-5. Row 0 and row 6 stay dark.
- **There is no lowercase.** Mixed-case text renders as capitals.
- **Glyphs are 3×5** with one dark column between them, except `N` (4 wide), `M` and
  `W` (5), and `I` (1). A space is 2 wide.
- **A `goalData` bar** occupies row 7 across the same 28 columns, `round(fraction ×
  28)` lit. The rest of the track is not dark, it stays lit at about a quarter
  brightness.

## Known limits

- **LMSP pixel streaming** is available on `model=sa8` devices. The LM 37X8 returns
  `Resource not found`.
- **App actions require `POST`.** `PUT` returns `Not allowed`; the
  `clock.clockface` action works with `POST`.
- **Apps cannot be installed through the API** on firmware 2.1.0 and newer.
- **SSH is unavailable** on this device; port 22 refuses connections.
- **Store icon IDs are unreliable.** Some render and others show a placeholder, so the
  hub uses base64 files.
- **Per-frame `duration` is ignored** by the DIY widget even though the API validates
  its range.
- **Text formatting is not supported.** `font`, `bold` and similar keys are accepted
  but have no visible effect.

## Persistent DIY widget

The installed **My Data DIY** app accepts local pushes:

```http
POST https://<device>:4343/api/v2/widget/update/com.lametric.diy.devwidget/<widget-id>
Authorization: Basic dev:<key>
Content-Type: application/json

{"frames": [{"text": "Build passed", "icon": "data:image/png;base64,..."}]}
```

The widget ID differs per device. [`lametric.py`](../lametric.py) discovers it through
`GET /api/v2/device/apps` and caches it for the process lifetime.

The endpoint can answer HTTP 200 while discarding an invalid payload. A successful
status code alone is therefore not visual verification.

## Clock-face action

```http
POST /api/v2/device/apps/com.lametric.clock/widgets/<widget-id>/actions

{"id": "clock.clockface", "params": {"type": "custom", "icon": "data:image/gif;base64,..."}, "activate": false}
```

The current custom face cannot be read back through the API. `CLOCK_ICON_DEFAULT` is a
configured fallback, not a backup of the previous face.

## Screensaver and quiet hours

On firmware 2.3.9, the ambient-light screensaver accepts only `critical`
notifications. A rejected lower-priority notification returns HTTP 400.

The time-based mode cannot be enabled reliably through the device API: writes return
HTTP 200 but the device ignores the mode fields. LaMetric Relay therefore implements its
own schedule; see [Configuration and operations](configuration.md#quiet-hours).

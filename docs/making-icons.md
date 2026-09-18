# Making icons

The colour area on a LaMetric Time is only 8×8 pixels. Simple silhouettes and deliberate
pixel art survive; detailed logos usually do not.

## Fetch the project icons

Third-party icons are not committed to this repository. Their LaMetric library IDs and
recolouring rules live in [`assets/icons.json`](../assets/icons.json):

```bash
scripts/fetch-icons.py
scripts/fetch-icons.py --check
scripts/fetch-icons.py --force
```

The Docker build fetches them in a separate stage, so ImageMagick is not part of the
runtime image. [`tests/test_icons.py`](../tests/test_icons.py) verifies that every
default icon used by the code is either in the manifest or committed as original work.

## Convert an existing image

[`scripts/icon.py`](../scripts/icon.py) accepts a Dashboard Icons slug, URL or local
file. It needs ImageMagick.

```bash
scripts/icon.py plex
scripts/icon.py https://example.com/logo.svg -o assets/logo.png
scripts/icon.py ./logo.png --sharpen -o assets/logo.png
scripts/icon.py openai --tint white -o assets/openai.png
```

Use `--tint` for dark monochrome artwork that would disappear on the device's black
background. Use `--alpha` only when transparency is intentional.

Preview the final pixels in the terminal:

```bash
scripts/icon.py --preview assets/logo.png
```

## Draw pixel art

When downscaling loses the shape, draw the pixels directly with
[`scripts/pixelart.py`](../scripts/pixelart.py):

```bash
scripts/pixelart.py assets/mark.png '#D97757' <<'MAP'
...##...
#..##..#
.#.##.#.
########
########
.#.##.#.
#..##..#
...##...
MAP
```

`.` and spaces are off pixels; `#` uses the first colour. Pass a second colour as the
third argument and use any other visible character for those pixels.

## Add an icon to an add-in

For original artwork, commit the generated PNG or GIF under `assets/`. For a third-party
LaMetric library icon, add its ID and optional single transformation to
`assets/icons.json` instead of committing the downloaded file.

Reference it through `lametric.icon()` so local files become base64 data URIs:

```python
ICON = os.environ.get("EXAMPLE_ICON", "assets/example.png")
frame = {"icon": lametric.icon(ICON), "text": "Example"}
```

Run `scripts/fetch-icons.py --check` and `python3 tests/test_icons.py` before committing.

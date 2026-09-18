# Contributing

The most useful thing you can add is an add-in: one Python file that turns some source
you care about into frames. This page is the process around that. The contract itself
lives in [Writing an add-in](docs/writing-add-ins.md).

## What belongs here

The hub owns policy — scheduling, state, quiet hours, rate limiting, talking to the
device. An add-in owns one source: it parses a payload or polls an API and decides
whether something is worth showing. If a change needs the hub to learn about a specific
source, it is usually in the wrong half.

Two constraints that are not up for negotiation, because they are the reason this thing
is pleasant to run:

- **The runtime is the Python standard library.** No `pip install` to run the hub.
  Development tools may need more (ImageMagick for icons, Playwright for the README
  recording); the service may not.
- **An unconfigured add-in disables itself.** Nobody should have to configure a source
  they do not use in order to use the ones they do.

## Adding an add-in

Copy [`examples/addins/doorbell.py`](examples/addins/doorbell.py) into `addins/` and
work from there. A complete contribution is:

- [ ] **`addins/<name>.py`** with a module docstring that says how to point the source
      at it — the exact webhook URL, the exact settings page. Every existing add-in
      does this, and it is the documentation people actually read.
- [ ] **`configured()`** returning `False` when its settings are missing, so the hub
      skips it instead of failing every minute.
- [ ] **Edge detection.** A poller runs forever; report the moment something changed,
      not the fact that it is still true. Use the `state` dict — it survives restarts.
- [ ] **Icons in [`assets/icons.json`](assets/icons.json)**, never committed as files.
      See [Making icons](docs/making-icons.md).
- [ ] **A test** in `tests/`, plain `assert`, no framework. Feed your `hook` or `poll` a
      real payload and check the frame that comes out. Include the quiet cases: the
      heartbeat that should produce nothing is as important as the event that should.
- [ ] **A scenario** in [`scripts/build-preview.py`](scripts/build-preview.py) so your
      add-in appears on the [preview site](https://nichtlegacy.github.io/lametric-relay/).
      Add a line to `GROUPS` for the display name, one to `SHORT` for the chip label,
      and a `dict(...)` to `scenarios()`. The frames come from running your code, so
      there is nothing to keep in sync.

Text is worth thinking about. The display is 37 × 8 pixels: an 8 × 8 icon and 28 usable
columns of text, which is about seven capitals before it starts scrolling. The device
has no lowercase, so whatever you send comes out in capitals. The **Custom** tab on
the preview site counts the pixels for you.

## Running the checks

```bash
scripts/fetch-icons.py                        # once, needs ImageMagick
for test in tests/test_*.py; do LAMETRIC_KEY=test python3 "$test"; done
LAMETRIC_KEY=test ./hub.py --list             # your add-in should appear, or be skipped
scripts/build-preview.py --check --no-library
```

CI runs the same things, plus a Docker build. It needs no secrets, so it runs on pull
requests from forks.

## Style

Match what is already there rather than any general rule:

- Comments explain **why**, not what. If a line looks odd, the comment says which
  problem made it that way. Several of them name a device quirk you would otherwise
  rediscover the hard way.
- Prefer the smaller change. This codebase stays readable because nothing was added
  speculatively.
- No new runtime dependency, and no abstraction with one implementation.

## Pull requests

One subject per pull request, and say what you observed rather than what you intended —
"Tautulli sends `media_type` empty for music, so the title fell back to the album" is
worth more than "improve Plex handling". If you changed device behaviour or discovered
one, add it to [Device notes](docs/device-notes.md); that file is the project's memory
of what the hardware actually does.

If you are unsure whether something fits, open an issue first. A short description of
the source and one example payload is enough to tell.

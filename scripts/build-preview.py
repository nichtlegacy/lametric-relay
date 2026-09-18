#!/usr/bin/env python3
"""Build the interactive display preview in `_site/`.

The scenarios are not written by hand. This script imports the real add-ins,
feeds them the payload a real source would send, and records whatever they
return. A change to `plex.py` therefore changes the preview on the next build,
and the site cannot drift into showing something the code no longer does.

    scripts/build-preview.py            # -> _site/
    scripts/build-preview.py --check    # build, then fail on an empty scenario

Icons must exist before this runs; see scripts/fetch-icons.py.
"""
import argparse
import base64
import json
import re
import subprocess
import os
import shutil
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "addins"))
# The add-ins build data URIs through lametric.icon(), which needs no device,
# but importing hub without a key would be inconsistent with how CI runs.
os.environ.setdefault("LAMETRIC_KEY", "preview")
# Always the committed example mapping, never a real one: this output is published.
os.environ["USER_MAPPING"] = os.path.join(ROOT, "assets", "user_mapping.example.json")

import hub          # noqa: E402  - path set above
import _shared      # noqa: E402
import claude       # noqa: E402
import clockface    # noqa: E402
import codex        # noqa: E402
import forgejo      # noqa: E402
import kuma         # noqa: E402
import lametric     # noqa: E402
import plex         # noqa: E402
import psn          # noqa: E402
import say          # noqa: E402

SITE = os.path.join(ROOT, "site")
OUT = os.path.join(ROOT, "_site")

# The composer needs something to play with. These come from the same public
# library the project's own icons do, ordered by popularity, and are cached so a
# rebuild does not hammer the API.
LIBRARY_API = "https://developer.lametric.com/api/v2/icons?page=1&page_size={n}&order=popular"
LIBRARY_FILE = "https://developer.lametric.com/content/apps/icon_thumbs/{id}.{ext}"
LIBRARY_PAGE = "https://developer.lametric.com/icons?icon={id}"
LIBRARY_SIZE = 185
CACHE = os.path.join(ROOT, ".icon-cache")

# A Wednesday at 03:30, inside the example quiet-hours window below. Fixed so the
# generated flags never depend on when the build ran.
QUIET_AT = time.struct_time((2026, 9, 9, 3, 30, 0, 2, 252, 0))
QUIET_PLAN = "mo-fr 00:00-09:00; sa,so 03:00-11:00"


def webhook(mod, payload, headers=None):
    """Run an add-in's hook the way the hub would and return its first event."""
    events = mod.hook(payload, headers or {}, {}) or []
    return events[0] if events else None


def quota_event(mod, before, now, resets=None):
    """Run the shared quota logic with a previous poll already in state."""
    state = {"last": before, "resets": {k: "2026-09-09T00:00:00Z" for k in before}}
    events = _shared.events(mod.ICON, mod.ICON_LOW, now, state, resets) or []
    return events[0] if events else None


def summary(count, priority, icon):
    """Ask the hub's own limiter what a burst collapses into."""
    limiter = hub.Limiter(maximum=1, window=60)
    limiter.allow(0)                       # use up the single slot
    for _ in range(count):
        limiter.hold({"frames": [{"icon": lametric.icon(icon)}], "priority": priority})
    return limiter.summary(3600)           # far enough ahead that the window reopened


# What "Surprise me" pulls from. Deliberately a spread of lengths, because half
# the point of the composer is seeing where a frame stops fitting and starts
# scrolling. A trailing percentage drives the progress bar along with it.
TEXTS = [
    "Nothing is on fire",
    "Build passed",
    "Merged to main",
    "Deploy done",
    "3 PRs waiting",
    "It works on my machine",
    "Rate limit reached",
    "5H 73%",
    "CPU 91%",
    "Battery 12%",
    "Disk 94% full",
    "Uptime 142 days",
    "Cert expires in 7 d",
    "Snapshot taken",
    "Backup complete",
    "NAS 4.2 TB free",
    "Solar 4.8 kW",
    "Plex offline",
    "Mr. Robot \u00b7 S01E01",
    "LEGACY: Cyberpunk 2077",
    "New follower",
    "42 unread",
    "Standup in 5",
    "Sprint ends today",
    "Coffee ready",
    "Pizza in 5 min",
    "Dishwasher done",
    "Washing machine done",
    "Bins out tonight",
    "Doorbell",
    "Motion at front door",
    "Garage door open",
    "Guest joined the Wi-Fi",
    "Train delayed 12 min",
    "22 \u00b0C and sunny",
    "Printer out of paper",
    "On air",
    "All up",
    "Lunch",
    "7 new",
    "Oops",
]

# Chip groups, in display order. A new add-in needs one line here and nothing in
# the page: anything missing is appended at the end under its own module name.
GROUPS = [
    ("plex", "Plex"),
    ("forgejo", "Forgejo"),
    ("kuma", "Uptime Kuma"),
    ("psn", "PlayStation"),
    ("codex", "Codex"),
    ("claude", "Claude"),
    ("say", "Say"),
    ("clockface", "Clock face"),
    ("hub", "Hub"),
]

# Poll intervals, read from the add-ins so the page quotes the real number.
INTERVALS = {"codex": codex.INTERVAL, "claude": claude.INTERVAL,
             "clockface": clockface.INTERVAL}

# Chip labels. Short enough that a dozen fit on one row under the display, which
# is the only place a scenario can be picked without scrolling the clock away.
SHORT = {
    "plex-movie": "Film starts",
    "plex-episode": "Episode starts",
    "plex-down": "Server offline",
    "plex-up": "Server back",
    "forgejo-fail": "Build fails",
    "forgejo-ok": "Build passes",
    "forgejo-push": "Push",
    "kuma-down": "Monitor down",
    "kuma-up": "Monitor back",
    "psn-game": "Game starts",
    "psn-warning": "Token expiring",
    "psn-token": "Token invalid",
    "say": "Custom message",
    "codex-warning": "Codex at 25%",
    "claude-critical": "Claude at 10%",
    "codex-reset": "Quota resets",
    "burst": "Burst collapsed",
    "widget-codex": "Codex quota",
    "widget-claude": "Claude quota",
    "widget-plex": "Plex stream",
    "widget-psn": "PSN game",
    "face-default": "Idle",
    "face-claude": "Claude",
    "face-codex": "Codex",
    "face-plex": "Plex",
}


# Index 0 is "off". Everything else is a palette entry addressed by one character,
# which caps an icon at 64 colours -- far more than an 8x8 frame ever uses.
ALPHABET = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
# ImageMagick drops the alpha column for a frame that happens to be fully
# opaque, so the tuple is three values there and four everywhere else. Requiring
# four silently blanked those frames, which showed up as the display going dark
# for a beat at the loop boundary.
PIXEL = re.compile(r"^(\d+),(\d+): \((\d+),(\d+),(\d+)(?:,(\d+))?\)")
# ImageMagick 7 puts everything behind `magick`; 6 ships the separate binaries.
IM = ["magick"] if shutil.which("magick") else ["convert"]
IDENTIFY = ["magick", "identify"] if shutil.which("magick") else ["identify"]


def decode(path, size=8):
    """Turn an image into palette indices per frame, plus frame delays in ms.

    The browser only advances a GIF that it is actually painting, which makes
    drawing one into a canvas unreliable. Decoding here instead means the page
    owns the animation clock and behaves the same everywhere.
    """
    dump = subprocess.run([*IM, path, "-coalesce", "-alpha", "set", "-depth", "8", "txt:-"],
                          capture_output=True, text=True, check=True).stdout
    palette, frames, current = [], [], None
    for line in dump.splitlines():
        if line.startswith("# ImageMagick"):
            current = ["."] * (size * size)
            frames.append(current)
            continue
        m = PIXEL.match(line)
        if not m or current is None:
            continue
        x, y, r, g, b = (int(v) for v in m.groups()[:5])
        a = int(m.group(6)) if m.group(6) is not None else 255
        if x >= size or y >= size:
            continue
        # The same rule the renderer uses: on an LED matrix there is no
        # difference between black and not lit.
        if a <= 24 or r + g + b <= 24:
            continue
        colour = f"#{r:02x}{g:02x}{b:02x}"
        if colour not in palette:
            palette.append(colour)
        current[y * size + x] = ALPHABET[palette.index(colour) % len(ALPHABET)]

    delays = subprocess.run([*IDENTIFY, "-format", "%T ", path],
                            capture_output=True, text=True, check=True).stdout.split()
    # Only 0 and 1 centisecond mean "as fast as possible", and every browser reads
    # those as 100 ms. Everything else is used as written: clamping 2 cs up to
    # 100 ms turned the fast stretches of an animation into a stutter.
    ms = [(10 if int(d) <= 1 else int(d)) * 10 for d in delays] or [1000]
    ms = (ms * len(frames))[:len(frames)]
    return {"w": size, "h": size, "palette": palette,
            "frames": ["".join(f) for f in frames], "delays": ms}


def project_icons():
    """The icons this project already ships, as data URIs."""
    with open(os.path.join(ROOT, "assets", "icons.json")) as fh:
        manifest = json.load(fh)
    out = []
    for name, spec in manifest.items():
        if name.startswith("_"):
            continue
        ext = spec.get("ext", "png")
        path = os.path.join(ROOT, "assets", f"{name}.{ext}")
        if os.path.isfile(path):
            ref = f"assets/{name}.{ext}"
            out.append({"id": name, "title": spec.get("title", name), "ref": ref,
                        "uri": lametric.icon(ref), "own": True, **decode(path)})
    return out


# A loop this long reads as a broken icon in a picker rather than an animation.
# The two that hit it are a 67-second Tetris and a clock ticking once a second.
MAX_LOOP_MS = 15000


def library_icons(count=LIBRARY_SIZE):
    """Fetch the most popular community icons, or return nothing if offline.

    A failure here must not fail the build: the composer falls back to the
    project's own icons, which are always present.
    """
    os.makedirs(CACHE, exist_ok=True)
    try:
        # Ask for extra, because some are dropped below.
        with urllib.request.urlopen(LIBRARY_API.format(n=count + 15), timeout=20) as r:
            listing = json.loads(r.read())["data"]
    except Exception as exc:
        print(f"[preview] icon library unavailable ({type(exc).__name__}); "
              f"the composer will offer project icons only", file=sys.stderr)
        return []

    out = []
    for item in listing:
        ext = "gif" if item.get("type") == "movie" else "png"
        cached = os.path.join(CACHE, f"{item['id']}.{ext}")
        if not os.path.isfile(cached):
            try:
                with urllib.request.urlopen(
                        LIBRARY_FILE.format(id=item["id"], ext=ext), timeout=20) as r:
                    blob = r.read()
            except Exception:
                continue
            with open(cached, "wb") as fh:
                fh.write(blob)
        try:
            frames = decode(cached)
        except subprocess.CalledProcessError:
            continue      # a corrupt download is not worth failing the build over
        if sum(frames["delays"]) > MAX_LOOP_MS:
            continue
        # Someone uploaded a solid black square. On an LED matrix that is simply
        # nothing, and an icon that cannot light a pixel has no business in a
        # picker: it looks like the grid is broken.
        if not frames["palette"]:
            continue
        out.append({
            "id": item["code"],
            "title": item["title"],
            # The store code from the API, not a guess: animated icons are "a123"
            # and static ones "i123", and the device tells them apart.
            "ref": item["code"],
            "page": LIBRARY_PAGE.format(id=item["id"]),
            **frames,
        })
        if len(out) == count:
            break
    return out


def quiet_verdict():
    """Ask hub.muted() what happens to each priority, so the composer does not
    have to restate the rule in JavaScript."""
    out = {}
    for priority in ("info", "warning", "critical"):
        event = {"frames": [{"text": "x"}], "priority": priority}
        hub.QUIET_ALLOW_CRITICAL = False
        strict = hub.muted(event, QUIET_AT)
        hub.QUIET_ALLOW_CRITICAL = True
        lenient = hub.muted(event, QUIET_AT)
        hub.QUIET_ALLOW_CRITICAL = False
        out[priority] = {"muted": strict, "muted_allow_critical": lenient}
    return out


def scenarios():
    """Every state the preview can show, grouped by the route it takes."""
    return [
        # --- Notifications -------------------------------------------------
        dict(id="plex-movie", route="notification", addin="plex",
             title="A film starts", source="Tautulli webhook",
             note="Films get their release year, so two versions of the same title stay apart.",
             payload={"event": "start", "user": "SomePlexLogin",
                      "title": "The Big Lebowski", "media_type": "movie", "year": "1998"},
             event=webhook(plex, {"event": "start", "user": "SomePlexLogin",
                                  "title": "The Big Lebowski", "media_type": "movie",
                                  "year": "1998"})),
        dict(id="plex-episode", route="notification", addin="plex",
             title="An episode starts", source="Tautulli webhook",
             note="The series title wins over the episode title. At eight pixels high "
                  "“Mr. Robot” is worth a great deal more than "
                  "“eps1.0_hellofriend.mov”.",
             payload={"event": "start", "user": "SomePlexLogin",
                      "title": "eps1.0_hellofriend.mov", "show": "Mr. Robot",
                      "media_type": "episode", "season": "1", "episode": "1"},
             event=webhook(plex, {"event": "start", "user": "SomePlexLogin",
                                  "title": "eps1.0_hellofriend.mov", "show": "Mr. Robot",
                                  "media_type": "episode", "season": "1", "episode": "1"})),
        dict(id="plex-down", route="notification", addin="plex",
             title="The server goes offline", source="Tautulli webhook",
             note="Critical, so it survives quiet hours when they allow critical events.",
             payload={"event": "server_down"},
             event=webhook(plex, {"event": "server_down"})),
        dict(id="plex-up", route="notification", addin="plex",
             title="The server comes back", source="Tautulli webhook",
             note="Ordinary priority. Recovery is good news, not something to wake "
                  "anyone for.",
             payload={"event": "server_up"},
             event=webhook(plex, {"event": "server_up"})),
        dict(id="forgejo-push", route="notification", addin="forgejo",
             title="Someone pushes", source="Forgejo webhook",
             note="The branch is only named when it is not main; on main the count is enough.",
             payload={"repository": {"name": "lametric-relay"},
                      "ref": "refs/heads/dev", "commits": [{}, {}, {}]},
             event=webhook(forgejo, {"repository": {"name": "lametric-relay"},
                                     "ref": "refs/heads/dev", "commits": [{}, {}, {}]},
                           {"x-forgejo-event": "push"})),
        dict(id="forgejo-ok", route="notification", addin="forgejo",
             title="An action run passes", source="Forgejo webhook",
             note="Green Octocat, one cycle, no sound. A run that is still going "
                  "produces nothing at all.",
             payload={"repository": {"name": "lametric-relay"},
                      "action_run": {"conclusion": "success"}},
             event=webhook(forgejo, {"repository": {"name": "lametric-relay"},
                                     "action_run": {"conclusion": "success"}},
                           {"x-forgejo-event": "action_run"})),
        dict(id="forgejo-fail", route="notification", addin="forgejo",
             title="An action run fails", source="Forgejo webhook",
             note="Red Octocat and two cycles. The LaMetric library has no Forgejo icon.",
             payload={"repository": {"name": "lametric-relay"},
                      "action_run": {"conclusion": "failure"}},
             event=webhook(forgejo, {"repository": {"name": "lametric-relay"},
                                     "action_run": {"conclusion": "failure"}},
                           {"x-forgejo-event": "action_run"})),
        dict(id="kuma-down", route="notification", addin="kuma",
             title="A monitor goes down", source="Uptime Kuma webhook",
             note="Kuma flags only state changes as important, so a service that stays "
                  "down never notifies twice.",
             payload={"heartbeat": {"status": 0, "important": True},
                      "monitor": {"name": "Plex"}},
             event=webhook(kuma, {"heartbeat": {"status": 0, "important": True},
                                  "monitor": {"name": "Plex"}})),
        dict(id="kuma-up", route="notification", addin="kuma",
             title="It comes back", source="Uptime Kuma webhook",
             note="Same add-in, green icon, ordinary priority. Recovery is good news, "
                  "not an emergency.",
             payload={"heartbeat": {"status": 1, "important": True},
                      "monitor": {"name": "Plex"}},
             event=webhook(kuma, {"heartbeat": {"status": 1, "important": True},
                                  "monitor": {"name": "Plex"}})),
        dict(id="psn-game", route="notification", addin="psn",
             title="A game starts", source="psn-insights webhook",
             note="™ and ® are stripped: the clock has no glyph for them and "
                  "would waste pixels on a box.",
             payload={"event": "game_start", "user": "some_psn_id",
                      "metadata": {"game_name": "Cyberpunk 2077™"}},
             event=webhook(psn, {"event": "game_start", "user": "some_psn_id",
                                 "metadata": {"game_name": "Cyberpunk 2077™"}})),
        dict(id="psn-warning", route="notification", addin="psn",
             title="The NPSSO token is expiring", source="psn-insights webhook",
             note="A warning rather than a failure: there is still time to renew it, "
                  "so it does not need to override the screensaver.",
             payload={"event": "npsso_warning"},
             event=webhook(psn, {"event": "npsso_warning"})),
        dict(id="psn-token", route="notification", addin="psn",
             title="The NPSSO token dies", source="psn-insights webhook",
             note="The one PSN event worth interrupting for: nothing else works until "
                  "it is renewed.",
             payload={"event": "npsso_error"},
             event=webhook(psn, {"event": "npsso_error"})),
        dict(id="codex-warning", route="notification", addin="codex",
             title="Quota crosses 25%", source="Codex-LB poll",
             note="Only the crossing is reported. The next poll at 24% stays quiet, "
                  "because nothing changed that you did not already know.",
             payload={"5h": "60% -> 24%"},
             event=quota_event(codex, {"5h": 60, "7d": 80}, {"5h": 24, "7d": 80})),
        dict(id="codex-reset", route="notification", addin="codex",
             title="A quota window resets", source="Codex-LB poll",
             note="Detected from the provider\u2019s own reset timestamp moving, not from "
                  "the number jumping: with several accounts a stamp can move while the "
                  "total has not recovered.",
             payload={"5h": "8% -> 100%", "resetAt5h": "moved forward"},
             event=quota_event(codex, {"5h": 8, "7d": 40}, {"5h": 100, "7d": 40},
                               {"5h": "2026-09-09T06:00:00Z", "7d": "2026-09-14T00:00:00Z"})),
        dict(id="claude-critical", route="notification", addin="claude",
             title="Quota crosses 10%", source="ai-usage-insights poll",
             note="Below ten percent the priority becomes critical and the frame runs "
                  "twice. This one is worth looking up for.",
             payload={"5h": "30% -> 8%"},
             event=quota_event(claude, {"5h": 30, "7d": 60}, {"5h": 8, "7d": 60})),
        dict(id="say", route="notification", addin="say",
             title="Anything else", source="curl or Home Assistant",
             note="No payload shape to match. The sender decides the text, the icon and "
                  "the priority.",
             payload={"text": "Washing machine is done", "icon": "hass"},
             event=webhook(say, {"text": "Washing machine is done", "icon": "hass"})),
        dict(id="burst", route="notification", addin="hub",
             title="Seven events at once", source="hub rate limiter",
             note="Past the limit the hub stops forwarding and collapses the rest into "
                  "one frame, keeping the icon and priority of the worst event.",
             payload={"RATE_MAX": 4, "RATE_WINDOW": 60},
             event=summary(7, "critical", "assets/kuma_down.png")),

        # --- DIY widget ----------------------------------------------------
        dict(id="widget-codex", route="widget", addin="codex",
             title="Codex quota", source="Codex-LB poll",
             note="Two windows, weighted by credits across accounts. Always current, "
                  "never an interruption.",
             payload={"last": {"5h": 73, "7d": 41},
                      "resets": {"5h": "2026-09-09T06:00:00Z"}},
             frames=codex.widget({"last": {"5h": 73, "7d": 41}})),
        dict(id="widget-claude", route="widget", addin="claude",
             title="Claude quota", source="ai-usage-insights poll",
             note="Same shared renderer as Codex. An add-in supplies numbers, not layout.",
             payload={"last": {"5h": 92, "7d": 55}},
             frames=claude.widget({"last": {"5h": 92, "7d": 55}})),
        dict(id="widget-plex", route="widget", addin="plex",
             title="What is playing", source="Tautulli webhook",
             note="Whatever is playing, from any account: the webhook writes it and the "
                  "widget reads it. PLEX_WATCHER only gates the clock face, never this. "
                  "The frame disappears on the stop event.",
             payload={"playing": "Mr. Robot · S01E01", "playing_user": "LEGACY",
                      "playing_at": 1789000000},
             trigger={"event": "start", "user": "SomePlexLogin",
                      "title": "eps1.0_hellofriend.mov", "show": "Mr. Robot",
                      "media_type": "episode", "season": "1", "episode": "1"},
             frames=plex.widget({"playing": "Mr. Robot · S01E01"})),
        dict(id="widget-psn", route="widget", addin="psn",
             title="What is running", source="psn-insights webhook",
             note="Same idea on the console side.",
             payload={"playing": "Cyberpunk 2077", "user": "LEGACY"},
             trigger={"event": "game_start", "user": "some_psn_id",
                      "metadata": {"game_name": "Cyberpunk 2077™"}},
             frames=psn.widget({"playing": "Cyberpunk 2077"})),

        # --- Clock face ----------------------------------------------------
        dict(id="face-default", route="clockface", addin="clockface",
             title="Nothing is running", source="opt-in poll",
             note="The configured default. The device cannot report its current face, "
                  "so this is a setting rather than a restore.",
             payload={"CLOCKFACE": "1", "CLOCK_ICON_DEFAULT": clockface.ICONS["default"]},
             face=clockface.ICONS["default"]),
        dict(id="face-claude", route="clockface", addin="clockface",
             title="Claude is working", source="opt-in poll",
             note="Detected from usage records. A source keeps the face for at least ten "
                  "minutes so parallel tools cannot make the icon flicker.",
             payload={"CLOCKFACE_ACTIVE": clockface.ACTIVE_WINDOW,
                      "CLOCKFACE_HOLD": clockface.MIN_HOLD},
             face=clockface.ICONS["claude"]),
        dict(id="face-codex", route="clockface", addin="clockface",
             title="Codex is working", source="opt-in poll",
             note="Whichever left the most recent trace wins, once the hold has passed.",
             payload={"CLOCKFACE_IDLE": clockface.IDLE_AFTER},
             face=clockface.ICONS["codex"]),
        dict(id="face-plex", route="clockface", addin="clockface",
             title="A film is on", source="opt-in poll",
             note="Plex preempts everything: starting a film is deliberate, code typed "
                  "alongside it should not take the face back.",
             payload={"CLOCKFACE_PRIORITY": list(clockface.PRIORITY),
                      "PLEX_WATCHER": "LEGACY", "CLOCKFACE_PLEX_MAX": clockface.PLEX_MAX},
             face=clockface.ICONS["plex"]),
    ]


def build():
    out = []
    for s in scenarios():
        s["short"] = SHORT.get(s["id"], s["title"])
        s["interval"] = INTERVALS.get(s["addin"])
        event = s.pop("event", None)
        frames = s.pop("frames", None)
        face = s.pop("face", None)
        if event:
            s["frames"] = event.get("frames") or []
            s["priority"] = event.get("priority", "info")
            s["cycles"] = event.get("cycles", 1)
            s["sound"] = event.get("sound")
            # Ask the hub itself, rather than restating its rule in JavaScript.
            s["muted"] = hub.muted(event, QUIET_AT)
            hub.QUIET_ALLOW_CRITICAL = True
            s["muted_allow_critical"] = hub.muted(event, QUIET_AT)
            hub.QUIET_ALLOW_CRITICAL = False
        elif frames is not None:
            s["frames"] = frames
        elif face is not None:
            s["face"] = lametric.icon(face)
            s["frames"] = []
        out.append(s)
    return out


def link_icons(scenarios, icons):
    """Replace the data URIs the add-ins produced with a key into the icon table.

    The add-ins genuinely return base64, which is what the device wants. The page
    wants decoded frames, and storing each icon once instead of once per frame it
    appears in keeps the payload honest as well as small.
    """
    by_uri = {i.pop("uri"): i["ref"] for i in icons if i.get("uri")}
    missing = set()
    for s in scenarios:
        for frame in s.get("frames") or []:
            if "icon" in frame:
                ref = by_uri.get(frame["icon"])
                missing.add(frame["icon"][:40]) if ref is None else None
                frame["icon"] = ref
        if s.get("face"):
            s["face"] = by_uri.get(s["face"])
    return missing


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true",
                   help="fail when a scenario produced no frames")
    p.add_argument("--no-library", action="store_true",
                   help="skip the community icon library; the composer then offers "
                        "only this project's icons")
    a = p.parse_args()

    hub.QUIET_HOURS = QUIET_PLAN
    hub.QUIET_ALLOW_CRITICAL = False
    icons = project_icons() + ([] if a.no_library else library_icons())
    scenes = build()
    unlinked = link_icons(scenes, icons)
    if unlinked:
        print(f"[preview] icons with no table entry: {unlinked}", file=sys.stderr)
    data = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": __import__("version").__version__,
        "device": {"width": 37, "height": 8, "icon": 8, "text": 29},
        "quiet_hours": QUIET_PLAN,
        "quiet_verdict": quiet_verdict(),
        "groups": [{"addin": a, "label": l} for a, l in GROUPS],
        "texts": TEXTS,
        "icons": icons,
        "scenarios": scenes,
    }

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SITE, OUT)
    with open(os.path.join(OUT, "preview.json"), "w") as fh:
        json.dump(data, fh, indent=1)

    empty = [s["id"] for s in data["scenarios"]
             if not s.get("frames") and not s.get("face")] + sorted(unlinked)
    for s in data["scenarios"]:
        mark = "!" if s["id"] in empty else " "
        text = " / ".join(f.get("text", "") for f in s.get("frames") or []) or "(icon only)"
        print(f" {mark} {s['route']:12} {s['id']:18} {text}")
    print(f"\n{len(data['scenarios'])} scenarios, {len(data['icons'])} icons "
          f"-> {os.path.relpath(OUT, ROOT)}/preview.json")

    if empty:
        print(f"\nempty: {', '.join(empty)}", file=sys.stderr)
        if a.check:
            sys.exit(1)


if __name__ == "__main__":
    main()

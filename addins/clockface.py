"""Clock face icon based on the current activity.

The default is the coding icon. When Claude or Codex is in use, its logo is shown;
when the primary user is watching a Plex stream, the Plex logo is shown.

This is detected without any additional data sources:

* Claude and Codex through `ai.usage_event` records in the mounted ai-usage-insights
  database. Its collector runs every five minutes, so detection can lag by up to five
  minutes.
* Plex through the state of the plex add-in in the same `state.json` file.

The clock face cannot be read back because the API does not return it. The default is
therefore configuration, not a fallback based on the previous state.
"""
import json
import os
import sqlite3
import time
from datetime import datetime

import lametric

NAME = "clockface"
INTERVAL = int(os.environ.get("CLOCKFACE_INTERVAL", "60"))
# Deliberately disabled until explicitly enabled: the clock face cannot be read back,
# so overwriting it is irreversible. No one should lose a custom icon just because the
# relay was started.
ENABLED = os.environ.get("CLOCKFACE", "0") == "1"

# Without a delivery database, coding detection is unavailable; the clock face then
# follows only Plex and otherwise falls back to the default.
DB = os.path.expanduser(os.environ.get("AIUSAGE_DB", ""))
STATE_FILE = os.environ.get("STATE_FILE",
                            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         "state.json"))
# Empty means every active stream counts. A name limits it to that user.
WATCHER = os.environ.get("PLEX_WATCHER", "")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = "default"
ICONS = {
    DEFAULT: os.environ.get("CLOCK_ICON_DEFAULT", "assets/clock_default.gif"),
    "claude": os.environ.get("CLOCK_ICON_CLAUDE", "assets/claude.png"),
    "codex": os.environ.get("CLOCK_ICON_CODEX", "assets/codex.png"),
    "plex": os.environ.get("CLOCK_ICON_PLEX", "assets/plex.png"),
}

# A source is considered active if it left a trace within this window. This is generous
# enough to bridge thinking pauses and the collector's five-minute delay.
ACTIVE_WINDOW = int(os.environ.get("CLOCKFACE_ACTIVE", "600"))
# Minimum hold time before switching away from a session that is still running. Without
# it, the icon would jump back and forth when switching between tools.
MIN_HOLD = int(os.environ.get("CLOCKFACE_HOLD", "600"))
# How long a silent session keeps its icon before falling back. Short pauses should not
# end the session.
IDLE_AFTER = int(os.environ.get("CLOCKFACE_IDLE", "1800"))
# Safety cutoff if Tautulli sends no stop event: a stream is never treated as running
# longer than this.
PLEX_MAX = int(os.environ.get("CLOCKFACE_PLEX_MAX", "14400"))
# Sources that take priority while active. Starting a movie is deliberate; code typed in
# parallel should not displace the icon.
PRIORITY = tuple(p for p in os.environ.get("CLOCKFACE_PRIORITY", "plex").split(",") if p)


def configured():
    """Return whether explicitly enabled and the default icon is available."""
    return ENABLED and os.path.isfile(os.path.join(ROOT, ICONS[DEFAULT]))


def decide(now, seen, current, since):
    """Choose the icon to display.

    `seen` maps each source to the timestamp of its latest trace, `current` is the
    currently selected icon, and `since` is the timestamp at which it was selected.

    In plain language: a priority source (Plex) wins immediately and keeps its icon
    while active. Otherwise, a running session keeps its icon for at least MIN_HOLD,
    even if something else is used in parallel. Once it goes quiet, another active
    source takes over immediately. There is no value in holding on to a dead session.
    If nothing is active, the icon remains for IDLE_AFTER and then falls back to the
    default.
    """
    active = {k: t for k, t in seen.items() if now - t <= ACTIVE_WINDOW}
    newest = max(active, key=lambda k: active[k]) if active else None

    # Priority sources take over immediately and keep the icon while active. MIN_HOLD
    # and the newest-source rule do not apply to them.
    for key in PRIORITY:
        if key in active:
            return key

    if current in (None, DEFAULT):
        return newest or DEFAULT

    if current in active:
        if newest != current and now - (since or 0) >= MIN_HOLD:
            return newest
        return current

    # The current source has gone quiet.
    if newest:
        return newest
    # Use `since` as an anchor rather than only the latest trace: if a source vanishes
    # from `seen` after a Plex stop or a database error in coding(), the grace
    # period would otherwise expire immediately and the icon would switch after one
    # second.
    if now - max(seen.get(current, 0), since or 0) >= IDLE_AFTER:
        return DEFAULT
    return current


def coding(db=None, scan=200):
    """Return the latest usage per provider from delivery-database usage events."""
    out = {}
    try:
        with sqlite3.connect(f"file:{db or DB}?mode=ro", uri=True, timeout=5) as con:
            rows = con.execute(
                "select envelope_json from core_outbox "
                "where envelope_json like '%usage_event%' order by id desc limit ?", (scan,))
            for (blob,) in rows:
                for rec in json.loads(blob).get("canonicalRecords", []):
                    if not str(rec.get("recordType", "")).startswith("ai.usage_event"):
                        continue
                    provider = (rec.get("data") or {}).get("provider")
                    stamp = rec.get("eventTime")
                    if provider in ICONS and stamp:
                        ts = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                        out[provider] = max(out.get(provider, 0), ts)
    except (sqlite3.Error, OSError, ValueError):
        return {}   # A missing database is no reason to lose the clock face.
    return out


def watching(now, state_file=None):
    """Is the primary user's Plex stream currently playing?"""
    try:
        with open(state_file or STATE_FILE) as fh:
            plex = json.load(fh).get("plex") or {}
    except (OSError, ValueError):
        return None
    if not plex.get("playing"):
        return None
    if WATCHER and plex.get("playing_user") != WATCHER:
        return None   # Stream belonging to another user.
    started = plex.get("playing_at") or 0
    if now - started > PLEX_MAX:
        return None   # Tautulli probably did not send a stop event.
    return now        # An ongoing stream counts as continuously active.


def apply(key):
    """Set the clock face. Keep `activate` disabled so the display does not switch."""
    widget = lametric.widget_id(lametric.CLOCK_PACKAGE)
    lametric.call(f"/device/apps/{lametric.CLOCK_PACKAGE}/widgets/{widget}/actions", "POST",
                  {"id": "clock.clockface",
                   "params": {"type": "custom", "icon": lametric.icon(ICONS[key])},
                   "activate": False})


def poll(state, now=None):
    now = time.time() if now is None else now
    seen = coding()
    plex = watching(now)
    if plex:
        seen["plex"] = plex

    want = decide(now, seen, state.get("current"), state.get("since"))
    if want != state.get("current"):
        apply(want)
        state["current"], state["since"] = want, now
        print(f"[clockface] {state.get('current')}")
    return []   # The clock face is not a notification.

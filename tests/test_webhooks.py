#!/usr/bin/env python3
"""Self-check for the Uptime Kuma, say, and PSN webhook add-ins."""

import context  # noqa: F401  - sets the import path and working directory

import kuma
import psn
import say

# --- Uptime Kuma -----------------------------------------------------------
def beat(status, important=True, name="Plex"):
    return {"heartbeat": {"status": status, "important": important},
            "monitor": {"name": name}}


down = kuma.hook(beat(kuma.DOWN), {}, {})
assert down[0]["frames"][0]["text"] == "Plex down"
assert down[0]["priority"] == "critical"

up = kuma.hook(beat(kuma.UP), {}, {})
assert up[0]["frames"][0]["text"] == "Plex up"
assert up[0].get("priority", "info") == "info"
assert up[0]["frames"][0]["icon"] != down[0]["frames"][0]["icon"]   # green vs red

# Kuma marks only state changes. Without the flag it stays quiet; otherwise a service
# that remains down would notify again on every heartbeat.
assert kuma.hook(beat(kuma.DOWN, important=False), {}, {}) == []

# Intermediate states do not produce a notification.
assert kuma.hook(beat(kuma.PENDING), {}, {}) == []
assert kuma.hook(beat(kuma.MAINTENANCE), {}, {}) == []

# Test messages from the Kuma UI have no heartbeat and must not crash.
assert kuma.hook({"msg": "Testing"}, {}, {}) == []
assert kuma.hook({}, {}, {}) == []

# A monitor without a name gets a placeholder instead of crashing.
assert kuma.hook({"heartbeat": {"status": 0, "important": True}}, {}, {})[0] \
    ["frames"][0]["text"] == "Service down"

# --- say: generic message endpoint -----------------------------------------------
ev = say.hook({"text": "Washing machine is done"}, {}, {})
assert ev[0]["frames"][0]["text"] == "Washing machine is done"
assert ev[0]["priority"] == "info"

# Nothing is displayed without text.
assert say.hook({}, {}, {}) == []
assert say.hook({"text": "   "}, {}, {}) == []

# Priority is preserved; invalid values fall back to info.
assert say.hook({"text": "x", "priority": "critical"}, {}, {})[0]["priority"] == "critical"
assert say.hook({"text": "x", "priority": "CRITICAL"}, {}, {})[0]["cycles"] == 2
assert say.hook({"text": "x", "priority": "nonsense"}, {}, {})[0]["priority"] == "info"

# Icon short names are resolved from assets/.
assert say.resolve("plex").endswith("assets/plex.png")
assert say.resolve("clock_default").endswith("assets/clock_default.gif")   # GIF also supported
# A typo must not discard the notification.
assert say.resolve("doesnotexist") == say.ICON
assert say.resolve(None) == say.ICON
assert say.resolve("/does/not/exist.png") == say.ICON
assert say.resolve("assets/plex.png") == "assets/plex.png"

# --- PSN ------------------------------------------------------------------
# Use a local table instead of the private assets/user_mapping.json: it is not
# versioned, is absent in CI, and a new mapping must not change this test.
psn.NAMES = {"somepsnid_": "Alex", "somepsnid": "Alex", "otherpsnid": "Sam"}
st = {}
ev = psn.hook({"event": "game_start", "user_id": "Alex",
               "metadata": {"game_name": "Elden Ring"}}, {}, st)
assert ev[0]["frames"][0]["text"] == "Alex: Elden Ring"
assert st["playing"] == "Elden Ring"

# PSN logins are mapped to display names case-insensitively.
for login in ("somepsnid_", "somepsnid", "SomePsnId_"):
    assert psn.hook({"event": "game_start", "user_id": login,
                     "metadata": {"game_name": "Elden Ring"}}, {}, {}) \
        [0]["frames"][0]["text"] == "Alex: Elden Ring", login
assert psn.hook({"event": "game_start", "user_id": "otherpsnid",
                 "metadata": {"game_name": "Elden Ring"}}, {}, {}) \
    [0]["frames"][0]["text"] == "Sam: Elden Ring"
# Unknown logins remain visible instead of disappearing.
assert psn.hook({"event": "game_start", "user_id": "unknownuser",
                 "metadata": {"game_name": "Tetris"}}, {}, {}) \
    [0]["frames"][0]["text"] == "unknownuser: Tetris"

# The clock has no trademark glyphs; they would render as empty boxes.
for raw, clean in (("Battlefield\u2122 6", "Battlefield 6"),
                   ("Tetris\u00ae", "Tetris"),
                   ("Assassin's Creed\u2122 Black Flag", "Assassin's Creed Black Flag")):
    assert psn.hook({"event": "game_start", "metadata": {"game_name": raw}}, {}, {}) \
        [0]["frames"][0]["text"] == clean, raw

# Without a user, display only the game name.
assert psn.hook({"event": "game_start", "metadata": {"game_name": "Bloodborne"}}, {}, {}) \
    [0]["frames"][0]["text"] == "Bloodborne"

# Without a game name, there is nothing to report.
assert psn.hook({"event": "game_start", "user_id": "Alex"}, {}, {}) == []

# Session end produces no notification but clears the widget state.
assert psn.hook({"event": "session_end"}, {}, st) == []
assert "playing" not in st

warn = psn.hook({"event": "npsso_warning"}, {}, {})
assert warn[0]["priority"] == "warning"
fail = psn.hook({"event": "error"}, {}, {})
assert fail[0]["priority"] == "critical"
assert warn[0]["frames"][0]["icon"] != fail[0]["frames"][0]["icon"]

# Heartbeat, online, and offline events stay quiet. Discord handles those.
for quiet in ("heartbeat", "online", "offline", "game_end", ""):
    assert psn.hook({"event": quiet}, {}, {}) == [], quiet

# The widget shows only an active game.
assert psn.widget({}) == []
assert psn.widget({"playing": "Elden Ring"})[0]["text"] == "Elden Ring"

print("ok")

"""PlayStation webhook from psn-insights.

psn-insights has a backend notification interface with a `LaMetricWebhook` that
sends its events here:

    LAMETRIC_HUB_URL=http://<hub-host>:8099/hook/psn

Only useful events are reported: a game starting and NPSSO token problems. Session
ends, heartbeats, and state changes remain quiet; Discord handles those.
"""
import os
import re

import lametric
import names

NAME = "psn"
ICON = os.environ.get("PSN_ICON", "assets/psn.png")
ICON_WARN = os.environ.get("PSN_ICON_WARN", "assets/psn_warn.png")
ICON_FAIL = os.environ.get("PSN_ICON_FAIL", "assets/psn_fail.png")

NAMES = names.load("psn")
# The clock has no glyphs for these characters. They would render as empty boxes and
# only consume space.
MARKS = re.compile(r"[™®©]")


def hook(payload, headers, state):
    event = str(payload.get("event") or payload.get("event_type") or "").lower()
    meta = payload.get("metadata") or {}
    user = names.person(str(payload.get("user") or payload.get("user_id") or "").strip(), NAMES)

    if event in ("game_start", "start"):
        game = MARKS.sub("", str(meta.get("game_name") or payload.get("game") or "")).strip()
        if not game:
            return []
        state["playing"], state["user"] = game, user
        return [{"frames": [{"icon": lametric.icon(ICON),
                             "text": f"{user}: {game}" if user else game}]}]

    if event in ("game_end", "session_end"):
        for key in ("playing", "user"):
            state.pop(key, None)
        return []

    if event in ("npsso_warning", "warning"):
        return [{"frames": [{"icon": lametric.icon(ICON_WARN), "text": "PSN token expires soon"}],
                 "priority": "warning"}]

    if event in ("error", "npsso_error"):
        return [{"frames": [{"icon": lametric.icon(ICON_FAIL), "text": "PSN token is invalid"}],
                 "priority": "critical", "cycles": 2}]

    return []


def widget(state):
    if not state.get("playing"):
        return []
    return [{"icon": lametric.icon(ICON), "text": str(state["playing"])}]

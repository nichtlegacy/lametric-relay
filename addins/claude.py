"""Claude quota from the private ai-usage-insights delivery database.

**Environment-specific.** ai-usage-insights is not publicly available. It tracks
Codex, Claude and OpenCode usage. Without `AIUSAGE_DB`, the relay does not load this
add-in.

The project stores snapshots as `ai.quota_snapshot` records in its outbox. This add-in
reads them without modifying the database.
"""
import json, os, sqlite3, time
from datetime import datetime

import _shared

NAME = "claude"
INTERVAL = int(os.environ.get("CLAUDE_INTERVAL", "120"))
DB = os.path.expanduser(os.environ.get("AIUSAGE_DB", ""))
ICON = os.environ.get("CLAUDE_ICON", "assets/claude.png")
ICON_LOW = os.environ.get("CLAUDE_ICON_LOW", "assets/claude.png")
# ai-usage-insights collects data every five minutes. If the newest snapshot is
# significantly older, show nothing rather than stale data.
MAX_AGE = int(os.environ.get("CLAUDE_MAX_AGE", "1800"))


def configured():
    """Return whether an accessible ai-usage-insights database is configured."""
    return bool(DB) and os.path.isfile(DB)


def age(observed_at, now=None):
    """Return the age of a snapshot in seconds."""
    ts = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp()
    return (now if now is not None else time.time()) - ts


def age_text(seconds):
    """Use seconds below two minutes; ``0 min old`` would be diagnostically useless."""
    return f"{int(seconds)} s" if seconds < 120 else f"{int(seconds / 60)} min"


def remaining(db=None, scan=400, max_age=None, now=None):
    with sqlite3.connect(f"file:{db or DB}?mode=ro&immutable=1", uri=True, timeout=5) as con:
        best = {}
        for (blob,) in con.execute(
                "select envelope_json from core_outbox "
                "where envelope_json like '%quota_snapshot%' order by id desc limit ?", (scan,)):
            for rec in json.loads(blob).get("canonicalRecords", []):
                d = rec.get("data") or {}
                if d.get("provider") != "claude" or d.get("windowKey") not in _shared.WINDOWS:
                    continue
                seen = d.get("observedAt")
                if not seen:
                    continue   # Account intervals do not include observedAt.
                if seen >= best.get(d["windowKey"], ("",))[0]:
                    best[d["windowKey"]] = (seen, round(d["remaining"]), d.get("resetAt"))
            if len(best) == len(_shared.WINDOWS):
                break
    if not best:
        raise RuntimeError("no Claude quota snapshots found in the delivery database")

    limit = MAX_AGE if max_age is None else max_age
    oldest = max(age(v[0], now) for v in best.values())
    if limit and oldest > limit:
        raise RuntimeError(
            f"Claude quota data is {age_text(oldest)} old - is ai-usage-insights still running?")
    return ({k: v[1] for k, v in best.items()},
            {k: v[2] for k, v in best.items() if v[2]})


def poll(state):
    pct, resets = remaining()
    return _shared.events(ICON, ICON_LOW, pct, state, resets)


def widget(state):
    return _shared.widget(ICON, state.get("last"))

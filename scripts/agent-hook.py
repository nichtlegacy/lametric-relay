#!/usr/bin/env python3
"""Hook for Claude Code and Codex: report when a long-running turn finishes.

There is deliberately **only** a completion hook. A second hook on submission would
add work for every input and is unnecessary: both tools record the start of the turn,
so the elapsed time can be measured from there.

    agent-hook.py done --tool claude
    agent-hook.py done --tool codex

Only runs longer than MIN_SECONDS are reported. Those are long enough to step away
from the desk. Without this threshold, every response would light up the clock.

The script must never interfere with the session: it always exits with 0, uses short
timeouts, and swallows every error.
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

HUB = os.environ.get("LAMETRIC_HUB", "http://127.0.0.1:8099")
MIN_SECONDS = int(os.environ.get("AGENT_MIN_SECONDS", "120"))
CLAUDE_PROJECTS = os.path.expanduser(os.environ.get("CLAUDE_PROJECTS", "~/.claude/projects"))
CODEX_SESSIONS = os.path.expanduser(os.environ.get("CODEX_SESSIONS", "~/.codex/sessions"))
TAIL_BYTES = 512 * 1024
ICONS = {"claude": "claude", "codex": "codex"}
LABELS = {"claude": "Claude", "codex": "Codex"}


def stamp(text):
    """Convert an ISO-8601 timestamp with Z to Unix time."""
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()


def tail(path, size=TAIL_BYTES):
    """Read the last lines of a file without loading large transcripts in full."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        total = fh.tell()
        fh.seek(max(0, total - size))
        lines = fh.read().decode("utf8", "replace").splitlines()
    # Only a seek into the file can leave the first line truncated.
    return lines[1:] if total > size else lines


def newest(pattern):
    files = glob.glob(pattern)
    return max(files, key=os.path.getmtime) if files else None


def turn_start(payload, tool):
    """Find the start of the latest turn in the tool's transcript."""
    if tool == "claude":
        path = payload.get("transcript_path") or newest(
            os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl"))
        if not path or not os.path.isfile(path):
            return None
        for line in reversed(tail(path)):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            # Tool results also have type "user"; only actual inputs carry
            # promptSource.
            if d.get("type") == "user" and d.get("promptSource") and d.get("timestamp"):
                return stamp(d["timestamp"])
        return None

    path = next((v for k, v in payload.items()
                 if isinstance(v, str) and v.endswith(".jsonl") and os.path.isfile(v)), None)
    path = path or newest(os.path.join(CODEX_SESSIONS, "*", "*", "*", "*.jsonl"))
    if not path:
        return None
    fallback = None
    for line in reversed(tail(path)):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        when = d.get("timestamp")
        if not when:
            continue
        if (d.get("payload") or {}).get("type") == "task_started":
            return stamp(when)
        if fallback is None and d.get("type") == "turn_context":
            fallback = stamp(when)
    return fallback


def human(seconds):
    if seconds < 90:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}"


def say(text, icon):
    body = json.dumps({"text": text, "icon": icon}).encode()
    req = urllib.request.Request(f"{HUB}/hook/say", data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=2).read()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("event", choices=["done"])
    p.add_argument("--tool", default="claude", choices=sorted(ICONS))
    p.add_argument("--label", default=None)
    p.add_argument("--min-seconds", type=int, default=MIN_SECONDS)
    a = p.parse_args()

    raw = "" if sys.stdin.isatty() else sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    started = turn_start(payload, a.tool)
    if started is None:
        return                      # no start found; do not report anything
    elapsed = time.time() - started
    if elapsed < a.min_seconds:
        return                      # too short to be useful
    say(f"{a.label or LABELS[a.tool]} {human(elapsed)}", ICONS[a.tool])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                        # a hook must never hold up a session
    sys.exit(0)

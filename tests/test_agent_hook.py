#!/usr/bin/env python3
"""Self-check for the agent hook.

The hook must never interfere: it runs after every response and must neither slow
down nor abort the session.
"""

import context  # noqa: F401  - sets the import path and working directory

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(context.ROOT, "scripts", "agent-hook.py")
spec = importlib.util.spec_from_file_location("agent_hook", HOOK)
ah = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ah)

# --- Time formatting -------------------------------------------------------
assert ah.human(5) == "5s"
assert ah.human(89) == "89s"
assert ah.human(90) == "1m"
assert ah.human(600) == "10m"
assert ah.human(3600) == "1h00"
assert ah.human(4500) == "1h15"

assert ah.stamp("2026-09-06T03:24:08.504Z") == 1788665048.504

# --- Turn start from the transcript ----------------------------------------
tmp = tempfile.mkdtemp()


def write(name, rows):
    path = os.path.join(tmp, name)
    with open(path, "w") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


# Claude: Tool results also have type "user". Only actual inputs carry promptSource;
# without this distinction, the measured duration would be far too short.
claude = write("transcript.jsonl", [
    {"type": "user", "promptSource": "cli", "timestamp": "2026-09-06T03:00:00Z"},
    {"type": "assistant", "timestamp": "2026-09-06T03:00:10Z"},
    {"type": "user", "timestamp": "2026-09-06T03:05:00Z"},          # tool result
    {"type": "assistant", "timestamp": "2026-09-06T03:05:10Z"},
])
assert ah.turn_start({"transcript_path": claude}, "claude") == ah.stamp("2026-09-06T03:00:00Z")

# Tool results only, with no actual input: nothing measurable.
only_tools = write("tools.jsonl", [{"type": "user", "timestamp": "2026-09-06T03:05:00Z"}])
assert ah.turn_start({"transcript_path": only_tools}, "claude") is None

# Small files must not be truncated; the first line here is complete, not a fragment.
assert len(ah.tail(claude)) == 4
big = write("big.jsonl", [{"type": "user", "promptSource": "cli",
                           "timestamp": "2026-09-06T03:00:00Z", "fill": "x" * 200}] * 60)
assert len(ah.tail(big, size=1000)) < 60      # large file: the beginning is truncated

# Malformed lines must not abort the search.
broken = write("broken.jsonl", [{"type": "assistant"}])
open(broken, "a").write("{not json\n")
open(broken, "a").write(json.dumps(
    {"type": "user", "promptSource": "cli", "timestamp": "2026-09-06T02:00:00Z"}) + "\n")
assert ah.turn_start({"transcript_path": broken}, "claude") == ah.stamp("2026-09-06T02:00:00Z")

# Codex: task_started marks the turn start; turn_context is the fallback.
codex = write("rollout.jsonl", [
    {"type": "turn_context", "timestamp": "2026-09-06T01:00:00Z"},
    {"type": "event_msg", "payload": {"type": "task_started"},
     "timestamp": "2026-09-06T01:00:01Z"},
    {"type": "event_msg", "payload": {"type": "token_count"},
     "timestamp": "2026-09-06T01:30:00Z"},
])
assert ah.turn_start({"rollout": codex}, "codex") == ah.stamp("2026-09-06T01:00:01Z")

only_ctx = write("ctx.jsonl", [{"type": "turn_context", "timestamp": "2026-09-06T01:00:00Z"}])
assert ah.turn_start({"rollout": only_ctx}, "codex") == ah.stamp("2026-09-06T01:00:00Z")

# An invalid payload path must not crash or invent a result. With no fallback file,
# the result is None; the previous "or True" assertion could never fail.
ah.CLAUDE_PROJECTS = os.path.join(tmp, "no_such_directory")
assert ah.turn_start({"transcript_path": "/does/not/exist.jsonl"}, "claude") is None
ah.CLAUDE_PROJECTS = tmp

# --- The hook must never exit with an error --------------------------------
# The hub intentionally points nowhere here. The return code must still be 0, or an
# agent session could be interrupted by an unreachable service.
def run(payload, tool="claude", extra=None):
    env = dict(os.environ, LAMETRIC_HUB="http://127.0.0.1:1",
               CLAUDE_PROJECTS=tmp, CODEX_SESSIONS=tmp, **(extra or {}))
    return subprocess.run([sys.executable, HOOK, "done", "--tool", tool],
                          input=json.dumps(payload) if payload is not None else "",
                          text=True, capture_output=True, env=env, timeout=20)


assert run({"transcript_path": claude}).returncode == 0        # well past the threshold
assert run({}).returncode == 0
assert run({"transcript_path": "/does/not/exist.jsonl"}).returncode == 0
assert run(None).returncode == 0

for bad in ("not json", "[]", "null", ""):
    r = subprocess.run([sys.executable, HOOK, "done"], input=bad, text=True,
                       capture_output=True,
                       env=dict(os.environ, LAMETRIC_HUB="http://127.0.0.1:1",
                                CLAUDE_PROJECTS=tmp), timeout=20)
    assert r.returncode == 0, bad

# A hook must not write to stdout: during UserPromptSubmit, that output would end up
# in the agent's context.
assert run({"transcript_path": claude}).stdout == ""

print("ok")

#!/usr/bin/env python3
"""Self-check for clock-face hysteresis.

The interesting part is not setting the icon, but deciding when *not* to switch:
switching too often makes the clock restless, while switching too slowly leaves a
dead session displayed.
"""

import context  # noqa: F401  - sets the import path and working directory

import json
import os
import tempfile
from datetime import datetime

import clockface as cf

T = 1_000_000.0
D = cf.DEFAULT

# From an idle state, the active source takes over immediately.
assert cf.decide(T, {}, None, None) == D
assert cf.decide(T, {"claude": T - 10}, D, T - 5000) == "claude"

# A running session keeps its icon even when something else is used in parallel.
assert cf.decide(T, {"claude": T - 10, "codex": T - 5}, "claude", T - 60) == "claude"

# Switching is allowed only after the minimum hold time.
assert cf.decide(T, {"claude": T - 10, "codex": T - 5}, "claude",
                 T - cf.MIN_HOLD - 1) == "codex"

# Switch only to the newest source, not just any source.
assert cf.decide(T, {"claude": T - 5, "codex": T - 200}, "claude",
                 T - cf.MIN_HOLD - 1) == "claude"

# When the running source goes quiet, an active source takes over immediately.
assert cf.decide(T, {"claude": T - cf.ACTIVE_WINDOW - 1, "codex": T - 5},
                 "claude", T - 5) == "codex"

# When everything goes quiet, the icon initially remains: short pauses do not end a
# session.
quiet = {"claude": T - cf.ACTIVE_WINDOW - 1}
assert cf.decide(T, quiet, "claude", T - 5) == "claude"

# After the idle period, it falls back to the default.
assert cf.decide(T, {"claude": T - cf.IDLE_AFTER - 1}, "claude", T - 9999) == D

# With no traces at all, it also falls back to the default.
assert cf.decide(T, {}, "claude", T - 9999) == D

# --- Plex takes priority while the stream is active ------------------------
# Starting a movie is deliberate and takes effect immediately, without waiting for
# the minimum hold time.
assert cf.decide(T, {"plex": T, "claude": T - 5}, "claude", T - 1) == "plex"
assert cf.decide(T, {"plex": T}, D, T - 9999) == "plex"

# While the stream is active, code typed in parallel does not displace it.
assert cf.decide(T, {"plex": T, "claude": T}, "plex", T - cf.MIN_HOLD - 1) == "plex"

# When the stream ends, the normal rules apply again: an active source takes over
# immediately.
assert cf.decide(T, {"plex": T - cf.ACTIVE_WINDOW - 1, "claude": T - 5},
                 "plex", T - 5) == "claude"

# When the stream ends with nothing else running, the icon remains for the idle period.
quiet_plex = {"plex": T - cf.ACTIVE_WINDOW - 1}
assert cf.decide(T, quiet_plex, "plex", T - 5) == "plex"
assert cf.decide(T, {"plex": T - cf.IDLE_AFTER - 1}, "plex", T - 9999) == D

# --- Plex detection from state.json -----------------------------------------
def state_file(plex):
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    with open(path, "w") as fh:
        json.dump({"plex": plex}, fh)
    return path


assert cf.watching(T, state_file({})) is None
assert cf.watching(T, state_file({"playing": "Dune"})) is None           # without a start time

# Default: no user is configured, so every active stream counts.
saved = cf.WATCHER
cf.WATCHER = ""
assert cf.watching(T, state_file({"playing": "Dune", "playing_user": "Kim",
                                  "playing_at": T - 60})) == T

# With a configured user, only that user's stream counts. Otherwise any viewer could
# put the Plex logo on the clock face.
cf.WATCHER = "Alex"
assert cf.watching(T, state_file({"playing": "Dune", "playing_user": "Kim",
                                  "playing_at": T - 60})) is None
assert cf.watching(T, state_file({"playing": "Dune", "playing_user": "Alex",
                                  "playing_at": T - 60})) == T
# Safety cutoff: without a stop event, a stream would otherwise run forever.
assert cf.watching(T, state_file({"playing": "Dune", "playing_user": "Alex",
                                  "playing_at": T - cf.PLEX_MAX - 1})) is None
assert cf.watching(T, "/does/not/exist.json") is None
cf.WATCHER = saved

# --- coding(): the path actually used in production ------------------------
# Previously, only the error path was tested. That passes every regression,
# because the broad except also returns {}.
def usage_db(rows):
    import sqlite3
    path = os.path.join(tempfile.mkdtemp(), "outbox.sqlite3")
    con = sqlite3.connect(path)
    con.execute("create table core_outbox (id integer primary key, envelope_json text)")
    for provider, when in rows:
        envelope = {"canonicalRecords": [
            {"recordType": "ai.usage_event.v1", "eventTime": when,
             "data": {"provider": provider}}]}
        con.execute("insert into core_outbox (envelope_json) values (?)",
                    (json.dumps(envelope),))
    con.commit()
    con.close()
    return path


# The order is intentionally reversed: the query runs in descending id order, so the
# newest timestamp appears *last* in the loop. This exposes implementations that take
# the first rather than the newest match for each provider.
db = usage_db([("claude", "2026-09-06T12:00:00Z"),
               ("codex", "2026-09-06T11:00:00Z"),
               ("claude", "2026-09-06T10:00:00Z")])
seen = cf.coding(db=db)
assert set(seen) == {"claude", "codex"}, seen
# The newest timestamp per provider, not the first one found.
assert seen["claude"] > seen["codex"], seen
assert seen["claude"] == datetime.fromisoformat("2026-09-06T12:00:00+00:00").timestamp()

# Unknown providers are ignored because there is no icon for them.
assert cf.coding(db=usage_db([("opencode", "2026-09-06T10:00:00Z")])) == {}

# A missing database must not make the clock face disappear.
assert cf.coding(db="/does/not/exist.sqlite3") == {}

# --- poll(): update the clock only when the icon actually changes -----------
# Otherwise, the same icon would be set every 60 seconds.
calls = []
_apply, _coding, _watching = cf.apply, cf.coding, cf.watching
try:
    cf.apply = lambda key: calls.append(key)
    cf.coding = lambda *a, **k: {"claude": T}
    cf.watching = lambda *a, **k: None
    st = {}
    cf.poll(st, now=T)
    cf.poll(st, now=T + 30)
    assert calls == ["claude"], calls          # second poll remains silent
    assert st["current"] == "claude" and st["since"] == T

    # When the source changes, set the icon again.
    cf.coding = lambda *a, **k: {"codex": T + cf.MIN_HOLD + 100}
    cf.poll(st, now=T + cf.MIN_HOLD + 100)
    assert calls == ["claude", "codex"], calls
finally:
    cf.apply, cf.coding, cf.watching = _apply, _coding, _watching

print("ok")

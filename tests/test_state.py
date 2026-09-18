#!/usr/bin/env python3
"""Self-check the state file and failure containment.

The state is written by both the main loop *and* webhook threads. Without a lock and
a unique temporary filename, two writers can overwrite each other: `os.replace`
may move fragments into state.json or fail, and `State.__init__` then silently starts
empty, causing every threshold to fire again.
"""

import context  # noqa: F401 - sets the import path and working directory

import datetime
import json
import os
import tempfile
import threading

import hub


class Counter:
    """Add-in that touches its section on every run."""

    @staticmethod
    def poll(own):
        own["n"] = own.get("n", 0) + 1
        return []


class Boom:
    @staticmethod
    def poll(own):
        raise RuntimeError("broken")


class Unsavable:
    @staticmethod
    def poll(own):
        own["when"] = datetime.datetime.now()   # not JSON serializable
        return []


def fresh():
    return hub.State(os.path.join(tempfile.mkdtemp(), "state.json"))


# --- Concurrent writers ----------------------------------------------------
state = fresh()
errors, invalid = [], []


def hammer(i):
    for _ in range(60):
        try:
            hub.run_addin(f"w{i}", Counter, state)
        except Exception as exc:
            errors.append(type(exc).__name__)
        try:
            json.load(open(state.path))
        except FileNotFoundError:
            pass
        except ValueError:
            invalid.append(1)


threads = [threading.Thread(target=hammer, args=(i,)) for i in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()

assert not errors, f"Exceptions during concurrent saves: {set(errors)}"
assert not invalid, f"Read invalid state.json {len(invalid)} times"
saved = json.load(open(state.path))
assert len(saved) == 6, saved                      # no section was lost
assert all(v["n"] == 60 for v in saved.values()), saved
leftovers = [f for f in os.listdir(os.path.dirname(state.path)) if f.endswith(".tmp")]
assert not leftovers, leftovers

# --- A failing add-in must not take down the others ------------------------
# This is a core README promise. Previously it was tested only during loading,
# not while running.
state = fresh()
before = dict(hub.HEALTH["errors"])
assert hub.run_addin("boom", Boom, state) == 0
assert hub.HEALTH["errors"].get("boom", 0) == before.get("boom", 0) + 1
assert hub.run_addin("counter", Counter, state) == 0        # continues afterwards
assert json.load(open(state.path))["counter"] == {"n": 1}

# --- A non-serializable value must not terminate the service ----------------
# A datetime in state is the most likely mistake by an add-in author.
state = fresh()
assert hub.run_addin("unsavable", Unsavable, state) == 0    # no failure leaks out
assert hub.HEALTH["errors"].get("unsavable", 0) >= 1
assert not [f for f in os.listdir(os.path.dirname(state.path)) if f.endswith(".tmp")]

# --- A missing or broken file is empty state, not a crash -------------------
missing = hub.State(os.path.join(tempfile.mkdtemp(), "missing.json"))
assert missing == {}
broken = os.path.join(tempfile.mkdtemp(), "broken.json")
open(broken, "w").write("not json")
assert hub.State(broken) == {}

print("ok")

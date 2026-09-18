#!/usr/bin/env python3
"""Self-check for edge detection, which prevents repeated quota notifications."""

import context  # noqa: F401  - sets the import path and working directory

import quota as q

# The first run has no previous state and must remain silent.
assert q.crossed(None, 5) is None
assert q.reset(None, 100) is False

# Report the threshold crossing once, then remain silent.
assert q.crossed(60, 24) == 25
assert q.crossed(24, 22) is None
assert q.crossed(26, 25) == 25          # exact threshold
assert q.crossed(25, 24) is None        # already reported

# The most urgent threshold wins when one step crosses several.
assert q.crossed(80, 5) == 10

# Upward movement is never a warning.
assert q.crossed(20, 60) is None

# Only a real jump back to full counts as a reset.
assert q.reset(8, 100) is True
assert q.reset(8, 60) is False          # rolling window recovers slowly
assert q.reset(96, 100) is False        # already full
print("ok")

#!/usr/bin/env python3
"""Shared logic for quota add-ins: decide when a change is worth reporting.

The key is edge detection. A poller sees the same state on every run; without
comparing it with the previous run, it would send the same warning every minute.
"""

LEVELS = (50, 25, 10)   # remaining quota in percent
RESET_TO = 95           # at or above this value, a window is reset


def crossed(prev, now, levels=LEVELS):
    """Return the lowest threshold just crossed, or None.

    Only downward transitions count: a drop from 60 to 24 reports 25, while a drop
    from 24 to 22 reports nothing. If one step crosses several thresholds, return the
    lowest because it is the more urgent notification.
    """
    if prev is None:
        return None
    hit = [l for l in levels if prev > l >= now]
    return min(hit) if hit else None


def reset(prev, now, to=RESET_TO):
    """Return True when a quota window has just recovered.

    A reset is a jump up to (nearly) full. Small recoveries caused by requests
    expiring from the rolling window are not worth reporting.
    """
    return prev is not None and now >= to > prev

"""Shared helpers for quota add-ins."""
import lametric, quota

WINDOWS = ("5h", "7d")


def events(icon, icon_low, now, state, resets=None, windows=WINDOWS):
    """Emit notifications based on changes since the previous poll.

    `now` maps each window to its remaining percentage, while `resets` optionally
    contains provider-reported reset timestamps for each window. `windows` selects
    which windows to inspect. A source with different windows such as `1h` or `month` passes
    them here; otherwise it would silently produce no notifications. State is
    persisted at the end so the same threshold does not fire again.

    A reset is detected from the moving reset timestamp, not from a jump in the
    value: that timestamp comes from the provider rather than being inferred. The
    quota must also have increased; with multiple accounts, a timestamp can move
    otherwise without the aggregate recovering.
    """
    prev, out = state.get("last") or {}, []
    prev_resets = state.get("resets") or {}
    for win in windows:
        if win not in now:
            continue
        before, after = prev.get(win), now[win]
        rolled = rolled_over(prev_resets.get(win), (resets or {}).get(win), before, after)
        level = None if rolled else quota.crossed(before, after)
        if rolled:
            out.append({"frames": [bar(icon, win, after)], "priority": "info"})
        elif level is not None:
            out.append({
                "frames": [bar(icon_low if level <= 10 else icon, win, after)],
                "priority": "critical" if level <= 10 else "warning",
                "cycles": 2,
            })
        elif not (resets or {}).get(win) and quota.reset(before, after):
            # No timestamp is available for this window, so the jump is the only
            # indication left. Add-ins pass an empty dict rather than None when the
            # provider does not include a reset time.
            out.append({"frames": [bar(icon, win, after)], "priority": "info"})
    state["last"] = now
    if resets:
        state["resets"] = resets
    return out


def bar(icon, window, pct):
    """Build a frame with both a bar **and** custom text.

    The clock renders `text` and `goalData` together when `unit` is empty. This is
    the only way to put the window label first and keep it aligned ("5H 73%") while
    retaining the bar; using `unit` would force the clock to place it after the number.
    """
    return {"icon": lametric.icon(icon),
            "text": f"{window.upper()} {pct}%",
            "goalData": {"start": 0, "current": pct, "end": 100, "unit": ""}}


def rolled_over(before_ts, now_ts, before_pct, after_pct):
    """Did the provider reset the window?"""
    if not before_ts or not now_ts or now_ts <= before_ts:
        return False
    return before_pct is not None and after_pct > before_pct


def widget(icon, last, windows=WINDOWS):
    """Build frames for the glance widget: one bar per window."""
    return [bar(icon, w, last[w]) for w in windows if w in (last or {})]

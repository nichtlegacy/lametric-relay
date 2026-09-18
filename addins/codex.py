"""Codex quota from Codex-LB.

**Environment-specific.** Codex-LB is a self-hosted load balancer in front of multiple
Codex accounts. Without it, this add-in has no data source. Without `CODEX_LB_URL` and
`CODEX_LB_PASSWORD`, the relay does not load it.

It is still useful as a template for custom sources: the entire contract is
`poll(state)` plus `configured()`, and the notification logic lives in
`_shared.events()`. For OpenCode, cliproxyapi, or anything else, a neighboring file
that returns the remaining percentage for each window is enough.

The data source is the `/api/accounts` API rather than session logs. With multiple
accounts, quotas are weighted by credits rather than by averaging percentages:
otherwise, one large empty account and one small full account would produce 50%
instead of 10%.
"""
import http.cookiejar, json, os, urllib.request
import _shared

NAME = "codex"
INTERVAL = int(os.environ.get("CODEX_INTERVAL", "120"))
LB_URL = os.environ.get("CODEX_LB_URL", "")
LB_PASSWORD = os.environ.get("CODEX_LB_PASSWORD", "")
ICON = os.environ.get("CODEX_ICON", "assets/codex.png")
ICON_LOW = os.environ.get("CODEX_ICON_LOW", "assets/codex_low.png")


def configured():
    """Return whether this add-in has access to Codex-LB."""
    return bool(LB_URL and LB_PASSWORD)


def accounts(url=None, password=None):
    url = (url or LB_URL).rstrip("/")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    login = urllib.request.Request(
        f"{url}/api/dashboard-auth/password/login",
        data=json.dumps({"password": password if password is not None else LB_PASSWORD}).encode(),
        headers={"Content-Type": "application/json"})
    with opener.open(login, timeout=15) as r:
        if not json.loads(r.read()).get("authenticated"):
            raise RuntimeError("Codex-LB rejected the login")
    with opener.open(f"{url}/api/accounts", timeout=15) as r:
        return json.loads(r.read())["accounts"]


def remaining(accs):
    """Return remaining quota and reset timestamp for each window.

    Quotas are weighted by credits across active accounts. The **earliest** upcoming
    reset is used as the reset marker. Once the first account resets, the aggregate
    rises, and that is the moment we want to show.
    """
    act = [a for a in accs if a.get("status") == "active"]
    if not act:
        raise RuntimeError("no active Codex account")
    out, resets = {}, {}
    for win, suffix, pct_key in (("5h", "Primary", "primaryRemainingPercent"),
                                 ("7d", "Secondary", "secondaryRemainingPercent")):
        cap = sum(a.get(f"capacityCredits{suffix}") or 0 for a in act)
        rem = sum(a.get(f"remainingCredits{suffix}") or 0 for a in act)
        if cap:
            out[win] = round(rem / cap * 100)
        else:
            vals = [a["usage"][pct_key] for a in act if a["usage"].get(pct_key) is not None]
            if vals:
                out[win] = round(sum(vals) / len(vals))
        stamps = [a.get(f"resetAt{suffix}") for a in act if a.get(f"resetAt{suffix}")]
        if stamps:
            resets[win] = min(stamps)
    return out, resets


def poll(state):
    pct, resets = remaining(accounts())
    return _shared.events(ICON, ICON_LOW, pct, state, resets)


def widget(state):
    return _shared.widget(ICON, state.get("last"))

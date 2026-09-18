#!/usr/bin/env python3
"""Self-check the hub: loading, state, edge detection, and webhook routing."""

import context  # noqa: F401 - sets the import path and working directory

import json, os, tempfile, urllib.request

import hub
import lametric

os.environ.setdefault("LAMETRIC_KEY", "test")

# --- Add-in loading --------------------------------------------------------
def quota_db(observed_at, path=None):
    import sqlite3
    path = path or os.path.join(tempfile.mkdtemp(), "outbox.sqlite3")
    con = sqlite3.connect(path)
    con.execute("create table core_outbox (id integer primary key, envelope_json text)")
    envelope = {"canonicalRecords": [
        {"recordType": "ai.quota_snapshot",
         "data": {"provider": "claude", "windowKey": w, "remaining": r,
                  "resetAt": reset, "observedAt": observed_at}}
        for w, r, reset in (("5h", 93.0, "2026-09-06T07:00:00Z"),
                            ("7d", 81.0, "2026-09-09T20:00:00Z"))]}
    con.execute("insert into core_outbox (envelope_json) values (?)", (json.dumps(envelope),))
    con.commit()
    con.close()
    return path


# Without credentials, sources that require them are absent. The hub skips them
# entirely so they do not fail every minute on the same missing setting and users
# do not have to configure every source just to use two of them.
for var in ("CODEX_LB_URL", "CODEX_LB_PASSWORD", "AIUSAGE_DB", "CLOCKFACE"):
    os.environ.pop(var, None)
bare = hub.load()
assert {"plex", "forgejo", "kuma", "say", "psn"} <= set(bare), sorted(bare)
# codex and claude require credentials. clockface irreversibly overwrites the clock
# face, so it stays disabled until someone explicitly enables it.
for name in ("codex", "claude", "clockface"):
    assert name not in bare, sorted(bare)

# With configuration, they are loaded as well.
os.environ["CODEX_LB_URL"] = "http://127.0.0.1:1"
os.environ["CODEX_LB_PASSWORD"] = "unused"
os.environ["AIUSAGE_DB"] = quota_db("2026-09-05T20:00:00Z")
os.environ["CLOCKFACE"] = "1"
addins = hub.load()
assert {"codex", "claude", "plex", "forgejo", "clockface", "kuma", "say", "psn"} <= set(addins), \
    sorted(addins)
assert hasattr(addins["forgejo"], "hook") and not hasattr(addins["forgejo"], "poll")

# A broken add-in must not take down the others.
d = tempfile.mkdtemp()
open(os.path.join(d, "broken.py"), "w").write("import missing_module\n")
open(os.path.join(d, "healthy.py"), "w").write("NAME='healthy'\n")
assert set(hub.load(d)) == {"healthy"}

# --- State survives restarts -----------------------------------------------
p = os.path.join(d, "state.json")
s = hub.State(p); s.of("x")["a"] = 1; s.save()
assert hub.State(p).of("x") == {"a": 1}
assert hub.State(os.path.join(d, "missing.json")) == {}   # missing file is empty

# --- Edge detection in the shared module ----------------------------------
import _shared

st = {}
assert _shared.events("assets/codex.png", "assets/codex_low.png",
                      {"5h": 60, "7d": 80}, st) == []      # first run is silent
assert st["last"] == {"5h": 60, "7d": 80}

ev = _shared.events("assets/codex.png", "assets/codex_low.png",
                    {"5h": 24, "7d": 80}, st)
assert len(ev) == 1 and ev[0]["priority"] == "warning"
# Text and progress bar together: the clock renders both only with an empty `unit`,
# and only then does the window label appear first.
frame = ev[0]["frames"][0]
assert frame["text"] == "5H 24%"
assert frame["goalData"] == {"start": 0, "current": 24, "end": 100, "unit": ""}

ev = _shared.events("assets/codex.png", "assets/codex_low.png",
                    {"5h": 22, "7d": 80}, st)
assert ev == []                                            # already reported

ev = _shared.events("assets/codex.png", "assets/codex_low.png",
                    {"5h": 8, "7d": 80}, st)
assert ev[0]["priority"] == "critical"                     # below 10%

ev = _shared.events("assets/codex.png", "assets/codex_low.png",
                    {"5h": 100, "7d": 80}, st)
assert ev[0]["frames"][0]["goalData"]["current"] == 100    # reset, per heuristic

# --- Detect reset from the provider timestamp --------------------------------
# More reliable than a value jump: the provider says when the window rolls over.
ICONS = ("assets/codex.png", "assets/codex_low.png")
A, B = "2026-09-06T07:00:00Z", "2026-09-06T12:00:00Z"

st = {}
assert _shared.events(*ICONS, {"5h": 40}, st, {"5h": A}) == []   # first run
assert st["resets"] == {"5h": A}

# Timestamp advances and the quota rises -> reset.
ev = _shared.events(*ICONS, {"5h": 100}, st, {"5h": B})
assert len(ev) == 1 and ev[0]["frames"][0]["text"] == "5H 100%"
assert ev[0]["frames"][0]["goalData"]["unit"] == ""

# The same timestamp does not report again, even if the value fluctuates.
assert _shared.events(*ICONS, {"5h": 95}, st, {"5h": B}) == []

# Timestamp advances but nothing recovers: with multiple accounts, one can roll over
# without increasing the total. That is not a reset.
st2 = {"last": {"5h": 40}, "resets": {"5h": A}}
assert _shared.events(*ICONS, {"5h": 38}, st2, {"5h": B}) == []

# If the quota falls below a threshold, the warning wins.
st3 = {"last": {"5h": 60}, "resets": {"5h": A}}
ev = _shared.events(*ICONS, {"5h": 24}, st3, {"5h": A})
assert ev[0]["priority"] == "warning"

# Without a timestamp, retain the heuristic as a fallback.
st4 = {"last": {"5h": 8}}
assert _shared.events(*ICONS, {"5h": 100}, st4)[0]["frames"][0]["goalData"]["current"] == 100

# Also handle a provider returning an *empty* resets dict. Both add-ins do this when
# no reset time is available; checking `is None` made the reset notification vanish.
st5 = {"last": {"5h": 8}, "resets": {}}
assert _shared.events(*ICONS, {"5h": 100}, st5, {})[0]["frames"][0]["goalData"]["current"] == 100

# Mixed case: only 7d has a timestamp, but 5h must still report.
st6 = {"last": {"5h": 8, "7d": 50}, "resets": {"7d": A}}
got = _shared.events(*ICONS, {"5h": 100, "7d": 50}, st6, {"7d": A})
assert len(got) == 1 and got[0]["frames"][0]["text"] == "5H 100%"

# Custom windows: a source with a different shape passes them explicitly, otherwise
# it would silently produce no notifications.
st7 = {"last": {"month": 60}}
month = _shared.events(*ICONS, {"month": 20}, st7, None, windows=("month",))
assert month and month[0]["frames"][0]["text"] == "MONTH 20%"
assert _shared.events(*ICONS, {"month": 20}, {"last": {"month": 60}}) == []
assert _shared.widget("assets/codex.png", {"month": 20}, windows=("month",))

# The building blocks individually.
assert _shared.rolled_over(A, B, 40, 100) is True
assert _shared.rolled_over(A, A, 40, 100) is False        # timestamp unchanged
assert _shared.rolled_over(B, A, 40, 100) is False        # backwards does not count
assert _shared.rolled_over(None, B, 40, 100) is False     # no previous state
assert _shared.rolled_over(A, B, 40, 38) is False         # nothing gained
st = {}

# No add-in produces sounds.
for mod in addins.values():
    src = open(mod.__file__).read()
    assert '"sound"' not in src, mod.__file__

# --- Plex: show instead of episode ----------------------------------------
px, st = addins["plex"], {}
T = 1_000_000.0

# Show: use the show title plus season and episode, not the episode title.
assert px.media_title({"media_type": "episode", "show": "Hannibal",
                       "title": "Harpsichord Suite", "season": "2",
                       "episode": "3"}) == "Hannibal · S02E03"
# Missing episode metadata is omitted instead of inventing S00E00.
assert px.media_title({"media_type": "episode", "show": "Hannibal",
                       "title": "Harpsichord Suite"}) == "Hannibal"
# Movie: append the release year when supplied.
assert px.media_title({"media_type": "movie", "show": "", "title": "Dune",
                       "year": "2021"}) == "Dune (2021)"
assert px.media_title({"media_type": "movie", "show": "", "title": "Dune"}) == "Dune"
# Music is grouped like a show.
assert px.media_title({"media_type": "track", "show": "Radiohead",
                       "title": "Creep"}) == "Radiohead"
# Fallback without a media type: split the "show - episode" form.
assert px.media_title({"title": "Hannibal - Harpsichord Suite"}) == "Hannibal"
# A movie title with a hyphen must not be truncated when its type is present.
assert px.media_title({"media_type": "movie",
                       "title": "Mission - Impossible"}) == "Mission - Impossible"

# --- Plex: display names ---------------------------------------------------
# tests/test_names.py checks the mapping itself. Set it here instead of loading it:
# assets/user_mapping.json contains real names, is not versioned, and is unavailable
# in CI.
px.NAMES = {"thirdlogin": "Kim", "fourthlogin": "Lee"}

# --- Plex: debouncing ------------------------------------------------------
ev = px.hook({"event": "start", "user": "thirdlogin", "title": "Dune"}, {}, st, now=T)
assert ev[0]["frames"][0]["text"] == "Kim: Dune"          # display name instead of login

# Same user, same title shortly afterwards: silent.
assert px.hook({"event": "start", "user": "thirdlogin", "title": "Dune"}, {}, st, now=T + 600) == []

# Record the active stream with its user and timestamp; the clock face uses this. A
# suppressed start still refreshes the timestamp because it proves the stream is active.
assert st["playing"] == "Dune" and st["playing_user"] == "Kim"
assert st["playing_at"] == T + 600

# Another user's stop must not delete the active session; with multiple simultaneous
# streams, the clock face would otherwise disappear immediately.
assert px.hook({"event": "stop", "user": "fourthlogin"}, {}, st, now=T + 601) == []
assert st["playing"] == "Dune" and st["playing_user"] == "Kim"

# The matching stop clears everything, regardless of login or display name.
assert px.hook({"event": "stop", "user": "thirdlogin"}, {}, st, now=T + 602) == []
assert not any(k in st for k in ("playing", "playing_user", "playing_at"))

# Without a user, clear the session unconditionally.
px.hook({"event": "start", "user": "thirdlogin", "title": "Dune"}, {}, st, now=T + 700)
assert px.hook({"event": "stop"}, {}, st, now=T + 701) == []
assert "playing" not in st

# A different title reports immediately; switching means something new.
assert px.hook({"event": "start", "user": "thirdlogin", "title": "Alien"}, {}, st, now=T + 610)

# Another user, same title: separate debounce counter.
assert px.hook({"event": "start", "user": "fourthlogin", "title": "Dune"}, {}, st, now=T + 620)

# Repeated starts within the debounce period must not move the window.
assert px.hook({"event": "start", "user": "thirdlogin", "title": "Dune"}, {}, st, now=T + 1200) == []
assert px.hook({"event": "start", "user": "thirdlogin", "title": "Dune"}, {}, st, now=T + 1700) == []

# Report again after the debounce period, measured from the last notification.
assert px.hook({"event": "start", "user": "thirdlogin", "title": "Dune"}, {}, st, now=T + 1801)

# Old entries are pruned so state.json does not grow without bound.
px.hook({"event": "start", "user": "late-user", "title": "X"}, {}, st, now=T + 100_000)
assert "Kim|Dune" not in st["seen"] and "late-user|X" in st["seen"]

down = px.hook({"event": "server_down"}, {}, {})
assert down[0]["priority"] == "critical" and down[0]["frames"][0]["text"] == "Plex offline"
assert px.hook({"event": "server_up"}, {}, {})[0]["frames"][0]["text"] == "Plex online"

# --- Forgejo: event type comes from the header ----------------------------
fj = addins["forgejo"]
H = {"x-forgejo-event": "action_run"}

bad = fj.hook({"action_run": {"conclusion": "failure"}, "repository": {"name": "api"}}, H, {})
assert bad[0]["frames"][0]["text"] == "api failed" and bad[0]["priority"] == "critical"

good = fj.hook({"action_run": {"conclusion": "success"}, "repository": {"name": "api"}}, H, {})
assert good[0]["frames"][0]["text"] == "api succeeded"
assert good[0]["frames"][0]["icon"] != bad[0]["frames"][0]["icon"]   # green vs red

# Forgejo 16 sends Action runs under `run`, including the repository there.
real = fj.hook({"action": "success", "run": {"status": "success", "repository": {"name": "api"}}},
               {"x-forgejo-event": "action_run_success"}, {})
assert real[0]["frames"][0]["text"] == "api succeeded"

# A running job is not a notification yet.
assert fj.hook({"action_run": {"status": "running"}, "repository": {"name": "api"}}, H, {}) == []

push = fj.hook({"ref": "refs/heads/main", "commits": [1, 2, 3], "repository": {"name": "api"}},
               {"x-forgejo-event": "push"}, {})
assert push[0]["frames"][0]["text"] == "api +3"
push = fj.hook({"ref": "refs/heads/fix", "commits": [1], "repository": {"name": "api"}},
               {"x-gitea-event": "push"}, {})
assert push[0]["frames"][0]["text"] == "api +1 fix"      # branch only when not main

# Without a header, detect the event from the body shape.
assert fj.hook({"ref": "refs/heads/main", "commits": [], "repository": {"name": "api"}},
               {}, {})[0]["frames"][0]["text"] == "api +0"
assert fj.hook({"zen": "just a ping"}, {}, {}) == []

# --- Claude: reject stale data instead of displaying it incorrectly ----------
# Use a small test database instead of the real delivery database so the test runs
# everywhere, including CI where the private ai-usage-insights project is unavailable.
cl = addins["claude"]

db = os.environ["AIUSAGE_DB"]
NOW = cl.age.__globals__["datetime"].fromisoformat("2026-09-05T20:10:00+00:00").timestamp()

assert round(cl.age("2026-09-05T20:00:00Z", now=NOW)) == 600
pct, resets = cl.remaining(db=db, max_age=0, now=NOW)                    # 0 = no limit
assert pct == {"5h": 93, "7d": 81}
# The provider's reset timestamp is included; the reset detector relies on it.
assert resets == {"5h": "2026-09-06T07:00:00Z", "7d": "2026-09-09T20:00:00Z"}
assert cl.remaining(db=db, max_age=3600, now=NOW)[0] == {"5h": 93, "7d": 81}

try:
    cl.remaining(db=db, max_age=60, now=NOW)      # ten minutes old, one-minute limit
except RuntimeError as e:
    assert "old" in str(e), e
else:
    raise AssertionError("stale quota should have raised an error")

# A missing database must raise an error instead of silently reporting nothing.
try:
    cl.remaining(db=os.path.join(tempfile.mkdtemp(), "missing.sqlite3"), max_age=0)
except Exception:
    pass
else:
    raise AssertionError("missing database should have raised an error")

# Age display uses seconds below two minutes; otherwise "0 min old" would be shown.
assert cl.age_text(37) == "37 s"
assert cl.age_text(119) == "119 s"
assert cl.age_text(120) == "2 min"
assert cl.age_text(3600) == "60 min"

# --- deliver(): quiet hours and rate limiting together ---------------------
# Previously only `muted()` and `Limiter` were tested individually, not the path
# an event actually takes.
sent_events = []
_send = hub.send
hub.send = lambda e: sent_events.append(e)
_limiter, _quiet, _crit = hub.LIMITER, hub.QUIET_HOURS, hub.QUIET_ALLOW_CRITICAL
_health = dict(hub.HEALTH)
try:
    hub.LIMITER = hub.Limiter(maximum=2, window=60)
    hub.QUIET_HOURS = ""
    hub.HEALTH.update({"sent": 0, "muted": 0, "limited": 0})
    NOW = 1_700_000_000.0

    assert hub.deliver("t", {"frames": [{"text": "a"}]}, NOW) is True
    assert hub.deliver("t", {"frames": [{"text": "b"}]}, NOW) is True
    # The third event does not fit in the window: hold it, do not discard it.
    assert hub.deliver("t", {"frames": [{"text": "c"}]}, NOW) is False
    assert len(sent_events) == 2 and len(hub.LIMITER.pending) == 1
    assert hub.HEALTH["sent"] == 2 and hub.HEALTH["limited"] == 1

    # During quiet hours nothing is sent, and `now` must reach the check or quiet
    # hours could not be tested.
    hub.QUIET_HOURS = "mo-so 00:00-23:59"
    assert hub.deliver("t", {"frames": [{"text": "d"}]}, NOW) is False
    assert hub.HEALTH["muted"] == 1
    assert len(sent_events) == 2                       # nothing added

    # Critical events pass when explicitly allowed.
    hub.QUIET_ALLOW_CRITICAL = True
    hub.LIMITER = hub.Limiter(maximum=2, window=60)
    assert hub.deliver("t", {"frames": [{"text": "e"}], "priority": "critical"}, NOW) is True
finally:
    hub.send = _send
    hub.LIMITER, hub.QUIET_HOURS, hub.QUIET_ALLOW_CRITICAL = _limiter, _quiet, _crit
    hub.HEALTH.clear(); hub.HEALTH.update(_health)

# --- A device error must not kill the service -------------------------------
# push_widget used to run unguarded in main(): a wrong key or a briefly unreachable
# clock sent the hub into a restart loop.
class Boom:
    NAME = "boom"

    @staticmethod
    def widget(state):
        return [{"text": "x"}]


_push = lametric.push


def explode(*a, **k):
    raise lametric.LametricError(401, "Authorization is required")


lametric.push = explode
try:
    assert hub.push_widget({"boom": Boom}, hub.State(os.path.join(d, "boom.json"))) == 0
finally:
    lametric.push = _push

# --- Best-effort delivery and quiet logs -----------------------------------
_send, _retries, _health = hub.send, list(hub.RETRIES), dict(hub.HEALTH)
hub.RETRIES.clear()
hub.HEALTH.update({"sent": 0, "failed": 0, "retried": 0, "dropped": 0})
retry_event = {"frames": [{"text": "retry me"}]}
hub.queue_retry("test", retry_event, now=100)
hub.send = lambda event: sent_events.append(event)
assert hub.retry_pending(100 + hub.RETRY_DELAY) is True
assert sent_events[-1] == retry_event
assert hub.HEALTH["sent"] == 1 and hub.HEALTH["retried"] == 1

hub.queue_retry("test", retry_event, now=200)
assert hub.retry_pending(200 + hub.RETRY_TTL) is False
assert hub.HEALTH["dropped"] == 1 and not hub.RETRIES
hub.send = _send
hub.RETRIES[:] = _retries
hub.HEALTH.clear(); hub.HEALTH.update(_health)

import contextlib
import io

_failures = dict(hub.LOG_FAILURES)
hub.LOG_FAILURES.clear()
logged = io.StringIO()
with contextlib.redirect_stderr(logged):
    hub.log_failure("probe", "boom", now=0)
    hub.log_failure("probe", "boom", now=1)
    hub.log_failure("probe", "boom", now=hub.LOG_REPEAT)
hub.log_recovery("probe")
assert logged.getvalue().count("boom") == 2
assert "1 repeats suppressed" in logged.getvalue()
hub.LOG_FAILURES.clear(); hub.LOG_FAILURES.update(_failures)

# --- Rate limiter -----------------------------------------------------------
# Without a cap, a Matrix pipeline or flapping monitor can make the clock unusable
# for minutes.
lim = hub.Limiter(maximum=2, window=60)
T0 = 5_000.0

assert lim.allow(T0) is True
assert lim.allow(T0 + 1) is True
assert lim.allow(T0 + 2) is False          # window full
assert lim.summary(T0 + 2) is None         # nothing held, nothing to report

# Held events are counted, not discarded.
lim.hold({"priority": "info", "frames": [{"text": "a", "icon": "I"}]})
lim.hold({"priority": "critical", "frames": [{"text": "b", "icon": "C"}]})
assert lim.summary(T0 + 3) is None         # still no capacity

# Once the window advances, an aggregate notification is sent.
pack = lim.summary(T0 + 61)
assert pack["frames"][0]["text"] == "2 events"
# Priority and icon come from the most urgent event; otherwise the aggregate would
# hide that a critical event was included.
assert pack["priority"] == "critical" and pack["frames"][0]["icon"] == "C"
assert lim.summary(T0 + 62) is None        # report only once

# An event without an icon must not break the aggregate notification.
bare = hub.Limiter(maximum=1, window=60)
bare.hold({"priority": "info", "frames": [{"text": "x"}]})
assert "icon" not in bare.summary(T0)["frames"][0]

# A configured zero would otherwise silence the clock permanently.
assert hub.Limiter(maximum=0, window=60).maximum == 1

# --- Device health ----------------------------------------------------------
saved = dict(hub.HEALTH)
hub.HEALTH.update({"device_ok": None, "device_fail": None})
assert hub.healthy() is True               # healthy when no history exists
hub.device_result(False)
assert hub.healthy() is False
hub.device_result(True)
assert hub.healthy() is True
# A 400 means quiet-hours behavior, not a defect.
hub.device_result(False, status=400)
assert hub.healthy() is True
hub.HEALTH.clear(); hub.HEALTH.update(saved)

# --- Quiet hours by weekday ------------------------------------------------
import time as _time

# Quiet hours use local time, and the host usually runs on UTC. The hub calls tzset()
# at import so TZ from the environment takes effect; without it, a window would start
# an hour or two late depending on the season.
os.environ["TZ"] = "Europe/Berlin"
_time.tzset()
assert _time.tzname[0] in ("CET", "MEZ"), _time.tzname
del os.environ["TZ"]
_time.tzset()

# 2026-09-05 is a Saturday; tm_wday 0 is Monday.
def at(weekday, hh, mm=0):
    return _time.struct_time((2026, 9, 7 + weekday, hh, mm, 0, weekday, 250, 0))


MO, DI, MI, DO, FR, SA, SO = range(7)
PLAN = "mo-fr 00:00-09:00; sa,so 03:00-11:00"

# Resolve day ranges and lists.
assert hub._days("mo-fr") == {MO, DI, MI, DO, FR}
assert hub._days("sa,so") == {SA, SO}
assert hub._days("fr-mo") == {FR, SA, SO, MO}          # range across the weekend
assert hub._days("mi") == {MI}
assert hub._days("nonsense") == set()

# Work week: quiet from midnight until nine.
assert hub.in_quiet(at(MI, 0, 0), PLAN) is True
assert hub.in_quiet(at(MI, 8, 59), PLAN) is True
assert hub.in_quiet(at(MI, 9, 0), PLAN) is False       # end is exclusive
assert hub.in_quiet(at(MI, 23, 0), PLAN) is False

# Weekend: starts at three and runs until eleven.
assert hub.in_quiet(at(SA, 2, 0), PLAN) is False       # Friday night remains awake
assert hub.in_quiet(at(SA, 3, 0), PLAN) is True
assert hub.in_quiet(at(SO, 10, 59), PLAN) is True
assert hub.in_quiet(at(SO, 11, 0), PLAN) is False

# Sunday night does not belong to Monday's rule yet.
assert hub.in_quiet(at(SO, 23, 0), PLAN) is False
assert hub.in_quiet(at(MO, 1, 0), PLAN) is True

# A window crossing midnight belongs to its start day and continues into the next day.
NIGHT = "fr 22:00-06:00"
assert hub.in_quiet(at(FR, 23, 0), NIGHT) is True
assert hub.in_quiet(at(SA, 5, 59), NIGHT) is True      # Saturday morning, Friday's rule
assert hub.in_quiet(at(SA, 6, 0), NIGHT) is False
assert hub.in_quiet(at(FR, 21, 59), NIGHT) is False

# "24:00" as the start means midnight.
assert hub.in_quiet(at(MO, 0, 30), "mo 24:00-09:00") is True

# Without a day, the rule applies daily; invalid input is ignored.
assert hub.in_quiet(at(SO, 4, 0), "03:00-05:00") is True
assert hub.in_quiet(at(MO, 4, 0), "") is False
assert hub.in_quiet(at(MO, 4, 0), "nonsense") is False

# During quiet hours everything is muted by default, including critical events.
hub.QUIET_HOURS, hub.QUIET_ALLOW_CRITICAL = PLAN, False
assert hub.muted({"priority": "critical"}, at(MI, 2, 0)) is True
assert hub.muted({"priority": "info"}, at(MI, 12, 0)) is False
hub.QUIET_ALLOW_CRITICAL = True
assert hub.muted({"priority": "critical"}, at(MI, 2, 0)) is False
assert hub.muted({"priority": "info"}, at(MI, 2, 0)) is True
hub.QUIET_HOURS, hub.QUIET_ALLOW_CRITICAL = "", False

# --- Webhook routing (without a real clock) -------------------------------
sent = []
hub.send = lambda e: sent.append(e)
state = hub.State(os.path.join(d, "hook-state.json"))
srv = hub.serve(addins, state, port=0)
port = srv.server_address[1]


def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


assert post("/hook/plex", {"event": "start", "user": "fourthlogin", "title": "Alien"}) == (200, "1 events")
assert sent[-1]["frames"][0]["text"] == "Lee: Alien"
assert post("/hook/missing", {})[0] == 404
assert post("/hook/codex", {})[0] == 404          # codex has no hook()

# An oversized body is rejected on the Content-Length alone, before it is read into
# memory. The listener has no authentication, so this is the only thing between the
# LAN and an arbitrary allocation.
_max, hub.MAX_BODY = hub.MAX_BODY, 64
assert post("/hook/say", {"text": "x" * 200})[0] == 413
assert post("/hook/say", {"text": "ok"})[0] == 200
hub.MAX_BODY = _max


def get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


status, body = get("/healthz")
assert status == 200, body
health = json.loads(body)
assert health["status"] == "ok" and "codex" in health["addins"]
assert {"sent", "muted", "rate_limited", "retry_pending", "retry_dropped",
        "quiet_now"} <= set(health)

status, body = get("/metrics")
assert status == 200
assert "lametric_events_sent_total" in body
assert "lametric_device_up 1" in body
assert body.endswith("\n")

assert get("/missing")[0] == 404
srv.shutdown()

print("ok")

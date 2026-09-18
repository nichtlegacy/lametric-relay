#!/usr/bin/env python3
"""LaMetric Relay event hub. The clock remains a clock and only speaks up for events.

An add-in is a file in `addins/` with:

    NAME     = "codex"
    INTERVAL = 60                  # seconds; omit it to disable polling
    def configured() -> bool       # optional; False = skip the add-in
    def poll(state)   -> [event]   # optional
    def hook(payload, headers, state) -> [event]   # optional, via POST /hook/<name>
    def widget(state) -> [frame]   # optional, glanceable widget

The hub persists `state` between runs. Add-ins use it for edge detection instead
of sending the same notification on every run.

An event is a dict: {"frames": [...], "sound": "positive1", "priority": "info"}

  ./hub.py                # pollers + webhook server
  ./hub.py --once         # poll every add-in once; send nothing except events
  ./hub.py --list         # show loaded add-ins
"""
import argparse, importlib.util, json, os, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lametric
from version import __version__

# Quiet hours are evaluated in local time, and containers usually run on UTC.
# Apply TZ from the environment before the first time.localtime(); without this,
# a "22:00-07:00" window would run on the wrong clock and shift again at the
# daylight-saving change. Unset TZ keeps the host's own local time.
time.tzset()

ADDIN_DIR = os.environ.get("ADDIN_DIR", os.path.join(os.path.dirname(__file__), "addins"))
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(__file__), "state.json"))
HOOK_PORT = int(os.environ.get("HOOK_PORT", "8099"))
# Refuse oversized webhook bodies instead of reading them into memory. The listener
# has no authentication, so anything that can reach the port can otherwise make the
# hub allocate as much as it claims in Content-Length. No real payload comes close
# to this; Tautulli's largest is a few kilobytes.
MAX_BODY = int(os.environ.get("HOOK_MAX_BODY", str(256 * 1024)))
# Dump every incoming webhook as-is. This is the fastest way to inspect what a new
# source actually sends while it is being connected.
HOOK_DUMP = os.environ.get("HOOK_DUMP", "")
# Cap the event rate so a burst cannot make the clock unusable for minutes. A Matrix
# pipeline or flapping monitor can otherwise produce twenty notifications in a row.
# Anything above the cap becomes an aggregated notification.
RATE_MAX = int(os.environ.get("RATE_MAX", "4"))
RATE_WINDOW = int(os.environ.get("RATE_WINDOW", "60"))
WIDGET_INTERVAL = int(os.environ.get("WIDGET_INTERVAL", "300"))
# Device delivery is intentionally best effort. One short-lived retry covers a
# Wi-Fi wobble without replaying old notifications after the clock was unplugged.
RETRY_DELAY = 60
RETRY_TTL = 300
RETRY_MAX = 20
LOG_REPEAT = 900
# Quiet hours by weekday: "mo-fr 00:00-09:00; sa,so 03:00-11:00".
# The clock only knows its ambient-light sensor; firmware 2.3.9 does not expose
# its schedule through the API, so the calendar lives here. Empty by default:
# silently swallowing notifications is not something anyone should inherit
# from a default.
QUIET_HOURS = os.environ.get("QUIET_HOURS", "")
# Allow critical events during quiet hours? This checks the event's original
# priority, not a priority raised later for the screensaver.
QUIET_ALLOW_CRITICAL = os.environ.get("QUIET_ALLOW_CRITICAL", "0") == "1"
# Send everything as `critical` outside quiet hours. This is required for
# notifications to appear while the screensaver is active; the documentation says
# `info` is not shown then.
OVERRIDE_SCREENSAVER = os.environ.get("OVERRIDE_SCREENSAVER", "1") == "1"

DAYS = {"mo": 0, "di": 1, "tu": 1, "mi": 2, "we": 2, "do": 3, "th": 3,
        "fr": 4, "sa": 5, "so": 6, "su": 6}
ENABLED = [n for n in os.environ.get("ADDINS", "").split(",") if n]


def load(directory=ADDIN_DIR, enabled=None):
    """Every .py file in the directory is an add-in: no registry or entry point."""
    out = {}
    # Let add-ins import each other (_shared) and find project modules such as
    # lametric and quota. Do this once here instead of in every add-in.
    for extra in (directory, os.path.dirname(os.path.abspath(directory))):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        path = os.path.join(directory, fn)
        spec = importlib.util.spec_from_file_location(f"addins.{fn[:-3]}", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            # A broken add-in must not take down the others.
            print(f"[hub] failed to load {fn}:\n{traceback.format_exc()}", file=sys.stderr)
            continue
        name = getattr(mod, "NAME", fn[:-3])
        if enabled and name not in enabled:
            continue
        # Skip add-ins without credentials. Otherwise the same missing setting
        # would fail every minute, and using two sources would require configuring
        # every source.
        check = getattr(mod, "configured", None)
        if check is not None and not check():
            print(f"[hub] skipping {name}: not configured")
            continue
        out[name] = mod
    return out


class State(dict):
    """Persistent dictionary for each add-in.

    The state is written by both the main loop **and** webhook threads, so each
    thread gets a unique temporary filename. With a shared name, one writer could
    overwrite the other's unfinished file, causing `os.replace` to move fragments
    into `state.json` or fail with FileNotFoundError. LOCK in `run_addin` serializes
    the writers.
    """

    def __init__(self, path=STATE_FILE):
        self.path = path
        try:
            super().__init__(json.load(open(path)))
        except (OSError, ValueError):
            super().__init__()

    def save(self):
        tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(self, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)   # never leave a partially written file behind
        except Exception:
            # A non-serializable state value must not terminate the service; Docker
            # would otherwise restart it into the same crash.
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def of(self, name):
        return self.setdefault(name, {})


def _minutes(text):
    h, _, m = text.strip().partition(":")
    return int(h) * 60 + int(m or 0)


def _days(spec):
    """Map day ranges such as "mo-fr", "sa,so", or "mi" to weekday numbers.

    English names and legacy German aliases are both accepted for compatibility.
    """
    out = set()
    for part in spec.split(","):
        part = part.strip().lower()
        if "-" in part:
            a, _, b = part.partition("-")
            if a in DAYS and b in DAYS:
                i, j = DAYS[a], DAYS[b]
                out |= {d % 7 for d in range(i, i + (j - i) % 7 + 1)}
        elif part in DAYS:
            out.add(DAYS[part])
    return out


def schedule(text=None):
    """Parse quiet-hour rules into ``[(days, start, end)]`` in minutes."""
    rules = []
    for chunk in (QUIET_HOURS if text is None else text).split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        days_spec, _, window = chunk.rpartition(" ")
        if "-" not in window:
            continue
        start, _, end = window.partition("-")
        try:
            a, b = _minutes(start) % 1440, _minutes(end)   # "24:00" at start means midnight
        except ValueError:
            continue
        days = _days(days_spec) if days_spec else set(range(7))
        if days:
            rules.append((days, a, b))
    return rules


def in_quiet(now=None, text=None):
    """Return whether `now` falls within a quiet-hour window.

    A window belongs to the day on which it starts. If it crosses midnight, the
    previous day's rule applies after midnight; otherwise "fr 22:00-06:00" would
    suddenly stop being active on Saturday morning.
    """
    now = time.localtime() if now is None else now
    minutes, weekday = now.tm_hour * 60 + now.tm_min, now.tm_wday
    for days, a, b in schedule(text):
        if a < b:
            if weekday in days and a <= minutes < b:
                return True
        else:
            if weekday in days and minutes >= a:
                return True
            if (weekday - 1) % 7 in days and minutes < b:
                return True
    return False


def muted(event, now=None):
    """Return True when the event should not be sent right now."""
    if not in_quiet(now):
        return False
    return not (QUIET_ALLOW_CRITICAL and event.get("priority") == "critical")


def send(event):
    frames = event.get("frames") or []
    if not frames:
        return
    model = {"frames": frames, "cycles": event.get("cycles", 1)}
    if event.get("sound"):
        model["sound"] = {"category": "notifications", "id": event["sound"]}
    priority = "critical" if OVERRIDE_SCREENSAVER else event.get("priority", "info")
    lametric.call("/device/notifications", "POST",
                  {"priority": priority, "icon_type": "none", "model": model})


def run_addin(name, mod, state, fn="poll", *args):
    """Run an add-in, deliver its events, and contain failures."""
    func = getattr(mod, fn, None)
    if func is None:
        return 0
    with LOCK:
        return _run_locked(name, mod, state, fn, func, args)


def _run_locked(name, mod, state, fn, func, args):
    own = state.of(name)
    try:
        events = func(*args, own) or []
    except Exception:
        HEALTH["errors"][name] = HEALTH["errors"].get(name, 0) + 1
        log_failure(f"{name}.{fn}", f"[hub] {name}.{fn} failed:\n{traceback.format_exc()}")
        return 0
    log_recovery(f"{name}.{fn}")
    for e in events:
        try:
            if deliver(name, e):
                device_result(True)
                log_recovery("device-send")
        except lametric.LametricError as exc:
            device_result(False, exc.status)
            # With the screensaver active, the clock accepts only `critical`. This
            # is quiet-hours behavior, not a fault, so keep the log concise.
            if exc.status == 400 and "critical" in exc.body:
                print(f"[hub] {name}: dropped; the clock currently accepts only critical")
            else:
                queue_retry(name, e)
                HEALTH["failed"] += 1
                log_failure("device-send", f"[hub] send failed: {exc}")
        except Exception as exc:
            device_result(False)
            queue_retry(name, e)
            HEALTH["failed"] += 1
            log_failure("device-send", f"[hub] send failed: {type(exc).__name__}: {exc}")

    try:
        state.save()
    except Exception as exc:
        HEALTH["errors"][name] = HEALTH["errors"].get(name, 0) + 1
        log_failure(f"{name}.state", f"[hub] {name}: state could not be saved: "
                    f"{type(exc).__name__}: {exc}")
    return len(events)


def push_widget(addins, state):
    """Collect widget() frames and write them to the DIY widget.

    The widget is glanceable: visible when swiped to, and always current.
    """
    frames = []
    for name, mod in addins.items():
        if not hasattr(mod, "widget"):
            continue
        try:
            frames += mod.widget(state.of(name)) or []
        except Exception:
            log_failure(f"{name}.widget",
                        f"[hub] {name}.widget failed:\n{traceback.format_exc()}")
        else:
            log_recovery(f"{name}.widget")
    if not frames:
        return 0
    try:
        lametric.push(lametric.diy(), frames)
        device_result(True)
    except Exception as exc:
        # A device error must not terminate the service. Otherwise a wrong key or
        # a briefly unreachable clock would put the hub into a restart loop.
        #
        # Recording this matters more than for notifications: the widget is the
        # only unattended device access. Without it, /healthz stayed "ok" as long
        # as no event occurred.
        device_result(False, getattr(exc, "status", None))
        HEALTH["errors"]["widget"] = HEALTH["errors"].get("widget", 0) + 1
        # The widget UUID may have changed; refresh it on the next run.
        lametric.forget_widgets()
        log_failure("device-widget", f"[hub] widget: {type(exc).__name__}: {exc}")
        return 0
    log_recovery("device-widget")
    return len(frames)


class Limiter:
    """Sliding window; overflow is counted instead of discarded."""

    def __init__(self, maximum=None, window=None):
        # Keep at least one: a configured zero would hold every notification
        # forever and silence the clock.
        self.maximum = max(1, RATE_MAX if maximum is None else maximum)
        self.window = RATE_WINDOW if window is None else window
        self.sent = []
        self.pending = []

    def allow(self, now):
        self.sent = [t for t in self.sent if now - t < self.window]
        if len(self.sent) < self.maximum:
            self.sent.append(now)
            return True
        return False

    def hold(self, event):
        self.pending.append(event)

    def summary(self, now):
        """Return an aggregate notification when capacity is available.

        The icon and priority come from the most urgent held event; a number alone
        would hide that a critical event was included.
        """
        if not self.pending or not self.allow(now):
            return None
        rank = {"info": 0, "warning": 1, "critical": 2}
        worst = max(self.pending, key=lambda e: rank.get(e.get("priority", "info"), 0))
        count, self.pending = len(self.pending), []
        icon = (worst.get("frames") or [{}])[0].get("icon")
        frame = {"text": f"{count} events"}
        if icon:
            frame["icon"] = icon
        return {"frames": [frame], "priority": worst.get("priority", "info")}


LIMITER = Limiter()
RETRIES = []
# The queue is written by webhook threads through _run_locked, which holds LOCK,
# and by the main loop's aggregate branch, which does not -- so LOCK does not
# actually separate the two. Its own lock does.
RETRY_LOCK = threading.Lock()
LOG_FAILURES = {}
LOG_LOCK = threading.Lock()
# State and counters are touched by the main loop and webhook threads. One lock
# around each add-in run covers updating its section, delivery, and saving as one unit.
LOCK = threading.Lock()
HEALTH = {"started": time.time(), "sent": 0, "muted": 0, "limited": 0,
          "failed": 0, "retried": 0, "dropped": 0,
          "errors": {}, "device_ok": None, "device_fail": None}


def log_failure(key, message, now=None):
    """Log the first failure, then one summary per 15 minutes."""
    now = time.time() if now is None else now
    with LOG_LOCK:
        item = LOG_FAILURES.setdefault(key, {"count": 0, "last": None, "suppressed": 0})
        item["count"] += 1
        if item["last"] is not None and now - item["last"] < LOG_REPEAT:
            item["suppressed"] += 1
            return
        suffix = (f" ({item['suppressed']} repeats suppressed)"
                  if item["suppressed"] else "")
        print(message + suffix, file=sys.stderr)
        item["last"] = now
        item["suppressed"] = 0


def log_recovery(key):
    with LOG_LOCK:
        item = LOG_FAILURES.pop(key, None)
    if item:
        print(f"[hub] {key} recovered after {item['count']} failures")


def queue_retry(name, event, now=None):
    now = time.time() if now is None else now
    with RETRY_LOCK:
        if len(RETRIES) >= RETRY_MAX:
            RETRIES.pop(0)
            HEALTH["dropped"] += 1
        RETRIES.append({"name": name, "event": event, "due": now + RETRY_DELAY,
                        "expires": now + RETRY_TTL})


def retry_pending(now=None):
    """Try one recent failed notification once; stale events are discarded."""
    now = time.time() if now is None else now
    expired = 0
    # Take the item under the lock, then send outside it: a device call can block
    # for the full timeout, and no webhook thread should wait on that to queue.
    with RETRY_LOCK:
        while RETRIES and RETRIES[0]["expires"] <= now:
            RETRIES.pop(0)
            expired += 1
        item = RETRIES.pop(0) if RETRIES and RETRIES[0]["due"] <= now else None
    if expired:
        HEALTH["dropped"] += expired
        log_failure("retry-expired", f"[hub] retry: dropped {expired} stale notifications", now)
    if item is None:
        return False
    try:
        send(item["event"])
    except Exception as exc:
        HEALTH["failed"] += 1
        HEALTH["dropped"] += 1
        device_result(False, getattr(exc, "status", None))
        log_failure("device-send", f"[hub] retry failed; notification dropped: "
                    f"{type(exc).__name__}: {exc}", now)
        return False
    HEALTH["sent"] += 1
    HEALTH["retried"] += 1
    device_result(True)
    log_recovery("device-send")
    print(f"[hub] {item['name']}: delivered on retry")
    return True


def device_result(ok, status=None):
    """Record device health for /healthz.

    A 400 is deliberately not treated as a fault: the clock uses it to reject a
    notification during the screensaver, which is quiet-hours behavior, not a defect.
    """
    if ok:
        HEALTH["device_ok"] = time.time()
    elif status != 400:
        HEALTH["device_fail"] = time.time()


def healthy():
    fail, ok = HEALTH["device_fail"], HEALTH["device_ok"]
    return fail is None or (ok is not None and ok >= fail)


def deliver(name, event, now=None):
    """Deliver an event when quiet hours and the rate limiter allow it.

    `now` is seconds since the epoch; quiet-hour checks convert it to local time.
    """
    now = time.time() if now is None else now
    if muted(event, time.localtime(now)):
        HEALTH["muted"] += 1
        print(f"[hub] {name}: dropped during quiet hours {QUIET_HOURS}")
        return False
    if not LIMITER.allow(now):
        LIMITER.hold(event)
        HEALTH["limited"] += 1
        print(f"[hub] {name}: held; more than {LIMITER.maximum} events in "
              f"{LIMITER.window}s")
        return False
    send(event)
    HEALTH["sent"] += 1
    return True


def dump(name, headers, payload):
    if not HOOK_DUMP:
        return
    with open(HOOK_DUMP, "a") as fh:
        fh.write(json.dumps({"at": time.strftime("%FT%T"), "addin": name,
                             "headers": headers, "payload": payload}) + "\n")


def metrics():
    """Build Prometheus text format by hand; six metrics do not justify a dependency."""
    lines = [
        "# HELP lametric_events_sent_total Events sent to the device",
        "# TYPE lametric_events_sent_total counter",
        f"lametric_events_sent_total {HEALTH['sent']}",
        "# HELP lametric_events_muted_total Events dropped during quiet hours",
        "# TYPE lametric_events_muted_total counter",
        f"lametric_events_muted_total {HEALTH['muted']}",
        "# HELP lametric_events_rate_limited_total Events held by the rate limiter",
        "# TYPE lametric_events_rate_limited_total counter",
        f"lametric_events_rate_limited_total {HEALTH['limited']}",
        "# HELP lametric_delivery_failures_total Failed device delivery attempts",
        "# TYPE lametric_delivery_failures_total counter",
        f"lametric_delivery_failures_total {HEALTH['failed']}",
        "# HELP lametric_retry_delivered_total Notifications delivered by the best-effort retry",
        "# TYPE lametric_retry_delivered_total counter",
        f"lametric_retry_delivered_total {HEALTH['retried']}",
        "# HELP lametric_retry_dropped_total Notifications dropped after the retry window",
        "# TYPE lametric_retry_dropped_total counter",
        f"lametric_retry_dropped_total {HEALTH['dropped']}",
        "# HELP lametric_retry_pending Notifications waiting for one retry",
        "# TYPE lametric_retry_pending gauge",
        f"lametric_retry_pending {len(RETRIES)}",
        "# HELP lametric_device_up Device reachable on the last attempt",
        "# TYPE lametric_device_up gauge",
        f"lametric_device_up {1 if healthy() else 0}",
        "# HELP lametric_uptime_seconds Relay uptime",
        "# TYPE lametric_uptime_seconds gauge",
        f"lametric_uptime_seconds {int(time.time() - HEALTH['started'])}",
        "# HELP lametric_addin_errors_total Failed add-in runs",
        "# TYPE lametric_addin_errors_total counter",
    ]
    for name, count in sorted(HEALTH["errors"].items()):
        lines.append(f'lametric_addin_errors_total{{addin="{name}"}} {count}')
    return "\n".join(lines) + "\n"


def serve(addins, state, port=HOOK_PORT):
    """Forward the body of POST /hook/<name> to the add-in."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            name = self.path.strip("/").split("/")[-1]
            mod = addins.get(name)
            if mod is None or not hasattr(mod, "hook"):
                return self.reply(404, "unknown add-in")
            # If the length is missing or not numeric, the body was never read. The
            # sender would receive "0 events" and mistake it for success. This also
            # catches clients that send chunked requests.
            try:
                length = int(self.headers["Content-Length"])
            except (KeyError, TypeError, ValueError):
                return self.reply(411, "Content-Length is missing or not numeric")
            if length > MAX_BODY:
                return self.reply(413, f"body larger than {MAX_BODY} bytes")
            raw = self.rfile.read(max(0, length))
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                dump(name, dict(self.headers), {"_raw": raw.decode("utf8", "replace")})
                return self.reply(400, "invalid JSON")
            headers = {k.lower(): v for k, v in self.headers.items()}
            dump(name, headers, payload)
            n = run_addin(name, mod, state, "hook", payload, headers)
            if n:
                print(time.strftime("%H:%M:%S"), f"webhook {name}: {n} events")
            self.reply(200, f"{n} events")

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/healthz":
                ok = healthy()
                body = {"status": "ok" if ok else "degraded",
                        "uptime_s": int(time.time() - HEALTH["started"]),
                        "addins": sorted(addins),
                        "sent": HEALTH["sent"], "muted": HEALTH["muted"],
                        "rate_limited": HEALTH["limited"],
                        "pending_summary": len(LIMITER.pending),
                        "delivery_failures": HEALTH["failed"],
                        "retry_delivered": HEALTH["retried"],
                        "retry_dropped": HEALTH["dropped"],
                        "retry_pending": len(RETRIES),
                        "addin_errors": HEALTH["errors"],
                        "quiet_now": in_quiet()}
                return self.reply(200 if ok else 503, json.dumps(body, indent=2),
                                  "application/json")
            if path == "/metrics":
                return self.reply(200, metrics(), "text/plain; version=0.0.4")
            return self.reply(404, "only /healthz and /metrics are available")

        def reply(self, code, msg, ctype="text/plain; charset=utf-8"):
            body = msg.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="poll once, then exit")
    p.add_argument("--list", action="store_true", help="show loaded add-ins")
    p.add_argument("--no-hooks", action="store_true", help="disable the webhook server")
    p.add_argument("--version", action="version", version=f"lametric-relay {__version__}")
    a = p.parse_args()

    addins = load(enabled=ENABLED or None)
    if a.list or not addins:
        for name, mod in addins.items():
            can = [f for f in ("poll", "hook", "widget") if hasattr(mod, f)]
            print(f"{name:10} {'/'.join(can)}  interval={getattr(mod, 'INTERVAL', '-')}")
        if not addins:
            print("no add-ins in", ADDIN_DIR)
        return

    state = State()
    if a.once:
        for name, mod in addins.items():
            print(f"{name}: {run_addin(name, mod, state)} events")
        # Flush events held by the rate limiter so a diagnostic run does not
        # swallow them.
        pack = LIMITER.summary(time.time())
        if pack:
            send(pack)
            print(pack["frames"][0]["text"])
        print("widget:", push_widget(addins, state), "frames")
        return

    if not a.no_hooks:
        serve(addins, state)
        print(f"[hub] webhooks listening on :{HOOK_PORT}/hook/<name>")
    due = {name: 0.0 for name in addins}
    widget_due = 0.0
    print(f"[hub] LaMetric Relay {__version__}, add-ins:", ", ".join(addins))
    while True:
        now = time.time()
        for name, mod in addins.items():
            iv = getattr(mod, "INTERVAL", None)
            if iv and now >= due[name]:
                n = run_addin(name, mod, state)
                if n:
                    print(time.strftime("%H:%M:%S"), f"{name}: {n} events")
                due[name] = now + iv
        pack = LIMITER.summary(now)
        if pack:
            try:
                send(pack)
                HEALTH["sent"] += 1
                device_result(True)
                log_recovery("device-send")
                print(time.strftime("%H:%M:%S"), pack["frames"][0]["text"])
            except Exception as exc:
                queue_retry("summary", pack, now)
                HEALTH["failed"] += 1
                device_result(False, getattr(exc, "status", None))
                log_failure("device-send", f"[hub] aggregate notification failed: "
                            f"{type(exc).__name__}: {exc}")
        with LOCK:
            retry_pending(now)
        if now >= widget_due:
            push_widget(addins, state)
            widget_due = now + WIDGET_INTERVAL
        time.sleep(1)


if __name__ == "__main__":
    main()

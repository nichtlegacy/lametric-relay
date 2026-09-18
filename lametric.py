#!/usr/bin/env python3
"""Minimal LaMetric Local API client. Stdlib only.

  export LAMETRIC_HOST=<device ip> LAMETRIC_KEY=<device api key>
  ./lametric.py info
  ./lametric.py notify "Build ok" --icon ./assets/ok.png   # 8x8 PNG/GIF -> base64
  ./lametric.py notify "Plex" --icon assets/plex.png --sound positive1

Store icon IDs ("i120", "a2740") depend on the LaMetric cloud library; unknown IDs
render as placeholder symbols. Base64 is local and always reliable.
"""
import argparse, base64, json, os, ssl, sys, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("LAMETRIC_HOST", "")
KEY = os.environ.get("LAMETRIC_KEY", "")
CTX = ssl._create_unverified_context()  # the device has a self-signed certificate


class LametricError(RuntimeError):
    """Device error response.

    Deliberately an exception rather than sys.exit: as a library, this must not
    take down a running service.
    """

    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"HTTP {status}: {body}")


def call(path, method="GET", body=None):
    if not HOST:
        raise LametricError(0, "LAMETRIC_HOST is not set - find the IP in the "
                               "LaMetric app under Device Settings")
    req = urllib.request.Request(
        f"https://{HOST}:4343/api/v2" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": "Basic " + base64.b64encode(f"dev:{KEY}".encode()).decode(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise LametricError(e.code, e.read().decode()[:300]) from None


def icon(spec):
    """Convert 'i120' / '120' to an icon ID, or a file path to a base64 data URI.

    Relative paths are anchored at the project root rather than the working
    directory. Otherwise `python3 /path/to/hub.py` run elsewhere would send the
    path as a store icon ID and the clock would silently show a placeholder.
    """
    path = spec if os.path.isabs(spec) else os.path.join(ROOT, spec)
    if os.path.isfile(path):
        mime = "gif" if path.lower().endswith(".gif") else "png"
        return f"data:image/{mime};base64," + base64.b64encode(open(path, "rb").read()).decode()
    if os.sep in spec or spec.lower().endswith((".png", ".gif")):
        # This looks like a path but is not a file. Passing it through as a store ID
        # would only show a placeholder, so report it once.
        print(f"[lametric] icon not found: {spec} - did scripts/fetch-icons.py run?",
              file=sys.stderr)
    return spec


def push(package, frames, token=None):
    """Write frames persistently to a push-indicator app.

    Local Basic Auth is the default. With --token, the same payload goes through
    the cloud if the clock does not accept the local widget/update path.
    """
    body = {"frames": [dict(f, index=i) for i, f in enumerate(frames)]}
    if token:
        req = urllib.request.Request(
            f"https://developer.lametric.com/api/v1/dev/widget/update/{package}/1",
            data=json.dumps(body).encode(), method="POST",
            headers={"X-Access-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    return call(f"/widget/update/{package}", "POST", body)


DIY_PACKAGE = "com.lametric.diy.devwidget"
CLOCK_PACKAGE = "com.lametric.clock"
_widgets = {}


def widget_id(package):
    """Read an app's widget UUID from the device.

    The UUID differs per device but appears in the app list, so configuring it
    manually would be unnecessary work. Cache the result; the list does not change
    during operation.
    """
    if package not in _widgets:
        widgets = (call("/device/apps").get(package) or {}).get("widgets") or {}
        if not widgets:
            raise LametricError(404, f"App {package} is not installed on the device")
        _widgets[package] = next(iter(widgets))
    return _widgets[package]


def forget_widgets():
    """Forget cached UUIDs.

    Reinstalling the DIY app changes its UUID; without this, the hub would push to
    a dead address until it restarted.
    """
    _widgets.clear()


def diy():
    """Return the path for local pushes to the "My Data DIY" app."""
    return f"{DIY_PACKAGE}/{widget_id(DIY_PACKAGE)}"


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    w = sub.add_parser("push", help="write persistent frames to a push app")
    w.add_argument("--package", default=None, help="default: My Data DIY on this clock")
    w.add_argument("frames", nargs="+", metavar="TEXT[:ICON]",
                   help='for example, "73%%:assets/plex.png" "Build ok"')
    w.add_argument("--token", help="use the cloud instead of the local device")
    n = sub.add_parser("notify")
    n.add_argument("text")
    n.add_argument("--icon", default=None)
    n.add_argument("--sound", default=None)       # sound ID (cat, alarm1, ...) or MP3 URL
    n.add_argument("--lifetime", type=int, default=None, help="display duration in ms")
    n.add_argument("--priority", default="info")  # info | warning | critical
    n.add_argument("--cycles", type=int, default=1)  # 0 = until dismissed manually
    n.add_argument("--icon-type", default="none", choices=["none", "info", "alert"],
                   help="indicator symbol before the notification")

    a = p.parse_args()
    if not KEY:
        sys.exit("LAMETRIC_KEY is not set (developer.lametric.com -> My Devices)")

    if a.cmd == "push":
        frames = []
        for f in a.frames:
            text, _, ic = f.partition(":")
            frames.append({"text": text, "icon": icon(ic)} if ic else {"text": text})
        print(push(a.package or diy(), frames, a.token))
        return

    if a.cmd == "info":
        d = call("/device")
        print(json.dumps(d, indent=2))
        # sa8 = TIME 2022+, supports LMSP pixel streaming. LM 37X8 = legacy, REST only.
        print(f"\n-> model={d.get('model')}  streaming={'yes' if d.get('model') == 'sa8' else 'probably no'}")
        return

    frame = {"text": a.text}
    if a.icon:
        frame["icon"] = icon(a.icon)
    model = {"frames": [frame], "cycles": a.cycles}
    if a.sound:
        # Custom MP3 URLs work from API 2.3.0 onward, with a fallback if download fails.
        model["sound"] = ({"url": a.sound, "type": "mp3",
                           "fallback": {"category": "notifications", "id": "notification"}}
                          if a.sound.startswith("http")
                          else {"category": "alarms" if a.sound.startswith("alarm") else "notifications",
                                "id": a.sound})
    print(call("/device/notifications", "POST",
               {"priority": a.priority, "icon_type": a.icon_type, "model": model}
               | ({"lifeTime": a.lifetime} if a.lifetime else {})))


if __name__ == "__main__":
    try:
        main()
    except LametricError as e:
        sys.exit(str(e))

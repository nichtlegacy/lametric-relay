"""Generic message endpoint: anything that needs to write to the clock.

Unlike kuma or forgejo, this add-in does not expect a third-party data structure; the
sender determines the content. Scripts and projects can send a message without a
dedicated add-in:

    curl -X POST http://<hub-host>:8099/hook/say \
      -H 'Content-Type: application/json' \
      -d '{"text": "Backup complete", "icon": "ok"}'

From Home Assistant, in `configuration.yaml`:

    rest_command:
      lametric:
        url: "http://<hub-host>:8099/hook/say"
        method: POST
        content_type: "application/json"
        payload: >-
          {"text": "{{ text }}", "icon": "{{ icon | default('hass') }}",
           "priority": "{{ priority | default('info') }}"}

    action:
      - service: rest_command.lametric
        data:
          text: "Washing machine is done"
          icon: hass

Fields: `text` is required, `icon` is a filename without an extension from `assets/`,
and `priority` is info, warning, or critical. Unknown icons fall back to the default
symbol so a typo does not discard the notification.
"""
import os

import lametric

NAME = "say"
ICON = os.environ.get("SAY_ICON", "assets/hass.png")
ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
PRIORITIES = ("info", "warning", "critical")


def resolve(name):
    """Resolve an icon short name from assets/, or use the default symbol."""
    if not name:
        return ICON
    if os.path.sep in str(name) or str(name).endswith((".png", ".gif")):
        return name if os.path.isfile(name) else ICON
    for ext in (".png", ".gif"):
        path = os.path.join(ASSETS, f"{name}{ext}")
        if os.path.isfile(path):
            return path
    return ICON


def hook(payload, headers, state):
    text = str(payload.get("text") or "").strip()
    if not text:
        return []   # nothing to display without text

    priority = str(payload.get("priority") or "info").lower()
    if priority not in PRIORITIES:
        priority = "info"

    event = {"frames": [{"icon": lametric.icon(resolve(payload.get("icon"))), "text": text}],
             "priority": priority}
    if priority == "critical":
        event["cycles"] = 2
    return [event]

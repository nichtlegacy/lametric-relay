"""Uptime Kuma webhook.

In Uptime Kuma: Settings -> Notifications -> Setup Notification
  Notification Type: Webhook
  Post URL:          http://<hub-host>:8099/hook/kuma
  Content Type:      application/json

Enable it for the monitors that should appear on the clock (or use
"Default enabled" for all monitors).

Kuma marks only state changes as `important`. The add-in relies on that: it does not
repeat notifications while a service remains down, so no custom debouncing is needed.
"""
import os

import lametric

NAME = "kuma"
ICON = os.environ.get("KUMA_ICON", "assets/kuma.png")
ICON_DOWN = os.environ.get("KUMA_ICON_DOWN", "assets/kuma_down.png")

DOWN, UP, PENDING, MAINTENANCE = 0, 1, 2, 3


def hook(payload, headers, state):
    beat = payload.get("heartbeat") or {}
    if not beat:
        return []   # Test messages from the Kuma UI have no heartbeat

    # Kuma sets `important` only when the state changes. Everything else is noise.
    if not beat.get("important"):
        return []

    name = (payload.get("monitor") or {}).get("name") or "Service"
    status = beat.get("status")

    if status == DOWN:
        return [{"frames": [{"icon": lametric.icon(ICON_DOWN), "text": f"{name} down"}],
                 "priority": "critical", "cycles": 2}]
    if status == UP:
        return [{"frames": [{"icon": lametric.icon(ICON), "text": f"{name} up"}]}]
    return []   # Pending and maintenance states are not worth reporting

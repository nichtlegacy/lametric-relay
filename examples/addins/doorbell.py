"""Minimal polling and webhook add-in. Copy this file into addins/ to load it."""
import os

import lametric

NAME = "doorbell"
INTERVAL = 60
ICON = os.environ.get("DOORBELL_ICON", "assets/ok.png")


def poll(state):
    """Replace the input with a real sensor; report only a new press."""
    pressed = False
    previous = state.get("pressed", False)
    state["pressed"] = pressed
    return [_event()] if pressed and not previous else []


def hook(payload, headers, state):
    """POST {"pressed": true} to /hook/doorbell."""
    return [_event()] if payload.get("pressed") is True else []


def _event():
    return {"frames": [{"icon": lametric.icon(ICON), "text": "Doorbell"}]}

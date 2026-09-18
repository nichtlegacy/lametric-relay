"""Forgejo webhook.

Repo (or organization) -> Settings -> Webhooks -> Forgejo
  Target URL:    http://<hub-host>:8099/hook/forgejo
  HTTP Method:   POST
  Content Type:  application/json
  Trigger On:    Custom Events -> Push, Action Run Failure, Action Run Success
                 (or simply "All Events")

The event type is provided in the `X-Forgejo-Event` or `X-Gitea-Event` header; the
body alone is ambiguous. If the header is missing, the event is inferred from the
body as a best effort.
"""
import os

import lametric

NAME = "forgejo"
# The LaMetric library has no Forgejo icon (0 hits among 23,609), but it has an
# Octocat set in white/red/green that works like a traffic light for action runs.
ICON = os.environ.get("FORGEJO_ICON", "assets/git.png")
ICON_FAIL = os.environ.get("FORGEJO_ICON_FAIL", "assets/git_fail.png")
ICON_OK = os.environ.get("FORGEJO_ICON_OK", "assets/git_ok.png")
FAILED = ("failure", "failed", "cancelled", "canceled", "timed_out", "error")
PASSED = ("success", "succeeded", "passed")


def _repo(payload):
    repository = payload.get("repository") or (payload.get("run") or {}).get("repository") or {}
    return repository.get("name") or "repo"


def _event(payload, headers):
    """Read the event type from the header, or infer it from the body shape."""
    ev = (headers or {}).get("x-forgejo-event") or (headers or {}).get("x-gitea-event")
    if ev:
        return ev.lower()
    if payload.get("action_run") or payload.get("workflow_run") or payload.get("run"):
        return "action_run"
    if payload.get("commits") is not None or payload.get("ref"):
        return "push"
    return ""


def _conclusion(payload):
    run = payload.get("action_run") or payload.get("workflow_run") or payload.get("run") or {}
    for key in ("conclusion", "status", "state"):
        val = run.get(key) or payload.get(key)
        if val:
            return str(val).lower()
    return ""


def hook(payload, headers, state):
    event = _event(payload, headers)
    repo = _repo(payload)

    if "run" in event or payload.get("action_run") or payload.get("workflow_run") or payload.get("run"):
        result = _conclusion(payload)
        if result in FAILED:
            return [{"frames": [{"icon": lametric.icon(ICON_FAIL), "text": f"{repo} failed"}],
                     "priority": "critical", "cycles": 2}]
        if result in PASSED:
            return [{"frames": [{"icon": lametric.icon(ICON_OK), "text": f"{repo} succeeded"}]}]
        return []   # still running or unknown; no notification yet

    if event == "push":
        n = len(payload.get("commits") or [])
        branch = str(payload.get("ref") or "").rsplit("/", 1)[-1]
        text = f"{repo} +{n}" + (f" {branch}" if branch and branch != "main" else "")
        return [{"frames": [{"icon": lametric.icon(ICON), "text": text}]}]

    return []

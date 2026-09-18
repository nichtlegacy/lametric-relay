# Writing an add-in

An add-in is one Python file in `addins/`. The hub discovers it automatically; there is
no registry, package metadata, base class or third-party plugin framework.

Start by copying [`examples/addins/doorbell.py`](../examples/addins/doorbell.py) into
`addins/` and renaming it.

## The contract

Only `NAME` is required. Implement the functions your source needs:

```python
NAME = "example"
INTERVAL = 60

def configured():
    return True

def poll(state):
    return []

def hook(payload, headers, state):
    return []

def widget(state):
    return []
```

| Member | Purpose |
| --- | --- |
| `NAME` | Add-in name and webhook path |
| `INTERVAL` | Seconds between calls to `poll`; omit it for webhook-only add-ins |
| `configured()` | Optional readiness check; returning `False` skips the add-in |
| `poll(state)` | Fetch or inspect a source and return zero or more events |
| `hook(payload, headers, state)` | Handle `POST /hook/<NAME>` and return events |
| `widget(state)` | Return frames for the shared DIY widget |

All functions are synchronous. The hub isolates exceptions per add-in, logs the failure
and continues running the others.

## Events and frames

`poll` and `hook` return a list of event dictionaries:

```python
return [{
    "frames": [{"icon": lametric.icon("assets/ok.png"), "text": "Doorbell"}],
    "priority": "info",
    "cycles": 1,
}]
```

`frames` is required. `priority` defaults to `info`, and `cycles` defaults to `1`.
Frames use the LaMetric notification model:

```python
{"text": "Build passed", "icon": lametric.icon("assets/ok.png")}
{"text": "5H 73%", "goalData": {"start": 0, "current": 73, "end": 100, "unit": ""}}
{"chartData": [2, 4, 3, 7, 5]}
```

`text` and `goalData` appear together only when `unit` is empty. With a non-empty unit,
the device renders the bar alone and appends the label to the number.

Use `warning` or `critical` only when the source truly warrants interruption. Quiet
hours and rate limiting are hub policy; add-ins should not reimplement either.

## State and edge detection

`state` is a mutable dictionary dedicated to the current add-in. The hub persists it in
`state.json` after each run.

Pollers must compare the new value with stored state. Otherwise the same condition is
sent every interval:

```python
def poll(state):
    current = read_status()
    previous = state.get("status")
    state["status"] = current
    if previous is None or previous == current:
        return []
    return [{"frames": [{"text": f"Service {current}"}]}]
```

For percentage thresholds and reset detection, reuse [`quota.py`](../quota.py) or the
shared quota behaviour in [`addins/_shared.py`](../addins/_shared.py).

## Polling add-ins

Set `INTERVAL` and implement `poll`. Network calls need a finite timeout so one stalled
source cannot hold its scheduler thread forever. Credentials belong in environment
variables, and `configured()` should return `False` when required values are missing.

[`codex.py`](../addins/codex.py) is the complete polling example. It uses stdlib HTTP,
declares its required configuration and emits only quota edges.

## Webhook add-ins

Implement `hook(payload, headers, state)`. `payload` is decoded JSON and header names
are lowercase. The endpoint is:

```text
POST http://<hub-host>:8099/hook/<NAME>
```

Return `[]` for valid but irrelevant input. While wiring an unfamiliar sender, set
`HOOK_DUMP=/tmp/lametric-hooks.jsonl` and inspect the real payload instead of guessing.

[`forgejo.py`](../addins/forgejo.py) shows header-based event detection;
[`say.py`](../addins/say.py) shows the smallest generic webhook.

## Widget frames

`widget(state)` returns frames, not events. The hub combines frames from all add-ins and
pushes them to the installed **My Data DIY** app every `WIDGET_INTERVAL` seconds.

Return `[]` when there is nothing useful to show. Widget UUIDs are discovered from the
device and must not be hard-coded.

## Test it

First check discovery:

```bash
./hub.py --list
./hub.py --once
```

Then add one small executable check under `tests/`. Existing tests are plain scripts
with `assert`; import [`tests/context.py`](../tests/context.py) first so they run from
any working directory. Stub network or device calls and exercise the event edge that
would regress most easily.

Run all checks with:

```bash
for test in tests/test_*.py; do LAMETRIC_KEY=test python3 "$test"; done
```

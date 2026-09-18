# Configuration and operations

Copy [`.env.example`](../.env.example) to `.env`. Only the device address and API key
are required; the rest opt into sources or tune behaviour.

## Device and hub

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAMETRIC_HOST` | not set | device IP on the LAN, required |
| `LAMETRIC_KEY` | not set | device API key, required |
| `HOOK_PORT` | `8099` | webhook and health listener |
| `HOOK_DUMP` | not set | append incoming webhooks as JSONL |
| `HOOK_MAX_BODY` | `262144` | reject webhook bodies larger than this, in bytes |
| `STATE_FILE` | `./state.json` | persisted per-add-in state |
| `ADDINS` | all | comma-separated allow-list |
| `ADDIN_DIR` | `./addins` | add-in directory |
| `WIDGET_INTERVAL` | `300` | DIY widget refresh interval in seconds |
| `TZ` | host default | timezone used for quiet hours; containers run on UTC |

## Notification policy

| Variable | Default | Purpose |
| --- | --- | --- |
| `OVERRIDE_SCREENSAVER` | `1` | promote outgoing notifications so they appear during the device screensaver |
| `QUIET_HOURS` | empty | hub-side mute schedule; empty never mutes |
| `QUIET_ALLOW_CRITICAL` | `0` | allow genuinely critical events during quiet hours |
| `RATE_MAX` | `4` | maximum notifications per rate window, floored at 1 |
| `RATE_WINDOW` | `60` | rate window in seconds |

Excess events are held and later collapsed into one summary frame. Its icon and
priority come from the most urgent held event.

## Sources and clock face

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEX_LB_URL` | not set | Codex-LB endpoint; unset disables `codex` |
| `CODEX_LB_PASSWORD` | not set | Codex-LB dashboard password |
| `CODEX_INTERVAL` | `120` | Codex poll interval |
| `AIUSAGE_DB` | not set | private `ai-usage-insights` delivery database; unset disables `claude` |
| `CLAUDE_INTERVAL` | `120` | Claude poll interval |
| `CLAUDE_MAX_AGE` | `1800` | reject older quota snapshots |
| `PLEX_DEDUP` | `1800` | playback notification window per user and title |
| `USER_MAPPING` | `assets/user_mapping.json` | login-to-display-name mapping |
| `USER_MAPPING_FILE` | `./assets/user_mapping.json` | host path Compose mounts over it |
| `AIUSAGE_DIR` | not set | host directory Compose mounts at `/aiusage`; must be absolute |
| `PLEX_WATCHER` | not set | display name whose stream drives the face; empty means any |
| `CLOCKFACE` | `0` | set to `1` to allow clock-face changes |
| `CLOCKFACE_INTERVAL` | `60` | face decision interval |
| `CLOCKFACE_ACTIVE` | `600` | source activity window |
| `CLOCKFACE_HOLD` | `600` | minimum hold for a live source |
| `CLOCKFACE_IDLE` | `1800` | idle hold before returning to default |
| `CLOCKFACE_PLEX_MAX` | `14400` | fallback when no playback-stop event arrives |
| `CLOCKFACE_PRIORITY` | `plex` | comma-separated sources that preempt others |
| `CLOCK_ICON_DEFAULT` | `assets/clock_default.gif` | fallback clock icon |

Clock-face changes are opt-in because the device API cannot return the previous custom
face. Read [Device notes](device-notes.md#clock-face-action) before setting
`CLOCKFACE=1`.

## Network boundary

The webhook listener does not authenticate requests. It rejects bodies larger than
`HOOK_MAX_BODY`, but anything that can reach the port can write to the clock. Expose port
`8099` only on a trusted LAN. Do not forward it from a router or publish it directly to
the internet. Remote senders should connect through a VPN or an authenticated reverse
proxy.

`HOOK_DUMP` writes complete request headers and payloads. Treat that file as sensitive,
restrict its permissions and remove it after debugging.

Each add-in also exposes environment variables for its icon paths. Their defaults are
defined next to the implementation in [`addins/`](../addins/).

Display-name mappings may contain personal data and are intentionally ignored by git.
Create `assets/user_mapping.json` from
[`assets/user_mapping.example.json`](../assets/user_mapping.example.json).

## Agent hook

[`scripts/agent-hook.py`](../scripts/agent-hook.py) can send completed Claude Code and
Codex runs through the `say` add-in. It exits successfully even when the hub is down, so
notification trouble cannot block an agent session.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAMETRIC_HUB` | `http://127.0.0.1:8099` | hub URL used by the hook |
| `AGENT_MIN_SECONDS` | `120` | minimum reported run time |
| `CLAUDE_PROJECTS` | `~/.claude/projects` | transcript fallback for Claude Code |
| `CODEX_SESSIONS` | `~/.codex/sessions` | rollout fallback for Codex |

The script docstring contains the current hook commands. Use `done --tool claude` or
`done --tool codex` at the tool's completion event.

## Quiet hours

Quiet hours are off by default. A schedule looks like this:

```dotenv
QUIET_HOURS="mo-fr 00:00-09:00; sa,so 03:00-11:00"
QUIET_ALLOW_CRITICAL=0
OVERRIDE_SCREENSAVER=1
```

- Separate rules with `;`, days with `,`, and ranges with `-`.
- Day tokens may be German (`mo di mi do fr sa so`) or English
  (`mo tu we th fr sa su`).
- Omit days for a daily rule: `23:00-07:00`.
- Overnight windows belong to the day on which they start.
- Day ranges may wrap the weekend: `fr-mo`.
- End times are exclusive.

The hub applies `TZ` itself so the schedule remains local even when the host runs UTC.

## Health and metrics

`GET /healthz` returns JSON with uptime, loaded add-ins, counters, pending summaries,
add-in errors, best-effort retry state and the current quiet-hours state. A failed
notification is retried once after one minute, and discarded if that retry fails too or
if five minutes pass before it gets one, so an unplugged clock does not receive stale
events when it returns. The in-memory queue holds
at most 20 events and is cleared by a restart. It returns HTTP 503 after a device call
fails and until a later call succeeds.

`GET /metrics` exposes Prometheus text metrics for sent, muted and rate-limited events,
device state, uptime and per-add-in errors.

```bash
curl http://<hub-host>:8099/healthz
curl http://<hub-host>:8099/metrics
```

## Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

Compose rotates the container log at 5 MB and keeps three files. Repeated failures are
also collapsed in-process to one summary every 15 minutes, followed by one recovery line.

The Compose service publishes `8099` and stores `state.json` in its
`<project>_lametric-state` named volume. It mounts the optional `AIUSAGE_DIR` and user
mapping read-only. `AIUSAGE_DIR` must be absolute because Compose does not expand `~`
inside volume definitions.

## systemd user service

[`deploy/lametric-relay.service`](../deploy/lametric-relay.service) contains the service
definition. Adapt its absolute paths before installing it:

```bash
cp deploy/lametric-relay.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lametric-relay
journalctl --user -u lametric-relay -f
```

## Troubleshooting

**Nothing appears and the log is quiet.** Check `state.json`; the same Plex user and
title remain silent for `PLEX_DEDUP` seconds.

**Only critical notifications are allowed.** The device screensaver is active. Keep
`OVERRIDE_SCREENSAVER=1` if notifications should remain visible outside hub quiet
hours.

**Claude quota is stale.** Check the private `ai-usage-insights` collector before
raising `CLAUDE_MAX_AGE`.

**A webhook arrives but emits nothing.** Set `HOOK_DUMP`, send one test and inspect the
actual headers and payload.

**An add-in disappeared from `--list`.** Its import failed or `configured()` returned
`False`; check stderr for the skip reason or traceback.

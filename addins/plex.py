"""Plex through a Tautulli webhook.

Tautulli: Settings -> Notification Agents -> Webhook
  Webhook URL:  http://<hub-host>:8099/hook/plex
  Method:       POST

Enter the JSON body under "Data" for each trigger (Tautulli builds the body itself,
so each trigger needs its own body):

  Playback Start   {"event": "start", "user": "{username}", "title": "{title}",
                    "show": "{show_name}", "media_type": "{media_type}",
                    "season": "{season_num}", "episode": "{episode_num}",
                    "year": "{year}"}
  Playback Stop    {"event": "stop", "user": "{username}"}
  Plex Server Down {"event": "server_down"}
  Plex Server Up   {"event": "server_up"}

Plex logins are mapped to display names through `assets/user_mapping.json`.

For series, the series title is shown instead of the episode. `{full_title}` returns
"Hannibal - Suite for Harpsichord", but the desired display is "Hannibal". Tautulli
therefore sends the title, series name, and media type separately, and the add-in
selects the appropriate value.

For debouncing, the same user and title are reported at most once every DEDUP
seconds. A different title is always reported: switching means something new has
started, which is exactly what should be visible.
"""
import os, time

import lametric
import names

NAME = "plex"
ICON = os.environ.get("PLEX_ICON", "assets/plex.png")
ICON_DOWN = os.environ.get("PLEX_ICON_DOWN", "assets/plex_down.png")
DEDUP = int(os.environ.get("PLEX_DEDUP", "1800"))   # 30 minutes
# Display names instead of Plex logins. The mapping is mounted at runtime so private
# account names never enter the repository or image.
NAMES = names.load("plex", "jellyfin")
# For these media types, use the parent title: the series rather than the episode,
# or the album/artist rather than the individual track.
GROUPED = ("episode", "track", "show", "season")
FORGET = 24 * 3600                                  # prune debounce entries after 24 hours


def _prune(seen, now):
    """Remove old entries so state.json does not grow indefinitely."""
    for key in [k for k, ts in seen.items() if now - ts > FORGET]:
        del seen[key]


def media_title(payload):
    """Return a series title with episode number or a film title with year."""
    media_type = str(payload.get("media_type") or "").lower()
    show = str(payload.get("show") or "").strip()
    title = str(payload.get("title") or "").strip()
    if media_type in GROUPED and show:
        title = show
    # If the media type is missing, use the "Series - Episode" form as a fallback.
    if not title:
        return show or "Plex"
    if not show and " - " in title and payload.get("media_type") is None:
        title = title.split(" - ", 1)[0].strip()
    if media_type == "episode":
        season = str(payload.get("season") or "").strip()
        episode = str(payload.get("episode") or "").strip()
        if season and episode:
            return f"{title} · S{season.zfill(2)}E{episode.zfill(2)}"
    if media_type == "movie" and str(payload.get("year") or "").strip():
        return f"{title} ({str(payload['year']).strip()})"
    return title


def hook(payload, headers, state, now=None):
    now = time.time() if now is None else now
    event = str(payload.get("event") or "").lower()

    if event in ("server_down", "down"):
        return [{"frames": [{"icon": lametric.icon(ICON_DOWN), "text": "Plex offline"}],
                 "priority": "critical", "cycles": 2}]

    if event in ("server_up", "up"):
        return [{"frames": [{"icon": lametric.icon(ICON), "text": "Plex online"}]}]

    if event in ("stop", "pause", "playback_stop"):
        # No notification is needed, but clear the state; otherwise the clockface
        # add-in would treat the stream as active indefinitely.
        #
        # Only clear state when the stopping user is the one recorded as active:
        # with multiple concurrent streams, a stop from another user would otherwise
        # clear this session. Without a user in the payload, always clear the state.
        who = payload.get("user")
        if who is None or names.person(str(who).strip(), NAMES) == state.get("playing_user"):
            for key in ("playing", "playing_user", "playing_at"):
                state.pop(key, None)
        return []

    if event not in ("start", "play", "playback_start"):
        return []

    user = names.person(str(payload.get("user") or "?").strip(), NAMES)
    title = media_title(payload)
    seen = state.setdefault("seen", {})
    _prune(seen, now)

    key = f"{user}|{title}"
    last = seen.get(key)
    # The clockface add-in uses this user and timestamp to decide whether to put the
    # Plex logo on the clock face, with a timeout in case no stop event arrives.
    state["playing"], state["playing_user"], state["playing_at"] = title, user, now
    if last is not None and now - last < DEDUP:
        # Deliberately do not refresh the timestamp: measure from the last
        # notification, not the last start. Otherwise every suppressed start would
        # move the window and another notification would never arrive.
        return []
    seen[key] = now

    text = f"{user}: {title}" if user != "?" else title
    return [{"frames": [{"icon": lametric.icon(ICON), "text": text}]}]


def widget(state):
    if not state.get("playing"):
        return []
    return [{"icon": lametric.icon(ICON), "text": str(state["playing"])}]

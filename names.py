#!/usr/bin/env python3
"""Display names instead of login names.

Plex and PSN both provide technical identifiers that are hard to read at eight
pixels high. The shared mapping lives in `assets/user_mapping.json`, with one
section per service.
"""
import json
import os

MAPPING = os.environ.get(
    "USER_MAPPING",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "user_mapping.json"))


def load(*sections, path=None):
    """Merge sections into a lower-case keyed lookup table.

    Services may report the same name with different casing; Tautulli, for example,
    switches between `SomePlexLogin` and `someplexlogin` depending on the trigger.
    """
    try:
        with open(path or MAPPING) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    table = {}
    for section in sections:
        for login, clear in (data.get(section) or {}).items():
            table.setdefault(login.strip().lower(), clear)
    return table


def person(user, table):
    """Map a login to its display name. Keep unknown users visible."""
    return table.get(str(user).strip().lower(), user)

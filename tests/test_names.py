#!/usr/bin/env python3
"""Self-check for the display-name mapping shared by Plex and PSN.

The real `assets/user_mapping.json` contains people's names. It is not versioned or
available in CI, so this test uses temporary data.
"""

import context  # noqa: F401  - sets the import path and working directory

import json
import os
import tempfile

import names


def mapping(data):
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    json.dump(data, open(path, "w"))
    return path


p = mapping({"plex": {"SomeLogin": "Alex", "other.login": "Robin"},
             "jellyfin": {"somepsnid": "Alex"},
             "psn": {"otherpsnid": "Sam"}})

table = names.load("plex", "psn", path=p)
assert table.get("somelogin") == "Alex"           # from the Plex section
assert table.get("otherpsnid") == "Sam"           # from the PSN section

# Sections remain separate. An entry found only under PSN must not appear under Plex.
assert "otherpsnid" not in names.load("plex", path=p)

# Matching ignores case and surrounding whitespace because services vary their output.
assert names.person("  SomeLogin ", table) == "Alex"
assert names.person("somelogin", table) == "Alex"
# Unknown names remain unchanged.
assert names.person("unknown-user", table) == "unknown-user"

# The first section wins when both define the same login.
dup = mapping({"a": {"x": "First"}, "b": {"x": "Second"}})
assert names.load("a", "b", path=dup)["x"] == "First"

# Missing or invalid files produce an empty mapping, as they do in CI and images without
# a mounted mapping.
assert names.load("plex", path="/does/not/exist.json") == {}
bad = os.path.join(tempfile.mkdtemp(), "bad.json")
open(bad, "w").write("not json")
assert names.load("plex", path=bad) == {}

# The example file must contain the expected sections.
example = names.load("plex", "jellyfin", "psn",
                     path=os.path.join(context.ROOT, "assets", "user_mapping.example.json"))
assert example, "user_mapping.example.json is empty or missing"

print("ok")

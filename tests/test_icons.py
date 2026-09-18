#!/usr/bin/env python3
"""Self-check for the icon manifest.

The graphics are fetched from assets/icons.json rather than stored in the repository.
The manifest is the source of truth. If an entry is missing, an add-in silently uses a
path that does not exist and the clock shows a placeholder.
"""

import context  # noqa: F401  - sets the import path and working directory

import importlib.util
import json
import os
import re

MANIFEST = os.path.join(context.ROOT, "assets", "icons.json")
spec = json.load(open(MANIFEST))
icons = {k: v for k, v in spec.items() if not k.startswith("_")}

assert icons, "manifest is empty"

# Every entry needs a library ID so the fetcher can download it.
for name, entry in icons.items():
    assert isinstance(entry.get("id"), int), f"{name}: missing ID"
    assert entry.get("ext", "png") in ("png", "gif"), f"{name}: unknown extension"
    ops = [k for k in ("tint", "replace", "colorize") if k in entry]
    assert len(ops) <= 1, f"{name}: multiple color operations"

# Every default icon path in the code must exist in the manifest or be original,
# versioned artwork.
OWN = {"ok"}
referenced = set()
for folder in ("addins", "."):
    base = os.path.join(context.ROOT, folder)
    for fn in sorted(os.listdir(base)):
        if not fn.endswith(".py"):
            continue
        for path in re.findall(r'"assets/([\w.-]+)\.(?:png|gif)"', open(os.path.join(base, fn)).read()):
            referenced.add(path)

unknown = referenced - set(icons) - OWN
assert not unknown, f"used in code but not defined: {sorted(unknown)}"

# Original icons must exist because no script fetches them.
for name in OWN:
    assert os.path.isfile(os.path.join(context.ROOT, "assets", f"{name}.png")), name

# The fetch script must understand every manifest entry.
loader = importlib.util.spec_from_file_location(
    "fetch_icons", os.path.join(context.ROOT, "scripts", "fetch-icons.py"))
fetch = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fetch)
assert set(fetch.manifest()) == set(icons)
for name, entry in icons.items():
    assert fetch.path_for(name, entry).endswith(f"{name}.{entry.get('ext', 'png')}")

print("ok")

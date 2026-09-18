#!/usr/bin/env python3
"""Self-check the version.

The version is maintained manually and must match the newest Git tag; otherwise a
published image reports a different version from its tag.
"""

import context  # noqa: F401 - sets the import path and working directory

import os
import re
import subprocess
import sys

import version

assert re.fullmatch(r"\d+\.\d+\.\d+", version.__version__), version.__version__

# If tags exist, the newest must match the version. Without tags, nothing has been
# published yet and there is nothing to compare.
tags = subprocess.run(["git", "tag", "--list", "v*", "--sort=-v:refname"],
                      cwd=context.ROOT, capture_output=True, text=True)
newest = (tags.stdout or "").strip().splitlines()
if newest:
    assert newest[0] == f"v{version.__version__}", (newest[0], version.__version__)

# The hub must also report the version.
out = subprocess.run([sys.executable, os.path.join(context.ROOT, "hub.py"), "--version"],
                     capture_output=True, text=True,
                     env=dict(os.environ, LAMETRIC_KEY="test"))
assert version.__version__ in out.stdout + out.stderr, (out.stdout, out.stderr)

print("ok")

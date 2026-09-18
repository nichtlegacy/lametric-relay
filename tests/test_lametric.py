#!/usr/bin/env python3
"""Self-check for icon IDs and file-to-data-URI conversion."""

import context  # noqa: F401  - sets the import path and working directory

import base64, os, tempfile
import lametric

assert lametric.icon("i120") == "i120"
assert lametric.icon("./missing.png") == "./missing.png"

for ext, mime in (("png", "png"), ("gif", "gif")):
    p = os.path.join(tempfile.mkdtemp(), "x." + ext)
    open(p, "wb").write(b"\x89PNGfake")
    got = lametric.icon(p)
    assert got == f"data:image/{mime};base64," + base64.b64encode(b"\x89PNGfake").decode(), got

# Device errors must raise an exception. A sys.exit would terminate the hub thread and
# leave the client with a dropped connection.
assert issubclass(lametric.LametricError, Exception)
assert not issubclass(lametric.LametricError, SystemExit)
try:
    raise lametric.LametricError(429, "Too many")
except Exception as e:
    assert e.status == 429 and "429" in str(e)

print("ok")

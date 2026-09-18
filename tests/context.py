"""Set the import path and working directory for the tests.

Add-ins live in their own directory, while icon paths in the code and tests are relative
to the project root. Set both once here so the tests work from any directory.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for path in (ROOT, os.path.join(ROOT, "addins")):
    if path not in sys.path:
        sys.path.insert(0, path)

os.chdir(ROOT)

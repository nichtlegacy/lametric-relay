#!/usr/bin/env python3
"""Download icons from the LaMetric library as described in assets/icons.json.

The images are intentionally not kept in the repository: third parties uploaded
them to the community library, and some are trademarked logos. The manifest stores
only the IDs and recoloring instructions; this script creates the files from it.

  scripts/fetch-icons.py            # download missing icons
  scripts/fetch-icons.py --force    # download all icons again
  scripts/fetch-icons.py --check    # only check whether all are present

ImageMagick is required for recoloring, the same tool used by icon.py.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icon as icon_tool  # noqa: E402  - the tool originally used to create the icons

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
MANIFEST = os.path.join(ASSETS, "icons.json")
SOURCE = "https://developer.lametric.com/content/apps/icon_thumbs/{id}.{ext}"
IM = "magick" if subprocess.run(["which", "magick"], capture_output=True).returncode == 0 else "convert"


def manifest():
    with open(MANIFEST) as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def download(spec, target):
    url = SOURCE.format(id=spec["id"], ext=spec.get("ext", "png"))
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read()
    with open(target, "wb") as fh:
        fh.write(data)


def transform(spec, source, target):
    """Apply recoloring, or copy the source unchanged when no transform is set."""
    if spec.get("tint"):
        # Use icon.py instead of custom ImageMagick commands: it originally created
        # these icons, so the result remains consistent.
        icon_tool.convert(source, target, tint=spec["tint"])
    elif spec.get("replace"):
        r = spec["replace"]
        run([IM, source, "-fuzz", r.get("fuzz", "40%"),
             "-fill", r["to"], "-opaque", r["from"], "PNG32:" + target])
    elif spec.get("ramp"):
        # Keep the artwork's own shading and map it onto a black -> colour ramp,
        # so a gradient logo stays a gradient instead of a flat silhouette.
        run([IM, source, "-channel", "RGB", "+level-colors", "black," + spec["ramp"],
             "PNG32:" + target])
    elif spec.get("hue"):
        # Rotate the colour wheel so a multi-tone logo changes hue but keeps its
        # shading and its white details, which a flat tint would swallow.
        run([IM, source, "-modulate", f"100,{spec.get('saturate', 100)},{spec['hue']}",
             "PNG32:" + target])
    elif spec.get("colorize"):
        # Recolor every pixel while preserving transparency.
        run([IM, source, "-coalesce", "-fill", spec["colorize"], "-colorize", "100",
             "-background", "none", "-extent", "8x8", target])
    else:
        os.replace(source, target)
        return
    if os.path.exists(source):
        os.remove(source)


def run(cmd):
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode:
        sys.exit(f"ImageMagick: {result.stderr.decode()[:300]}")


def path_for(name, spec):
    return os.path.join(ASSETS, f"{name}.{spec.get('ext', 'png')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="re-download existing files")
    p.add_argument("--check", action="store_true", help="check only; do not download")
    p.add_argument("--into", default=ASSETS, help="destination directory (for tests)")
    a = p.parse_args()

    icons = manifest()
    missing = [n for n, s in icons.items()
               if not os.path.isfile(os.path.join(a.into, f"{n}.{s.get('ext', 'png')}"))]
    if a.check:
        print(f"{len(icons) - len(missing)}/{len(icons)} present"
              + (f", missing: {', '.join(sorted(missing))}" if missing else ""))
        return 1 if missing else 0

    os.makedirs(a.into, exist_ok=True)
    done = 0
    for name, spec in sorted(icons.items()):
        target = os.path.join(a.into, f"{name}.{spec.get('ext', 'png')}")
        if os.path.isfile(target) and not a.force:
            continue
        raw = target + ".src"
        download(spec, raw)
        transform(spec, raw, target)
        done += 1
        print(f"  {name:14} <- id {spec['id']}")
    print(f"{done} downloaded, {len(icons) - done} already present")
    return 0


if __name__ == "__main__":
    sys.exit(main())

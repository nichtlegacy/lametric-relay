#!/usr/bin/env python3
"""Convert any icon to an 8x8 PNG/GIF for LaMetric (left 8x8 RGB zone).

  ./icon.py plex                       # slug from dashboardicons.com
  ./icon.py https://.../foo.svg -o assets/foo.png
  ./icon.py ~/Downloads/logo.png --sharpen
  ./icon.py --preview assets/plex.png  # preview in the terminal

Uses ImageMagick (already installed) instead of Pillow. SVG/PNG/JPG/GIF in, 8x8 out.
"""
import argparse, os, subprocess, sys, tempfile, urllib.request, zlib, struct

CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/{}.svg"
IM = "magick" if subprocess.run(["which", "magick"], capture_output=True).returncode == 0 else "convert"


def fetch(src):
    """Resolve a slug, URL, or path to a local file path."""
    if os.path.isfile(src):
        return src
    url = src if src.startswith("http") else CDN.format(src)
    ext = os.path.splitext(url)[1] or ".svg"
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            tmp.write(r.read())
    except Exception as e:
        sys.exit(f"Download failed: {url}\n{e}")
    tmp.close()
    return tmp.name


def native_size(src):
    """Pixel size of the source, or None when it cannot be read."""
    r = subprocess.run([IM, src + ("[0]" if src.endswith(".gif") else ""),
                        "-format", "%w %h", "info:"], capture_output=True)
    try:
        w, h = r.stdout.decode().split()
        return int(w), int(h)
    except ValueError:
        return None


def convert(src, out, size=8, sharpen=False, keep_alpha=False, tint=None):
    # A source that is already the target size must not be trimmed and re-centred.
    # Trimming an empty top row and centring what is left moves the artwork down by
    # a pixel, which is how a recoloured icon ended up sitting a row off from the
    # untouched original it was made from.
    fitted = native_size(src) == (size, size)
    # Use high density to rasterize SVG cleanly before downscaling.
    cmd = [IM, "-background", "none", "-density", "384", src + ("[0]" if src.endswith(".gif") else "")]
    if not fitted:
        cmd += ["-trim", "+repage"] if not keep_alpha else []
        cmd += ["-resize", f"{size}x{size}", "-gravity", "center", "-extent", f"{size}x{size}"]
    if tint:
        # Monochrome logos (OpenAI and others) are black on transparent and disappear
        # when flattened onto black. Extract the silhouette and tint it instead.
        cmd += ["-alpha", "extract", "-threshold", "40%",
                "-background", tint, "-alpha", "shape",
                "-background", "black", "-flatten", "PNG32:" + out]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode:
            sys.exit(r.stderr.decode()[:400])
        return out
    if sharpen:
        # Increase contrast; thin shapes otherwise disappear at 8x8.
        cmd += ["-modulate", "100,160,100", "-sigmoidal-contrast", "4,50%"]
    if not keep_alpha:
        cmd += ["-background", "black", "-flatten"]  # LaMetric displays a black background
    cmd += ["PNG32:" + out]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        sys.exit(r.stderr.decode()[:400])
    return out


def read_png_rgba(path):
    """Convert an 8x8 PNG (RGB or RGBA) to a list of (r, g, b, a) tuples."""
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    i, w, h, idat = 8, 0, 0, b""
    while i < len(d):
        ln, typ = struct.unpack("!I", d[i:i + 4])[0], d[i + 4:i + 8]
        body = d[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack("!2I2B", body[:10])
            assert depth == 8 and ctype in (2, 6), f"expected 8-bit RGB/RGBA, got {depth}/{ctype}"
            nch = 4 if ctype == 6 else 3
        elif typ == b"IDAT":
            idat += body
        i += 12 + ln
    raw, px, stride = zlib.decompress(idat), [], w * nch
    prev = bytearray(stride)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):  # reverse the PNG filter
            a = line[x - nch] if x >= nch else 0
            b = prev[x]
            c = prev[x - nch] if x >= nch else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        px += [tuple(line[x:x + nch]) + ((255,) if nch == 3 else ()) for x in range(0, stride, nch)]
    return w, h, px


def preview(path):
    w, h, px = read_png_rgba(path)
    for y in range(h):
        print("".join(f"\033[38;2;{r};{g};{b}m██" if a > 40 else "\033[38;2;30;30;30m··"
                      for r, g, b, a in px[y * w:(y + 1) * w]) + "\033[0m")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("src", help="dashboard-icons slug, URL, or file")
    p.add_argument("-o", "--out")
    p.add_argument("--size", type=int, default=8)
    p.add_argument("--sharpen", action="store_true")
    p.add_argument("--tint", help="tint the silhouette, e.g. white or '#74aa9c'")
    p.add_argument("--alpha", action="store_true", help="keep transparency instead of flattening onto black")
    p.add_argument("--preview", action="store_true", help="src is already an 8x8 PNG; only preview it")
    a = p.parse_args()

    if a.preview:
        return preview(a.src)
    out = a.out or os.path.join("assets", os.path.splitext(os.path.basename(a.src))[0] + ".png")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    convert(fetch(a.src), out, a.size, a.sharpen, a.alpha, a.tint)
    print(out, os.path.getsize(out), "bytes")
    preview(out)


if __name__ == "__main__":
    main()

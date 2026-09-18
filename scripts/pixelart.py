#!/usr/bin/env python3
"""ASCII map -> 8x8 PNG. For icons that lose detail when downscaled.

  ./pixelart.py assets/claude.png '#D97757' <<'MAP'
  ...##...
  #..##..#
  MAP

'.' or ' ' = off; everything else = on. Lines shorter than 8 are padded.
An optional second color can be passed as the third argument; every character other
than '#' is then rendered in that color.
"""
import struct, sys, zlib


def png(pixels, w, h, out):
    raw = b"".join(b"\0" + b"".join(bytes(p) for p in pixels[y * w:(y + 1) * w]) for y in range(h))
    ch = lambda t, d: struct.pack("!I", len(d)) + t + d + struct.pack("!I", zlib.crc32(t + d))
    open(out, "wb").write(b"\x89PNG\r\n\x1a\n"
                          + ch(b"IHDR", struct.pack("!2I5B", w, h, 8, 2, 0, 0, 0))
                          + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def main():
    out = sys.argv[1]
    fg = rgb(sys.argv[2]) if len(sys.argv) > 2 else (255, 255, 255)
    fg2 = rgb(sys.argv[3]) if len(sys.argv) > 3 else fg
    rows = [r for r in sys.stdin.read().splitlines() if r.strip()]
    w = max(8, max(len(r) for r in rows))
    px = []
    for y in range(len(rows)):
        line = rows[y].ljust(w)
        px += [(0, 0, 0) if c in ". " else (fg if c == "#" else fg2) for c in line]
    png(px, w, len(rows), out)
    print(out, w, "x", len(rows))


if __name__ == "__main__":
    main()

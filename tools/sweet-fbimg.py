#!/usr/bin/env python3
"""Decode the kernel's FBIMG hex thumbnails into PNG files.

The kernel prints, for a few presented frames, one line per thumbnail row:

    FBIMG begin <frame> <width> <height> panel <pw> <ph>
    FBIMG row <frame> <y> <hex...>
    FBIMG end <frame>

Each hex pair is the luminance of one sample of the surface the panel is
scanning out, so the result is a small grayscale picture of the frame.
"""
import re
import struct
import sys
import zlib
from pathlib import Path

ROW = re.compile(r'FBIMG row (\d+) (\d+) ([0-9a-fA-F]+)')
BEGIN = re.compile(r'FBIMG begin (\d+) (\d+) (\d+)')


def write_png(path, width, height, gray):
    raw = b''.join(b'\x00' + bytes(gray[y * width:(y + 1) * width]) for y in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 9))
    png += chunk(b'IEND', b'')
    Path(path).write_bytes(png)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    text = Path(sys.argv[1]).read_text(errors='ignore')
    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)

    frames = {}
    sizes = {}
    for line in text.splitlines():
        m = BEGIN.search(line)
        if m:
            sizes[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
            continue
        m = ROW.search(line)
        if m:
            frame = int(m.group(1))
            rows = frames.setdefault(frame, {})
            rows[int(m.group(2))] = bytes.fromhex(m.group(3))

    if not frames:
        print('no FBIMG rows found')
        return 1
    for frame in sorted(frames):
        rows = frames[frame]
        width, height = sizes.get(frame, (80, len(rows)))
        if len(rows) != height:
            print('frame %d: %d of %d rows, skipped' % (frame, len(rows), height))
            continue
        gray = b''.join(rows[y] for y in range(height))
        path = outdir / ('frame%04d.png' % frame)
        write_png(path, width, height, gray)
        print('%s %dx%d' % (path, width, height))
    return 0


if __name__ == '__main__':
    sys.exit(main())

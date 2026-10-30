#!/usr/bin/env python3
"""Recover complete grayscale framebuffer thumbnails from a sweetfbshot log.

Usage: sweet-fbimage.py CONSOLE.txt OUTPUT_DIRECTORY
These show framebuffer RAM samples, not the physical panel's appearance.
Incomplete or malformed frames are reported and never emitted as images.
"""
import binascii
from pathlib import Path
import re
import struct
import sys
import zlib


def png(width, height, pixels):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data +
                struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff))
    rows = b''.join(b'\0' + pixels[y * width:(y + 1) * width] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def extract(console, output):
    output.mkdir(parents=True, exist_ok=True)
    frames, complete = {}, []
    for line in console.read_text(errors='replace').splitlines():
        begin = re.fullmatch(r'FBIMG begin (\d+) (\d+) (\d+) panel (\d+) (\d+)', line)
        if begin:
            frame, width, height, _, _ = map(int, begin.groups())
            if 0 < width <= 160 and 0 < height <= 320:
                frames[frame] = {'width': width, 'height': height, 'rows': {}}
            continue
        row = re.fullmatch(r'FBIMG row (\d+) (\d+) ([0-9a-f]+)', line)
        if row:
            frame, y = map(int, row.groups()[:2])
            f = frames.get(frame)
            if f and y < f['height'] and len(row[3]) == f['width'] * 2:
                f['rows'][y] = bytes.fromhex(row[3])
            continue
        end = re.fullmatch(r'FBIMG end (\d+)', line)
        if end:
            frame = int(end[1])
            f = frames.pop(frame, None)
            if f and len(f['rows']) == f['height']:
                pixels = b''.join(f['rows'][y] for y in range(f['height']))
                path = output / f'frame-{frame}.png'
                path.write_bytes(png(f['width'], f['height'], pixels))
                complete.append(path)
                print(path.resolve())
            else:
                print(f'frame {frame}: incomplete; no image emitted', file=sys.stderr)
    for frame in frames:
        print(f'frame {frame}: missing end; no image emitted', file=sys.stderr)
    return complete


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(0 if extract(Path(sys.argv[1]), Path(sys.argv[2])) else 1)

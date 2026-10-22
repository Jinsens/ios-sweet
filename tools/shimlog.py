#!/usr/bin/env python3
"""Read the shim's console log out of a boot partition, over adb.

The shim cannot show anything when the panel is dark, so it writes its
console text into the boot partition it was loaded from, 4 MiB in (always
past the boot image): "SWLG", a u32 length, then the text.

  tools/shimlog.py                 # boot_a, via adb (recovery must be up)
  tools/shimlog.py --part boot_b
  tools/shimlog.py --file dump.bin # parse a dd'd file instead

From a recovery shell the equivalent read is
  dd if=/dev/block/by-name/boot_a bs=4096 skip=1024 count=8 | tail -c +9
"""
import argparse
import struct
import subprocess
import sys

OFF_BLOCKS = 1024
BLOCKS = 8
BLOCK = 4096


def read_adb(part):
    cmd = ['adb', 'exec-out',
           f'dd if=/dev/block/by-name/{part} bs={BLOCK} skip={OFF_BLOCKS} count={BLOCKS} 2>/dev/null']
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f'adb failed: {r.stderr.decode(errors="replace").strip()}')
    return r.stdout


def parse(blob):
    if len(blob) < 8 or blob[:4] != b'SWLG':
        return None
    n, = struct.unpack_from('<I', blob, 4)
    return blob[8:8 + n].decode('utf-8', 'replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--part', default='boot_a')
    ap.add_argument('--file')
    ap.add_argument('--scan', action='store_true',
                    help='search the whole dump for SWLG (expdb tail)')
    a = ap.parse_args()

    blob = open(a.file, 'rb').read() if a.file else read_adb(a.part)
    text = None
    if a.scan:
        off = blob.find(b'SWLG')
        if off >= 0:
            print(f'SWLG at 0x{off:x}', file=sys.stderr)
            text = parse(blob[off:])
    else:
        text = parse(blob)
    if text is None:
        head = blob[:16].hex(' ')
        raise SystemExit(f'no shim log in {a.part} (first bytes: {head})')
    sys.stdout.write(text)
    if not text.endswith('\n'):
        sys.stdout.write('\n')


if __name__ == '__main__':
    main()

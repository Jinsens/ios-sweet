#!/usr/bin/env python3
"""Copy a SWEETXNU payload with one BRK for same-boot entry diagnostics.

No Mach-O, original payload, or device partition is modified. The probe's
EL1 vectors can report this BRK only before XNU replaces VBAR_EL1.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def insert_breakpoint(payload, offset=0):
    if len(payload) < 0x2a8 or payload[:8] != b'SWEETXNU':
        raise ValueError('invalid SWEETXNU payload')
    version, count = struct.unpack_from('<II', payload, 8)
    if version != 1 or not count or count > (len(payload) - 0x2a8) // 32:
        raise ValueError('invalid payload segment table')
    virt, entry = struct.unpack_from('<QQ', payload, 16)
    phys, = struct.unpack_from('<Q', payload, 56)
    target = entry + offset
    if offset < 0 or target > 0xffffffffffffffff or target & 3:
        raise ValueError('breakpoint must be an aligned nonnegative entry offset')
    matches = []
    for i in range(count):
        va, size, source, length = struct.unpack_from('<4Q', payload, 0x2a8 + i * 32)
        if source < 0x2a8 + count * 32 or source > len(payload) or length > len(payload) - source or length > size:
            raise ValueError('invalid payload segment bounds')
        if va <= target and target + 4 <= va + length:
            matches.append(source + target - va)
    if len(matches) != 1:
        raise ValueError('breakpoint must resolve to exactly one file-backed segment')
    at = matches[0]
    original, = struct.unpack_from('<I', payload, at)
    instruction = 0xd4200000 | (0x5357 << 5)  # BRK #0x5357
    if original == instruction:
        raise ValueError('breakpoint already present')
    result = bytearray(payload)
    struct.pack_into('<I', result, at, instruction)
    return bytes(result), {
        'entry_offset': offset, 'target_va': hex(target),
        'target_pa': hex(target - virt + phys), 'payload_offset': at,
        'original_instruction': hex(original), 'instruction': hex(instruction),
        'expected_esr': '0xf2005357',
        'source_sha256': hashlib.sha256(payload).hexdigest(),
        'payload_sha256': hashlib.sha256(result).hexdigest(),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('payload', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--offset', type=lambda value: int(value, 0), default=0,
                    help='byte offset from entry (default: first instruction)')
    args = ap.parse_args()
    try:
        if args.payload.resolve() == args.out.resolve() or args.out.exists():
            raise ValueError('output must be a new file, separate from the source')
        patched, manifest = insert_breakpoint(args.payload.read_bytes(), args.offset)
        with args.out.open('xb') as stream:
            stream.write(patched)
    except (ValueError, OSError) as error:
        ap.error(str(error))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()

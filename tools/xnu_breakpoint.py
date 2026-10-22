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


def payload_location(payload, offset=0, length=4):
    requested_length = length
    if len(payload) < 0x2a8 or payload[:8] != b'SWEETXNU':
        raise ValueError('invalid SWEETXNU payload')
    version, count = struct.unpack_from('<II', payload, 8)
    if version != 1 or not count or count > (len(payload) - 0x2a8) // 32:
        raise ValueError('invalid payload segment table')
    virt, entry = struct.unpack_from('<QQ', payload, 16)
    phys, = struct.unpack_from('<Q', payload, 56)
    target = entry + offset
    if target < 0 or target + requested_length > 0x10000000000000000 or target & 3:
        raise ValueError('target must be an aligned address in the payload')
    matches = []
    for i in range(count):
        va, size, source, length = struct.unpack_from('<4Q', payload, 0x2a8 + i * 32)
        if source < 0x2a8 + count * 32 or source > len(payload) or length > len(payload) - source or length > size:
            raise ValueError('invalid payload segment bounds')
        if va <= target and target + requested_length <= va + length:
            matches.append(source + target - va)
    if len(matches) != 1:
        raise ValueError('breakpoint must resolve to exactly one file-backed segment')
    return matches[0], target, target - virt + phys


def early_vector_pa(payload, offset):
    at, _, pa = payload_location(payload, offset, 2048)
    if pa & 2047:
        raise ValueError('early vector table must be 2048-byte aligned')
    if any(struct.unpack_from('<I', payload, at + slot * 128)[0] != 0x14000000
           for slot in range(16)):
        raise ValueError('expected early exception slots to begin with B .')
    phys, size = struct.unpack_from('<QQ', payload, 56)
    if pa < phys or pa + 2048 > phys + size:
        raise ValueError('early vector table outside declared physical memory')
    return pa


def high_vector_pa(payload, offset):
    at, _, pa = payload_location(payload, offset, 2048)
    if pa & 2047:
        raise ValueError('high vector table must be 2048-byte aligned')
    for slot in range(16):
        words = struct.unpack_from('<32I', payload, at + slot * 128)
        if (words[0] & 0xfc000000 != 0x14000000 and words[0] != 0xd503201f) or any(
                word != 0xd503201f for word in words[1:]):
            raise ValueError('expected branch/NOP padded high exception slots')
    phys, size = struct.unpack_from('<QQ', payload, 56)
    if pa < phys or pa + 2048 > phys + size:
        raise ValueError('high vector table outside declared physical memory')
    return pa


def bootstrap_clean_range(payload, clean_limit=None):
    # Same layout as loader.c: kernel, ADT, then one 16K boot_args page.
    payload_location(payload)
    virt, _, _, adt_size, kernel_end, phys, size = struct.unpack_from('<7Q', payload, 16)
    adt_pa = kernel_end - virt + phys
    end = ((adt_pa + adt_size + 0x3fff) & ~0x3fff) + 0x4000
    if kernel_end < virt or phys & 0x3fff or not phys < end <= phys + size or end > 1 << 48:
        raise ValueError('invalid bootstrap cache-clean range')
    if clean_limit is not None:
        if clean_limit & 0x3fff or not end <= clean_limit <= phys + size or clean_limit > 1 << 48:
            raise ValueError('cache-clean limit must cover bootstrap data and stay in managed RAM')
        end = clean_limit
    return phys, end


def insert_breakpoint(payload, offset=0):
    at, target, pa = payload_location(payload, offset)
    original, = struct.unpack_from('<I', payload, at)
    instruction = 0xd4200000 | (0x5357 << 5)  # BRK #0x5357
    if original == instruction:
        raise ValueError('breakpoint already present')
    result = bytearray(payload)
    struct.pack_into('<I', result, at, instruction)
    return bytes(result), {
        'entry_offset': offset, 'target_va': hex(target),
        'target_pa': hex(pa), 'payload_offset': at,
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

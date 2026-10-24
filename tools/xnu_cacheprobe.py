#!/usr/bin/env python3
"""Inject a per-type cache-geometry proof into a SWEETXNU payload.

The checkpoint (`cbz w0` after `bl _vfs_mountroot`) is redirected to an
unused early-vector slot. The cave reads cpuid_cache_info[CLUSTER_TYPE_E]
and [CLUSTER_TYPE_P], packs each type's private L2 and L3 sizes into one
64-bit value, and executes BRK #0x5357.

Banner x0 = (E.c_priv_l2size << 32) | E.c_l3size
Banner x16 = (P.c_priv_l2size << 32) | P.c_l3size

The kernel must be built with the cache_info_t private-L2/L3 fields and the
matisse ADT must mark the A510 cores E and the A710/X2 cores P.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from xnu_breakpoint import payload_location
from xnu_smp_irqprobe import _add_imm, _ldr_w
from xnu_tickprobe import _adrp, BRK, NOP, SELF_LOOP

CBZ_W0 = 0x34000180
CLUSTER_TYPE_E = 1
CLUSTER_TYPE_P = 2


def _lsl_x(rd, rn, shift):
    if not 0 < shift < 64:
        raise ValueError('lsl shift must be in 1..63')
    return 0xd3400000 | ((shift & 0x3f) << 16) | ((63 - shift) << 10) | (rn << 5) | rd


def _orr_x(rd, rn, rm):
    return 0xaa000000 | (rm << 16) | (rn << 5) | rd


def _type_offset(entry_size, cluster_type):
    return cluster_type * entry_size


def cave_words(cave_va, cache_info, entry_size, priv_l2_offset, l3_offset):
    if entry_size < 8 or entry_size & 3:
        raise ValueError('entry_size must be a positive multiple of 4')
    for offset in (priv_l2_offset, l3_offset):
        if offset < 0 or offset & 3 or offset + 4 > entry_size:
            raise ValueError('cache offsets must be aligned and inside an entry')
    e = _type_offset(entry_size, CLUSTER_TYPE_E)
    p = _type_offset(entry_size, CLUSTER_TYPE_P)
    for offset in (e + priv_l2_offset, e + l3_offset,
                   p + priv_l2_offset, p + l3_offset):
        if offset > 0x3ffc:
            raise ValueError('cache-info offsets exceed the ldr immediate range')
    return [
        _adrp(cave_va, cache_info, 8),                  # 0
        _add_imm(8, 8, cache_info & 0xfff),             # 4
        _ldr_w(9, 8, e + l3_offset),                    # 8  E L3
        _ldr_w(10, 8, e + priv_l2_offset),              # 12 E private L2
        _ldr_w(11, 8, p + l3_offset),                   # 16 P L3
        _ldr_w(12, 8, p + priv_l2_offset),              # 20 P private L2
        _lsl_x(10, 10, 32),                             # 24
        _orr_x(0, 10, 9),                               # 28 banner x0
        _lsl_x(12, 12, 32),                             # 32
        _orr_x(16, 12, 11),                             # 36 banner x16
        BRK,                                            # 40
    ]


def inject(payload, checkpoint_va, cave_va, cache_info, entry_size,
           priv_l2_offset, l3_offset):
    if len(payload) < 0x2a8 or payload[:8] != b'SWEETXNU':
        raise ValueError('invalid SWEETXNU payload')
    entry_va = _entry_va(payload)
    checkpoint_off, _, checkpoint_pa = payload_location(payload, checkpoint_va - entry_va)
    if checkpoint_va & 3 or cave_va & 3:
        raise ValueError('checkpoint and cave must be 4-byte aligned')
    cave_off, _, cave_pa = payload_location(payload, cave_va - entry_va)
    original, = struct.unpack_from('<I', payload, checkpoint_off)
    if original != CBZ_W0:
        raise ValueError('checkpoint is not the expected cbz w0 after vfs_mountroot')
    slot = cave_va & ~0x7f
    if cave_va < slot + 20:
        raise ValueError('cave must start after the five-word early-vector stub')
    slot_off, _, slot_pa = payload_location(payload, slot - entry_va)
    slot_word, = struct.unpack_from('<I', payload, slot_off)
    if slot_word != SELF_LOOP:
        raise ValueError('cave slot must start with B .')
    words = cave_words(cave_va, cache_info, entry_size, priv_l2_offset, l3_offset)
    if cave_off + len(words) * 4 > slot_off + 128:
        raise ValueError('cave exceeds its vector slot')
    for index in range(len(words)):
        current, = struct.unpack_from('<I', payload, cave_off + index * 4)
        if current != NOP:
            raise ValueError('cave area is not NOP padding')
    result = bytearray(payload)
    struct.pack_into('<I', result, checkpoint_off, _branch(checkpoint_va, cave_va))
    for index, word in enumerate(words):
        struct.pack_into('<I', result, cave_off + index * 4, word)
    manifest = {
        'checkpoint_va': hex(checkpoint_va), 'checkpoint_pa': hex(checkpoint_pa),
        'checkpoint_offset': checkpoint_off, 'checkpoint_original': hex(original),
        'cave_va': hex(cave_va), 'cave_pa': hex(cave_pa),
        'cave_offset': cave_off, 'cave_slot_va': hex(slot),
        'cave_slot_pa': hex(slot_pa),
        'cache_info': hex(cache_info), 'entry_size': entry_size,
        'priv_l2_offset': hex(priv_l2_offset), 'l3_offset': hex(l3_offset),
        'x0': '(E.c_priv_l2size << 32) | E.c_l3size',
        'x16': '(P.c_priv_l2size << 32) | P.c_l3size',
        'expected_pc': hex(cave_va + (len(words) - 1) * 4),
        'expected_esr': '0xf2005357',
        'instructions': [hex(word) for word in words],
        'source_sha256': hashlib.sha256(payload).hexdigest(),
        'payload_sha256': hashlib.sha256(result).hexdigest(),
    }
    return bytes(result), manifest


def _branch(pc, target):
    delta = target - pc
    if delta & 3:
        raise ValueError('branch target must be 4-byte aligned')
    if not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('branch target out of range')
    return 0x14000000 | ((delta >> 2) & 0x03ffffff)


def _entry_va(payload):
    return struct.unpack_from('<Q', payload, 24)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('payload', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--checkpoint', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--cave', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--cache-info', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--entry-size', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--priv-l2-offset', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--l3-offset', required=True, type=lambda s: int(s, 0))
    args = ap.parse_args()
    try:
        if args.payload.resolve() == args.out.resolve() or args.out.exists():
            raise ValueError('output must be a new file, separate from the source')
        patched, manifest = inject(args.payload.read_bytes(), args.checkpoint,
                                   args.cave, args.cache_info, args.entry_size,
                                   args.priv_l2_offset, args.l3_offset)
        with args.out.open('xb') as stream:
            stream.write(patched)
    except (ValueError, OSError) as error:
        ap.error(str(error))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Inject a timer-tick proof into a SWEETXNU payload.

The probe replaces one checkpoint `bl vm_commpage_init` with a branch to an
unused self-loop vector slot. The cave performs the original call, busy-waits
for `delay_ticks` CNTVCT_EL0 ticks with interrupts left enabled, loads the
32-bit `sched_tick`, and executes BRK #0x5357.

`sched_tick` is 1 after `sched_startup`. It is incremented only when the
scheduler maintenance continuation runs, which requires a timer interrupt to
expire the 125 ms maintenance deadline and wake that thread. A returned x0
greater than 1 therefore proves timer interrupt delivery and a live scheduler
maintenance wakeup. The wait uses CNTVCT directly, so a broken interrupt path
cannot hang the wait itself.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from xnu_breakpoint import payload_location

NOP = 0xd503201f
SELF_LOOP = 0x14000000
BRK = 0xd4200000 | (0x5357 << 5)


def _branch(pc, target, link):
    delta = target - pc
    if delta & 3:
        raise ValueError('branch target must be 4-byte aligned')
    if not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('branch target out of range')
    return (0x94000000 if link else 0x14000000) | ((delta >> 2) & 0x03ffffff)


def _cond_branch(pc, target, condition):
    delta = target - pc
    if delta & 3 or not -(1 << 20) <= delta < (1 << 20):
        raise ValueError('conditional branch target out of range')
    return 0x54000000 | (((delta >> 2) & 0x7ffff) << 5) | condition


def _adrp(pc, target, rd):
    delta = (target & ~0xfff) - (pc & ~0xfff)
    if delta % 0x1000:
        raise ValueError('adrp target page calculation failed')
    imm = delta >> 12
    if not -(1 << 20) <= imm < (1 << 20):
        raise ValueError('adrp target out of range')
    return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7ffff) << 5) | rd


def _ldr_w(rt, rn, offset):
    if offset < 0 or offset & 3 or offset > 0x3ffc:
        raise ValueError('ldr offset must be a non-negative multiple of 4')
    return 0xb9400000 | ((offset >> 2) << 10) | (rn << 5) | rt


def cave_words(cave_va, vm_commpage_init, sched_tick, delay_ticks):
    if delay_ticks <= 0:
        raise ValueError('delay_ticks must be positive')
    high = delay_ticks >> 16
    if high > 0xffff:
        raise ValueError('delay_ticks does not fit movz/movk')
    loop = cave_va + 28
    return [
        _branch(cave_va, vm_commpage_init, True),                    # bl
        _adrp(cave_va + 4, sched_tick, 8),
        _ldr_w(20, 8, sched_tick & 0xfff),                           # initial sched_tick
        0xd53be053,                                                  # mrs x19, cntvct_el0
        0xd2800000 | ((delay_ticks & 0xffff) << 5) | 1,              # movz x1
        0xf2a00000 | (high << 5) | 1,                                # movk x1, lsl #16
        0x8b010273,                                                  # add x19, x19, x1
        0xd53be040,                                                  # mrs x0, cntvct_el0
        0xeb13001f,                                                  # cmp x0, x19
        _cond_branch(cave_va + 36, loop, 0x3),                       # b.lo
        _ldr_w(0, 8, sched_tick & 0xfff),
        0x4b140000,                                                  # sub w0, w0, w20
        BRK,
    ]


def inject(payload, checkpoint_va, cave_va, vm_commpage_init, sched_tick, delay_ticks):
    if len(payload) < 0x2a8 or payload[:8] != b'SWEETXNU':
        raise ValueError('invalid SWEETXNU payload')
    checkpoint_off, _, checkpoint_pa = payload_location(payload, checkpoint_va - _entry_va(payload))
    if checkpoint_va & 3 or cave_va & 3:
        raise ValueError('checkpoint and cave must be 4-byte aligned')
    cave_off, _, cave_pa = payload_location(payload, cave_va - _entry_va(payload))
    original, = struct.unpack_from('<I', payload, checkpoint_off)
    expected = _branch(checkpoint_va, vm_commpage_init, True)
    if original != expected:
        raise ValueError('checkpoint is not the expected bl vm_commpage_init')
    slot = cave_va & ~0x7f
    # diag_arm_sentinels() rewrites the first five words of every early
    # vector slot before MMU enable. Start the cave after that stub so the
    # installed relay cannot destroy it.
    if cave_va < slot + 20:
        raise ValueError('cave must start after the five-word early-vector stub')
    slot_off, _, slot_pa = payload_location(payload, slot - _entry_va(payload))
    slot_word, = struct.unpack_from('<I', payload, slot_off)
    if slot_word != SELF_LOOP:
        raise ValueError('cave slot must start with B .')
    words = cave_words(cave_va, vm_commpage_init, sched_tick, delay_ticks)
    if cave_off + len(words) * 4 > slot_off + 128:
        raise ValueError('cave exceeds its vector slot')
    for index in range(len(words)):
        current, = struct.unpack_from('<I', payload, cave_off + index * 4)
        if current != NOP:
            raise ValueError('cave area is not NOP padding')
    result = bytearray(payload)
    struct.pack_into('<I', result, checkpoint_off, _branch(checkpoint_va, cave_va, False))
    for index, word in enumerate(words):
        struct.pack_into('<I', result, cave_off + index * 4, word)
    manifest = {
        'checkpoint_va': hex(checkpoint_va), 'checkpoint_pa': hex(checkpoint_pa),
        'checkpoint_offset': checkpoint_off,
        'cave_va': hex(cave_va), 'cave_pa': hex(cave_pa),
        'cave_offset': cave_off, 'cave_slot_va': hex(slot), 'cave_slot_pa': hex(slot_pa),
        'vm_commpage_init': hex(vm_commpage_init),
        'sched_tick': hex(sched_tick), 'delay_ticks': delay_ticks,
        'expected_pc': hex(cave_va + (len(words) - 1) * 4),
        'expected_esr': '0xf2005357',
        'instructions': [hex(word) for word in words],
        'source_sha256': hashlib.sha256(payload).hexdigest(),
        'payload_sha256': hashlib.sha256(result).hexdigest(),
    }
    return bytes(result), manifest


def _entry_va(payload):
    return struct.unpack_from('<Q', payload, 24)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('payload', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--checkpoint', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--cave', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--vm-commpage-init', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--sched-tick', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--delay-ticks', required=True, type=lambda s: int(s, 0))
    args = ap.parse_args()
    try:
        if args.payload.resolve() == args.out.resolve() or args.out.exists():
            raise ValueError('output must be a new file, separate from the source')
        patched, manifest = inject(args.payload.read_bytes(), args.checkpoint, args.cave,
                                   args.vm_commpage_init, args.sched_tick, args.delay_ticks)
        with args.out.open('xb') as stream:
            stream.write(patched)
    except (ValueError, OSError) as error:
        ap.error(str(error))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()

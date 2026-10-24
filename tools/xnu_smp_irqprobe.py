#!/usr/bin/env python3
"""Inject a per-CPU timer/IPI proof into a SWEETXNU payload.

The checkpoint (`cbz w0` after `bl _vfs_mountroot`) is redirected to an unused
early-vector slot. The cave waits `delay_ticks` CNTVCT_EL0 ticks, walks
CpuDataEntries[7..0] and executes BRK #0x5357.

Banner x0 holds a bitmask: bit i is set when cpu_stat.timer_cnt on logical CPU
i is non-zero. The banner x16 field carries x12, a second bitmask built from
cpu_stat.ipi_cnt. Both counters are cumulative since boot. Every CPU answered
a self-SGI1 during initCPU, so a full IPI mask also validates the walk. A
clear timer bit means that CPU never took a timer interrupt; XNU arms the
vtimer only for real deadlines, so an idle CPU with no scheduled work
legitimately stays at zero.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from xnu_breakpoint import payload_location
from xnu_tickprobe import _adrp, _branch, _cond_branch, BRK, NOP, SELF_LOOP

CBZ_W0 = 0x34000180
CPUS = 8


def _add_imm(rd, rn, imm):
    if not 0 <= imm <= 0xfff:
        raise ValueError('add immediate out of range')
    return 0x91000000 | (imm << 10) | (rn << 5) | rd


def _sub_imm(rd, rn, imm):
    if not 0 <= imm <= 0xfff:
        raise ValueError('sub immediate out of range')
    return 0xd1000000 | (imm << 10) | (rn << 5) | rd


def _movz_x(rd, imm):
    if not 0 <= imm <= 0xffff:
        raise ValueError('movz immediate out of range')
    return 0xd2800000 | (imm << 5) | rd


def _ldr_x(rt, rn, offset):
    if offset < 0 or offset & 7 or offset > 0x7ff8:
        raise ValueError('ldr x offset must be a non-negative multiple of 8')
    return 0xf9400000 | ((offset >> 3) << 10) | (rn << 5) | rt


def _ldr_w(rt, rn, offset):
    if offset < 0 or offset & 3 or offset > 0x3ffc:
        raise ValueError('ldr w offset must be a non-negative multiple of 4')
    return 0xb9400000 | ((offset >> 2) << 10) | (rn << 5) | rt


def _cmp_w_imm(rn, imm):
    if not 0 <= imm <= 0xfff:
        raise ValueError('cmp immediate out of range')
    return 0x7100001f | (imm << 10) | (rn << 5)


def _adc_w(rd, rn, rm):
    return 0x1a000000 | (rm << 16) | (rn << 5) | rd


def _subs_x_imm(rd, rn, imm):
    if not 0 <= imm <= 0xfff:
        raise ValueError('subs immediate out of range')
    return 0xf1000000 | (imm << 10) | (rn << 5) | rd


def _mov_w_reg(rd, rm):
    return 0x2a000000 | (rm << 16) | (31 << 5) | rd


MRS_X19_CNTVCT = 0xd53be053
MRS_X0_CNTVCT = 0xd53be040
CMP_X0_X19 = 0xeb13001f
ADD_X19_X19_X1 = 0x8b010273


def cave_words(cave_va, cpu_data_entries, timer_offset, ipi_offset, delay_ticks):
    if not 1 <= delay_ticks <= 0xffffffff:
        raise ValueError('delay_ticks must be positive and fit movz/movk')
    for offset in (timer_offset, ipi_offset):
        if offset < 0 or offset & 3 or offset > 0x3ffc:
            raise ValueError('cpu_stat offsets must be non-negative multiples of 4')
    high = delay_ticks >> 16
    scan = cave_va + 52
    return [
        MRS_X19_CNTVCT,                                          # 0
        _movz_x(1, delay_ticks & 0xffff),                        # 4
        0xf2a00000 | (high << 5) | 1,                            # 8  movk x1, high, lsl 16
        ADD_X19_X19_X1,                                          # 12 deadline = cntvct + delay
        MRS_X0_CNTVCT,                                           # 16 wait (IRQs stay enabled)
        CMP_X0_X19,                                              # 20
        _cond_branch(cave_va + 24, cave_va + 16, 0x3),           # 24 b.lo wait
        _adrp(cave_va + 28, cpu_data_entries, 8),                # 28
        _add_imm(8, 8, cpu_data_entries & 0xfff),                # 32
        _add_imm(8, 8, (CPUS - 1) * 16),                         # 36 start at the last entry
        0x52800009,                                              # 40 mov w9, #0 (timer mask)
        0x5280000a,                                              # 44 mov w10, #0 (IPI mask)
        _movz_x(13, CPUS),                                       # 48
        _ldr_x(11, 8, 8),                                        # 52 ldr x11, [x8, #8] cpu_data_vaddr
        _sub_imm(8, 8, 16),                                      # 56
        _ldr_w(14, 11, ipi_offset),                              # 60
        _ldr_w(15, 11, timer_offset),                            # 64
        _cmp_w_imm(14, 1),                                       # 68 C = ipi_cnt != 0
        _adc_w(10, 10, 10),                                      # 72 mask = mask << 1 | C
        _cmp_w_imm(15, 1),                                       # 76 C = timer_cnt != 0
        _adc_w(9, 9, 9),                                         # 80
        _subs_x_imm(13, 13, 1),                                  # 84
        _cond_branch(cave_va + 88, scan, 0x1),                   # 88 b.ne scan
        _mov_w_reg(0, 9),                                        # 92 banner x0 = timer mask
        _mov_w_reg(12, 10),                                      # 96 banner x16 = IPI mask
        BRK,                                                     # 100
    ]


def inject(payload, checkpoint_va, cave_va, cpu_data_entries, timer_offset,
           ipi_offset, delay_ticks):
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
    words = cave_words(cave_va, cpu_data_entries, timer_offset, ipi_offset, delay_ticks)
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
        'checkpoint_offset': checkpoint_off, 'checkpoint_original': hex(original),
        'cave_va': hex(cave_va), 'cave_pa': hex(cave_pa),
        'cave_offset': cave_off, 'cave_slot_va': hex(slot), 'cave_slot_pa': hex(slot_pa),
        'cpu_data_entries': hex(cpu_data_entries),
        'timer_offset': hex(timer_offset), 'ipi_offset': hex(ipi_offset),
        'cpus': CPUS, 'delay_ticks': delay_ticks,
        'mask_x0': 'bit i = cpu_stat.timer_cnt[i] != 0',
        'mask_x12': 'bit i = cpu_stat.ipi_cnt[i] != 0 (banner x16)',
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
    ap.add_argument('--cpu-data-entries', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--timer-offset', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--ipi-offset', required=True, type=lambda s: int(s, 0))
    ap.add_argument('--delay-ticks', required=True, type=lambda s: int(s, 0))
    args = ap.parse_args()
    try:
        if args.payload.resolve() == args.out.resolve() or args.out.exists():
            raise ValueError('output must be a new file, separate from the source')
        patched, manifest = inject(args.payload.read_bytes(), args.checkpoint, args.cave,
                                   args.cpu_data_entries, args.timer_offset,
                                   args.ipi_offset, args.delay_ticks)
        with args.out.open('xb') as stream:
            stream.write(patched)
    except (ValueError, OSError) as error:
        ap.error(str(error))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()

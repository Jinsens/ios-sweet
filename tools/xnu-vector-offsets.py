#!/usr/bin/env python3
"""Recompute the shim's XNU vector-table offsets for a freshly built kernel.

mkentryprobe needs two offsets relative to the payload entry point
(``--xnu-vector-offset`` for the early EL1 vector table, whose 16 slots are
all ``b .``, and ``--xnu-high-vector-offset`` for the high table installed by
``arm_vm_init``). Both are properties of the built kernel: any rebuilt kernel
can move them, and mkentryprobe then refuses the stale value with
"expected branch/NOP padded high exception slots".

  xnu-vector-offsets.py NEW.payload [--ref OLD.payload]

Without --ref the tables are found by their shape and each candidate is
checked the same way mkentryprobe checks it. With --ref the exact 2048-byte
contents of the reference payload's tables are searched for instead, which is
unambiguous.
"""
import struct
import sys

HEADER = 0x2a8


def segments(payload):
    if payload[:8] != b'SWEETXNU':
        raise SystemExit('not a SWEETXNU payload')
    count, = struct.unpack_from('<I', payload, 12)
    virt, entry = struct.unpack_from('<QQ', payload, 16)
    out = []
    for i in range(count):
        va, size, source, length = struct.unpack_from('<4Q', payload, HEADER + i * 32)
        out.append((va, size, source, length))
    return entry, out


def at(payload, offset, length):
    entry, segs = segments(payload)
    target = entry + offset
    hits = [s for s in segs if s[0] <= target and target + length <= s[0] + s[3]]
    if len(hits) != 1:
        raise SystemExit(f'offset {offset:#x} does not resolve to one segment')
    va, _size, source, _length = hits[0]
    return source + target - va, va


def scan(payload, want):
    """Yield (offset, va) for every candidate table whose shape validates."""
    entry, segs = segments(payload)
    for va, _size, source, length in segs:
        for off in range(0, length - 2048 + 1, 2048):
            at_file = source + off
            words = struct.unpack_from('<32I', payload, at_file)
            if want == 'early':
                ok = all(struct.unpack_from('<I', payload, at_file + s * 128)[0] == 0x14000000
                         for s in range(16))
            else:
                ok = True
                for s in range(16):
                    slot = struct.unpack_from('<32I', payload, at_file + s * 128)
                    if slot[0] & 0xfc000000 != 0x14000000 and slot[0] != 0xd503201f:
                        ok = False
                        break
                    if any(w != 0xd503201f for w in slot[1:]):
                        ok = False
                        break
            if ok:
                yield va + off - entry, va + off


def slot_targets(payload, file_at, va):
    """Decode each slot's B target relative to the table, 0 for a NOP slot."""
    out = []
    for s in range(16):
        w, = struct.unpack_from('<I', payload, file_at + s * 128)
        if w == 0xd503201f:
            out.append(0)
            continue
        imm = w & 0x03ffffff
        if imm & 0x02000000:
            imm -= 0x04000000
        out.append((va + s * 128 + imm * 4) - va)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    new = open(args[0], 'rb').read()
    ref_path = None
    if '--ref' in sys.argv:
        ref_path = sys.argv[sys.argv.index('--ref') + 1]
    ref = open(ref_path, 'rb').read() if ref_path else None

    for kind, ref_off in (('early', 0xb78), ('high', -0x4488)):
        want = None
        if ref is not None:
            file_at, va = at(ref, ref_off, 2048)
            want = slot_targets(ref, file_at, va)
        found = list(scan(new, kind))
        if want is not None and kind == 'high':
            keep = []
            for o, v in found:
                file_at, va = file_to(new, o)
                if slot_targets(new, file_at, va) == want:
                    keep.append((o, v))
            found = keep
        elif want is not None:
            blob = ref[at(ref, ref_off, 2048)[0]:][:2048]
            found = [(o, v) for o, v in found
                     if new[file_to(new, o)[0]:][:2048] == blob]
        if len(found) != 1:
            print(f'{kind}: {len(found)} candidates {[hex(o) for o, _ in found]}', file=sys.stderr)
            continue
        off, va = found[0]
        print(f'{kind} {off:#x}   (va {va:#x})')


def file_to(payload, offset):
    entry, segs = segments(payload)
    target = entry + offset
    for va, _size, source, length in segs:
        if va <= target and target + 2048 <= va + length:
            return source + target - va, target
    raise SystemExit(f'offset {offset:#x} outside the payload')



if __name__ == '__main__':
    main()

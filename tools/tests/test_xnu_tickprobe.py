"""Validate tick-probe injection against a synthetic SWEETXNU payload."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xnu_breakpoint import payload_location
from xnu_tickprobe import BRK, NOP, SELF_LOOP, cave_words, inject

CHECKPOINT = 0xfffffe0006001020
CAVE = 0xfffffe0006001094
COMMPAGE_INIT = 0xfffffe0006002000
SCHED_TICK = 0xfffffe0006003000
DELAY = 6500000


def payload():
    result = bytearray(0x700)
    result[:8] = b'SWEETXNU'
    struct.pack_into('<II', result, 8, 1, 1)
    struct.pack_into('<7Q', result, 16,
                     0xfffffe0006000000, 0xfffffe0006001004,
                     0x380, 8, 0xfffffe0006004000, 0x100000000, 0x10000000)
    struct.pack_into('<4Q', result, 0x2a8,
                     0xfffffe0006001000, 0x400, 0x300, 0x400)
    struct.pack_into('<I', result, 0x320,
                     0x94000000 | (((COMMPAGE_INIT - CHECKPOINT) >> 2) & 0x03ffffff))
    struct.pack_into('<I', result, 0x380, SELF_LOOP)
    for offset in range(0x394, 0x394 + len(cave_words(CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)) * 4, 4):
        struct.pack_into('<I', result, offset, NOP)
    return bytes(result)


class TickProbeTests(unittest.TestCase):
    def test_injects_branch_and_cave(self):
        source = payload()
        patched, manifest = inject(source, CHECKPOINT, CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)
        diff_offsets = [i for i in range(len(source)) if source[i] != patched[i]]
        self.assertEqual(len(patched), len(source))
        self.assertEqual(manifest['checkpoint_pa'], '0x100001020')
        self.assertEqual(manifest['expected_pc'], hex(CAVE + 48))
        branch, = struct.unpack_from('<I', patched, 0x320)
        self.assertEqual(branch & 0xfc000000, 0x14000000)
        self.assertEqual(branch, 0x14000000 | (((CAVE - CHECKPOINT) >> 2) & 0x03ffffff))
        words = cave_words(CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)
        for index, word in enumerate(words):
            self.assertEqual(struct.unpack_from('<I', patched, 0x394 + index * 4)[0], word)
        self.assertEqual(struct.unpack_from('<I', patched, 0x394 + (len(words) - 1) * 4)[0], BRK)
        allowed = set(range(0x320, 0x324)) | set(range(0x394, 0x394 + len(words) * 4))
        self.assertLessEqual(set(diff_offsets), allowed)
        self.assertTrue(diff_offsets)

    def test_refuse_wrong_checkpoint_instruction(self):
        source = bytearray(payload())
        struct.pack_into('<I', source, 0x320, NOP)
        with self.assertRaisesRegex(ValueError, 'vm_commpage_init'):
            inject(bytes(source), CHECKPOINT, CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)

    def test_refuse_unaligned_or_outside_targets(self):
        source = payload()
        with self.assertRaises(ValueError):
            inject(source, CHECKPOINT + 2, CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)
        with self.assertRaises(ValueError):
            inject(source, CHECKPOINT, 0xfffffe0006002004, COMMPAGE_INIT, SCHED_TICK, DELAY)

    def test_refuse_cave_not_after_self_loop(self):
        source = bytearray(payload())
        with self.assertRaisesRegex(ValueError, 'five-word early-vector stub'):
            inject(bytes(source), CHECKPOINT, CAVE - 4, COMMPAGE_INIT, SCHED_TICK, DELAY)
        struct.pack_into('<I', source, 0x380, 0x14000004)
        with self.assertRaisesRegex(ValueError, r'B \.'):
            inject(bytes(source), CHECKPOINT, CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)

    def test_refuse_non_nop_cave_area(self):
        source = bytearray(payload())
        struct.pack_into('<I', source, 0x384 + 20, 0x14000000)
        with self.assertRaisesRegex(ValueError, 'NOP'):
            inject(bytes(source), CHECKPOINT, CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)

    def test_refuse_bad_delay(self):
        source = payload()
        for delay in (0, -1, 1 << 32):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                inject(source, CHECKPOINT, CAVE, COMMPAGE_INIT, SCHED_TICK, delay)

    def test_payload_location_matches_manifest(self):
        patched, manifest = inject(payload(), CHECKPOINT, CAVE, COMMPAGE_INIT, SCHED_TICK, DELAY)
        offset, va, pa = payload_location(payload(), CHECKPOINT - 0xfffffe0006001004)
        self.assertEqual((offset, va, pa),
                         (manifest['checkpoint_offset'], CHECKPOINT, 0x100001020))


if __name__ == '__main__':
    unittest.main()

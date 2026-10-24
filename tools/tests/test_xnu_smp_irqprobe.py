"""Validate per-CPU IRQ-probe injection against a synthetic SWEETXNU payload."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xnu_breakpoint import payload_location
from xnu_smp_irqprobe import BRK, CBZ_W0, NOP, SELF_LOOP, cave_words, inject

CHECKPOINT = 0xfffffe0006001020
CAVE = 0xfffffe0006001094
ENTRIES = 0xfffffe0006003000
TIMER_OFFSET = 0x238
IPI_OFFSET = 0x228
DELAY = 26000000


def payload():
    result = bytearray(0x700)
    result[:8] = b'SWEETXNU'
    struct.pack_into('<II', result, 8, 1, 1)
    struct.pack_into('<7Q', result, 16,
                     0xfffffe0006000000, 0xfffffe0006001004,
                     0x380, 8, 0xfffffe0006004000, 0x100000000, 0x10000000)
    struct.pack_into('<4Q', result, 0x2a8,
                     0xfffffe0006001000, 0x400, 0x300, 0x400)
    struct.pack_into('<I', result, 0x320, CBZ_W0)
    struct.pack_into('<I', result, 0x380, SELF_LOOP)
    for offset in range(0x394, 0x394 + len(cave_words(CAVE, ENTRIES, TIMER_OFFSET,
                                                      IPI_OFFSET, DELAY)) * 4, 4):
        struct.pack_into('<I', result, offset, NOP)
    return bytes(result)


class SmpIrqProbeTests(unittest.TestCase):
    def test_injects_branch_and_cave(self):
        source = payload()
        patched, manifest = inject(source, CHECKPOINT, CAVE, ENTRIES, TIMER_OFFSET,
                                   IPI_OFFSET, DELAY)
        self.assertEqual(len(patched), len(source))
        self.assertEqual(manifest['checkpoint_pa'], '0x100001020')
        self.assertEqual(manifest['cpus'], 8)
        self.assertEqual(manifest['expected_pc'], hex(CAVE + 100))
        branch, = struct.unpack_from('<I', patched, 0x320)
        self.assertEqual(branch, 0x14000000 | (((CAVE - CHECKPOINT) >> 2) & 0x03ffffff))
        words = cave_words(CAVE, ENTRIES, TIMER_OFFSET, IPI_OFFSET, DELAY)
        for index, word in enumerate(words):
            self.assertEqual(struct.unpack_from('<I', patched, 0x394 + index * 4)[0], word)
        self.assertEqual(len(words), 26)
        self.assertEqual(words[23], 0x2a0903e0)  # mov w0, w9
        self.assertEqual(words[24], 0x2a0a03ec)  # mov w12, w10
        self.assertEqual(words[-1], BRK)
        diff_offsets = [i for i in range(len(source)) if source[i] != patched[i]]
        allowed = ({0x320, 0x321, 0x322, 0x323} |
                   set(range(0x394, 0x394 + len(words) * 4)))
        self.assertLessEqual(set(diff_offsets), allowed)
        self.assertTrue(diff_offsets)

    def test_refuse_wrong_checkpoint_instruction(self):
        source = bytearray(payload())
        struct.pack_into('<I', source, 0x320, NOP)
        with self.assertRaisesRegex(ValueError, 'cbz w0'):
            inject(bytes(source), CHECKPOINT, CAVE, ENTRIES, TIMER_OFFSET,
                   IPI_OFFSET, DELAY)

    def test_refuse_unaligned_targets(self):
        source = payload()
        with self.assertRaises(ValueError):
            inject(source, CHECKPOINT + 2, CAVE, ENTRIES, TIMER_OFFSET, IPI_OFFSET, DELAY)
        with self.assertRaises(ValueError):
            inject(source, CHECKPOINT, CAVE + 2, ENTRIES, TIMER_OFFSET, IPI_OFFSET, DELAY)

    def test_refuse_cave_not_after_self_loop(self):
        source = bytearray(payload())
        with self.assertRaisesRegex(ValueError, 'five-word early-vector stub'):
            inject(bytes(source), CHECKPOINT, CAVE - 4, ENTRIES, TIMER_OFFSET,
                   IPI_OFFSET, DELAY)
        struct.pack_into('<I', source, 0x380, 0x14000004)
        with self.assertRaisesRegex(ValueError, r'B \.'):
            inject(bytes(source), CHECKPOINT, CAVE, ENTRIES, TIMER_OFFSET,
                   IPI_OFFSET, DELAY)

    def test_refuse_non_nop_cave_area(self):
        source = bytearray(payload())
        struct.pack_into('<I', source, 0x394 + 20, 0x14000000)
        with self.assertRaisesRegex(ValueError, 'NOP'):
            inject(bytes(source), CHECKPOINT, CAVE, ENTRIES, TIMER_OFFSET,
                   IPI_OFFSET, DELAY)

    def test_refuse_bad_delay_and_offsets(self):
        source = payload()
        for delay in (0, -1, 1 << 32):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                inject(source, CHECKPOINT, CAVE, ENTRIES, TIMER_OFFSET, IPI_OFFSET, delay)
        for timer, ipi in ((2, IPI_OFFSET), (TIMER_OFFSET, 0x4000), (-4, IPI_OFFSET)):
            with self.subTest(timer=timer, ipi=ipi), self.assertRaises(ValueError):
                inject(source, CHECKPOINT, CAVE, ENTRIES, timer, ipi, DELAY)

    def test_manifest_records_masks(self):
        _, manifest = inject(payload(), CHECKPOINT, CAVE, ENTRIES, TIMER_OFFSET,
                             IPI_OFFSET, DELAY)
        self.assertEqual(manifest['timer_offset'], '0x238')
        self.assertEqual(manifest['ipi_offset'], '0x228')
        self.assertEqual(manifest['cpu_data_entries'], hex(ENTRIES))
        self.assertIn('timer_cnt', manifest['mask_x0'])
        self.assertIn('ipi_cnt', manifest['mask_x12'])
        offset, va, pa = payload_location(payload(), CHECKPOINT - 0xfffffe0006001004)
        self.assertEqual((offset, va, pa),
                         (manifest['checkpoint_offset'], CHECKPOINT, 0x100001020))


if __name__ == '__main__':
    unittest.main()

"""Validate cache-probe injection against a synthetic SWEETXNU payload."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xnu_breakpoint import payload_location
from xnu_cacheprobe import CBZ_W0, BRK, NOP, SELF_LOOP, cave_words, inject

CHECKPOINT = 0xfffffe0006001020
CAVE = 0xfffffe0006001094
CACHE_INFO = 0xfffffe0006003000
ENTRY_SIZE = 56
PRIV_L2 = 48
L3 = 52


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
    for offset in range(0x394, 0x394 + len(cave_words(
            CAVE, CACHE_INFO, ENTRY_SIZE, PRIV_L2, L3)) * 4, 4):
        struct.pack_into('<I', result, offset, NOP)
    return bytes(result)


class CacheProbeTests(unittest.TestCase):
    def test_injects_branch_and_cave(self):
        source = payload()
        patched, manifest = inject(source, CHECKPOINT, CAVE, CACHE_INFO,
                                   ENTRY_SIZE, PRIV_L2, L3)
        self.assertEqual(len(patched), len(source))
        self.assertEqual(manifest['checkpoint_pa'], '0x100001020')
        self.assertEqual(manifest['expected_pc'], hex(CAVE + 40))
        branch, = struct.unpack_from('<I', patched, 0x320)
        self.assertEqual(branch, 0x14000000 | (((CAVE - CHECKPOINT) >> 2) & 0x03ffffff))
        words = cave_words(CAVE, CACHE_INFO, ENTRY_SIZE, PRIV_L2, L3)
        self.assertEqual(len(words), 11)
        self.assertEqual(words[2], 0xb9406d09)  # ldr w9, [x8, #108] E L3
        self.assertEqual(words[3], 0xb940690a)  # ldr w10, [x8, #104] E private L2
        self.assertEqual(words[4], 0xb940a50b)  # ldr w11, [x8, #164] P L3
        self.assertEqual(words[5], 0xb940a10c)  # ldr w12, [x8, #160] P private L2
        self.assertEqual(words[6], 0xd3607d4a)  # lsl x10, x10, #32
        self.assertEqual(words[7], 0xaa090140)  # orr x0, x10, x9
        self.assertEqual(words[8], 0xd3607d8c)  # lsl x12, x12, #32
        self.assertEqual(words[9], 0xaa0b0190)  # orr x16, x12, x11
        self.assertEqual(words[-1], BRK)
        for index, word in enumerate(words):
            self.assertEqual(struct.unpack_from('<I', patched, 0x394 + index * 4)[0], word)
        diff_offsets = [i for i in range(len(source)) if source[i] != patched[i]]
        allowed = ({0x320, 0x321, 0x322, 0x323} |
                   set(range(0x394, 0x394 + len(words) * 4)))
        self.assertLessEqual(set(diff_offsets), allowed)
        self.assertTrue(diff_offsets)

    def test_refuse_wrong_checkpoint_instruction(self):
        source = bytearray(payload())
        struct.pack_into('<I', source, 0x320, NOP)
        with self.assertRaisesRegex(ValueError, 'cbz w0'):
            inject(bytes(source), CHECKPOINT, CAVE, CACHE_INFO,
                   ENTRY_SIZE, PRIV_L2, L3)

    def test_refuse_unaligned_targets(self):
        source = payload()
        with self.assertRaises(ValueError):
            inject(source, CHECKPOINT + 2, CAVE, CACHE_INFO, ENTRY_SIZE, PRIV_L2, L3)
        with self.assertRaises(ValueError):
            inject(source, CHECKPOINT, CAVE + 2, CACHE_INFO, ENTRY_SIZE, PRIV_L2, L3)

    def test_refuse_cave_not_after_self_loop(self):
        source = bytearray(payload())
        with self.assertRaisesRegex(ValueError, 'five-word early-vector stub'):
            inject(bytes(source), CHECKPOINT, CAVE - 4, CACHE_INFO,
                   ENTRY_SIZE, PRIV_L2, L3)
        struct.pack_into('<I', source, 0x380, 0x14000004)
        with self.assertRaisesRegex(ValueError, r'B \.'):
            inject(bytes(source), CHECKPOINT, CAVE, CACHE_INFO,
                   ENTRY_SIZE, PRIV_L2, L3)

    def test_refuse_non_nop_cave_area(self):
        source = bytearray(payload())
        struct.pack_into('<I', source, 0x394 + 12, 0x14000000)
        with self.assertRaisesRegex(ValueError, 'NOP'):
            inject(bytes(source), CHECKPOINT, CAVE, CACHE_INFO,
                   ENTRY_SIZE, PRIV_L2, L3)

    def test_refuse_bad_layout(self):
        source = payload()
        for entry, priv, l3 in ((52, 48, 52), (56, 50, 52), (8192, 48, 52),
                                (0, 48, 52), (56, -4, 52)):
            with self.subTest(entry=entry, priv=priv, l3=l3), self.assertRaises(ValueError):
                inject(source, CHECKPOINT, CAVE, CACHE_INFO, entry, priv, l3)

    def test_manifest_records_fields(self):
        _, manifest = inject(payload(), CHECKPOINT, CAVE, CACHE_INFO,
                             ENTRY_SIZE, PRIV_L2, L3)
        self.assertEqual(manifest['cache_info'], hex(CACHE_INFO))
        self.assertEqual(manifest['entry_size'], 56)
        self.assertEqual(manifest['priv_l2_offset'], '0x30')
        self.assertEqual(manifest['l3_offset'], '0x34')
        self.assertIn('E.c_priv_l2size', manifest['x0'])
        self.assertIn('P.c_priv_l2size', manifest['x16'])
        offset, va, pa = payload_location(payload(), CHECKPOINT - 0xfffffe0006001004)
        self.assertEqual((offset, va, pa),
                         (manifest['checkpoint_offset'], CHECKPOINT, 0x100001020))


if __name__ == '__main__':
    unittest.main()

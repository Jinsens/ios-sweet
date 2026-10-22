"""Validate diagnostic address translation without Apple artifacts."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xnu_breakpoint import insert_breakpoint, early_vector_pa, high_vector_pa


def payload():
    result = bytearray(0x400)
    result[:8] = b'SWEETXNU'
    struct.pack_into('<II', result, 8, 1, 1)
    struct.pack_into('<7Q', result, 16,
                     0xfffffe0006000000, 0xfffffe0006001004,
                     0x380, 8, 0xfffffe0006004000, 0x100000000, 0x10000000)
    struct.pack_into('<4Q', result, 0x2a8,
                     0xfffffe0006001000, 0x100, 0x300, 0x20)
    struct.pack_into('<8I', result, 0x300, *([0xd503201f] * 8))
    return bytes(result)


class BreakpointTests(unittest.TestCase):
    def test_exact_instruction_and_high_physical_address(self):
        original = payload()
        patched, manifest = insert_breakpoint(original, 4)
        self.assertEqual(manifest['target_pa'], '0x100001008')
        self.assertEqual(manifest['target_va'], '0xfffffe0006001008')
        self.assertEqual(struct.unpack_from('<I', patched, 0x308)[0], 0xd42a6ae0)
        self.assertEqual(patched[:0x308], original[:0x308])
        self.assertEqual(patched[0x30c:], original[0x30c:])
        self.assertEqual(len(patched), len(original))

    def test_refuse_zero_fill_unaligned_and_negative(self):
        for offset in (0x20, 2, -4):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                insert_breakpoint(payload(), offset)

    def test_refuse_bad_table_and_truncated_data(self):
        for field, value in ((12, 100), (0x2a8 + 16, 0x3f8), (0x2a8 + 24, 0x200)):
            source = bytearray(payload())
            struct.pack_into('<I' if field == 12 else '<Q', source, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                insert_breakpoint(source)

    def test_vector_table_must_be_file_backed_aligned_and_spin_only(self):
        source = bytearray(payload()) + bytearray(0xc00)
        struct.pack_into('<Q', source, 24, 0xfffffe0006001000)
        struct.pack_into('<4Q', source, 0x2a8,
                         0xfffffe0006001000, 0x1000, 0x300, 0x800)
        for slot in range(16):
            struct.pack_into('<I', source, 0x300 + slot * 128, 0x14000000)
        self.assertEqual(early_vector_pa(source, 0), 0x100001000)
        with self.assertRaises(ValueError):
            early_vector_pa(source, 4)
        struct.pack_into('<I', source, 0x300 + 15 * 128, 0xd503201f)
        with self.assertRaisesRegex(ValueError, 'B .'):
            early_vector_pa(source, 0)

    def test_high_vector_table_before_entry_and_padding_validation(self):
        source = bytearray(payload()) + bytearray(0xc00)
        struct.pack_into('<Q', source, 24, 0xfffffe0006001800)
        struct.pack_into('<4Q', source, 0x2a8,
                         0xfffffe0006001000, 0x1000, 0x300, 0x800)
        for slot in range(16):
            struct.pack_into('<32I', source, 0x300 + slot * 128,
                             0x14000400, *([0xd503201f] * 31))
        self.assertEqual(high_vector_pa(source, -0x800), 0x100001000)
        struct.pack_into('<I', source, 0x300 + 15 * 128 + 120, 0)
        with self.assertRaisesRegex(ValueError, 'padded'):
            high_vector_pa(source, -0x800)


if __name__ == '__main__':
    unittest.main()

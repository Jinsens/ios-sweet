"""Keep old display records distinguishable from a newly booted kernel."""
import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location(
    'sweet_marks', Path(__file__).resolve().parents[1] / 'sweet-marks.py')
marks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(marks)


def block(*records):
    data = bytearray(4096)
    struct.pack_into('<8sII', data, 0, b'SWEETMRK', 1, len(records))
    for i, (code, a, b) in enumerate(records):
        struct.pack_into('<IIQQ', data, 16 + i * 24, code, 0, a, b)
    return data


class MarksTests(unittest.TestCase):
    def test_old_record_does_not_claim_new_busy_diagnostics(self):
        text = marks.decode(block((92, 201, 201)))
        self.assertIn('may be a stale record', text)
        self.assertIn('TE timeouts', text)
        self.assertNotIn('busy skips', text)

    def test_build_id_selects_new_semantics_even_if_it_appears_later(self):
        text = marks.decode(block((92, 2, 0x80000000), (99, 0x2303, 0)))
        self.assertIn('kernel build ID 0x2303', text)
        self.assertIn('busy skips', text)
        self.assertNotIn('TE timeouts', text)

    def test_translation_fault_and_normal_nc_are_distinct(self):
        text = marks.decode(block((91, 0x44000000fcb3f000, 0x805)))
        self.assertIn('first: PA page=0xfcb3f000, attributes=0x44 (Normal-NC)', text)
        self.assertIn('last: translation FAULT', text)

    def test_corrupt_or_incompatible_records_are_rejected(self):
        for offset, value in ((8, 2), (12, 161)):
            data = block()
            struct.pack_into('<I', data, offset, value)
            with self.assertRaises(ValueError):
                marks.decode(data)
        with self.assertRaises(ValueError):
            marks.decode(block()[:100])


if __name__ == '__main__':
    unittest.main()

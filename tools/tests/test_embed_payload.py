"""Host-only tests for boot-v4 XNU delivery outside boot/vendor ramdisks."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import embed_payload


def image():
    blob = bytearray(4096)
    blob[56:60] = b'ARM\x64'
    struct.pack_into('<Q', blob, 16, len(blob))
    return bytes(blob)


def payload():
    return b'SWEETXNU' + bytes(0x2a8 - 8) + b'kernel-and-adt'


class EmbeddedPayloadTests(unittest.TestCase):
    def test_image_header_marker_and_payload(self):
        before, data = image(), payload()
        result = embed_payload.embed(before, data)
        self.assertEqual(struct.unpack_from('<Q', result, 16)[0], len(result))
        self.assertEqual(result[:16], before[:16])
        self.assertEqual(result[24:len(before)], before[24:])
        self.assertEqual(result[4096:4104], b'SWEETAPP')
        self.assertEqual(struct.unpack_from('<Q', result, 4104)[0], len(data))
        self.assertEqual(result[4112:], data)
        self.assertEqual(4112 % 8, 0)

    def test_rejects_unaligned_or_mismatched_image(self):
        with self.assertRaisesRegex(ValueError, '4 KiB-aligned'):
            embed_payload.embed(image() + b'x', payload())
        bad = bytearray(image())
        struct.pack_into('<Q', bad, 16, 8192)
        with self.assertRaisesRegex(ValueError, 'header size'):
            embed_payload.embed(bytes(bad), payload())

    def test_rejects_image_overlapping_dtb_window(self):
        with self.assertRaisesRegex(ValueError, 'DTB load window'):
            embed_payload.embed(image(), payload(), max_image_size=4096)
        self.assertEqual(len(embed_payload.embed(image(), payload(),
                                                max_image_size=8192)),
                         4096 + 16 + len(payload()))

    def test_rejects_wrong_image_magic(self):
        bad = bytearray(image())
        bad[56:60] = b'nope'
        with self.assertRaisesRegex(ValueError, 'arm64'):
            embed_payload.embed(bytes(bad), payload())

    def test_rejects_wrong_payload_magic_or_size(self):
        with self.assertRaisesRegex(ValueError, 'SWEETXNU'):
            embed_payload.embed(image(), b'SWEETXNU')
        with self.assertRaisesRegex(ValueError, 'SWEETXNU'):
            embed_payload.embed(image(), b'NOT-XNU!' + bytes(0x2a8))


if __name__ == '__main__':
    unittest.main()

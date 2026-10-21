"""Host-only layout/validation tests; no connected device or Apple files."""
import gzip
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mkbootimg
import mkentryprobe


def template(image=None, ramdisk=b'recovery-ramdisk'):
    if image is None:
        image = bytearray(0x8000)
        image[56:60] = b'ARM\x64'
        struct.pack_into('<3Q', image, 8, 0, len(image), 0xa)
        banner = b'Linux version test ' + b'x' * 500 + b'\n\0'
        image[0x1000:0x1000 + len(banner)] = banner
    kernel = gzip.compress(image, mtime=0)
    header = bytearray(4096)
    struct.pack_into('<8s4I', header, 0, b'ANDROID!', len(kernel), len(ramdisk), 0, 1584)
    struct.pack_into('<I', header, 40, 4)
    return (bytes(header) + kernel + bytes(mkbootimg.pad(len(kernel))-len(kernel))
            + ramdisk + bytes(mkbootimg.pad(len(ramdisk))-len(ramdisk)))


class ProbeLayoutTests(unittest.TestCase):
    def test_record_offsets(self):
        record, offsets = mkentryprobe.banner_record()
        self.assertLessEqual(len(record), 407)  # measured test kernel capacity
        self.assertTrue(record.endswith(b'\n\0'))
        for name in mkentryprobe.FIELDS:
            at = offsets[name]
            self.assertEqual(record[at:at+16], b'0' * 16)
            self.assertTrue(record[:at].endswith((name+'=').encode()))

    def test_only_banner_changed_and_ramdisk_retained(self):
        source = template()
        header, image, ramdisk, at, fields, old = mkentryprobe.prepare(source)
        self.assertEqual(at, 0x1000)
        self.assertEqual(header['header_version'], 4)
        self.assertEqual(ramdisk, b'recovery-ramdisk')
        self.assertEqual(image[56:60], b'ARM\x64')
        original = gzip.decompress(source[4096:4096+header['kernel_size']])
        image[at:at+len(old)] = old
        self.assertEqual(bytes(image), original)

    def test_short_printf_format_not_selected(self):
        source = template()
        h = mkbootimg.parse(source)
        image = bytearray(gzip.decompress(source[4096:4096+h['kernel_size']]))
        text = b'Linux version %s (%s)\0'
        image[0x200:0x200+len(text)] = text
        self.assertEqual(mkentryprobe.prepare(template(image))[3], 0x1000)

    def test_no_anywhere_flag_refused(self):
        source = template(); h = mkbootimg.parse(source)
        image = bytearray(gzip.decompress(source[4096:4096+h['kernel_size']]))
        struct.pack_into('<Q', image, 24, 2)
        with self.assertRaisesRegex(ValueError, 'anywhere'):
            mkentryprobe.prepare(template(image))

    def test_nonzero_text_offset_refused(self):
        source = template(); h = mkbootimg.parse(source)
        image = bytearray(gzip.decompress(source[4096:4096+h['kernel_size']]))
        struct.pack_into('<Q', image, 8, 0x80000)
        with self.assertRaisesRegex(ValueError, 'text_offset'):
            mkentryprobe.prepare(template(image))

    def test_truncated_ramdisk_refused(self):
        source = bytearray(template())
        struct.pack_into('<I', source, 12, len(source)+1)
        with self.assertRaisesRegex(ValueError, 'truncated'):
            mkentryprobe.prepare(source)

    def test_ambiguous_banner_refused(self):
        source=template(); h=mkbootimg.parse(source)
        image=bytearray(gzip.decompress(source[4096:4096+h['kernel_size']]))
        text=b'Linux version second ' + b'y'*500 + b'\0'
        image[0x4000:0x4000+len(text)] = text
        with self.assertRaisesRegex(ValueError, 'uniquely'):
            mkentryprobe.prepare(template(image))


if __name__ == '__main__':
    unittest.main()

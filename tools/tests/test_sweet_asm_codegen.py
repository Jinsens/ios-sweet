"""Check actual SWEET inline-asm templates with the Darwin ARM64 assembler.

Host mocks cannot catch Darwin treating ';' as a comment and silently losing
timer reads, translation results, or the secure-monitor call.
"""
import ast
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FILES = ('osfmk/arm64/sweet_matisse_dsi.c',
         'osfmk/arm64/sweet_early.c', 'bsd/dev/sweet_ufs.c')


@unittest.skipUnless(shutil.which('clang') and shutil.which('git'), 'needs clang and git')
class DarwinAsmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        for patch in sorted((ROOT / 'xnu/patches').glob('*.patch')):
            subprocess.run(['git', 'apply', *['--include=' + p for p in FILES],
                            str(patch)], cwd=cls.work, check=True, capture_output=True)
        cls.templates = []
        for name in FILES:
            source = (cls.work / name).read_text()
            for match in re.finditer(r'__asm__\s+volatile\s*\(\s*'
                                     r'((?:"(?:\\.|[^"\\])*"\s*)+)', source):
                template = ''.join(ast.literal_eval(s) for s in
                                   re.findall(r'"(?:\\.|[^"\\])*"', match[1]))
                cls.templates.append((name, template))

    def assemble(self, template):
        # Preserve the actual separators and instructions. Operand substitution
        # is irrelevant to the delimiter bug; use distinct architectural regs.
        source = template.replace('%0', 'x0').replace('%1', 'x1')
        (self.work / 'probe.s').write_text('.text\n_probe:\n' + source + '\nret\n')
        subprocess.run(['clang', '-target', 'arm64-apple-macos11', '-c',
                        str(self.work / 'probe.s'), '-o', str(self.work / 'probe.o')],
                       check=True, capture_output=True)
        data = (self.work / 'probe.o').read_bytes()
        self.assertEqual(struct.unpack_from('<I', data)[0], 0xfeedfacf)
        offset = 32
        for _ in range(struct.unpack_from('<I', data, 16)[0]):
            command, size = struct.unpack_from('<II', data, offset)
            if command == 0x19:  # LC_SEGMENT_64
                count = struct.unpack_from('<I', data, offset + 64)[0]
                for index in range(count):
                    section = offset + 72 + index * 80
                    if data[section:section + 16].rstrip(b'\0') == b'__text':
                        length, start = struct.unpack_from('<QI', data, section + 40)
                        return list(struct.unpack_from('<' + 'I' * (length // 4), data, start))
            offset += size
        self.fail('no __text section in assembler output')

    def test_display_counter_really_reads_the_counter(self):
        templates = [t for _, t in self.templates if 'cntpct_el0' in t]
        self.assertEqual(len(templates), 1)
        self.assertEqual(self.assemble(templates[0]),
                         [0xd5033fdf, 0xd53be020, 0xd65f03c0])  # ISB; MRS; RET

    def test_all_framebuffer_checks_read_par_after_isb(self):
        templates = [(name, t) for name, t in self.templates if 'par_el1' in t]
        self.assertEqual(len(templates), 4)
        for name, template in templates:
            with self.subTest(source=name, template=template):
                self.assertEqual(self.assemble(template),
                                 [0xd5087821, 0xd5033fdf, 0xd5387400, 0xd65f03c0])

    def test_ufs_smc_is_emitted_with_all_unused_arguments_zeroed(self):
        templates = [t for _, t in self.templates if 'smc #0' in t]
        self.assertEqual(len(templates), 1)
        self.assertEqual(self.assemble(templates[0]),
                         [0xaa1f03e0 | reg for reg in range(3, 8)] +
                         [0xd4000003, 0xd65f03c0])


if __name__ == '__main__':
    unittest.main()

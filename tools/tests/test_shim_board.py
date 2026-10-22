"""Check actual board discovery against synthetic ARM timer bindings."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from test_shim_fdt import fdt


class BoardTimerTests(unittest.TestCase):
    def test_virtual_timer_uses_number_instead_of_flags(self):
        cc = shutil.which('clang') or shutil.which('cc')
        if not cc:
            self.skipTest('host C compiler not installed')
        shim = Path(__file__).resolve().parents[2] / 'shim'
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'board'
            subprocess.run([cc, '-O1', '-I' + str(shim),
                            str(shim / 'host_board.c'), str(shim / 'board.c'),
                            str(shim / 'fdt.c'), '-o', str(exe)], check=True)
            # Deliberately distinguish IRQ number from trigger/affinity flags.
            for virt, phys, flags, count in ((11, 14, 1, 4), (3, 2, 0xf08, 3)):
                cells = tuple(cell for irq in (13, phys, virt, 10)
                              for cell in ((1, irq, flags, 0) if count == 4
                                           else (1, irq, flags)))
                tree = Path(tmp) / 'timer.dtb'
                tree.write_bytes(fdt([('interrupt-controller', [
                    ('compatible', b'arm,gic-v3\0'),
                    ('#interrupt-cells', struct.pack('>I', count))]), ('timer', [
                    ('compatible', b'arm,armv8-timer\0'),
                    ('interrupts', struct.pack('>'+'I'*len(cells), *cells))])]))
                result = subprocess.check_output([str(exe), str(tree)], text=True)
                self.assertIn(f'timer      virt {16+virt} phys {16+phys} sgi 1', result)


if __name__ == '__main__':
    unittest.main()

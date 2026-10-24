"""Board-profile regressions without downloading an Apple device tree."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adt
import mkadt
import mkpayload


def child(node, name):
    return next(c for c in node.children if c.name == name)


def words(data, bits=64):
    size = bits // 8
    return struct.unpack('<' + ('Q' if bits == 64 else 'I') * (len(data)//size), data)


class BoardProfileTests(unittest.TestCase):
    def build(self, **kwargs):
        with patch.object(mkadt, 'd22ap_product', return_value=None):
            return mkadt.build(seed=bytes(64), **kwargs)

    def test_matisse_eight_cpus_and_timer(self):
        root = self.build(board='matisse')
        cpus = child(root, 'cpus').children
        self.assertEqual(len(cpus), 8)
        self.assertEqual([words(c.prop('reg'), 32)[0] for c in cpus],
                         [core << 8 for core in range(8)])
        for core, cpu in enumerate(cpus):
            compat = cpu.prop('compatible')
            self.assertTrue(compat.startswith(b'qcom,kryo470\0'))
            self.assertIn(b'arm,cortex-a55\0' if core < 4 else b'arm,cortex-a78\0', compat)
            self.assertEqual(cpu.prop('state'), b'running\0' if core == 0 else b'stopped\0')
        self.assertEqual(words(cpus[0].prop('timebase-frequency'), 32), (13000000,))
        gic = child(child(root, 'arm-io'), 'gic')
        self.assertEqual(words(gic.prop('virtual-timer-intid'), 32), (27,))
        d, ds, r, rs = words(gic.prop('reg'))
        self.assertEqual((d+mkadt.SOC_BASE, ds, r+mkadt.SOC_BASE, rs),
                         (0xc400000, 0x40000, 0xc440000, 0x200000))

    def test_matisse_ram_framebuffer_identity_and_mtk_ufs(self):
        root = self.build(board='matisse')
        blob = adt.serialize(root)
        self.assertEqual(mkpayload.adt_dram(blob), (0x100000000, 0x12e000000))
        fb = child(root, 'sweet-fbcon')
        self.assertEqual(words(fb.prop('reg')), (0xfcb3f000, 0x34c0000))
        self.assertEqual(words(fb.prop('width'), 32), (1440,))
        self.assertEqual(words(fb.prop('height'), 32), (3200,))
        self.assertEqual(words(fb.prop('stride'), 32), (5760,))
        ufs = child(root, 'sweet-ufs')
        self.assertIn(b'mediatek,mt8183-ufshci\0', ufs.prop('compatible'))
        self.assertEqual(words(ufs.prop('reg')), (0x112b0000, 0x1000))
        self.assertEqual(root.prop('model'), b'iPhone10,3\0')
        self.assertIn(b'D22AP\0', root.prop('compatible'))
        self.assertIn(b'xiaomi,matisse\0', root.prop('compatible'))
        self.assertNotIn(b'qcom,sm7150\0', root.prop('compatible'))
        disp = child(child(root, 'arm-io'), 'disp0')
        self.assertEqual(words(disp.prop('canvas-width'), 32), (1125,))
        self.assertEqual(words(disp.prop('canvas-height'), 32), (2436,))

    def test_matisse_payload_disables_qualcomm_drivers(self):
        blob = adt.serialize(self.build(board='matisse'))
        args = mkpayload.platform_cmdline(blob, 'debug=0xc serial=3 cpus=1')
        for token in ('cpus=1', 'sweetkeys=0', 'sweettouch=0',
                      'sweetbattery=0', 'sweetusb=0', 'ufsrw=0', '-noprogress'):
            self.assertIn(token, args.split())
        self.assertEqual(args, mkpayload.platform_cmdline(blob, args))
        self.assertIn('cpus=8', mkpayload.platform_cmdline(blob, 'debug=0xc').split())
        self.assertIn('cpus=8', mkpayload.platform_cmdline(blob, 'cpus=8').split())
        for unsafe in ('cpus=9', 'cpus=0', 'cpus=bad', 'sweetkeys=1',
                       'sweetusb=1', 'ufsrw=1'):
            with self.assertRaises(ValueError):
                mkpayload.platform_cmdline(blob, unsafe)
        with self.assertRaises(ValueError):
            mkpayload.platform_cmdline(blob, 'x'*mkpayload.BOOT_LINE_LENGTH)
        self.assertEqual(mkpayload.platform_cmdline(adt.serialize(self.build()),
                                                   'cpus=8 sweetusb=1'),
                         'cpus=8 sweetusb=1')

    def test_sweet_defaults_unchanged(self):
        root = self.build()
        self.assertEqual(len(child(root, 'cpus').children), 8)
        self.assertEqual(words(child(root, 'sweet-ufs').prop('reg')), (0x1d84000, 0x3000))
        gic = child(child(root, 'arm-io'), 'gic')
        self.assertEqual(words(gic.prop('virtual-timer-intid'), 32), (19,))
        self.assertEqual(mkpayload.adt_dram(adt.serialize(root)), (0xb0000000, 0x4d000000))

    def test_qemu_preserved(self):
        root = self.build(qemu=True)
        self.assertEqual(len(child(root, 'cpus').children), 1)
        self.assertNotIn('sweet-ufs', [c.name for c in root.children])
        gic = child(child(root, 'arm-io'), 'gic')
        self.assertEqual(words(gic.prop('virtual-timer-intid'), 32), (27,))

    def test_matisse_refuses_other_memory_modes(self):
        for flags in ({'qemu': True}, {'highmem': True}):
            with self.assertRaises(ValueError):
                self.build(board='matisse', **flags)


if __name__ == '__main__':
    unittest.main()

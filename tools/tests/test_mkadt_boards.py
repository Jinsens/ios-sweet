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
        l2_sizes = [0x80000] * 7 + [0x100000]
        for core, cpu in enumerate(cpus):
            compat = cpu.prop('compatible')
            self.assertTrue(compat.startswith(b'qcom,kryo470\0'))
            self.assertIn(b'arm,cortex-a55\0' if core < 4 else b'arm,cortex-a78\0', compat)
            self.assertEqual(cpu.prop('state'), b'running\0' if core == 0 else b'stopped\0')
            self.assertEqual(words(cpu.prop('cluster-id'), 32), (0 if core < 4 else 1,))
            self.assertEqual(words(cpu.prop('cluster-type'), 32),
                             (ord('E') if core < 4 else ord('P'),))
            self.assertEqual(words(cpu.prop('l2-cache-size'), 32), (l2_sizes[core],))
            self.assertEqual(words(cpu.prop('l3-cache-size'), 32), (0x800000,))
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
        display = child(root, 'sweet-display')
        self.assertEqual(words(display.prop('reg')), (0x14000000, 0x18000))
        self.assertEqual(words(display.prop('secondary-reg')), (0x14400000, 0x8000))
        ufs = child(root, 'sweet-ufs')
        self.assertIn(b'mediatek,mt8183-ufshci\0', ufs.prop('compatible'))
        self.assertEqual(words(ufs.prop('reg')), (0x112b0000, 0x1000))
        usb = child(root, 'sweet-usb')
        self.assertIn(b'mediatek,mt6983-mtu3\0', usb.prop('compatible'))
        self.assertEqual(words(usb.prop('reg')),
                         (0x11201000, 0x2e00, 0x11203e00, 0x100))
        keys = child(root, 'sweet-keys')
        self.assertIn(b'mediatek,mt6363-keys\0', keys.prop('compatible'))
        self.assertEqual(words(keys.prop('reg')),
                         (0x1c015000, 0x900, 0x1c00e000, 0x1000))
        self.assertEqual(words(keys.prop('spmi-sid'), 32), (4,))
        self.assertEqual(words(keys.prop('power-bit'), 32), (1,))
        self.assertEqual(words(keys.prop('volup-bit'), 32), (3,))
        self.assertEqual(words(keys.prop('voldown-index'), 32), (0,))
        self.assertEqual(root.prop('model'), b'iPhone10,3\0')
        self.assertIn(b'D22AP\0', root.prop('compatible'))
        self.assertIn(b'xiaomi,matisse\0', root.prop('compatible'))
        self.assertNotIn(b'qcom,sm7150\0', root.prop('compatible'))
        disp = child(child(root, 'arm-io'), 'disp0')
        self.assertEqual(words(disp.prop('canvas-width'), 32), (1125,))
        self.assertEqual(words(disp.prop('canvas-height'), 32), (2436,))

    def test_matisse_native_canvas_matches_physical_panel(self):
        blob = adt.serialize(self.build(board='matisse'))
        self.assertEqual(mkpayload.platform_adt(blob, ''), blob)
        self.assertEqual(mkpayload.platform_adt(blob, 'sweetfb_native=0'), blob)
        native = mkpayload.platform_adt(blob, 'sweetfb_native=1')
        root, _ = adt.parse(native)
        disp = child(child(root, 'arm-io'), 'disp0')
        self.assertEqual(words(disp.prop('canvas-width'), 32), (1440,))
        self.assertEqual(words(disp.prop('canvas-height'), 32), (3200,))
        self.assertEqual(mkpayload.platform_adt(native, 'sweetfb_native=1'), native)
        # Reverting just the two properties must reconstruct the input exactly.
        dims = {'canvas-width': 1125, 'canvas-height': 2436}
        disp.props = [(n, f, struct.pack('<I', dims[n]) if n in dims else v)
                      for n, f, v in disp.props]
        self.assertEqual(adt.serialize(root), blob)
        sweet = adt.serialize(self.build())
        self.assertEqual(mkpayload.platform_adt(sweet, 'sweetfb_native=1'), sweet)

    def test_matisse_payload_disables_qualcomm_drivers(self):
        blob = adt.serialize(self.build(board='matisse'))
        args = mkpayload.platform_cmdline(blob, 'debug=0xc serial=3 cpus=1')
        for token in ('cpus=1', 'sweetkeys=2', 'sweettouch=0',
                      'sweetbattery=0', 'sweetusb=2', 'ufsrw=0', '-noprogress'):
            self.assertIn(token, args.split())
        self.assertEqual(args, mkpayload.platform_cmdline(blob, args))
        self.assertIn('cpus=8', mkpayload.platform_cmdline(blob, 'debug=0xc').split())
        self.assertIn('cpus=8', mkpayload.platform_cmdline(blob, 'cpus=8').split())
        for usb in ('sweetusb=0', 'sweetusb=1', 'sweetusb=2'):
            self.assertIn(usb, mkpayload.platform_cmdline(blob, usb).split())
        for keys in ('sweetkeys=0', 'sweetkeys=1', 'sweetkeys=2'):
            self.assertIn(keys, mkpayload.platform_cmdline(blob, keys).split())
        for unsafe in ('cpus=9', 'cpus=0', 'cpus=bad', 'sweetkeys=3',
                       'sweetkeys=-1', 'sweetusb=4', 'sweetusb=-1', 'ufsrw=1'):
            with self.assertRaises(ValueError):
                mkpayload.platform_cmdline(blob, unsafe)
        with self.assertRaises(ValueError):
            mkpayload.platform_cmdline(blob, 'x'*mkpayload.BOOT_LINE_LENGTH)
        self.assertEqual(mkpayload.platform_cmdline(adt.serialize(self.build()),
                                                   'cpus=8 sweetusb=1'),
                         'cpus=8 sweetusb=1')

    def test_matisse_root_write_requires_explicit_opt_in(self):
        blob = adt.serialize(self.build(board='matisse'))
        with self.assertRaises(ValueError):
            mkpayload.platform_cmdline(blob, 'rd=ufsroot0 ufsrw=1 rootrw=1')
        args = mkpayload.platform_cmdline(
            blob, 'rd=ufsroot0 ufsrw=1 rootrw=1', allow_matisse_root_write=True)
        self.assertIn('ufsrw=1', args.split())
        self.assertNotIn('ufsrw=0', args.split())
        self.assertIn('sweetbattery=0', args.split())
        default = mkpayload.platform_cmdline(blob, '', allow_matisse_root_write=True)
        self.assertIn('ufsrw=0', default.split())
        for value in ('2', '-1', 'bad'):
            with self.assertRaises(ValueError):
                mkpayload.platform_cmdline(blob, 'ufsrw=' + value,
                                           allow_matisse_root_write=True)

    def test_sweet_defaults_unchanged(self):
        root = self.build()
        cpus = child(root, 'cpus').children
        self.assertEqual(len(cpus), 8)
        self.assertEqual(words(cpus[0].prop('cluster-type'), 32), (0,))
        self.assertEqual(words(cpus[0].prop('cluster-id'), 32), (0,))
        for prop in ('l2-cache-size', 'l3-cache-size'):
            self.assertNotIn(prop, [name for name, _, _ in cpus[0].props])
        self.assertEqual(words(child(root, 'sweet-ufs').prop('reg')), (0x1d84000, 0x3000))
        self.assertNotIn('sweet-usb', [c.name for c in root.children])
        self.assertNotIn('sweet-keys', [c.name for c in root.children])
        self.assertNotIn('sweet-display', [c.name for c in root.children])
        gic = child(child(root, 'arm-io'), 'gic')
        self.assertEqual(words(gic.prop('virtual-timer-intid'), 32), (19,))
        self.assertEqual(mkpayload.adt_dram(adt.serialize(root)), (0xb0000000, 0x4d000000))

    def test_qemu_preserved(self):
        root = self.build(qemu=True)
        self.assertEqual(len(child(root, 'cpus').children), 1)
        self.assertNotIn('sweet-ufs', [c.name for c in root.children])
        self.assertNotIn('sweet-usb', [c.name for c in root.children])
        gic = child(child(root, 'arm-io'), 'gic')
        self.assertEqual(words(gic.prop('virtual-timer-intid'), 32), (27,))

    def test_matisse_refuses_other_memory_modes(self):
        for flags in ({'qemu': True}, {'highmem': True}):
            with self.assertRaises(ValueError):
                self.build(board='matisse', **flags)


if __name__ == '__main__':
    unittest.main()

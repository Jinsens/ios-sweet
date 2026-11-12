"""Synthetic DT contracts and read-only wireless inventory collection."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wireless', ROOT / 'tools/matisse-wireless-inventory.py')
wireless = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wireless)


def words(*values):
    return struct.pack('>%dI' % len(values), *values)


def binary_fdt(nodes):
    """Independent small DT writer; nodes are (name, properties, children)."""
    strings, offsets = bytearray(), {}

    def write(node):
        name, props, children = node
        raw = name.encode() + b'\0'
        tokens = words(1) + raw + bytes(-len(raw) % 4)
        for key, value in props.items():
            if key not in offsets:
                offsets[key] = len(strings)
                strings.extend(key.encode() + b'\0')
            tokens += words(3, len(value), offsets[key]) + value + bytes(-len(value) % 4)
        for child in children:
            tokens += write(child)
        return tokens + words(2)

    tokens = write(nodes) + words(9)
    head = words(0xd00dfeed, 56 + len(tokens) + len(strings), 56,
                 56 + len(tokens), 40, 17, 16, 0, len(strings), len(tokens))
    return head + bytes(16) + tokens + bytes(strings)


def fixture():
    return ('', {'#address-cells': words(2), '#size-cells': words(2),
                 'interrupt-parent': words(1)}, [
        ('chosen', {'bootargs': b'SERIAL=private password=secret\0',
                    'mac-address-wifi0': bytes.fromhex('aabbccddeeff')}, []),
        ('interrupt-controller', {'compatible': b'arm,gic-v3\0',
                                  'phandle': words(1), '#interrupt-cells': words(4)}, []),
        ('clocks', {'phandle': words(2), '#clock-cells': words(1)}, []),
        ('power', {'phandle': words(3), '#power-domain-cells': words(1)}, []),
        ('reserved-memory', {'#address-cells': words(2), '#size-cells': words(2),
                             'ranges': b''}, [
            ('wifi-memory', {'phandle': words(4), 'reg': words(0, 0xbe000000, 0, 0x1400000),
                             'no-map': b''}, [])]),
        ('wifi@18000000', {'compatible': b'mediatek,wifi\0',
                           'reg': words(0, 0x18000000, 0, 0x700000),
                           'interrupts': words(0, 554, 4, 0, 0, 569, 4, 0),
                           'clocks': words(2, 10, 2, 33), 'power-domains': words(3, 1),
                           'memory-region': words(4), 'serial-number': b'private\0',
                           'local-mac-address': bytes.fromhex('aabbccddeeff')}, []),
        ('btif@1100c000', {'compatible': b'mediatek,btif\0',
                           'reg': words(0, 0x1100c000, 0, 0x100, 0, 0x11301400, 0, 0x80),
                           'interrupts': words(0, 234, 4, 0, 0, 226, 4, 0)}, [])])


class WirelessInventoryTests(unittest.TestCase):
    def nodes(self):
        return wireless.parse_fdt(binary_fdt(fixture()))

    def test_registers_irq_conversion_and_provider_array(self):
        report = wireless.wireless_nodes(self.nodes())
        wifi = report['/wifi@18000000']
        self.assertEqual(wifi['registers'], [{'address': '0x18000000', 'bytes': 0x700000}])
        self.assertEqual([x['gic_intid'] for x in wifi['interrupts']], [586, 601])
        self.assertEqual([x['gic_intid'] for x in report['/btif@1100c000']['interrupts']], [266, 258])
        clocks = [x for x in wifi['references'] if x['property'] == 'clocks']
        self.assertEqual(clocks, [{'property': 'clocks', 'provider': '/clocks', 'arguments': [10]},
                                  {'property': 'clocks', 'provider': '/clocks', 'arguments': [33]}])
        self.assertIn('/power', report)
        self.assertIn('/reserved-memory/wifi-memory', report)
        self.assertNotIn('/chosen', report)
        rendered = json.dumps(report)
        for private in ('password', 'secret', 'serial-number', 'aabbccddeeff', 'local-mac-address'):
            self.assertNotIn(private, rendered)

    def test_three_cell_gic_and_ppi(self):
        nodes = self.nodes()
        nodes['/interrupt-controller']['#interrupt-cells'] = words(3)
        nodes['/btif@1100c000']['interrupts'] = words(1, 11, 4)
        del nodes['/wifi@18000000']['interrupts']
        irq, = wireless.wireless_nodes(nodes)['/btif@1100c000']['interrupts']
        self.assertEqual((irq['type'], irq['gic_intid']), ('PPI', 27))

    def test_missing_radio_empty_and_malformed_fdt_rejected(self):
        self.assertEqual(wireless.wireless_nodes({'/': {}}), {})
        blob = binary_fdt(fixture())
        for value in (b'', blob[:-1], bytes(40)):
            with self.assertRaises(ValueError):
                wireless.parse_fdt(value)

    def test_malformed_resources_fail_without_guessing(self):
        for key, value in (('reg', words(0, 1, 0)), ('reg', b'\0'),
                           ('interrupts', words(0, 554, 4)),
                           ('interrupts', words(0, 988, 4, 0)),
                           ('clocks', words(2)), ('clocks', words(99, 1)),
                           ('memory-region', words(4, 99))):
            nodes = self.nodes()
            nodes['/wifi@18000000'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                wireless.wireless_nodes(nodes)
        nodes = self.nodes()
        nodes['/clocks']['phandle'] = words(1)
        with self.assertRaisesRegex(ValueError, 'duplicate phandle'):
            wireless.wireless_nodes(nodes)

    def test_module_dependencies_are_directional(self):
        rows = wireless.module_inventory('wlan_drv_gen4m_6983 123 1 conninfra, Live 0xdead\n'
                                         'unrelated 123 0 - Live 0x0\n')
        self.assertEqual(rows, [{'name': 'wlan_drv_gen4m_6983', 'bytes': 123,
                                 'use_count': 1, 'dependents': ['conninfra']}])
        rows = wireless.module_dependencies('kernel/wlan_drv_gen4m_6983.ko: kernel/conninfra.ko\n'
                                              'kernel/unrelated.ko: kernel/wlan_drv_gen4m_6983.ko\n')
        self.assertEqual(rows, [{'module': 'kernel/wlan_drv_gen4m_6983.ko',
                                 'requires': ['kernel/conninfra.ko']}])
        for bad in ('conninfra -1 0 - Live 0x0', 'btif 1 -2 - Live 0x0',
                    'bt_drv_6983 NaN 0 - Live 0x0'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                wireless.module_inventory(bad)
        self.assertEqual(wireless.module_inventory('conninfra 0 0 - Live 0x0'),
                         [{'name': 'conninfra', 'bytes': 0, 'use_count': 0, 'dependents': []}])

    def test_platform_driver_links_retain_device_association(self):
        rows = wireless.platform_drivers('18000000.bt\t../../../bus/platform/drivers/mtk_bt\n'
                                         '1100c000.btif\t../../../bus/platform/drivers/mtk_btif\n')
        self.assertEqual(rows, [{'device': '18000000.bt', 'driver': '../../../bus/platform/drivers/mtk_bt'},
                                {'device': '1100c000.btif', 'driver': '../../../bus/platform/drivers/mtk_btif'}])
        for bad in ('18000000.bt\n', 'bt\t\n', '../bt\tdriver\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                wireless.platform_drivers(bad)

    def test_firmware_inventory_excludes_config_calibration_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('WIFI_RAM_CODE_soc7_0.bin', 'BT_RAM_CODE_MT6983.bin', 'wifi.cfg',
                         'wifi_nvram.bin', 'wlan_calibration.bin', 'other.bin'):
                (root / name).write_bytes(b'firmware')
            (root / 'wifi-alias.bin').symlink_to(root / 'WIFI_RAM_CODE_soc7_0.bin')
            rows = wireless.firmware_inventory(root)
            self.assertEqual([r['path'] for r in rows], ['BT_RAM_CODE_MT6983.bin', 'WIFI_RAM_CODE_soc7_0.bin'])
            self.assertEqual(rows[0]['sha256'], hashlib.sha256(b'firmware').hexdigest())

    def test_collector_is_read_only_and_identity_guard_precedes_fdt(self):
        requests = []

        def fake_read(adb, args, maximum=wireless.MAX_TEXT):
            requests.append(args)
            if args[:2] == ['shell', 'getprop']:
                return {'ro.product.device': b'matisse\n', 'ro.board.platform': b'mt6983\n',
                        'ro.bootmode': b'recovery\n'}[args[2]]
            if args == ['exec-out', 'cat', '/sys/firmware/fdt']:
                return binary_fdt(fixture())
            if args == ['exec-out', 'cat', '/proc/modules']:
                return b''
            if args[0] == 'shell' and '/sys/bus/platform/devices/' in args[1]:
                self.assertIn('*.bt', args[1])
                return b'18000000.bt\t../../../bus/platform/drivers/mtk_bt\n'
            return b''

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(wireless, 'adb_read', fake_read):
            report = wireless.collect('/explicit/adb', tmp)
            self.assertFalse(report['radio_ready_verified'])
            self.assertFalse(report['mmio_read'])
            self.assertEqual(report['platform_drivers'][0]['device'], '18000000.bt')
            self.assertTrue((Path(tmp) / 'live-fdt.bin').is_file())
            text = str(requests)
            for action in ('reboot', 'insmod', '/dev/mem', '/dev/wmt', '/dev/bt', 'mount ', 'setprop', 'dd '):
                self.assertNotIn(action, text)
            self.assertEqual([a[2] for a in requests if a[:2] == ['shell', 'getprop']],
                             ['ro.product.device', 'ro.board.platform', 'ro.bootmode'])
        requests.clear()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(wireless, 'adb_read', return_value=b'other\n'):
            with self.assertRaisesRegex(ValueError, 'matisse'):
                wireless.collect('/explicit/adb', tmp)
            self.assertFalse((Path(tmp) / 'live-fdt.bin').exists())


if __name__ == '__main__':
    unittest.main()

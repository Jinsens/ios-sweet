#!/usr/bin/env python3
"""Read-only MT6983 Wi-Fi/Bluetooth inventory, from an FDT or Recovery.

Never mounts partitions, loads modules, opens radio devices, reads MMIO or
reboots. Raw FDTs contain identifiers: keep --out under ignored research/.
JSON reports deliberately exclude chosen/bootargs, MACs and device serials.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess


_spec = importlib.util.spec_from_file_location(
    '_wireless_fdt', Path(__file__).with_name('matisse-gpu-inventory.py'))
_fdt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fdt)
parse_fdt = _fdt.parse_fdt
property_value = _fdt.property_value

MAX_FDT = 8 << 20
MAX_TEXT = 4 << 20
RADIO = re.compile(r'(?:^|[/,@_-])(consys|conninfra|connfem|wifi|wlan|bluetooth|btif|bt)(?:$|[/,@_-])', re.I)
MODULE = re.compile(r'^(?:conninfra\w*|connfem\w*|wmt\w*|wlan\w*|bt_drv\w*|btif\w*)$')
FW = re.compile(r'(?:wifi|wlan|bluetooth|bt[_-]|(?:^|_)soc[0-9]|mt(?:66|69|79)|wmt)', re.I)
PROPERTIES = {
    'compatible', 'status', 'reg', 'reg-names', 'interrupts', 'interrupt-parent',
    'interrupts-extended', 'interrupt-names', '#interrupt-cells',
    'interrupt-controller', '#address-cells', '#size-cells', 'ranges',
    'clocks', 'clock-names', '#clock-cells', 'power-domains', '#power-domain-cells',
    'resets', '#reset-cells', 'reset-names', 'memory-region', 'memory-region-names',
    'phandle', 'linux,phandle', 'no-map', 'reusable', 'size', 'alignment',
    'alloc-ranges', 'emi-addr', 'emi-size', 'emi-alignment', 'emi-max-addr',
    'scp-shm-addr', 'scp-shm-size', 'tcxo_support', 'flavor_bin',
    'pinctrl-names', 'pinmux', 'parts', 'vid', 'pid', 'regulator-name',
    'regulator-min-microvolt', 'regulator-max-microvolt', 'regulator-always-on',
    'regulator-boot-on', 'clock-frequency', 'laa', 'open-loop', 'epa_elna', 'bypass',
}
ARRAYS = {'clocks': '#clock-cells', 'power-domains': '#power-domain-cells',
          'resets': '#reset-cells', 'interrupts-extended': '#interrupt-cells'}


def cells(raw, what):
    if len(raw) % 4:
        raise ValueError('unaligned cells: ' + what)
    return list(struct.unpack('>%dI' % (len(raw) // 4), raw))


def parent(path):
    return path.rsplit('/', 1)[0] or '/'


def inherit(nodes, path, key, default=None):
    while True:
        if key in nodes.get(path, {}):
            return nodes[path][key]
        if path == '/':
            return default
        path = parent(path)


def one(raw, what):
    values = cells(raw, what)
    if len(values) != 1:
        raise ValueError('expected one cell: ' + what)
    return values[0]


def phandle_index(nodes):
    result = {}
    for path, props in nodes.items():
        for name in ('phandle', 'linux,phandle'):
            if name not in props:
                continue
            value = one(props[name], path + '/' + name)
            if value in result and result[value] != path:
                raise ValueError('duplicate phandle')
            result[value] = path
    return result


def references(nodes, path, handles):
    result = []
    for key, raw in nodes[path].items():
        is_list = key in ('memory-region', 'interrupt-parent', 'parts') or bool(re.fullmatch(r'pinctrl-\d+', key))
        if not (key in ARRAYS or is_list or key.endswith('-supply')):
            continue
        values, at = cells(raw, path + '/' + key), 0
        while at < len(values):
            handle = values[at]
            at += 1
            target = handles.get(handle)
            if target is None:
                raise ValueError('unresolved phandle in ' + path + '/' + key)
            count = one(nodes[target].get(ARRAYS[key], b''), target + '/' + ARRAYS[key]) if key in ARRAYS else 0
            if count > 16 or at + count > len(values):
                raise ValueError('truncated phandle arguments in ' + path + '/' + key)
            result.append({'property': key, 'provider': target,
                           'arguments': values[at:at + count]})
            at += count
    return result


def registers(nodes, path):
    raw = nodes[path].get('reg')
    if raw is None:
        return []
    # reg's cell widths belong to its immediate parent, per DTSpec 2.3.5.
    props = nodes.get(parent(path), {})
    ac = one(props.get('#address-cells', struct.pack('>I', 2)), '#address-cells')
    sc = one(props.get('#size-cells', struct.pack('>I', 1)), '#size-cells')
    values = cells(raw, path + '/reg')
    if ac not in (1, 2) or sc not in (0, 1, 2) or len(values) % (ac + sc):
        raise ValueError('unsupported/malformed reg: ' + path)
    rows = []
    for at in range(0, len(values), ac + sc):
        address = int.from_bytes(raw[at * 4:(at + ac) * 4], 'big')
        size = int.from_bytes(raw[(at + ac) * 4:(at + ac + sc) * 4], 'big') if sc else None
        if size is not None and address + size > 1 << 64:
            raise ValueError('reg address overflow: ' + path)
        rows.append({'address': '0x%x' % address, 'bytes': size})
    return rows


def interrupts(nodes, path, handles):
    raw = nodes[path].get('interrupts')
    if raw is None:
        return []
    ip = inherit(nodes, path, 'interrupt-parent')
    if ip is None:
        raise ValueError('interrupts without interrupt-parent: ' + path)
    target = handles.get(one(ip, 'interrupt-parent'))
    if target is None:
        raise ValueError('missing interrupt provider: ' + path)
    count = one(nodes[target].get('#interrupt-cells', b''), '#interrupt-cells')
    values = cells(raw, path + '/interrupts')
    if not 0 < count <= 16 or len(values) % count:
        raise ValueError('malformed interrupt list: ' + path)
    is_gic = any(c.startswith(b'arm,gic') or c == b'arm,cortex-a15-gic'
                 for c in nodes[target].get('compatible', b'').split(b'\0'))
    rows = []
    for at in range(0, len(values), count):
        specifier = values[at:at + count]
        row = {'controller': target, 'specifier': specifier}
        if is_gic and count in (3, 4):
            kind, number, flags = specifier[:3]
            if kind == 0 and number <= 987:
                row.update({'type': 'SPI', 'number': number, 'gic_intid': number + 32, 'flags': flags})
            elif kind == 1 and number <= 15:
                row.update({'type': 'PPI', 'number': number, 'gic_intid': number + 16, 'flags': flags})
            else:
                raise ValueError('unsupported GIC interrupt specifier: ' + path)
        rows.append(row)
    return rows


def wireless_nodes(nodes):
    handles = phandle_index(nodes)
    selected = {path for path, props in nodes.items()
                if not path.startswith(('/chosen', '/__')) and
                (RADIO.search(path) or RADIO.search(props.get('compatible', b'').decode('ascii', 'replace')))}
    # Include all reservation boundaries, without publishing unrelated DT nodes.
    selected.update(p for p in nodes if p.startswith('/reserved-memory/'))
    todo = list(selected)
    refs = {}
    while todo:
        path = todo.pop()
        refs[path] = references(nodes, path, handles)
        for ref in refs[path]:
            target = ref['provider']
            if target not in selected and not target.startswith('/chosen'):
                selected.add(target)
                todo.append(target)
    report = {}
    for path in sorted(selected):
        props = {key: property_value(raw, key) for key, raw in nodes[path].items()
                 if key in PROPERTIES or key.endswith('-supply') or re.fullmatch(r'pinctrl-\d+', key)}
        report[path] = {'properties': props, 'references': refs[path],
                        'registers': registers(nodes, path),
                        'interrupts': interrupts(nodes, path, handles)}
    return report


def module_inventory(text):
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or not MODULE.fullmatch(fields[0]):
            continue
        if not all(re.fullmatch(r'[0-9]+', item) for item in fields[1:3]):
            raise ValueError('invalid module size/use count: ' + fields[0])
        deps = [name for name in fields[3].strip(',').split(',') if name != '-']
        rows.append({'name': fields[0], 'bytes': int(fields[1]),
                     'use_count': int(fields[2]), 'dependents': deps})
    return rows


def platform_drivers(text):
    rows = []
    for line in text.splitlines():
        parts = line.split('\t')
        if len(parts) != 2 or not all(parts):
            raise ValueError('malformed platform device/driver association')
        device, driver = parts
        if '/' in device or '\x00' in line:
            raise ValueError('invalid platform device name')
        rows.append({'device': device, 'driver': driver})
    return rows


def module_dependencies(text):
    rows = []
    for line in text.splitlines():
        if ':' not in line:
            continue
        path, rest = line.split(':', 1)
        if MODULE.fullmatch(Path(path).name.removesuffix('.ko')):
            rows.append({'module': path, 'requires': rest.split()})
    return rows


def firmware_inventory(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('firmware root is not a directory')
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file() or not FW.search(path.name):
            continue
        rel = path.relative_to(root)
        # Never hash calibration, NVRAM, network configuration or credentials.
        if (path.suffix.lower() not in ('.bin', '.ko') or
                re.search(r'nvram|nvdata|persist|calibration|efuse|eeprom', str(rel), re.I)):
            continue
        before = path.stat()
        if before.st_size > 64 << 20:
            raise ValueError('oversized firmware/module: ' + str(rel))
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b''):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('firmware changed during inventory')
        rows.append({'path': str(rel), 'bytes': before.st_size, 'sha256': digest.hexdigest()})
    return rows


def adb_read(adb, arguments, maximum=MAX_TEXT):
    result = subprocess.run([str(adb), *arguments], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('adb read failed: ' + ' '.join(arguments[:2]))
    if len(result.stdout) > maximum:
        raise ValueError('adb response exceeded bound')
    return result.stdout


def collect(adb, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # Only the three allowlisted properties are read; no getprop bulk dump.
    props = {key: adb_read(adb, ['shell', 'getprop', key]).decode().strip()
             for key in ('ro.product.device', 'ro.board.platform', 'ro.bootmode')}
    if props['ro.product.device'] != 'matisse' or props['ro.board.platform'] != 'mt6983':
        raise ValueError('collector only supports matisse/mt6983')
    if props['ro.bootmode'] != 'recovery':
        raise ValueError('collector requires Recovery')
    fdt = adb_read(adb, ['exec-out', 'cat', '/sys/firmware/fdt'], MAX_FDT)
    nodes = wireless_nodes(parse_fdt(fdt))
    modules = module_inventory(adb_read(adb, ['exec-out', 'cat', '/proc/modules']).decode())
    # These shell programs contain no caller-supplied device paths.
    links = platform_drivers(adb_read(adb, ['shell', 'for p in /sys/bus/platform/devices/*; do case "$p" in *consys*|*conninfra*|*wifi*|*wlan*|*btif*|*/bt@*|*.bt|*/bt|*bluetooth*) if test -L "$p/driver"; then printf "%s\\t" "${p##*/}"; readlink "$p/driver"; fi;; esac; done']).decode())
    firmware_paths = adb_read(adb, ['shell', 'for p in /vendor/firmware /vendor/etc/firmware /vendor/firmware_mnt; do if test -d "$p"; then find "$p" -maxdepth 2 -type f 2>/dev/null; fi; done']).decode().splitlines()
    firmware_paths = [p for p in firmware_paths if FW.search(Path(p).name) and Path(p).suffix.lower() == '.bin']
    (out / 'live-fdt.bin').write_bytes(fdt)
    return {'source': 'live-recovery-fdt', 'device': props, 'fdt_sha256': hashlib.sha256(fdt).hexdigest(),
            'nodes': nodes, 'loaded_modules': modules, 'platform_drivers': links,
            'existing_firmware_paths': firmware_paths,
            'controller_opened': False, 'mmio_read': False, 'radio_ready_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--fdt', type=Path)
    source.add_argument('--adb', type=Path, help='explicit adb executable; reads the attached matisse')
    parser.add_argument('--out', type=Path, help='local output directory (required with --adb)')
    parser.add_argument('--modules-dep', type=Path)
    parser.add_argument('--firmware-root', type=Path, help='optional existing local firmware/module tree')
    args = parser.parse_args()
    try:
        if args.adb:
            if not args.out:
                parser.error('--adb requires --out')
            report = collect(args.adb, args.out)
        else:
            if args.fdt.stat().st_size > MAX_FDT:
                raise ValueError('oversized FDT')
            data = args.fdt.read_bytes()
            report = {'source': 'offline-fdt', 'fdt_sha256': hashlib.sha256(data).hexdigest(),
                      'nodes': wireless_nodes(parse_fdt(data)),
                      'controller_opened': False, 'mmio_read': False, 'radio_ready_verified': False}
        if args.modules_dep:
            if args.modules_dep.stat().st_size > MAX_TEXT:
                raise ValueError('oversized modules.dep')
            report['module_dependencies'] = module_dependencies(args.modules_dep.read_text())
        if args.firmware_root:
            report['firmware_files'] = firmware_inventory(args.firmware_root)
        rendered = json.dumps(report, indent=2) + '\n'
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / 'inventory.json').write_text(rendered)
        print(rendered, end='')
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()

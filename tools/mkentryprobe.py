#!/usr/bin/env python3
"""Wrap a known-good arm64 boot v4 with a same-boot entry/FDT probe.

The prefix records entry registers into the bundled Linux Image's banner,
then runs that Linux kernel at a 2 MiB-aligned address. Read SWEET-ENTRY in
recovery dmesg. It does not need UART, display, UFS, or persistent reset logs.
The source boot image is never modified. Flashing is deliberately separate.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

import mkbootimg

PREFIX_SIZE = 2 << 20
FIELDS = ('el', 'midr', 'mpidr', 'freq', 'dtb', 'sctlr', 'stage',
          'fault', 'esr', 'far', 'pc', 'gicd', 'vtimer', 'fb')
DISPLAY_RECORD_FIELDS = ('el', 'midr', 'mpidr', 'freq', 'gce3', 'gce5',
                         'stage', 'fault', 'esr', 'far', 'pc', 'gicd',
                         'vtimer', 'fb', 'mutex1', 'mutex1_mod',
                         'display_status')
LEVELS = {'entry': 0, 'board': 1, 'logstore': 2, 'mmu': 3, 'timer': 4, 'irq': 5, 'display': 6}

# For display, the same banner fields hold (first u32 << 32 | second u32).
# All bases are live-FDT lookups. No writes or mode changes. The final
# display_status word stores a 2-bit code per pair: 0=compatible missing,
# 1=reg/range invalid, 2=both MMIO reads completed (even if they read 0xffffffff).
DISPLAY_FIELDS = {
    'el': ('dsi0.CON_CTRL', 'dsi0.MODE_CTRL'),
    'midr': ('dsi0.START', 'dsi0.INTSTA'),
    'mpidr': ('dsi0.TXRX_CTRL', 'dsi0.PSCTRL'),
    'freq': ('dsi0.CMDQ_SIZE', 'dsi0.SIZE_CON'),
    'gce3': ('GCE thread 3 CURR_STATUS', 'GCE thread 3 WAIT_TOKEN'),
    'gce5': ('GCE thread 5 CURR_STATUS', 'GCE thread 5 WAIT_TOKEN'),
    'esr': ('ovl0.EN', 'ovl0.SRC_CON'),
    'far': ('ovl0.L0_ADDR', 'ovl0.L0_PITCH'),
    'pc': ('ovl0_2l.EN', 'ovl0_2l.SRC_CON'),
    'gicd': ('ovl0_2l.L0_ADDR', 'ovl0_2l.L0_PITCH'),
    'vtimer': ('mutex0.EN', 'mutex0.SOF'),
    'fb': ('mutex0.MOD0', 'mutex0.MOD1'),
    'mutex1': ('mutex0.reg1.EN', 'mutex0.reg1.SOF'),
    'mutex1_mod': ('mutex0.reg1.MOD0', 'mutex0.reg1.MOD1'),
}

DISPLAY_STATUS_SLOTS = {
    0: 'dsi0.CON_CTRL/MODE_CTRL',
    1: 'dsi0.START/INTSTA',
    2: 'dsi0.TXRX_CTRL/PSCTRL',
    3: 'dsi0.CMDQ_SIZE/SIZE_CON',
    4: 'GCE thread 3 CURR_STATUS/WAIT_TOKEN',
    5: 'GCE thread 5 CURR_STATUS/WAIT_TOKEN',
    6: 'ovl0.EN/SRC_CON',
    7: 'ovl0.L0_ADDR/L0_PITCH',
    8: 'ovl0_2l.EN/SRC_CON',
    9: 'ovl0_2l.L0_ADDR/L0_PITCH',
    10: 'mutex0.EN/SOF',
    11: 'mutex0.MOD0/MOD1',
    12: 'mutex0.reg1.EN/SOF',
    13: 'mutex0.reg1.MOD0/MOD1',
}


DISPLAY_STATUS_CODES = {
    0: 'compatible missing',
    1: 'reg or range invalid',
    2: 'both reads completed',
    3: 'reserved',
}


def decode_display_status(value):
    return {slot: (value >> (2 * slot)) & 3
            for slot in DISPLAY_STATUS_SLOTS}


def banner_record(level=None):
    record = bytearray(b'SWEET-ENTRY v1')
    offsets = {}
    fields = DISPLAY_RECORD_FIELDS if level == 'display' else FIELDS
    for name in fields:
        record += (' ' + name + '=').encode()
        offsets[name] = len(record)
        record += b'0' * 16
    record += b'\n\0'
    return bytes(record), offsets


def prepare(template, level=None):
    """Validate all source ranges and placement constraints before building."""
    header = mkbootimg.parse(template)
    if header['header_version'] != 4:
        raise ValueError('entry probe currently requires a boot v4 template')
    ks, rs = header['kernel_size'], header['ramdisk_size']
    rd_off = mkbootimg.PAGE + mkbootimg.pad(ks)
    if ks < 64 or rd_off + rs > len(template):
        raise ValueError('truncated kernel or ramdisk in template')
    compressed = template[mkbootimg.PAGE:mkbootimg.PAGE + ks]
    image = bytearray(gzip.decompress(compressed) if compressed[:2] == b'\x1f\x8b'
                      else compressed)
    if len(image) < 64 or image[56:60] != b'ARM\x64':
        raise ValueError('template kernel is not an arm64 Linux Image')
    text_offset, image_size, flags = struct.unpack_from('<3Q', image, 8)
    if text_offset or not image_size or flags & 1 or not flags & 8:
        raise ValueError('need LE Image with text_offset=0 and anywhere-placement flag')
    record, offsets = banner_record(level)
    # Do not patch the short "Linux version %s (%s)" printf format string.
    candidates = []
    at = 0
    while True:
        at = image.find(b'Linux version ', at)
        if at < 0:
            break
        end = image.find(b'\0', at)
        if end >= 0 and end - at + 1 >= len(record) and b'%' not in image[at:end]:
            candidates.append((at, end + 1))
        at += 1
    if len(candidates) != 1:
        raise ValueError('cannot uniquely locate a Linux banner large enough for probe')
    start, end = candidates[0]
    original_banner = bytes(image[start:end])
    image[start:end] = record + bytes(end - start - len(record))
    return header, image, template[rd_off:rd_off + rs], start, offsets, original_banner


def build(template_path, out_path, toolchain, level, max_image_size=None):
    template = Path(template_path).read_bytes()
    header, image, ramdisk, banner_at, offsets, old_banner = prepare(template, level)
    image_size = PREFIX_SIZE + max(len(image), struct.unpack_from('<Q', image, 16)[0])
    if max_image_size is not None and image_size > max_image_size:
        raise ValueError('wrapped Image exceeds caller-supplied safe load window')
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parent.parent
    cc = str(Path(toolchain) / 'clang') if toolchain else shutil.which('clang')
    objcopy = str(Path(toolchain) / 'llvm-objcopy') if toolchain else shutil.which('llvm-objcopy')
    if not cc or not objcopy:
        raise ValueError('pass --toolchain with clang, ld.lld, and llvm-objcopy')
    with tempfile.TemporaryDirectory(prefix='entryprobe-', dir=out_path.parent) as tmp:
        tmp = Path(tmp)
        config = f'#define ENTRY_LEVEL {LEVELS[level]}\n'
        config += f'#define ENTRY_IMAGE_SIZE {image_size}\n'
        config += f'#define ENTRY_BANNER_OFFSET {banner_at}\n'
        config += f'#define ENTRY_BANNER_SIZE {len(banner_record(level)[0])}\n'
        config += ''.join(f'#define ENTRY_{n.upper()} {o}\n' for n, o in offsets.items())
        (tmp / 'entryprobe_config.h').write_text(config)
        (tmp / 'probe.ld').write_text('''ENTRY(_head)
SECTIONS {
 . = 0;
 .text : { *(.text.head) *(.text*) }
 .rodata : { *(.rodata*) }
 .data : { *(.data*) }
 .bss : { *(.bss*) *(COMMON) }
 . = ALIGN(16);
 _end = .;
 ASSERT(. <= 0x200000, "probe exceeds prefix")
 stock_image = 0x200000;
 /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
''')
        sources = ['entryprobe.S']
        if LEVELS[level]:
            sources += ['entryprobe.c', 'board.c', 'fdt.c', 'str.c', 'logstore.c']
        if 3 <= LEVELS[level] < 6:
            sources += ['mmu.c']
        if 4 <= LEVELS[level] < 6:
            sources += ['entryprobe_timer.c']
        if level == 'display':
            sources += ['entryprobe_display.c']
        objects = []
        flags = ['--target=aarch64-none-elf', '-O2', '-ffreestanding', '-nostdlib',
                 '-fno-pic', '-mgeneral-regs-only', '-mstrict-align',
                 '-fno-stack-protector', '-fno-builtin', '-Wall', '-Wextra', '-Werror',
                 '-I' + str(tmp), '-I' + str(repo / 'shim')]
        for source in sources:
            obj = tmp / (source + '.o')
            subprocess.run([cc, *flags, '-c', str(repo / 'shim' / source), '-o', str(obj)], check=True)
            objects.append(str(obj))
        elf = tmp / 'probe.elf'
        subprocess.run([cc, *flags, '-static', '-fuse-ld=lld', '-Wl,--build-id=none',
                        '-T', str(tmp / 'probe.ld'), *objects, '-o', str(elf)], check=True)
        raw = tmp / 'probe.bin'
        subprocess.run([objcopy, '-O', 'binary', str(elf), str(raw)], check=True)
        prefix = raw.read_bytes()
        if len(prefix) > PREFIX_SIZE:
            raise ValueError('probe larger than prefix')
        wrapped = prefix + bytes(PREFIX_SIZE - len(prefix)) + image
        wrapped += bytes(image_size - len(wrapped))
        packed = gzip.compress(wrapped, compresslevel=9, mtime=0)
        mkbootimg.build(packed, ramdisk, header['cmdline'].decode(), str(out_path),
                        header_version=4, os_version=header['os_version'],
                        signature_size=header['signature_size'], signature=header['signature'])
        shutil.copyfile(elf, out_path.with_suffix('.elf'))
    manifest = {
        'level': level, 'source_boot_sha256': hashlib.sha256(template).hexdigest(),
        'boot_sha256': hashlib.sha256(out_path.read_bytes()).hexdigest(),
        'boot_bytes': out_path.stat().st_size, 'image_size': image_size,
        'stock_entry_offset': PREFIX_SIZE, 'banner_offset_in_stock_image': banner_at,
        'display_fields': DISPLAY_FIELDS if level == 'display' else None,
        'display_status_slots': DISPLAY_STATUS_SLOTS if level == 'display' else None,
        'display_status_codes': DISPLAY_STATUS_CODES if level == 'display' else None,
        'banner_fields': offsets, 'original_banner': old_banner.rstrip(b'\0').decode(),
    }
    out_path.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: v for k, v in manifest.items() if k not in ('original_banner', 'banner_fields')}, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--template', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--level', choices=LEVELS, default='entry')
    ap.add_argument('--toolchain', default=os.environ.get('LLVM_BIN'))
    ap.add_argument('--max-image-size', type=lambda s: int(s, 0),
                    help='safe load-window size measured from this device; refuse to exceed')
    a = ap.parse_args()
    try:
        build(a.template, a.out, a.toolchain, a.level, a.max_image_size)
    except (ValueError, OSError) as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()

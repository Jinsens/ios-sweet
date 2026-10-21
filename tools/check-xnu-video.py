#!/usr/bin/env python3
"""Emulate the actual preprocessed XNU Boot_Video bootstrap mapping (no phone).

Requires a previously generated SWEET build tree + CI log, NDK LLVM, and
Unicorn. This checks descriptors, not hardware display output or MMU timing.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tree', required=True)
    ap.add_argument('--log', required=True)
    ap.add_argument('--llvm', required=True)
    ap.add_argument('--python-deps')
    args = ap.parse_args()
    if args.python_deps:
        sys.path.insert(0, args.python_deps)
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_X20, UC_ARM64_REG_X25
    spec = importlib.util.spec_from_file_location('xnu_cc', Path(__file__).with_name('xnu-cc.py'))
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    tree = str(Path(args.tree).resolve())
    commands = cc.commands(args.log)
    _, original = next(v for k, v in commands.items() if k.endswith('/arm64/start.s'))
    command = ['/usr/bin/clang', '-target', 'arm64-apple-macos14.0', '-E', '-P']
    skip = False
    for a in original:
        if skip:
            skip = False
            continue
        if a in cc.DROP or a == '-fsyntax-only':
            continue
        if a in cc.DROP_WITH_ARG:
            skip = True
            continue
        a = cc.CI_SDK_PLATFORM.sub(tree + '/sdk-kernel-platform/platform', a)
        command.append(a.replace(cc.CI_DXB, tree))
    preprocessed = subprocess.check_output(command, cwd=cc.component_dirs(tree)['osfmk'], text=True)
    # This is the real start.s fragment after macro expansion of C constants,
    # not a Python reimplementation of the algorithm.
    end = preprocessed.index('Lsweet_fb_map_done:') + len('Lsweet_fb_map_done:')
    start = preprocessed.rfind('ldr\tx6, [x20,', 0, end)
    if start < 0:
        start = preprocessed.rfind('ldr x6, [x20,', 0, end)
    if start < 0:
        raise RuntimeError('Boot_Video mapping fragment missing')
    fragment = preprocessed[start:end]
    fragment = fragment.replace('_sweet_boot_fb_l2@pageoff', ':lo12:sweet_boot_fb_l2')
    fragment = fragment.replace('_sweet_boot_fb_l2@page', 'sweet_boot_fb_l2')
    mov64 = '''.macro MOV64 reg, value
 movk \\reg, #(((\\value) >> 48) & 65535), lsl #48
 movk \\reg, #(((\\value) >> 32) & 65535), lsl #32
 movk \\reg, #(((\\value) >> 16) & 65535), lsl #16
 movk \\reg, #((\\value) & 65535)
.endm
'''
    assembly = mov64 + '.text\n.global _test\n_test:\n' + fragment + '\nnop\n.balign 16384\nsweet_boot_fb_l2: .zero 16384\n'
    # Strip the C ULL suffixes accepted by Apple's assembler, if present.
    assembly = re.sub(r'(?<=[0-9a-fA-F])ULL\b', '', assembly)
    llvm = Path(args.llvm)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'test.s').write_text(assembly)
        subprocess.run([str(llvm/'clang'), '-target', 'aarch64-none-elf', '-nostdlib',
                        '-Wl,-Ttext=0x100000,-e,_test,--build-id=none', str(tmp/'test.s'), '-o', str(tmp/'test.elf')], check=True)
        subprocess.run([str(llvm/'llvm-objcopy'), '-O', 'binary', str(tmp/'test.elf'), str(tmp/'test.bin')], check=True)
        raw = (tmp/'test.bin').read_bytes()
        symbols = subprocess.check_output([str(llvm/'llvm-nm'), str(tmp/'test.elf')], text=True)
        done = int(next(l.split()[0] for l in symbols.splitlines() if l.endswith(' Lsweet_fb_map_done')), 16)
        alternate = int(next(l.split()[0] for l in symbols.splitlines() if l.endswith(' sweet_boot_fb_l2')), 16)
        block = 1 << 25
        attrs = (1 << 54) | (1 << 53) | (2 << 8) | (1 << 10) | (1 << 2) | 1
        cases = [
            ('matisse-high-RAM', 0xfcb3f000, 1440, 3200, 5760, 32, 0x100000000, 0x12e000000, True, True),
            ('sweet', 0x9c000000, 1080, 2400, 4320, 32, 0xb0000000, 0x4d000000, True, True),
            ('two-block-offset', 0xfdfff000, 1440, 3200, 5760, 32, 0x100000000, 0x12e000000, True, True),
            ('another-L1', 0xfcb3f000, 1440, 3200, 5760, 32, 0x1000000000, 0x10000000, False, True),
            ('null-headless', 0, 1440, 3200, 5760, 32, 0x100000000, 0x12e000000, True, False),
            ('unaligned', 0xfcb3f001, 1440, 3200, 5760, 32, 0x100000000, 0x12e000000, True, False),
            ('wrong-depth', 0xfcb3f000, 1440, 3200, 5760, 16, 0x100000000, 0x12e000000, True, False),
            ('short-stride', 0xfcb3f000, 1440, 3200, 1440, 32, 0x100000000, 0x12e000000, True, False),
            ('oversize', 0xfcb3f000, 4096, 8192, 16384, 32, 0x100000000, 0x12e000000, True, False),
            ('RAM-overlap', 0x100004000, 1440, 3200, 5760, 32, 0x100000000, 0x12e000000, True, False),
            ('L1-crossing', 0xfffffff000, 1440, 3200, 5760, 32, 0x100000000, 0x12e000000, True, False),
        ]
        for name, base, width, height, stride, depth, ram, size, reuse, valid in cases:
            u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
            u.mem_map(0x100000, (len(raw)+0x3fff) & ~0x3fff)
            u.mem_write(0x100000, raw)
            u.mem_map(0x400000, 0x10000)
            root, l2, ba = 0x400000, 0x404000, 0x40c000
            words = [0] * 2048
            if reuse:
                words[(base >> 36) & 2047] = l2 | 3
            u.mem_write(root, struct.pack('<2048Q', *words))
            # A pre-existing RAM mapping must be preserved bit-for-bit.
            ram_slot = ((ram >> 25) & 2047) * 8
            sentinel = ram | 0x701
            u.mem_write(l2+ram_slot, struct.pack('<Q', sentinel))
            boot = bytearray(736)
            for offset, value in [(16, ram), (24, size), (40, base), (56, stride), (64, width), (72, height), (80, depth)]:
                struct.pack_into('<Q', boot, offset, value)
            u.mem_write(ba, bytes(boot))
            u.reg_write(UC_ARM64_REG_X20, ba)
            u.reg_write(UC_ARM64_REG_X25, root)
            before = bytes(u.mem_read(l2, 16384))
            u.emu_start(0x100000, done, count=100000)
            assert struct.unpack('<Q', u.mem_read(l2+ram_slot, 8))[0] == sentinel, name
            if valid:
                table = l2 if reuse else alternate
                first, last = base & ~(block-1), (base+stride*height-1) & ~(block-1)
                for pa in range(first, last+1, block):
                    value = struct.unpack('<Q', u.mem_read(table+((pa>>25)&2047)*8, 8))[0]
                    assert value == pa | attrs, (name, hex(value), hex(pa | attrs))
            else:
                assert bytes(u.mem_read(l2,16384)) == before, name
            print('PASS', name)
    return 0


if __name__ == '__main__':
    sys.exit(main())

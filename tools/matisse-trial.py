#!/usr/bin/env python3
"""Build a shim-gpu trial image and its one-shot runner from the previous one.

  trial.py 2488 --kernel-sha SHA --note "what changed" [--cmdline FILE]

Takes research/matisse/20261109-resident-gpu/shim<PREV>-ui-run-once.py as the
template, so the runner keeps the same device handling, and only the image,
its hashes and the authorization text change. Registers the build id in
tools/matisse-shim-gpu-image.py the first time it is used.
"""
import argparse
import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRIALS = ROOT / 'research/matisse/20261109-resident-gpu'
IMAGE_TOOL = ROOT / 'tools/matisse-shim-gpu-image.py'
KERNEL = ROOT / 'xnu/out/local/build/xnu.obj/DEVELOPMENT_ARM64_SWEET/kernel.development.sweet'
BASELINE = ROOT / 'backup/recovery-20261021/boot_a-live-20261021.img'
FIRMWARE = ROOT / 'research/matisse/20261031-gpu/shim2333-path-ready/csf.bin'
FIRMWARE_SHA = 'de22d2fc06bc8a859968499a6e609c27fe838be062bf5c97d92ea9f904f7229a'
TEMPLATE = ROOT / 'backup/boot_a-stock-trim.img'
TEMPLATE_SHA = 'fe6e8fbf321ea542eb060efcda27b70ae96065e82cc871d4c71082b7268c220d'
ADT = ROOT / 'research/matisse/20261031-hs/matisse-userland-native-keys-2317.adt'
ADT_SHA = '241d54a83d3073000a71048f425f641ee1912ab83b91f2304ecf1b114f9ad654'
FROZEN = ROOT / 'research/matisse/20261031-hs/frozen-shim-2313/shim'
NDK = '/Users/xiaohuasheng/Library/Android/sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/darwin-x86_64/bin'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('trial', type=int)
    ap.add_argument('--kernel-sha', required=True)
    ap.add_argument('--note', required=True, help='one sentence for the journal')
    ap.add_argument('--cmdline', default='cmdline2478.txt')
    a = ap.parse_args()

    trial = a.trial
    build_id = int(str(trial), 16)   # trial numbers are hex digits: 2488 -> 0x2488
    prev = trial - 1
    while not (TRIALS / f'shim{prev}-ui-run-once.py').exists():
        prev -= 1
    label = f'shim{trial}'
    te = TRIALS / f'{label}-te'

    # 1. register the build id
    tool = IMAGE_TOOL.read_text()
    if f'0x{build_id:x}' not in tool:
        prev_id = int(str(prev), 16)
        tool = tool.replace(f'0x{prev_id:x}', f'0x{prev_id:x}, 0x{build_id:x}')
        IMAGE_TOOL.write_text(tool)
        print(f'registered build id 0x{build_id:x}')

    # 2. build the image
    subprocess.run([
        sys.executable, str(IMAGE_TOOL),
        '--build-id', hex(build_id), '--vendor-leaves', '--mcu-control', '2',
        '--global-init', '--out', str(te),
        '--firmware', str(FIRMWARE), '--firmware-sha256', FIRMWARE_SHA,
        '--kernel', str(KERNEL), '--kernel-sha256', a.kernel_sha,
        '--template', str(TEMPLATE), '--template-sha256', TEMPLATE_SHA,
        '--stock', str(TEMPLATE), '--stock-sha256', TEMPLATE_SHA,
        '--adt', str(ADT), '--adt-sha256', ADT_SHA,
        '--frozen', str(FROZEN),
        '--cmdline', str(TRIALS / a.cmdline),
        '--ndk', NDK,
        '--autorecovery-seconds', '180', '--allow-matisse-root-write',
    ], check=True, stdout=subprocess.DEVNULL)

    image = te / f'matisse-shim-gpu{trial}.img'
    data = image.read_bytes()
    ih = sha(data)
    ph = sha(data + BASELINE.read_bytes()[len(data):])

    # 3. the runner, from the previous trial's
    src = (TRIALS / f'shim{prev}-ui-run-once.py').read_text()
    prev_ih = re.search(r"^IH='([0-9a-f]{64})'", src, re.M).group(1)
    prev_ph = re.search(r"^PH='([0-9a-f]{64})'", src, re.M).group(1)
    new = src.replace(prev_ih, ih).replace(prev_ph, ph)
    new = new.replace(f'shim{prev}-ui', f'{label}-ui').replace(f'shim{prev}-te', f'{label}-te')
    new = new.replace(f'matisse-shim-gpu{prev}.img', f'matisse-shim-gpu{trial}.img')
    new = re.sub(r'(?<!\d)0x' + f'{int(str(prev), 16):x}' + r'(?![0-9a-f])', hex(build_id), new)
    new = new.replace(f'248{prev % 10} ', f'{trial} ')
    new = re.sub(r"authorization='[^']*'",
                 f"authorization='2026-11-09 user authorized continuing GPU acceleration and "
                 f"device tests. {trial}: {a.note.replace(chr(39), chr(92) + chr(39))} "
                 f"Restore full boot_a after this trial.'", new)
    runner = TRIALS / f'{label}-ui-run-once.py'
    runner.write_text(new)

    # 4. the length assertions and the image name inside it
    for line in new.splitlines():
        m = re.search(r'len\(IMAGE\.read_bytes\(\)\)==(\d+)', line)
        if m and int(m.group(1)) != len(data):
            runner.write_text(new.replace(m.group(1), str(len(data))))
            new = runner.read_text()
            print(f'length assertion {m.group(1)} -> {len(data)}')
    print(f'image {image.name} {len(data)} bytes')
    print(f'IH={ih}')
    print(f'PH={ph}')
    print(f'runner {runner.name}')


if __name__ == '__main__':
    main()

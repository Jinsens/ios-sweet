#!/usr/bin/env python3
"""Decode the SWEET boot-progress marks (kernel patch 0095).

  sweet-marks.py [FILE] [--pull]

The kernel rewrites a record block into the LAST 4 KiB block of the userdata
partition on every sweet_mark() call, so after a hang the last step it
reached is still on disk. With --pull the block is read from a phone in
recovery over adb; otherwise FILE must be a 4096-byte dump of that block.

Layout: "SWEETMRK" | u32 version | u32 count | count * { u32 code, pad,
u64 a, u64 b }.
"""
import struct
import subprocess
import sys

NAMES = {
    1: 'mtu3 probe entry',
    2: 'adt /sweet-usb found',
    3: 'ml_io_map(MAC)',
    4: 'ml_io_map(IPPC)',
    5: 'read IPPC_HW_ID',
    6: 'read IPPC_HW_SUB_ID',
    7: 'read IPPC_DEV_CAP',
    8: 'mt_powered() ok',
    9: 'read DEVICE_CONF/EP0CSR/MISC/PM',
    10: 'userdata geometry known (mark channel live)',
    11: 'sweet_ufs_init -> sweet_usb_probe',
    12: 'sweet_usb_probe returned',
    13: 'sweet_keys_init returned',
    14: 'sweet_touch_init returned',
    15: 'read CAP_EP*FFSZ',
    20: 'mt_start entry',
    21: 'kmem_alloc_contig',
    22: 'pmap_find_phys',
    23: 'mt_if_create',
    24: 'mt_hw_init returned',
    30: 'hw_init entry',
    31: 'pull-up off',
    32: 'IPPC DEV_RST_CTRL toggle',
    33: 'IPPC PW_CTRL2 (port enable)',
    34: 'IPPC U3_CTRL_0P',
    35: 'IPPC U2_CTRL_0P',
    36: 'interrupt status cleared',
    37: 'vendor csr init',
    38: 'DEVICE_CONF=0 + VBUS force',
    39: 'TXDEEMPH',
    40: 'USB3 off + HS enable',
    41: 'EP0 reset state',
    42: 'soft connect (D+ pull-up)',
    50: 'bsd_init -> vfs_mountroot',
    51: 'root mounted',
    52: 'root mount FAILED (a=errno)',
    53: 'IOSecureBSDRoot done',
    54: 'VFS_ROOT (root vnode) ok',
    55: 'vfs_mountroot entry',
    56: 'mountroot hook returned (a=errno)',
    60: 'bsd_init -> load_init_program',
    61: 'load_init_program returned',
    62: 'load_init_program entry',
    63: 'init candidate tried (a=index, b=errno)',
    64: 'init exec failed, about to panic (a=index, b=errno)',
    65: 'load_init_program_at_path entry',
}


def pull() -> bytes:
    adb = subprocess.run(['which', 'adb'], capture_output=True, text=True).stdout.strip()
    adb = adb or f'{__import__("os").path.expanduser("~")}/Library/Android/sdk/platform-tools/adb'
    dev = subprocess.run([adb, 'shell', 'readlink -f /dev/block/by-name/userdata'],
                         capture_output=True, text=True).stdout.strip()
    size = int(subprocess.run([adb, 'shell', f'blockdev --getsize64 {dev}'],
                              capture_output=True, text=True).stdout.strip())
    skip = size // 4096 - 1
    out = subprocess.run([adb, 'exec-out', f'dd if={dev} bs=4096 skip={skip} count=1 2>/dev/null'],
                         capture_output=True).stdout
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    data = pull() if '--pull' in sys.argv or not args else open(args[0], 'rb').read()
    if len(data) < 4096:
        sys.exit(f'short read: {len(data)} bytes')
    magic, version, count = struct.unpack_from('<8sII', data, 0)
    if magic != b'SWEETMRK':
        sys.exit(f'no SWEETMRK record ({magic!r}): the kernel never reached '
                 'sweet_ufs_init, or the marks were overwritten')
    if count > 96:
        sys.exit(f'bad count {count}')
    print(f'# version {version}, {count} marks')
    for i in range(count):
        code, _pad, a, b = struct.unpack_from('<IIQQ', data, 16 + i * 24)
        name = NAMES.get(code, f'code {code}')
        print(f'{i:3d}  {code:3d}  {name:<40s} a=0x{a:x} b=0x{b:x}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

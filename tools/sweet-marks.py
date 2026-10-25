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
    86: 'display refresh: a=calls b=pushes',
    87: 'legacy display white probe',
    90: 'console mapping: a=VA b=physical address',
    91: 'console PAR: a=first pixel b=last pixel',
    93: 'display: a=stride<<32|height b=pre-poll INTSTA',
    94: 'LK DSI: a=CON_CTRL b=TXRX_CTRL',
    95: 'LK DSI: a=CMDQ_SIZE b=CMDQ0',
    96: 'touch SPI6 probe (a=error/id, b=CFG1/CFG2)',
    97: 'touch down (a=finger/raw-x, b=raw-y)',
    98: 'touch up (a=finger/panel-x, b=panel-y)',
    99: 'kernel build ID: a=build b=source line',
}


DISP_FIELDS = ('stage reached', 'width<<16|height', 'stride', 'clk gate0',
               'clk gate1', 'clk gate2', 'DSI mode', 'DSI PSCTRL',
               'DSI SIZE_CON', 'DSI VACT', 'DSI BUF_CON0', 'DSI INTSTA',
               'OVL_EN', 'OVL_L0_ADDR', 'OVL_L0_PITCH', 'fb phys')


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


def decode(data: bytes) -> str:
    if len(data) < 4096:
        raise ValueError(f'short read: {len(data)} bytes')
    magic, version, count = struct.unpack_from('<8sII', data, 0)
    if magic != b'SWEETMRK':
        raise ValueError(f'no SWEETMRK record ({magic!r}): no persisted boot marks')
    if version != 1:
        raise ValueError(f'unsupported version {version}')
    if count > 96:
        raise ValueError(f'bad count {count}')
    records = [struct.unpack_from('<IIQQ', data, 16 + i * 24) for i in range(count)]
    build = next((a for code, _pad, a, b in records if code == 99), None)
    lines = [f'# version {version}, {count} marks']
    if build is None:
        lines.append('# No build ID: this may be a stale record from an earlier boot.')
    else:
        lines.append(f'# kernel build ID 0x{build:x}')
    for i, (code, _pad, a, b) in enumerate(records):
        if 70 <= code <= 85:
            field = DISP_FIELDS[code - 70]
            lines.append(f'{i:3d}  {code:3d}  display {field:<28s} = 0x{b:x}')
            continue
        name = NAMES.get(code, f'code {code}')
        if code == 92:
            name = ('display: a=busy skips b=last INTSTA' if build is not None and build >= 0x2302
                    else 'legacy display: a=TE timeouts b=pushes')
        lines.append(f'{i:3d}  {code:3d}  {name:<40s} a=0x{a:x} b=0x{b:x}')
        if code == 91:
            for label, par in (('first', a), ('last', b)):
                if par & 1:
                    lines.append(f'     {label}: translation FAULT (PAR=0x{par:x})')
                else:
                    attr = par >> 56
                    lines.append(f'     {label}: PA page=0x{par & 0x0000fffffffff000:x}, '
                                 f'attributes=0x{attr:02x}' + (' (Normal-NC)' if attr == 0x44 else ''))
    return '\n'.join(lines)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    data = pull() if '--pull' in sys.argv or not args else open(args[0], 'rb').read()
    try:
        print(decode(data))
    except ValueError as e:
        sys.exit(str(e))
    return 0


if __name__ == '__main__':
    sys.exit(main())

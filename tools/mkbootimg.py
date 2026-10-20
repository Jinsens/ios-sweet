#!/usr/bin/env python3
"""Minimal Android boot image packer (header v2 and v4).

Replaces the AOSP mkbootimg for the two boards this project boots:

  v2  Qualcomm ABL (sweet): header 1660 bytes, optional dtb after the
      ramdisk, page size 4096.
  v4  MediaTek LK (matisse): header 1584 bytes plus a boot signature block;
      there is no kernel address in the header, LK takes it from mboot.

--template copies the signature size and bytes (and the os_version) from an
existing image, so a rebuilt image keeps the shape the bootloader expects and
only fails AVB on the hash - which an unlocked device accepts.

  mkbootimg.py --template stock/boot.img --kernel Image --ramdisk rd.bin \
               --out boot.img
"""
import argparse
import struct
import sys

PAGE = 4096
BOOT_MAGIC = b'ANDROID!'
V4_HEADER = 1584
V2_HEADER = 1660


def pad(n, page=PAGE):
    return (n + page - 1) // page * page


def parse(b):
    """Return the header fields of a v0-v4 boot image."""
    if b[:8] != BOOT_MAGIC:
        raise SystemExit('not an Android boot image')
    hv, = struct.unpack_from('<I', b, 40)
    if hv >= 3:
        ks, rs, osv, hsz = struct.unpack_from('<4I', b, 8)
        cmd = b[44:44 + 1536].split(b'\0')[0]
        sig_size, = struct.unpack_from('<I', b, 1580)
        off = PAGE + pad(ks) + pad(rs)
        sig = b[off:off + sig_size]
        dtb = b''
    else:
        (ks, ka, rs, ra, ss, sa, ta, ps, osv) = struct.unpack_from('<9I', b, 8)
        cmd = (b[64:64 + 512].split(b'\0')[0] +
               b[608:608 + 1024].split(b'\0')[0])
        sig_size, sig = 0, b''
        rds, rdo, hsz = struct.unpack_from('<IQI', b, 1632)
        ds, da = struct.unpack_from('<IQ', b, 1648)
        off = PAGE + pad(ks) + pad(rs) + pad(ss) + pad(rds)
        dtb = b[off:off + ds]
    return dict(header_version=hv, os_version=osv, cmdline=cmd,
                signature_size=sig_size, signature=sig, dtb=dtb,
                kernel_size=ks, ramdisk_size=rs, page_size=PAGE)


def build(kernel, ramdisk, cmdline, out, header_version=4, os_version=0,
          signature_size=0, signature=b'', dtb=None):
    ks, rs = len(kernel), len(ramdisk)
    cmd = cmdline.encode()[:1535]
    body = kernel + b'\0' * (pad(ks) - ks)
    body += ramdisk + b'\0' * (pad(rs) - rs)

    if header_version >= 3:
        hdr = bytearray(V4_HEADER)
        struct.pack_into('<8s4I', hdr, 0, BOOT_MAGIC, ks, rs, os_version, V4_HEADER)
        struct.pack_into('<I', hdr, 40, header_version)
        hdr[44:44 + len(cmd)] = cmd
        struct.pack_into('<I', hdr, 1580, signature_size)
        body += signature
    else:
        hdr = bytearray(V2_HEADER)
        struct.pack_into('<8s9I', hdr, 0, BOOT_MAGIC, ks, 0, rs, 0, 0, 0, 0, PAGE,
                         header_version)
        struct.pack_into('<I', hdr, 44, os_version)
        hdr[64:64 + len(cmd)] = cmd
        struct.pack_into('<I', hdr, 1644, V2_HEADER)
        if dtb is not None:
            struct.pack_into('<IQ', hdr, 1648, len(dtb), 0)
            body += dtb + b'\0' * (pad(len(dtb)) - len(dtb))

    img = bytes(hdr) + b'\0' * (PAGE - len(hdr)) + body
    img += b'\0' * (pad(len(img)) - len(img))
    open(out, 'wb').write(img)
    return len(img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kernel', required=True)
    ap.add_argument('--ramdisk')
    ap.add_argument('--dtb')
    ap.add_argument('--cmdline', default='')
    ap.add_argument('--template')
    ap.add_argument('--header-version', type=int, default=0)
    ap.add_argument('--os-version', type=lambda s: int(s, 0), default=None)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    tpl = parse(open(a.template, 'rb').read()) if a.template else None
    hv = a.header_version or (tpl['header_version'] if tpl else 2)
    osv = a.os_version if a.os_version is not None else (tpl['os_version'] if tpl else 0)
    cmd = a.cmdline or (tpl['cmdline'].decode() if tpl else '')
    sig_size = tpl['signature_size'] if tpl else 0
    sig = tpl['signature'] if tpl else b''

    kernel = open(a.kernel, 'rb').read()
    ramdisk = open(a.ramdisk, 'rb').read() if a.ramdisk else b''
    dtb = open(a.dtb, 'rb').read() if a.dtb else (tpl['dtb'] if tpl else None)

    n = build(kernel, ramdisk, cmd, a.out, hv, osv, sig_size, sig, dtb)
    print(f'{a.out}: header v{hv}, kernel {len(kernel)}, ramdisk {len(ramdisk)}, '
          f'sig {sig_size}, {n} bytes')


if __name__ == '__main__':
    main()

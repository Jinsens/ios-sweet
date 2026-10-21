#!/usr/bin/env python3
"""Minimal fastboot client for MediaTek LK on macOS.

Android platform-tools 34/37 fail on this LK with
"could not clear input pipe ... usb_read failed e00002e8" after the command
is sent, even though the bootloader processed it.  This speaks the fastboot
USB protocol directly through libusb/pyusb, which works.

Usage:
    tools/fbraw.py oem 'lkmsg' OUT.bin
    tools/fbraw.py oem 'lpmsg' OUT.bin
    tools/fbraw.py reboot-recovery
    tools/fbraw.py flash boot_a BACKUP/boot_a-stock-trim.img
    tools/fbraw.py erase misc

Requires: python3 -m pip install --user pyusb; brew install libusb
"""
import os
import sys
import usb.core
import usb.util

VID, PID = 0x18d1, 0xd00d


def find():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit('no fastboot device (18d1:d00d)')
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    cfg = dev.get_active_configuration() or dev[0]
    intf = next((i for i in cfg
                 if i.bInterfaceClass == 0xff and i.bInterfaceSubClass == 0x42
                 and i.bInterfaceProtocol == 0x03), cfg[(0, 0)])
    usb.util.claim_interface(dev, intf.bInterfaceNumber)
    eps = list(intf)
    out = next(e for e in eps if not (e.bEndpointAddress & 0x80))
    inp = next(e for e in eps if e.bEndpointAddress & 0x80)
    return dev, out, inp


def read_resp(inp, timeout=15000):
    return bytes(inp.read(64, timeout=timeout))


def command(out, inp, cmd, data=None, sink=None):
    """Send one command, stream DATA to sink, return the final response."""
    print('-> %s' % cmd, file=sys.stderr)
    try:
        out.write(cmd.encode(), timeout=15000)
    except usb.core.USBError as e:
        print('write failed: %s' % e, file=sys.stderr)

    sent = 0
    while True:
        r = read_resp(inp)
        print('<- %r' % r, file=sys.stderr)
        if r.startswith(b'INFO') or r.startswith(b'TEXT'):
            continue
        if r.startswith(b'DATA'):
            if data is not None:
                # The DATA reply to "download:" is the max packet size.
                maxpkt = int(r[4:12], 16)
                for i in range(0, len(data), maxpkt):
                    out.write(data[i:i + maxpkt], timeout=30000)
                sent = len(data)
                continue
            n = int(r[4:12], 16)
            got = 0
            while got < n:
                d = bytes(inp.read(min(65536, n - got), timeout=30000))
                got += len(d)
                if sink is not None:
                    sink.write(d)
            if sink is not None:
                sink.flush()
            print('   data %d bytes' % got, file=sys.stderr)
            continue
        return r


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    dev, out, inp = find()

    if cmd == 'oem':
        name = sys.argv[2]
        sink = open(sys.argv[3], 'wb') if len(sys.argv) > 3 else None
        r = command(out, inp, 'oem ' + name, sink=sink)
    elif cmd == 'flash':
        part, path = sys.argv[2], sys.argv[3]
        data = open(path, 'rb').read()
        r = command(out, inp, 'download:%08x' % len(data), data=data)
        if not r.startswith(b'OKAY'):
            raise SystemExit('download failed: %r' % r)
        r = command(out, inp, 'flash:' + part)
    elif cmd == 'erase':
        r = command(out, inp, 'erase:' + sys.argv[2])
    else:
        r = command(out, inp, cmd)

    if not r.startswith(b'OKAY'):
        raise SystemExit('fastboot failed: %r' % r)


if __name__ == '__main__':
    main()

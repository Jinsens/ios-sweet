#!/usr/bin/env python3
"""Decode a `SWTX1` line emitted by mkentryprobe.py --level display[-kick].

The compact record has 21 fields named 0..9,a..k; each field is 16 hex
digits holding (first u32 << 32 | second u32). Field k is the status word.

  python3 tools/decode-display-probe.py dmesg.txt [--kick]
"""
import re
import sys

FIELDS = {
    '0': ('dsi0.CON_CTRL', 'dsi0.MODE_CTRL'),
    '1': ('dsi0.START', 'dsi0.INTSTA'),
    '2': ('dsi0.TXRX_CTRL', 'dsi0.PSCTRL'),
    '3': ('dsi0.CMDQ_SIZE', 'dsi0.SIZE_CON'),
    '4': ('dsi0.MEM_CONTI', 'dsi0.BUF_CON0'),
    '5': ('dsi0.BUF_CON1', 'dsi0.TX_BUF_RW_TIMES'),
    '6': ('dsi0.CMD_TYPE1_HS', 'dsi0.DEBUG_SEL'),
    '7': ('dsi0.PHY_LCCON', 'dsi0.PHY_LD0CON'),
    '8': ('mipi.PLL_PWR', 'mipi.PLL_CON0'),
    '9': ('dsi0.VACT_NL', 'dsi0.PHY_TIMECON0'),
    'a': ('dispsys.clk_gate0_status', 'dispsys.clk_gate1_status'),
    'b': ('dispsys.clk_gate2_status', 'disp_mutex0.CFG'),
    'c': ('disp_ovl0_2l.EN', 'disp_ovl0_2l.L0_ADDR'),
    'd': ('disp_ovl0_2l.L0_PITCH', 'disp_ovl0_2l.CON0'),
    'e': ('disp_ovl0_2l.SRC_CON', 'disp_ovl0_2l.SRC_SIZE0'),
    'f': ('disp_mutex0.EN', 'disp_mutex0.SOF'),
    'g': ('disp_mutex0.MOD0', 'disp_mutex0.MOD1'),
    'h': ('disp_mutex0.INTEN', 'disp_mutex0.INTSTA'),
    'i': ('GCE thread 3 CURR_STATUS', 'GCE thread 3 WAIT_TOKEN'),
    'j': ('GCE thread 5 CURR_STATUS', 'GCE thread 5 WAIT_TOKEN'),
}
KICK_FIELDS = {
    'i': ('kick last INTSTA', 'kick completed frames'),
    'j': ('kick TE seen', 'kick status code'),
}
CODES = {0: 'ok', 1: 'FDT node missing', 2: 'DSI clock gates off',
         3: 'unexpected DSI state', 4: 'OVL not scanning boot fb',
         5: 'cmd-mode buffer disabled', 6: 'no TE pulse',
         7: 'frame not completed', 8: 'some frames missed'}


def decode_line(line, kick):
    at = line.find('SWTX1')
    if at < 0:
        return None
    text = line[at:]
    fields = dict(re.findall(r'\b([0-9a-k])=([0-9a-f]{16})', text))
    if len(fields) != 21:
        return None
    names = dict(FIELDS)
    if kick:
        names.update(KICK_FIELDS)
    out = []
    for key in '0123456789abcdefghijk':
        value = int(fields[key], 16)
        hi, lo = value >> 32, value & 0xffffffff
        if key == 'k':
            stage, code = hi, lo
            out.append(f'status: stage=0x{stage:02x} code={code} '
                       f'({CODES.get(code, "unknown")})')
            continue
        a, b = names[key]
        mark = ' (lookup failed)' if value == 0xffffffffffffffff else ''
        out.append(f'{key}: {a}=0x{hi:08x} {b}=0x{lo:08x}{mark}')
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    kick = '--kick' in sys.argv
    lines = open(args[0], errors='replace') if args else sys.stdin
    found = 0
    for line in lines:
        decoded = decode_line(line, kick)
        if decoded:
            found += 1
            print(line.strip())
            for item in decoded:
                print('  ' + item)
    if not found:
        raise SystemExit('no complete SWTX1 record found')


if __name__ == '__main__':
    main()

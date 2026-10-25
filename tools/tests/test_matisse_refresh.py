"""Exercise the actual patched refresh routine with a stalled DSI controller.

This checks boot-time latency and trigger sequencing; it cannot validate the
physical panel, DMA coherency, or the register map.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('cc') and shutil.which('git'), 'needs cc and git')
class RefreshTests(unittest.TestCase):
    def test_busy_timeouts_are_throttled_and_never_retrigger_dsi(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for patch in ('0083-matisse-command-mode-refresh.patch',
                          '0095-matisse-display-mapping-and-marks.patch',
                          '0097-sweet-darwin-asm-newlines.patch'):
                subprocess.run(['git', 'apply',
                                '--include=osfmk/arm64/sweet_matisse_dsi.c',
                                str(ROOT / 'xnu/patches' / patch)], cwd=work, check=True)
            source = (work / 'osfmk/arm64/sweet_matisse_dsi.c').read_text()
            function = source.split('void\nsweet_display_refresh(int force)', 1)[1]
            function = function.split('\n}\n', 1)[0] + '\n}\n'
            function = function.replace('__asm__ volatile("yield");', ';')
            function = function.replace('__asm__ volatile("dsb sy" ::: "memory");', ';')
            harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#define DSI_INT_BUSY (1u << 31)
#define DSI_INTSTA 0x1700c
#define MUTEX_EN 0x1020
#define DSI_START 0x17000
static uint64_t ticks = 1000, disp_last;
static int disp_ready = 1, disp_busy;
static uint32_t sweet_disp_calls, sweet_disp_pushes, sweet_disp_busy_skips;
static uint32_t sweet_disp_status, sweet_disp_status_before, status;
static uint32_t offsets[32], values[32], writes;
static uint64_t disp_counter(void) { ticks += 100; return ticks; }
static uint64_t disp_frequency(void) { return 1000000; }
static uint32_t disp_read(uint32_t off) { assert(off == DSI_INTSTA); return status; }
static void disp_write(uint32_t off, uint32_t val) {
    assert(writes < 32); offsets[writes] = off; values[writes++] = val;
}
void sweet_display_refresh(int force)
''' + function + r'''
int main(void) {
    /* An idle controller accepts exactly one mutex pulse and START strobe. */
    sweet_display_refresh(0);
    assert(writes == 4 && sweet_disp_pushes == 1);
    assert(offsets[0] == MUTEX_EN && values[0] == 0);
    assert(offsets[1] == MUTEX_EN && values[1] == 1);
    assert(offsets[2] == DSI_START && values[2] == 0);
    assert(offsets[3] == DSI_START && values[3] == 1);
    for (int i = 0; i < 100; i++) sweet_display_refresh(0);
    assert(writes == 4);
    /* Permanently BUSY must cost one bounded poll per interval, not per char. */
    ticks += 200000;
    status = DSI_INT_BUSY;
    uint64_t begin = ticks;
    sweet_display_refresh(0);
    assert(ticks - begin < 21000 && sweet_disp_busy_skips == 1 && writes == 4);
    for (int i = 0; i < 100; i++) sweet_display_refresh(0);
    assert(sweet_disp_busy_skips == 1 && writes == 4);
    assert(sweet_disp_status == DSI_INT_BUSY);
    /* Recovery from BUSY works at the next interval without resetting the IP. */
    ticks += 200000;
    status = 0;
    sweet_display_refresh(0);
    assert(sweet_disp_pushes == 2 && writes == 8);
    /* Forced refresh may bypass pacing, but never bypass the exclusion guard. */
    disp_busy = 1;
    sweet_display_refresh(1);
    assert(writes == 8);
    disp_busy = 0;
    sweet_display_refresh(1);
    assert(writes == 12);
    disp_ready = 0;
    sweet_display_refresh(1);
    assert(writes == 12);
    puts("refresh contracts passed");
}
'''
            (work / 'refresh.c').write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(work / 'refresh.c'), '-o', str(work / 'refresh')], check=True)
            result = subprocess.run([str(work / 'refresh')], check=True, capture_output=True, text=True)
            self.assertIn('refresh contracts passed', result.stdout)


if __name__ == '__main__':
    unittest.main()

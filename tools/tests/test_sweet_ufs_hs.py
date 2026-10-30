"""Execute the patched UFS transition against a bounded UFSHCI/DME model.

The model separates UIC command completion from power-mode completion and
injects missing capabilities, timeouts, failed requests and corrupt reads.
No physical device or MMIO is accessed.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('cc') and shutil.which('git'), 'needs cc and git')
class UfsHsTests(unittest.TestCase):
    def test_transition_requires_completion_readback_and_matching_data(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for patch in sorted((ROOT / 'xnu/patches').glob('*.patch')):
                subprocess.run(['git', 'apply', '--include=bsd/dev/sweet_ufs.c', str(patch)],
                               cwd=work, check=True, capture_output=True)
            source = (work / 'bsd/dev/sweet_ufs.c').read_text()
            constants = '\n'.join(re.findall(
                r'^#define (?:REG_|HCS_|IS_|PA_|UFS_BLOCK\b|BOUNCE_SIZE\b|DMA_BOUNCE\b).*',
                source, re.M))

            def function(name):
                start = source.index('\nstatic ', source.index('\n' + name + '(') - 20)
                end = source.index('\n}\n', start) + 3
                return source[start:end]

            harness = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
''' + constants + r'''
#define __builtin_arm_dsb(x) ((void)(x))
static uint32_t regs[64], attrs[65536];
static unsigned ticks, sets, reads, completion_after;
static bool no_completion, bad_status, bad_readback, corrupt, cap_missing, cmd_fail;
static bool pwr_pending;
static uint8_t dma[DMA_BOUNCE + BOUNCE_SIZE];
static struct { uintptr_t dma; uint64_t first_block, nblocks; bool writable; } su;
static uint32_t ufs_rd(uint32_t off) { return regs[off/4]; }
static void ufs_wr(uint32_t off, uint32_t value) {
    if (off == REG_IS) { regs[off/4] &= ~value; return; }
    regs[off/4] = value;
    if (off != REG_UIC_CMD) return;
    uint32_t attr = regs[REG_UIC_ARG1/4] >> 16;
    regs[REG_UIC_ARG2/4] = cmd_fail ? 1 : 0;
    if (value == 2) {
        sets++;
        attrs[attr] = regs[REG_UIC_ARG3/4];
        if (attr == PA_PWRMODE) {
            /* A previous UPMS must have been cleared before this request. */
            assert(!(regs[REG_IS/4] & IS_UIC_PWRMODE));
            assert(attrs[PA_RXGEAR] == 1 && attrs[PA_TXGEAR] == 1);
            assert(attrs[PA_RXTERMINATION] == 1 && attrs[PA_TXTERMINATION] == 1);
            pwr_pending = true; completion_after = ticks + 7;
        }
    } else {
        uint32_t out = attrs[attr];
        if (cap_missing && attr == PA_MAXRXHSGEAR && value == 3) out = 0;
        if (bad_readback && attr == PA_PWRMODE) out = 0x55;
        regs[REG_UIC_ARG3/4] = out;
    }
    regs[REG_IS/4] |= IS_UIC_COMPL;
}
static void delay(unsigned us) {
    assert(us == 1); ticks++;
    if (pwr_pending && !no_completion && ticks >= completion_after) {
        regs[REG_IS/4] |= IS_UIC_PWRMODE;
        regs[REG_HCS/4] = HCS_UCRDY | (bad_status ? 0 : HCS_PWR_LOCAL);
        pwr_pending = false;
    }
}
static uint64_t mach_absolute_time(void) { return ticks; }
static uint64_t sweet_perf_us(uint64_t t) { return t; }
static int ufs_read_blocks(uint64_t lba, uint32_t n) {
    assert(lba == su.first_block && n == BOUNCE_SIZE/UFS_BLOCK && !su.writable);
    reads++; memset(dma + DMA_BOUNCE, 0xa5, n * UFS_BLOCK);
    if (corrupt && reads == 2) dma[DMA_BOUNCE + 31] ^= 1;
    return 0;
}
''' + '\n'.join(function(n) for n in
                  ('ufs_uic', 'ufs_power_mode', 'ufs_hs_g1', 'ufs_hs_validate')) + r'''
static void reset(void) {
    memset(regs, 0, sizeof(regs)); memset(attrs, 0, sizeof(attrs));
    regs[REG_HCS/4] = HCS_UCRDY;
    /* Start with stale completions: neither may satisfy the new request. */
    regs[REG_IS/4] = IS_UIC_COMPL | IS_UIC_PWRMODE;
    attrs[PA_CONNECTEDRXDATALANES] = attrs[PA_CONNECTEDTXDATALANES] = 2;
    attrs[PA_MAXRXHSGEAR] = 4;
    ticks = sets = reads = 0;
    no_completion = bad_status = bad_readback = corrupt = cap_missing = cmd_fail = pwr_pending = false;
    su.dma = (uintptr_t)dma; su.first_block = 1234;
    su.nblocks = 1000; su.writable = false;
}
int main(void) {
    reset(); assert(ufs_hs_validate() == 0 && reads == 2 && ticks >= 7 && !su.writable);
    reset(); no_completion = true;
    assert(ufs_hs_validate() == EIO && reads == 1 && ticks == 500000 && !su.writable);
    reset(); bad_status = true; assert(ufs_hs_validate() == EIO && reads == 1);
    reset(); bad_readback = true; assert(ufs_hs_validate() == EIO && reads == 1);
    reset(); cap_missing = true; assert(ufs_hs_validate() == ENOTSUP && sets == 0);
    reset(); cmd_fail = true; assert(ufs_hs_validate() == ENOTSUP && sets == 0);
    reset(); corrupt = true; assert(ufs_hs_validate() == EIO && reads == 2 && !su.writable);
    reset(); su.writable = true; assert(ufs_hs_validate() == EINVAL && !reads && !sets);
    reset(); su.nblocks = 1; assert(ufs_hs_validate() == EINVAL && !reads && !sets);
    reset(); regs[REG_HCS/4] = 0;
    assert(ufs_hs_validate() == ENOTSUP && ticks == 500000 && sets == 0);
}
'''
            (work / 'check.c').write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-Wno-format', '-fsanitize=address,undefined',
                            str(work / 'check.c'), '-o', str(work / 'check')], check=True)
            subprocess.run([str(work / 'check')], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()

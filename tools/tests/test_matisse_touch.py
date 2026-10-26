"""Exercise patched PMIF transactions and SALIXP startup with hardware mocks.

These check command addressing, completion ownership and firmware handshakes;
they cannot validate voltages, SPI waveforms or a real touch report.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FILES = ('bsd/dev/sweet_matisse_touch.c', 'bsd/dev/sweet_spmi.c',
         'bsd/dev/sweet_spmi.h')


def function(source, name):
    start = source.index('\n' + name + '(') + 1
    start = source.rfind('\n', 0, start - 1) + 1
    end = source.index('\n}\n', start) + 3
    return source[start:end]


@unittest.skipUnless(shutil.which('cc') and shutil.which('git'), 'needs cc and git')
class MatisseTouchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='matisse-touch-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        for patch in sorted((ROOT / 'xnu/patches').glob('*.patch')):
            subprocess.run(['git', 'apply', *['--include=' + p for p in FILES],
                            str(patch)], cwd=cls.work, check=True, capture_output=True)
        cls.touch = (cls.work / FILES[0]).read_text()
        cls.spmi = (cls.work / FILES[1]).read_text()

    def compile_run(self, name, source):
        path = self.work / (name + '.c')
        path.write_text(source)
        binary = self.work / name
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(path), '-o', str(binary)], check=True, capture_output=True)
        subprocess.run([str(binary)], check=True, capture_output=True)

    def test_pmif_uses_full_addresses_and_clears_ownership(self):
        defines = '\n'.join(re.findall(
            r'^#define (?:PMIF_SWINF_\w+|PMIF_CMD_EXT_REG_LONG|SWINF_\w+) .*$',
            self.spmi, re.M))
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef uintptr_t vm_offset_t;
static vm_offset_t sp_pmif = 0x1000;
static uint32_t sp_last_sta, sp_sta_pre, sp_sta_post, sp_sta_clear;
static uint32_t state, command, commands, writes;
static bool no_completion, no_clear;
static void delay(unsigned us) { (void)us; }
''' + defines + r'''
static uint32_t sp_rd(vm_offset_t base, unsigned off) {
    assert(base == sp_pmif);
    if (off == PMIF_SWINF_STA) return state;
    assert(off == PMIF_SWINF_RDATA);
    return 0x73;
}
static void sp_wr(vm_offset_t base, unsigned off, uint32_t value) {
    assert(base == sp_pmif); writes++;
    if (off == PMIF_SWINF_ACC) {
        command = value; commands++;
        state = no_completion ? 2 : SWINF_WFVLDCLR << 1;
    } else {
        assert(off == PMIF_SWINF_VLD_CLR && value == 1);
        if (!no_clear) state = 0;
    }
}
''' + function(self.spmi, 'sp_pmif_clear') + function(self.spmi, 'sp_pmif_read') + \
            function(self.spmi, 'sweet_spmi_write') + r'''
int main(void) {
    uint8_t value = 0xee;
    assert(sp_pmif_read(5, 0x1d23, &value) == 0);
    assert(command == 0xc5001d23u && commands == 1 && value == 0x73 && state == 0);
    assert(sp_pmif_read(5, 0x1e35, &value) == 0);
    assert(command == 0xc5001e35u);
    unsigned before = writes;
    assert(sweet_spmi_write(5, 0x1d23, 1) != 0 && writes == before);
    value = 0xee; no_completion = true;
    assert(sp_pmif_read(5, 0x1d23, &value) != 0 && value == 0xee);
    state = 0; no_completion = false; no_clear = true;
    assert(sp_pmif_read(5, 0x1d23, &value) != 0 && value == 0xee);
}
'''
        self.compile_run('pmif', source)

    def test_salixp_ready_and_system_info_gate_sense_on(self):
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#define FTS_EVENT_SIZE 8u
#define FTS_DUMMY_BYTES 1u
#define FTS_CMD_FIFO_READ 0x87u
static struct { bool trace; } mt = { true };
static unsigned scenario, polls, resets, sense, waits;
static void mt_wait(unsigned ms) { assert(ms == 5); waits++; }
static void sweet_mark(unsigned c, uint64_t a, uint64_t b) {
    (void)c; (void)a; (void)b;
}
static int mt_spi_xfer(const uint8_t *tx, uint8_t *rx, unsigned len) {
    assert(len <= 32); memset(rx, 0, len);
    if (tx[0] == 0xfa) {
        const uint8_t expected[] = {0xfa,0x20,0,0,0x24,0x81};
        assert(len == 6 && !memcmp(tx, expected, 6)); resets++;
        return scenario == 4 ? EIO : 0;
    }
    if (tx[0] == 0x87) {
        assert(len == 10); polls++;
        rx[2] = scenario == 1 ? 0 : (scenario == 2 ? 0xf3 : 0x03);
        return 0;
    }
    if (tx[0] == 0xa7) {
        assert(len == 28 && tx[1] == 0 && tx[2] == 0);
        rx[4] = scenario == 5 ? 0 : 0xa5; rx[5] = 1;
        rx[4 + 16] = scenario == 3 ? 0 : 0x20; rx[4 + 20] = 0x1b;
        return 0;
    }
    const uint8_t expected[] = {0xa0,0,1};
    assert(len == 3 && !memcmp(tx, expected, 3)); sense++;
    return 0;
}
''' + function(self.touch, 'mt_fts_read_event') + \
            function(self.touch, 'mt_fts_start_sensing') + r'''
int main(void) {
    assert(mt_fts_start_sensing() == 0 && resets == 1 && sense == 1);
    for (scenario = 1; scenario <= 5; scenario++) {
        sense = polls = waits = 0;
        assert(mt_fts_start_sensing() != 0 && sense == 0);
        if (scenario == 1) assert(polls == 20 && waits == 20);
    }
}
'''
        self.compile_run('salixp', source)

    def test_pmic_audit_only_reads_identity_and_four_rails(self):
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
static unsigned reads, marks;
static int sweet_spmi_init(void) { return 0; }
static int sweet_spmi_read(unsigned sid, unsigned reg, uint8_t *value) {
    const unsigned expected[] = {9,0x1bbf,0x1d23,0x1e8d,0x1e35};
    const uint8_t golden[] = {0x73,1,1,12,13};
    assert(sid == 5 && reg == expected[reads % 5]);
    *value = golden[reads++ % 5]; return 0;
}
static void delay(unsigned us) { assert(us == 1000); }
static void sweet_mark(unsigned c, uint64_t a, uint64_t b) {
    assert(c == 201 && a == (uint64_t)marks << 32);
    assert(b == 0x0d0c010173ULL); marks++;
}
''' + function(self.touch, 'mt_pmic_audit') + r'''
int main(void) { mt_pmic_audit(); assert(reads == 40 && marks == 8); }
'''
        self.compile_run('audit', source)


if __name__ == '__main__':
    unittest.main()

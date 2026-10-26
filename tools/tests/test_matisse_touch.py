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

    def test_touch_supply_writes_are_masked_verified_and_limited(self):
        defines = '\n'.join(re.findall(
            r'^#define (?:PMIF_SWINF_\w+|PMIF_CMD_EXT_REG_LONG|SWINF_\w+) .*$',
            self.spmi, re.M))
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef uintptr_t vm_offset_t;
static vm_offset_t sp_pmif = 0x1000;
static bool sp_ready = true, sp_pmif_ok = true;
static void *sp_lock = (void *)1;
static unsigned state, command, writes, data;
static bool stuck, ignore_write;
static uint8_t identity = 0x73, en8, en3, sel8 = 0x0c, sel3 = 0x04;
static void delay(unsigned us) { assert(us == 1); }
static void IOLockLock(void *lock) { assert(lock == sp_lock); }
static void IOLockUnlock(void *lock) { assert(lock == sp_lock); }
static uint8_t *reg(unsigned addr) {
    switch (addr) {
    case 9: return &identity;
    case 0x1bbf: return &en8;
    case 0x1d23: return &en3;
    case 0x1e8d: return &sel8;
    default: assert(addr == 0x1e35); return &sel3;
    }
}
static int sp_pmif_read(unsigned sid, unsigned addr, uint8_t *value) {
    assert(sid == 5); *value = *reg(addr); return 0;
}
''' + defines + r'''
static uint32_t sp_rd(vm_offset_t base, unsigned off) {
    assert(base == sp_pmif && off == PMIF_SWINF_STA); return state;
}
static void sp_wr(vm_offset_t base, unsigned off, uint32_t value) {
    assert(base == sp_pmif);
    if (off == PMIF_SWINF_WDATA) { data = value; return; }
    assert(off == PMIF_SWINF_ACC && (value & 0xffff0000u) == 0xe5000000u);
    command = value; writes++;
    if (!ignore_write) *reg(value & 0xffffu) = data;
    state = stuck ? 2 : 0;
}
''' + function(self.spmi, 'sp_pmif_write_once') + \
            function(self.spmi, 'sweet_spmi_touch_configure') + r'''
int main(void) {
    assert(sweet_spmi_touch_configure(0x142a) != 0 && writes == 0);
    assert(sweet_spmi_touch_configure(9) != 0 && writes == 0);
    assert(sweet_spmi_touch_configure(0x1e8d) == 0 && writes == 0);
    assert(sweet_spmi_touch_configure(0x1e35) == 0 && writes == 1);
    assert(command == 0xe5001e35u && data == 0x0d);
    en8 = 0x80;
    assert(sweet_spmi_touch_configure(0x1bbf) == 0 && en8 == 0x81);
    assert(command == 0xe5001bbfu && data == 0x81);
    unsigned before = writes;
    en3 = 1; sel3 = 4;
    assert(sweet_spmi_touch_configure(0x1e35) != 0 && writes == before);
    en3 = 0; identity = 0;
    assert(sweet_spmi_touch_configure(0x1e35) != 0 && writes == before);
    identity = 0x73; ignore_write = true;
    assert(sweet_spmi_touch_configure(0x1e35) != 0);
    ignore_write = false; stuck = true;
    assert(sweet_spmi_touch_configure(0x1e35) != 0);
}
'''
        self.compile_run('supply-write', source)

    def test_power_sequence_keeps_reset_low_on_guard_or_transfer_failure(self):
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#define MTK_GPIO_DOUT0_CLR 0x108
#define MTK_TOUCH_RESET_MASK 8
static struct { bool platform_ready; uintptr_t pio; } mt = {true, 1};
static unsigned scenario, updates, releases;
static bool reset_low;
static uint8_t values[5];
static void delay(unsigned us) { assert(us == 5000 || us == 10000); }
static int sweet_spmi_init(void) { return scenario == 5 ? -1 : 0; }
static void mt_mmio_wr(uintptr_t base, unsigned off, unsigned value) {
    assert(base == 1 && off == MTK_GPIO_DOUT0_CLR && value == 8); reset_low = true;
}
static void sweet_mark(unsigned code, uint64_t a, uint64_t b) {
    (void)a; (void)b; assert(code >= 205 && code <= 208);
}
static unsigned index_of(unsigned addr) {
    const unsigned regs[] = {9,0x1bbf,0x1d23,0x1e8d,0x1e35};
    for (unsigned i=0; i<5; i++) if (addr == regs[i]) return i;
    assert(false); return 0;
}
static int sweet_spmi_read(unsigned sid, unsigned addr, uint8_t *value) {
    assert(sid == 5); *value = values[index_of(addr)];
    if (scenario == 4 && updates == 4 && addr == 0x1d23) *value = 0;
    return 0;
}
static int sweet_spmi_touch_configure(unsigned addr) {
    const unsigned order[] = {0x1e8d,0x1e35,0x1bbf,0x1d23};
    assert(reset_low && updates < 4 && addr == order[updates]); updates++;
    if (scenario == 3 && updates == 3) return -1;
    values[index_of(addr)] = updates == 1 ? 12 : (updates == 2 ? 13 : 1);
    return 0;
}
static int mt_touch_reset_init(void) { releases++; reset_low = false; return 0; }
''' + function(self.touch, 'mt_touch_power_on') + r'''
int main(void) {
    for (scenario=0; scenario<=6; scenario++) {
        values[0]=0x73; values[1]=values[2]=0; values[3]=12; values[4]=4;
        updates=releases=0; reset_low=false; mt.platform_ready = scenario != 6;
        if (scenario == 1) values[0]=0;
        if (scenario == 2) values[1]=1;
        int err = mt_touch_power_on();
        if (!scenario) assert(err == 0 && updates == 4 && releases == 1);
        else assert(err != 0 && releases == 0);
        if (scenario == 1 || scenario == 2) assert(updates == 0 && reset_low);
        if (scenario == 3 || scenario == 4) assert(reset_low);
    }
}
'''
        self.compile_run('power-sequence', source)


if __name__ == '__main__':
    unittest.main()

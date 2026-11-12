"""Run the actual AP-only CONN diagnostic against bounded MMIO callbacks."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r'''
#include "mt6983_conninfra.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct model {
    uint32_t registers[3];
    unsigned calls;
    int fail_at;
};

static int read_ap(void *opaque, uint64_t physical, uint32_t *value)
{
    struct model *m = opaque;
    /* Hard-coded separately from production constants: any accidental
     * CONN-domain or fourth read terminates the harness immediately.
     */
    static const uint64_t allowed[3] = {
        UINT64_C(0x1c001e04), UINT64_C(0x10001c9c), UINT64_C(0x10001c5c)
    };
    unsigned call = m->calls++;
    assert(call < 3);
    assert(physical == allowed[call]);
    assert(physical < UINT64_C(0x18000000) || physical >= UINT64_C(0x19000000));
    if ((int)call == m->fail_at) {
        *value = 0xdeadbeefu; /* A failed callback must not publish its data. */
        return -71;
    }
    *value = m->registers[call];
    return 0;
}

static struct mt6983_conninfra_report capture(struct model *m)
{
    struct mt6983_conninfra_io io = {m, read_ap};
    struct mt6983_conninfra_report r;
    memset(&r, 0xa5, sizeof(r));
    assert(mt6983_conninfra_capture(&io, &r) == 0);
    assert(m->calls == 3);
    assert(r.valid_mask == 7);
    assert(r.conn_pwr_con == m->registers[0]);
    assert(r.rx_protect == m->registers[1]);
    assert(r.tx_protect == m->registers[2]);
    assert(r.firmware_ready == 0);
    assert(r.failed_address == 0 && r.read_error == 0);
    return r;
}

int main(int argc, char **argv)
{
    struct model m = {{0xc000000du, 0, 0}, 0, -1};
    struct mt6983_conninfra_report r;
    struct mt6983_conninfra_io io = {&m, read_ap};
    const char *test;
    assert(argc == 2);
    test = argv[1];
    if (strcmp(test, "open") == 0) {
        r = capture(&m);
        assert(r.state == MT6983_CONNINFRA_HOST_GATES_OPEN && r.blockers == 0);
        /* Unrelated bits cannot turn a blocked gate into a safe one. */
        m = (struct model){{0xffffffedu, 0xfffffffeu, 0xffffefffu}, 0, -1};
        r = capture(&m);
        assert(r.state == MT6983_CONNINFRA_HOST_GATES_OPEN && r.blockers == 0);
    } else if (strcmp(test, "power") == 0) {
        const uint32_t masks[] = {0x40000000u, 0x80000000u, 4, 8};
        for (unsigned i = 0; i < 4; i++) {
            m = (struct model){{0xc000000du & ~masks[i], 0, 0}, 0, -1};
            r = capture(&m);
            assert(r.state == MT6983_CONNINFRA_HOST_BLOCKED);
            assert(r.blockers == MT6983_CONNINFRA_BLOCK_POWER);
        }
        m = (struct model){{0x12, 1, 0x1000}, 0, -1};
        r = capture(&m);
        assert(r.state == MT6983_CONNINFRA_HOST_BLOCKED);
        assert(r.blockers == 0x3fu);
    } else if (strcmp(test, "protection") == 0) {
        m.registers[1] = 1;
        r = capture(&m);
        assert(r.state == MT6983_CONNINFRA_HOST_BLOCKED);
        assert(r.blockers == MT6983_CONNINFRA_BLOCK_RX);
        m = (struct model){{0xc000000d, 0, 0x1000}, 0, -1};
        r = capture(&m);
        assert(r.blockers == MT6983_CONNINFRA_BLOCK_TX);
        assert(r.state == MT6983_CONNINFRA_HOST_BLOCKED);
    } else if (strcmp(test, "reset_isolation_clock") == 0) {
        const uint32_t power[] = {0xc000000c, 0xc000000f, 0xc000001d};
        const uint32_t blockers[] = {MT6983_CONNINFRA_BLOCK_RESET,
            MT6983_CONNINFRA_BLOCK_ISOLATION, MT6983_CONNINFRA_BLOCK_CLOCK};
        for (unsigned i = 0; i < 3; i++) {
            m = (struct model){{power[i], 0, 0}, 0, -1};
            r = capture(&m);
            assert(r.state == MT6983_CONNINFRA_HOST_BLOCKED);
            assert(r.blockers == blockers[i]);
        }
    } else if (strcmp(test, "error_stops") == 0) {
        const uint64_t addresses[] = {0x1c001e04, 0x10001c9c, 0x10001c5c};
        for (int at = 0; at < 3; at++) {
            m = (struct model){{0xc000000d, 0x2468, 0xabc}, 0, at};
            memset(&r, 0xa5, sizeof(r));
            assert(mt6983_conninfra_capture(&io, &r) == MT6983_CONNINFRA_IO);
            assert(m.calls == (unsigned)at + 1);
            assert(r.state == MT6983_CONNINFRA_READ_ERROR);
            assert(r.valid_mask == ((1u << at) - 1));
            assert(r.failed_address == addresses[at] && r.read_error == -71);
            assert(r.conn_pwr_con == (at > 0 ? 0xc000000du : 0));
            assert(r.rx_protect == (at > 1 ? 0x2468u : 0));
            assert(r.tx_protect == 0 && r.blockers == 0 && r.firmware_ready == 0);
        }
    } else if (strcmp(test, "invalid") == 0) {
        memset(&r, 0xa5, sizeof(r));
        assert(mt6983_conninfra_capture(0, &r) == MT6983_CONNINFRA_ARGUMENT);
        assert(r.state == MT6983_CONNINFRA_INVALID && r.valid_mask == 0);
        assert(r.blockers == 0 && r.conn_pwr_con == 0 && r.rx_protect == 0 && r.tx_protect == 0);
        assert(r.firmware_ready == 0 && r.read_error == 0 && r.failed_address == 0);
        io.read32 = 0;
        memset(&r, 0xa5, sizeof(r));
        assert(mt6983_conninfra_capture(&io, &r) == MT6983_CONNINFRA_ARGUMENT);
        assert(r.state == MT6983_CONNINFRA_INVALID && r.valid_mask == 0);
        assert(r.blockers == 0 && r.conn_pwr_con == 0 && r.rx_protect == 0 && r.tx_protect == 0);
        assert(r.firmware_ready == 0 && r.read_error == 0 && r.failed_address == 0);
        io.read32 = read_ap;
        assert(mt6983_conninfra_capture(&io, 0) == MT6983_CONNINFRA_ARGUMENT);
        assert(m.calls == 0);
    } else {
        abort();
    }
    puts("PASS");
    return 0;
}
'''


@unittest.skipUnless(shutil.which('cc'), 'needs C compiler')
class ConninfraActualCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mt6983-conninfra-')
        work = Path(cls.temp.name)
        source = work / 'harness.c'
        source.write_text(HARNESS)
        cls.binary = work / 'capture'
        subprocess.run([
            'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I', str(ROOT / 'xnu/wireless'), str(source),
            str(ROOT / 'xnu/wireless/mt6983_conninfra.c'), '-o', str(cls.binary),
        ], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')

    def test_open_gates_do_not_claim_firmware_ready(self):
        self.run_case('open')

    def test_both_power_requests_and_acknowledgements_are_required(self):
        self.run_case('power')

    def test_each_sleep_protection_blocks_host_access(self):
        self.run_case('protection')

    def test_reset_isolation_and_bus_clock_block_host_access(self):
        self.run_case('reset_isolation_clock')

    def test_failed_read_stops_and_never_publishes_failed_value(self):
        self.run_case('error_stops')

    def test_invalid_callback_and_arguments_perform_no_reads(self):
        self.run_case('invalid')


if __name__ == '__main__':
    unittest.main()

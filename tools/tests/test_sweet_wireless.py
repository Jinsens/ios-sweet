"""Execute the XNU wireless wrapper; replace only its raw MMIO boundary."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

HEADERS = {
    'sys/param.h': r'''
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
''',
    'sys/systm.h': r'''
#include <string.h>
int test_printf(const char *, ...);
#define printf test_printf
''',
    'pexpert/pexpert.h': r'''
int PE_parse_boot_argn(const char *, void *, int);
''',
    'pexpert/device_tree.h': r'''
typedef void *DTEntry;
enum { kSuccess = 0 };
int SecureDTLookupEntry(DTEntry, const char *, DTEntry *);
int SecureDTGetProperty(DTEntry, const char *, const void **, unsigned int *);
''',
}

HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct {
    int arg_present;
    unsigned mode;
    int dt_lookup_error, dt_property_error;
    const char *compatible;
    unsigned compatible_bytes;
    unsigned arg_calls, lookup_calls, property_calls, map_calls, unmap_calls, reads;
    unsigned live_ap, live_spm, map_fail_mask;
    uint32_t ap[0x4000 / 4], spm[0x4000 / 4];
    char log[2048];
} model;

int test_printf(const char *format, ...)
{
    size_t used = strlen(model.log);
    va_list args;
    int result;
    assert(used < sizeof(model.log));
    va_start(args, format);
    result = vsnprintf(model.log + used, sizeof(model.log) - used, format, args);
    va_end(args);
    assert(result >= 0 && (size_t)result < sizeof(model.log) - used);
    return result;
}

int PE_parse_boot_argn(const char *name, void *value, int bytes)
{
    assert(strcmp(name, "sweetconn") == 0);
    assert(bytes == (int)sizeof(unsigned));
    model.arg_calls++;
    if (model.arg_present)
        memcpy(value, &model.mode, sizeof(unsigned));
    return model.arg_present;
}

int SecureDTLookupEntry(DTEntry parent, const char *path, DTEntry *out)
{
    assert(parent == NULL && strcmp(path, "/") == 0);
    model.lookup_calls++;
    if (model.dt_lookup_error)
        return -1;
    *out = (DTEntry)&model;
    return 0;
}

int SecureDTGetProperty(DTEntry entry, const char *name,
    const void **value, unsigned int *bytes)
{
    assert(entry == (DTEntry)&model);
    assert(strcmp(name, "compatible") == 0);
    model.property_calls++;
    if (model.dt_property_error)
        return -1;
    *value = model.compatible;
    *bytes = model.compatible_bytes;
    return 0;
}

vm_offset_t ml_io_map_unmappable(vm_offset_t pa, vm_size_t bytes, unsigned flags)
{
    unsigned call = model.map_calls++;
    assert(call < 2);
    assert(bytes == 0x4000 && flags == 7);
    assert(pa == (call == 0 ? 0x10000000u : 0x1c000000u));
    if (model.map_fail_mask & (1u << call))
        return 0;
    if (call == 0) {
        assert(model.live_ap == 0);
        model.live_ap = 1;
        return (vm_offset_t)model.ap;
    }
    assert(model.live_spm == 0);
    model.live_spm = 1;
    return (vm_offset_t)model.spm;
}

void ml_io_unmap(vm_offset_t address, vm_size_t bytes)
{
    assert(bytes == 0x4000);
    assert(model.unmap_calls++ < 2);
    if (address == (vm_offset_t)model.spm) {
        assert(model.live_spm == 1);
        model.live_spm = 0;
        memset(model.spm, 0xa5, sizeof(model.spm));
    } else {
        assert(address == (vm_offset_t)model.ap);
        assert(model.live_ap == 1 && model.live_spm == 0);
        model.live_ap = 0;
        memset(model.ap, 0xa5, sizeof(model.ap));
    }
}

static uint32_t model_mmio_read32(vm_offset_t address)
{
    unsigned call = model.reads++;
    const vm_offset_t expected[] = {
        (vm_offset_t)model.spm + 0x1e04,
        (vm_offset_t)model.ap + 0x1c9c,
        (vm_offset_t)model.ap + 0x1c5c
    };
    /* This asserts the exact physical offsets after the real wrapper's
     * mappings and allowlist have translated them into virtual addresses.
     */
    assert(model.live_ap == 1 && model.live_spm == 1);
    assert(call < 3 && address == expected[call]);
    return *(uint32_t *)address;
}

static void no_hardware(void)
{
    assert(model.arg_calls == 1);
    assert(model.map_calls == 0 && model.unmap_calls == 0 && model.reads == 0);
    assert(model.live_ap == 0 && model.live_spm == 0);
}

int main(int argc, char **argv)
{
    static const char valid[] = "xiaomi,matisse\0mediatek,mt6983";
    static const char wrong_board[] = "xiaomi,sweet\0mediatek,mt6983";
    static const char wrong_soc[] = "xiaomi,matisse\0mediatek,mt6895";
    static const char only_board[] = "xiaomi,matisse";
    static const char only_soc[] = "mediatek,mt6983";
    static const char malformed[] = "xiaomi,matisse\0mediatek,mt6983\0unfinished";
    static const char reversed[] = "mediatek,mt6983\0other,board\0xiaomi,matisse";
    const char *test;
    assert(argc == 2);
    test = argv[1];
    if (strcmp(test, "reject_address") == 0) {
        struct wireless_maps maps = {(vm_offset_t)model.ap, (vm_offset_t)model.spm};
        uint32_t value = 0xdeadbeef;
        assert(wireless_read32(&maps, 0x18011000, &value) == -1);
        assert(value == 0xdeadbeef && model.reads == 0 && model.map_calls == 0);
        puts("PASS");
        return 0;
    }
    model.arg_present = 1;
    model.mode = 1;
    model.compatible = valid;
    model.compatible_bytes = sizeof(valid);
    model.spm[0x1e04 / 4] = 0xc000000d;
    model.ap[0x1c9c / 4] = 0x2468;
    model.ap[0x1c5c / 4] = 0xabc;

    if (strcmp(test, "absent") == 0)
        model.arg_present = 0;
    else if (strcmp(test, "disabled") == 0)
        model.mode = 0;
    else if (strcmp(test, "unsupported") == 0)
        model.mode = 2;
    else if (strcmp(test, "wrong_board") == 0) {
        model.compatible = wrong_board;
        model.compatible_bytes = sizeof(wrong_board);
    } else if (strcmp(test, "wrong_soc") == 0) {
        model.compatible = wrong_soc;
        model.compatible_bytes = sizeof(wrong_soc);
    } else if (strcmp(test, "only_board") == 0) {
        model.compatible = only_board;
        model.compatible_bytes = sizeof(only_board);
    } else if (strcmp(test, "only_soc") == 0) {
        model.compatible = only_soc;
        model.compatible_bytes = sizeof(only_soc);
    } else if (strcmp(test, "unterminated") == 0)
        model.compatible_bytes--;
    else if (strcmp(test, "malformed_suffix") == 0) {
        model.compatible = malformed;
        model.compatible_bytes = sizeof(malformed) - 1;
    } else if (strcmp(test, "empty") == 0)
        model.compatible_bytes = 0;
    else if (strcmp(test, "oversized") == 0)
        model.compatible_bytes = 513;
    else if (strcmp(test, "lookup_error") == 0)
        model.dt_lookup_error = 1;
    else if (strcmp(test, "property_error") == 0)
        model.dt_property_error = 1;
    else if (strcmp(test, "ap_map_failure") == 0)
        model.map_fail_mask = 1;
    else if (strcmp(test, "spm_map_failure") == 0)
        model.map_fail_mask = 2;
    else if (strcmp(test, "both_map_failures") == 0)
        model.map_fail_mask = 3;
    else if (strcmp(test, "reversed") == 0) {
        model.compatible = reversed;
        model.compatible_bytes = sizeof(reversed);
    } else
        assert(strcmp(test, "success") == 0);

    SweetWirelessStart();
    if (!model.arg_present || model.mode != 1) {
        no_hardware();
        assert(model.lookup_calls == 0 && model.property_calls == 0);
    } else if (model.map_fail_mask) {
        assert(model.lookup_calls == 1 && model.property_calls == 1);
        assert(model.map_calls == 2 && model.reads == 0);
        assert(model.unmap_calls == (model.map_fail_mask == 3 ? 0u : 1u));
        assert(model.live_ap == 0 && model.live_spm == 0);
        assert(strstr(model.log, "AP register mapping failed") != NULL);
    } else if (strcmp(test, "success") == 0 || strcmp(test, "reversed") == 0) {
        assert(model.lookup_calls == 1 && model.property_calls == 1);
        assert(model.map_calls == 2 && model.reads == 3 && model.unmap_calls == 2);
        assert(model.live_ap == 0 && model.live_spm == 0);
        assert(strstr(model.log, "rc=0 state=3 valid=7 blockers=0") != NULL);
        assert(strstr(model.log, "pwr=c000000d rx=00002468 tx=00000abc") != NULL);
        assert(strstr(model.log, "firmware=unprobed") != NULL);
    } else {
        no_hardware();
        assert(model.lookup_calls == 1);
        assert(model.property_calls == (model.dt_lookup_error ? 0u : 1u));
        assert(strstr(model.log, "unsupported diagnostic mode/board") != NULL);
    }
    puts("PASS");
    return 0;
}
'''


@unittest.skipUnless(shutil.which('cc'), 'needs C compiler')
class WirelessWrapperActualCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sweet-wireless-wrapper-')
        work = Path(cls.temp.name)
        for path, body in HEADERS.items():
            header = work / path
            header.parent.mkdir(parents=True, exist_ok=True)
            header.write_text(body)
        source = (ROOT / 'xnu/wireless/sweet_wireless.c').read_text()
        # Only the physical load plus ARM barrier changes. Platform entry,
        # board parsing, mappings, callback allowlist and report core run as C.
        expression = (r'static uint32_t\s+wireless_mmio_read32\(vm_offset_t address\)'
                      r'\s*\{[^}]*\}')
        source, count = re.subn(expression, r'''static uint32_t
wireless_mmio_read32(vm_offset_t address)
{
    return model_mmio_read32(address);
}''', source)
        if count != 1:
            raise AssertionError('raw MMIO boundary was not found exactly once')
        preamble = ('#include <sys/param.h>\n'
                    'static uint32_t model_mmio_read32(vm_offset_t address);\n')
        (work / 'wrapper.c').write_text(preamble + source + '\n' + HARNESS)
        cls.binary = work / 'wrapper'
        subprocess.run([
            'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-DARM64_BOARD_CONFIG_SWEET=1', '-I', str(work),
            '-I', str(ROOT / 'xnu/wireless'), str(work / 'wrapper.c'),
            str(ROOT / 'xnu/wireless/mt6983_conninfra.c'), '-o', str(cls.binary),
        ], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_cases(self, *names):
        for name in names:
            with self.subTest(case=name):
                result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'PASS')

    def test_absent_disabled_and_unsupported_arguments_never_touch_hardware(self):
        self.run_cases('absent', 'disabled', 'unsupported')

    def test_exact_board_and_soc_must_both_be_present(self):
        self.run_cases('wrong_board', 'wrong_soc', 'only_board', 'only_soc')

    def test_malformed_compatible_lists_never_map_or_read(self):
        self.run_cases('unterminated', 'malformed_suffix', 'empty', 'oversized')

    def test_device_tree_failures_never_map_or_read(self):
        self.run_cases('lookup_error', 'property_error')

    def test_partial_mapping_failure_unmaps_each_success_and_never_reads(self):
        self.run_cases('ap_map_failure', 'spm_map_failure', 'both_map_failures')

    def test_success_has_exactly_three_ap_reads_and_releases_both_mappings(self):
        self.run_cases('success', 'reversed')

    def test_mmio_allowlist_rejects_conn_domain_without_loading(self):
        self.run_cases('reject_address')


if __name__ == '__main__':
    unittest.main()

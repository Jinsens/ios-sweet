"""Exercise the real unlocked wait wrapper with a competing client's report."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

PREAMBLE = r'''
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "sweet_gpu_user.h"
#include "mali_csf_runtime.h"
using IOReturn=int;
enum { kIOReturnSuccess, kIOReturnNotReady, kIOReturnTimeout,
       kIOReturnIOError, GPU_STAT_WAIT };
struct GPUVM {
    bool broken;
    unsigned as_nr;
    sweet_csf_runtime_group groups[SWEET_CSF_GROUPS];
};
static sweet_csf_queue_config queue;
static sweet_gpu_io gpu_io;
static sweet_csf_queue_report last_report;
static sweet_csf_runtime_group *expected_group;
static unsigned calls, notes, logs;
static int wait_result;
static uint32_t expected_timeout;
static char logged[512];
static uint64_t gpu_us() { return 17; }
static void gpu_note(unsigned which,uint64_t began) {
    assert(which==GPU_STAT_WAIT && began==17);notes++;
}
static void IOLog(const char *format,...) {
    va_list ap;va_start(ap,format);
    int n=vsnprintf(logged,sizeof(logged),format,ap);va_end(ap);
    assert(n>0 && (size_t)n<sizeof(logged));logs++;
}
int sweet_csf_runtime_wait(const sweet_csf_queue_config *config,const sweet_gpu_io *io,
    sweet_csf_runtime_group *group,uint32_t sequence,uint32_t insert,
    uint32_t timeout,sweet_csf_queue_report *report) {
    assert(config==&queue && io==&gpu_io && group==expected_group);
    assert(sequence==42 && insert==5376 && timeout==expected_timeout);
    /* WAIT runs without gpu_lock. Its mutable report must belong to this
     * call, while a different client's enqueue may reset last_report. */
    assert(report!=&last_report);
    sweet_csf_queue_report empty={};
    assert(!memcmp(report,&empty,sizeof(empty)));
    calls++;
    report->error=wait_result;
    report->after=43;report->extract=5504;report->polls=23;
    report->fault=0x44;report->fatal=0x55;
    /* Deterministically interleave the competing enqueue after our write. */
    memset(&last_report,0xa5,sizeof(last_report));
    last_report.error=-62;
    return report->error;
}
'''

CHECKS = r'''
static GPUVM fresh() {
    GPUVM v={};v.as_nr=3;v.groups[1].active=1;
    queue.timeout_ms=1000;
    memset(&last_report,0x5a,sizeof(last_report));
    return v;
}
int main(int argc,char **argv) {
    assert(argc==2);GPUVM v=fresh();expected_group=&v.groups[1];
    sweet_gpu_wait w={};w.group=2;w.sequence=42;w.insert=5376;
    expected_timeout=queue.timeout_ms;
    IOReturn expected_result=kIOReturnSuccess;
    if(!strcmp(argv[1],"success")) {
        wait_result=0;
    } else if(!strcmp(argv[1],"timeout")) {
        wait_result=-63;w.timeout_ms=7;expected_timeout=7;
        expected_result=kIOReturnTimeout;
    } else if(!strcmp(argv[1],"fault")) {
        wait_result=-64;expected_result=kIOReturnIOError;
    } else if(!strcmp(argv[1],"argument_error")) {
        wait_result=-60;expected_result=kIOReturnIOError;
    } else if(!strcmp(argv[1],"unavailable")) {
        v.broken=true;
        assert(gpu_wait(&v,&w)==kIOReturnNotReady);
        v.broken=false;w.group=0;
        assert(gpu_wait(&v,&w)==kIOReturnNotReady);
        w.group=SWEET_CSF_GROUPS+1;
        assert(gpu_wait(&v,&w)==kIOReturnNotReady);
        w.group=2;v.groups[1].active=0;
        assert(gpu_wait(&v,&w)==kIOReturnNotReady);
        assert(!calls && !notes && !logs);return 0;
    } else return 2;
    assert(gpu_wait(&v,&w)==expected_result);
    assert(calls==1 && notes==1 && logs==1 && !v.broken);
    char expected[512];
    snprintf(expected,sizeof(expected),
        "SWEET GPU: CSF wait as=3 group=2 seq=42 rc=%d after=43 extract=5504 polls=23 fault=44 fatal=55\n",
        wait_result);
    assert(!strcmp(logged,expected));
    /* This call must neither publish into nor consume the competing report. */
    assert(last_report.error==-62 && last_report.after==0xa5a5a5a5u);
    assert(w.group==2 && w.sequence==42 && w.insert==5376);
    return 0;
}
'''


class WaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "xnu/gpu/SweetGPUVM.h").read_text()
        function = source[source.index("static IOReturn gpu_wait("):
                          source.index("static void gpu_vm_destroy(")]
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name)
        (path / "test.cpp").write_text(PREAMBLE + function + CHECKS)
        cls.binary = path / "test"
        subprocess.run(["clang++", "-std=c++14", "-O1", "-g", "-Wall", "-Wextra",
                        "-Werror", "-fsanitize=address,undefined",
                        "-I", str(ROOT / "xnu/gpu"), str(path / "test.cpp"),
                        "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_success_and_log_use_private_zeroed_report(self):
        self.check("success")

    def test_timeout_preserves_classification_and_explicit_budget(self):
        self.check("timeout")

    def test_hardware_fault_preserves_error_classification(self):
        self.check("fault")

    def test_other_runtime_error_preserves_error_classification(self):
        self.check("argument_error")

    def test_unavailable_group_does_not_enter_runtime(self):
        self.check("unavailable")


if __name__ == "__main__":
    unittest.main()

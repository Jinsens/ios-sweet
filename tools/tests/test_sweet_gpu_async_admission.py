"""Execute the real user-client async admission and retry/error classification."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

PREAMBLE = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include "sweet_gpu_user.h"
#include "mali_csf_runtime.h"
using IOReturn=int;
enum { kIOReturnSuccess, kIOReturnNotReady, kIOReturnBadArgument,
       kIOReturnBusy, kIOReturnIOError, GPU_STAT_SUBMIT };
#define GPU_MAX_BO 4
#define IOLog(...) ((void)0)
struct GPUBuffer { void *memory; uint64_t va,mapped; };
struct GPUVM {
    bool broken;
    unsigned as_nr;
    GPUBuffer bo[GPU_MAX_BO];
    sweet_csf_runtime_group groups[SWEET_CSF_GROUPS];
};
static sweet_csf_queue_config queue;
static sweet_gpu_io gpu_io;
static sweet_csf_queue_report last_report;
static unsigned submits, failures=7, attempts, notes;
static int enqueue_result;
static uint64_t gpu_us() { return 1; }
static void gpu_note(unsigned which,uint64_t began) {
    assert(which==GPU_STAT_SUBMIT && began==1);notes++;
}
int sweet_csf_runtime_enqueue(const sweet_csf_queue_config *,const sweet_gpu_io *,
    sweet_csf_runtime_group *g,uint64_t stream,uint32_t bytes,sweet_csf_queue_report *r) {
    assert(stream==0x10000000 && bytes==8);attempts++;
    if(!enqueue_result) { g->sequence++;g->insert+=128;r->insert=(uint32_t)g->insert; }
    r->error=enqueue_result;return enqueue_result;
}
'''

CHECKS = r'''
static GPUVM fresh() {
    GPUVM v={};v.as_nr=1;v.bo[0]={(void *)1,0x10000000,4096};
    v.groups[0].active=1;v.groups[0].sequence=31;v.groups[0].insert=3968;
    return v;
}
int main(int argc,char **argv) {
    assert(argc==2);GPUVM v=fresh();sweet_gpu_submit r={1,8,0x10000000,0};
    if(!strcmp(argv[1],"busy")) {
        enqueue_result=-62;
        assert(gpu_submit_async(&v,&r)==kIOReturnBusy);
        assert(!v.broken && failures==7 && submits==1 && attempts==1 && notes==1);
        assert(r.sequence==31 && v.groups[0].sequence==31 && v.groups[0].insert==3968);
        /* Firmware progress makes room; retry exactly the same stream. */
        enqueue_result=0;
        assert(gpu_submit_async(&v,&r)==kIOReturnSuccess);
        assert(!v.broken && failures==7 && submits==2 && attempts==2 && notes==2);
        assert(r.sequence==32 && v.groups[0].insert==4096);
    } else if(!strcmp(argv[1],"fault")) {
        enqueue_result=-64;
        assert(gpu_submit_async(&v,&r)==kIOReturnIOError);
        assert(v.broken && failures==8 && submits==1 && attempts==1);
        assert(gpu_submit_async(&v,&r)==kIOReturnNotReady);
        assert(failures==8 && submits==1 && attempts==1);
    } else if(!strcmp(argv[1],"invalid")) {
        r.stream=0x10001000;
        assert(gpu_submit_async(&v,&r)==kIOReturnBadArgument);
        assert(!v.broken && failures==7 && !submits && !attempts && !notes);
        r.stream=0x10000000;r.group=0;
        assert(gpu_submit_async(&v,&r)==kIOReturnNotReady && !attempts);
    } else return 2;
}
'''


class AsyncAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=(ROOT/"xnu/gpu/SweetGPUVM.h").read_text()
        function=source[source.index("static IOReturn gpu_submit_async("):
                        source.index("static IOReturn gpu_wait(")]
        cls.tmp=tempfile.TemporaryDirectory()
        path=Path(cls.tmp.name)
        (path/"test.cpp").write_text(PREAMBLE+function+CHECKS)
        cls.binary=path/"test"
        subprocess.run(["clang++","-std=c++14","-O1","-g",
                        "-fsanitize=address,undefined","-I",str(ROOT/"xnu/gpu"),
                        str(path/"test.cpp"),"-o",str(cls.binary)],check=True)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def check(self,case):
        result=subprocess.run([str(self.binary),case],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_ring_pressure_preserves_vm_and_allows_unchanged_retry(self):self.check("busy")
    def test_hardware_fault_breaks_vm_once(self):self.check("fault")
    def test_invalid_stream_never_reaches_queue(self):self.check("invalid")


if __name__=="__main__":unittest.main()

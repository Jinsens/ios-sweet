"""Execute GPU VM mapping against real page tables and faulting allocators.

The harness uses the driver's mapping functions unchanged except for a count
at the page-table walk entry. This measures CPU walks independently of timing
and checks that bulk writes preserve every 4 KiB descriptor and rollback.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

PREAMBLE = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <vector>
#include "sweet_gpu_user.h"
using IOReturn = int;
using IOByteCount = uint64_t;
enum { kIOReturnSuccess, kIOReturnNotReady, kIOReturnBadArgument,
       kIOReturnIOError, kIOReturnNotFound, kIOReturnBusy, kIOReturnNoMemory };
enum { GPU_STAT_MAP_PTE, GPU_STAT_MAP_FLUSH, GPU_STAT_UNMAP };
enum { GPU_MAX_BO=8, GPU_MAX_PT=64, GPU_PT_LOOKUP=16,
       kIOMemoryMapperNone=0 };
struct Segment { uint64_t pa, bytes; };
struct IOMemoryDescriptor {
    std::vector<Segment> segments;
    uint64_t getPhysicalSegment(uint64_t offset, IOByteCount *length, int) {
        for (auto s: segments) {
            if (offset < s.bytes) { *length=s.bytes-offset; return s.pa+offset; }
            offset-=s.bytes;
        }
        *length=0; return 0;
    }
};
struct GPUBuffer {
    IOMemoryDescriptor *memory=nullptr;
    uint64_t size=0, va=0, mapped=0, offset=0;
};
struct GPUPageTable { uint64_t pa=0; volatile uint64_t *cpu=nullptr; };
struct GPUTableLookup { uint64_t pa=0; GPUPageTable *table=nullptr; };
struct GPUVM {
    GPUBuffer bo[GPU_MAX_BO];
    GPUPageTable pt[GPU_MAX_PT];
    unsigned tables=0, as_nr=1;
    bool broken=false;
    GPUTableLookup lookup[GPU_PT_LOOKUP];
    uint64_t data[GPU_MAX_PT][512] = {};
};
static unsigned walks, barriers, invalidations, fail_table_at=GPU_MAX_PT;
static bool invalidate_failure;
static int queue, gpu_io;
static uint64_t gpu_us() { return 1; }
static void gpu_note(unsigned,uint64_t) {}
static void barrier(void *) { barriers++; }
static int sweet_csf_runtime_invalidate(int *,int *,unsigned) {
    assert(barriers); invalidations++; return invalidate_failure ? -1 : 0;
}
static GPUPageTable *gpu_new_table(GPUVM *v) {
    if (v->tables >= GPU_MAX_PT || v->tables == fail_table_at) return nullptr;
    unsigned n=v->tables++;
    v->pt[n].pa=0x10000000ull+4096*n;
    v->pt[n].cpu=v->data[n];
    return &v->pt[n];
}
static GPUBuffer *gpu_bo(GPUVM *v,uint32_t handle) {
    return handle && handle<=GPU_MAX_BO && v->bo[handle-1].memory
        ? &v->bo[handle-1] : nullptr;
}
'''

CHECKS = r'''
static std::unique_ptr<GPUVM> fresh() {
    fail_table_at=GPU_MAX_PT; invalidate_failure=false;
    walks=barriers=invalidations=0;
    std::unique_ptr<GPUVM> v(new GPUVM);
    assert(gpu_new_table(v.get())); return v;
}
static uint64_t spans(uint64_t va,uint64_t bytes) {
    return ((va+bytes-1)>>21)-(va>>21)+1;
}
static uint64_t entry(GPUVM *v,uint64_t va) {
    volatile uint64_t *pte=gpu_pte(v,va,false); return pte ? *pte : 0;
}
static uint64_t descriptor(uint64_t pa,unsigned flags) {
    return pa|0x457ull|((flags&1)?0x80ull:0)|
        ((flags&2)?0x0060000000000000ull:0);
}
static void geometry() {
    /* A run crossing 2 MiB leaf tables and a 1 GiB parent boundary. */
    for (uint64_t va: {0x081ff000ull,0x3ffff000ull}) {
        for (unsigned flags=0;flags<8;flags++) {
            auto v=fresh(); uint64_t pa=0x40000000, bytes=0x204000;
            assert(gpu_map_run(v.get(),va,pa,bytes,flags));
            for (uint64_t n=0;n<bytes;n+=4096)
                assert(entry(v.get(),va+n)==descriptor(pa+n,flags));
            assert(entry(v.get(),va-4096)==0);
            assert(entry(v.get(),va+bytes)==0);
            /* Partial unmap must preserve neighbours in shared tables. */
            assert(gpu_unmap_pages(v.get(),va+4096,bytes-8192));
            assert(invalidations==1 && !v->broken);
            assert(entry(v.get(),va)==descriptor(pa,flags));
            assert(entry(v.get(),va+bytes-4096)==descriptor(pa+bytes-4096,flags));
            for (uint64_t n=4096;n<bytes-4096;n+=4096)
                assert(entry(v.get(),va+n)==0);
        }
    }
}
static void bulk_walks() {
    auto v=fresh(); uint64_t va=0x081fc000, bytes=18432000, pa=0x40000000;
    /* First map populates all levels; the next map reuses them. */
    assert(gpu_map_run(v.get(),va,pa,bytes,2));
    assert(gpu_unmap_pages(v.get(),va,bytes));
    walks=barriers=0;
    assert(gpu_map_run(v.get(),va,pa,bytes,2));
    assert(walks==spans(va,bytes));
    assert(barriers==1);
    walks=barriers=0;
    assert(gpu_unmap_pages(v.get(),va,bytes));
    assert(walks==spans(va,bytes));
    assert(barriers==1);
}
static void absent_and_failure() {
    auto v=fresh(); uint64_t va=0x083ff000, bytes=0x404000;
    assert(gpu_map_run(v.get(),va,0x40000000,4096,0));
    unsigned tables=v->tables;
    walks=0;
    assert(gpu_unmap_pages(v.get(),va,bytes));
    assert(walks==spans(va,bytes) && v->tables==tables);
    for (uint64_t n=0;n<bytes;n+=4096) assert(!entry(v.get(),va+n));
    invalidate_failure=true;
    assert(!gpu_unmap_pages(v.get(),va,bytes));
    assert(v->broken);
}
static void map_failure_rollback() {
    auto v=fresh(); uint64_t va=0x081ff000, bytes=3*4096;
    IOMemoryDescriptor backing;
    backing.segments={{0x40000000,bytes}};
    v->bo[0].memory=&backing; v->bo[0].size=bytes;
    /* Root + L1 + L2 + first L3 fit. The next L3 allocation fails,
     * after the first page of this very same physical run was written. */
    fail_table_at=4;
    sweet_gpu_map r={}; r.handle=1; r.va=va; r.size=bytes;
    assert(gpu_vm_map(v.get(),&r)==kIOReturnNoMemory);
    assert(!v->bo[0].mapped && !v->broken && invalidations==1);
    for (uint64_t n=0;n<bytes;n+=4096) assert(!entry(v.get(),va+n));
    fail_table_at=GPU_MAX_PT;
    assert(gpu_vm_map(v.get(),&r)==kIOReturnSuccess);
    assert(v->bo[0].mapped==bytes);
    for (uint64_t n=0;n<bytes;n+=4096)
        assert(entry(v.get(),va+n)==descriptor(0x40000000+n,0));
}
static void physical_segments() {
    auto v=fresh(); uint64_t va=0x081fe000, bytes=6*4096;
    IOMemoryDescriptor backing;
    backing.segments={{0x40000000,3*4096},{0x50000000,3*4096}};
    v->bo[0].memory=&backing; v->bo[0].size=bytes;
    sweet_gpu_map r={}; r.handle=1; r.va=va; r.size=bytes; r.flags=3;
    assert(!gpu_vm_map(v.get(),&r));
    assert(v->bo[0].va==va && v->bo[0].mapped==bytes);
    for (uint64_t n=0;n<bytes;n+=4096) {
        uint64_t pa=n<3*4096 ? 0x40000000+n : 0x50000000+n-3*4096;
        assert(entry(v.get(),va+n)==descriptor(pa,3));
    }
    r.flags=1u<<28;
    assert(!gpu_vm_map(v.get(),&r));
    assert(!v->bo[0].mapped && invalidations==2);
    for (uint64_t n=0;n<bytes;n+=4096) assert(!entry(v.get(),va+n));
}
static void invalid_ranges() {
    auto v=fresh();
    struct Range {uint64_t va,pa,bytes;};
    const Range bad[] = {
        {0x08000000,0x40000000,0},
        {0x08000001,0x40000000,4096},
        {0x08000000,0x40000001,4096},
        {0x08000000,0x40000000,4095},
        {0x08000000,0,4096},
        {1ull<<48,0x40000000,4096},
        {(1ull<<48)-4096,0x40000000,8192},
        {0x08000000,1ull<<40,4096},
        {0x08000000,(1ull<<40)-4096,8192},
        {0x08000000,0x40000000,0xfffffffffffff000ull},
    };
    for (auto r: bad) {
        walks=barriers=invalidations=0;
        assert(!gpu_map_run(v.get(),r.va,r.pa,r.bytes,0));
        assert(walks==0 && barriers==0 && invalidations==0 && v->tables==1);
    }
    const Range bad_unmap[] = {
        {0x08000001,0,4096}, {0x08000000,0,4095},
        {1ull<<48,0,4096}, {(1ull<<48)-4096,0,8192},
        {0x08000000,0,0xfffffffffffff000ull},
    };
    for (auto r: bad_unmap) {
        assert(!gpu_unmap_pages(v.get(),r.va,r.bytes));
        assert(walks==0 && barriers==0 && invalidations==0 && v->tables==1);
    }
    /* Last representable page is legal; crossing either limit is not. */
    uint64_t va=(1ull<<48)-4096,pa=(1ull<<40)-4096;
    assert(gpu_map_run(v.get(),va,pa,4096,2));
    assert(entry(v.get(),va)==descriptor(pa,2));
    assert(gpu_unmap_pages(v.get(),va,4096));
}
static void rollback_invalidation_failure() {
    auto v=fresh();
    IOMemoryDescriptor backing;
    backing.segments={{0x40000000,8192}};
    v->bo[0].memory=&backing; v->bo[0].size=8192;
    fail_table_at=4; invalidate_failure=true;
    sweet_gpu_map r={}; r.handle=1; r.va=0x081ff000; r.size=8192;
    assert(gpu_vm_map(v.get(),&r)==kIOReturnIOError);
    assert(v->broken && !v->bo[0].mapped && invalidations==1);
    assert(entry(v.get(),r.va)==0);
    assert(gpu_vm_map(v.get(),&r)==kIOReturnNotReady);
}
int main(int argc,char **argv) {
    assert(argc==2);
    if (!strcmp(argv[1],"geometry")) geometry();
    else if (!strcmp(argv[1],"walks")) bulk_walks();
    else if (!strcmp(argv[1],"absent")) absent_and_failure();
    else if (!strcmp(argv[1],"rollback")) map_failure_rollback();
    else if (!strcmp(argv[1],"segments")) physical_segments();
    else if (!strcmp(argv[1],"invalid")) invalid_ranges();
    else if (!strcmp(argv[1],"rollback-invalidate")) rollback_invalidation_failure();
    else return 2;
}
'''


@unittest.skipUnless(shutil.which("clang++"), "requires C++ compiler")
class GPUVMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "xnu/gpu/SweetGPUVM.h").read_text()
        mapping = source[source.index("static GPUPageTable *gpu_table("):
                         source.index("static GPUVM *gpu_vm_create(")]
        mapping, count = re.subn(
            r"(static volatile uint64_t \*gpu_pte\([^\n]+\)\n\{)",
            r"\1\n    walks++;", mapping, count=1)
        if count != 1:
            raise AssertionError("page-table walker instrumentation missing")
        vm_map = source[source.index("static IOReturn gpu_vm_map("):
                        source.index("static IOReturn gpu_bo_close(")]
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name)
        (path / "test.cpp").write_text(PREAMBLE + mapping + vm_map + CHECKS)
        cls.binary = path / "test"
        subprocess.run([
            "clang++", "-std=c++14", "-O1", "-g",
            "-fsanitize=address,undefined", "-I", str(ROOT / "xnu/gpu"),
            str(path / "test.cpp"), "-o", str(cls.binary),
        ], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_descriptors_across_leaf_and_parent_boundaries(self):
        self.check("geometry")

    def test_one_walk_per_leaf_table_span(self):
        self.check("walks")

    def test_absent_tables_and_invalidation_failure(self):
        self.check("absent")

    def test_failed_run_rolls_back_its_partial_leaf_writes(self):
        self.check("rollback")

    def test_multiple_physical_segments_and_explicit_unmap(self):
        self.check("segments")

    def test_invalid_ranges_fail_before_page_table_access(self):
        self.check("invalid")

    def test_failed_rollback_invalidation_marks_vm_broken(self):
        self.check("rollback-invalidate")


if __name__ == "__main__":
    unittest.main()

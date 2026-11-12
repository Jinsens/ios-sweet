"""Execute BO allocation/cache lifetimes and enforce the wired-memory budget."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('clang++'), 'needs a host C++ compiler')
class BufferCacheTests(unittest.TestCase):
    def test_reuse_budget_eviction_and_failures(self):
        source = (ROOT / 'xnu/gpu/SweetGPUVM.h').read_text()
        functions = source[source.index('static GPUBuffer *gpu_bo('):
                           source.index('/* Metadata queries')]
        functions += source[source.index('static IOReturn gpu_bo_close('):
                            source.index('static IOReturn gpu_group_create(')]
        prelude = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include "sweet_gpu_user.h"
using IOReturn = int;
enum { kIOReturnSuccess, kIOReturnNotReady, kIOReturnBadArgument,
       kIOReturnNoMemory, kIOReturnNoResources, kIOReturnNotFound,
       kIOReturnIOError };
#define GPU_MAX_BO 16u
#define GPU_BO_CACHE 6u
#define PAGE_SIZE 16384u
#define GPU_STAT_CLOSE 0
#define OSSafeReleaseNULL(p) do { if (p) { (p)->release(); (p)=nullptr; } } while(0)
struct IOMemoryDescriptor {
    unsigned refs=0;
    uint64_t size=0;
    unsigned char bytes[16]={};
    void release(){assert(refs==1);refs--;}
    void *getBytesNoCopy(){assert(refs==1);return bytes;}
};
struct IOBufferMemoryDescriptor: IOMemoryDescriptor {};
struct SweetSurface {unsigned refs=1;void release(){assert(refs==1);refs--;}};
struct GPUBuffer {
    IOMemoryDescriptor *memory;
    SweetSurface *surface;
    uint64_t size,va,mapped,offset;
    uint32_t flags;
};
struct GPUCachedBuffer {IOBufferMemoryDescriptor *memory;uint64_t size;};
struct GPUVM {
    GPUBuffer bo[GPU_MAX_BO];
    uint64_t allocated;
    bool broken;
    GPUCachedBuffer cache[GPU_BO_CACHE];
    unsigned cacheNext;
};
static IOBufferMemoryDescriptor descriptors[64];
static unsigned allocations,zeroes;
static bool allocation_fails,unmap_fails;
static IOBufferMemoryDescriptor *gpu_buffer_alloc(uint64_t size) {
    if(allocation_fails)return nullptr;
    for(auto &m:descriptors)if(!m.refs) {
        m.refs=1;m.size=size;memset(m.bytes,0,sizeof(m.bytes));allocations++;return &m;
    }
    assert(false);return nullptr;
}
/* Descriptor backing is represented by a small sample, so boundary-budget
 * tests can cover a full GiB without allocating it in the host process. */
static void test_zero(void *p,uint64_t size) {
    for(auto &m:descriptors)if(p==m.bytes) {
        assert(m.refs==1&&size==m.size);memset(m.bytes,0,sizeof(m.bytes));zeroes++;return;
    }
    memset(p,0,size);
}
#define bzero(p,n) test_zero(p,n)
static uint64_t gpu_us(){return 1;}
static void gpu_note(unsigned,uint64_t){}
static bool gpu_unmap_pages(GPUVM *,uint64_t,uint64_t){return !unmap_fails;}
'''
        checks = r'''
static constexpr uint64_t MiB=1ull<<20,limit=1ull<<30;
static void invariant(GPUVM &vm) {
    uint64_t charged=0;
    for(auto &b:vm.bo)if(b.memory&&!b.surface){assert(b.memory->refs==1);charged+=b.size;}
    for(auto &c:vm.cache)if(c.memory){assert(c.memory->refs==1);charged+=c.size;}
    assert(vm.allocated==charged&&charged<=limit);
}
static uint32_t create(GPUVM &vm,uint64_t size) {
    sweet_gpu_bo r={size,0,0};
    assert(gpu_bo_create(&vm,&r)==kIOReturnSuccess);invariant(vm);return r.handle;
}
static void close(GPUVM &vm,uint32_t handle) {
    assert(gpu_bo_close(&vm,handle)==kIOReturnSuccess);invariant(vm);
}
static void flush(GPUVM &vm) {gpu_bo_cache_flush(&vm);invariant(vm);}
int main() {
    GPUVM vm={};
    /* A compositor allocates and discards an 18.4 MB target each frame.
     * Cached bytes stay charged once across arbitrarily many frames. */
    unsigned before=allocations;
    for(unsigned frame=0;frame<1000;frame++) {
        uint32_t h=create(vm,18432000);
        assert(vm.allocated==18432000);
        for(auto c:vm.bo[h-1].memory->bytes)assert(c==0);
        memset(vm.bo[h-1].memory->bytes,0xa5,16);
        close(vm,h);
    }
    assert(allocations==before+1&&zeroes==999);
    flush(vm);assert(vm.allocated==0);

    /* At the exact budget limit, cached reuse must succeed and a fresh
     * allocation must fail without altering live/cache ownership. */
    uint32_t h[4];for(auto &one:h)one=create(vm,256*MiB);
    assert(vm.allocated==limit);
    close(vm,h[0]);before=allocations;h[0]=create(vm,256*MiB);
    assert(allocations==before&&vm.allocated==limit);
    sweet_gpu_bo denied={4*MiB,0,0};
    assert(gpu_bo_create(&vm,&denied)==kIOReturnNoMemory);invariant(vm);
    for(auto one:h)close(vm,one);
    flush(vm);assert(vm.allocated==0);

    /* Cache replacement releases the evicted backing and its exact charge. */
    for(unsigned n=4;n<=10;n++){auto one=create(vm,n*MiB);close(vm,one);}
    assert(vm.allocated==45*MiB);
    flush(vm);assert(vm.allocated==0);

    /* Small uncached BOs release their budget; failed allocations and full
     * handle tables leave existing cache entries untouched. */
    auto small=create(vm,MiB);close(vm,small);assert(vm.allocated==0);
    auto cached=create(vm,8*MiB);close(vm,cached);
    allocation_fails=true;
    denied={9*MiB,0,0};assert(gpu_bo_create(&vm,&denied)==kIOReturnNoMemory);invariant(vm);
    cached=create(vm,8*MiB);close(vm,cached);allocation_fails=false;
    uint32_t slots[GPU_MAX_BO];for(auto &one:slots)one=create(vm,PAGE_SIZE);
    unsigned zeroes_before=zeroes;
    denied={8*MiB,0,0};assert(gpu_bo_create(&vm,&denied)==kIOReturnNoResources);
    assert(zeroes==zeroes_before);invariant(vm);
    for(auto one:slots)close(vm,one);
    cached=create(vm,8*MiB);
    vm.bo[cached-1].mapped=PAGE_SIZE;unmap_fails=true;
    assert(gpu_bo_close(&vm,cached)==kIOReturnIOError);invariant(vm);
    unmap_fails=false;close(vm,cached);flush(vm);assert(vm.allocated==0);
    for(auto &m:descriptors)assert(m.refs==0);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / 'test.cpp').write_text(prelude + functions + checks)
            subprocess.run(['clang++', '-std=c++14', '-O1', '-g',
                            '-fsanitize=address,undefined', '-I', str(ROOT / 'xnu/gpu'),
                            str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
            subprocess.run([str(work / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()

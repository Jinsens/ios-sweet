"""Exercise resident-only sampling without real MMIO."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('cc'), 'needs a host C compiler')
class PerformanceDiagnosticsTests(unittest.TestCase):
    def test_resident_sampler_refuses_faults_devices_and_cross_page_copies(self):
        patch = (ROOT / 'xnu/patches/0113-sweet-mmu-ufs-performance-diagnostics.patch').read_text()
        section = patch.split('--- a/osfmk/arm/pmap/pmap.c\n', 1)[1].split('--- a/', 1)[0]
        added = '\n'.join(line[1:] for line in section.splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        begin = added.index('uint64_t sweet_pmap_sample(pmap_t pmap,')
        end = added.index('\n#endif', begin)
        function = added[begin:end]
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
typedef uint64_t pt_entry_t, tt_entry_t, vm_map_address_t, pmap_paddr_t;
typedef struct { unsigned pta_root_level, pta_max_level; } pt_attr_t;
typedef struct { tt_entry_t entries[4]; } *pmap_t;
#define PMAP_LOCK_SHARED 1
#define TT_ENTRY_NULL NULL
#define ARM_TTE_VALID 1
#define ARM_TTE_TYPE_MASK 2
#define ARM_TTE_TYPE_TABLE 2
#define ARM_PTE_ATTRINDXMASK 28
#define ARM_PTE_ATTRINDX(x) ((x)<<2)
#define CACHE_ATTRINDX_WRITEBACK 0
static pt_attr_t geometry={1,3};
static unsigned char page[16384];
static int locked, pvlocked, mutate;
static pmap_t current;
static void pmap_lock(pmap_t p,int mode){assert(!locked&&mode==1);locked=1;current=p;}
static void pmap_unlock(pmap_t p,int mode){assert(locked&&!pvlocked&&p==current&&mode==1);locked=0;}
static const pt_attr_t *pmap_get_pt_attr(pmap_t p){assert(p==current);return &geometry;}
static tt_entry_t *pmap_ttne(pmap_t p,unsigned level,uint64_t va){
    assert(locked);return va<16384 ? &p->entries[level] : NULL;
}
static uint64_t pt_attr_page_size(const pt_attr_t *a){assert(a==&geometry);return 16384;}
static uint64_t pte_to_pa(uint64_t pte){return pte&0x0000ffffffffc000ULL;}
static int pa_valid(uint64_t pa){return pa==0x4000;}
static unsigned pa_index(uint64_t pa){assert(pa==0x4000);return 1;}
static void pvh_lock(unsigned i){assert(locked&&!pvlocked&&i==1);pvlocked=1;if(mutate)current->entries[3]^=4;}
static void pvh_unlock(unsigned i){assert(locked&&pvlocked&&i==1);pvlocked=0;}
static uintptr_t phystokv(uint64_t pa){assert(locked&&pvlocked);return (uintptr_t)page+pa-0x4000;}
''' + function + r'''
int main(void){
    struct {tt_entry_t entries[4];} storage={{0,3,3,0x4403}};
    pmap_t p=(pmap_t)&storage;
    for(unsigned i=0;i<sizeof(page);i++)page[i]=(unsigned char)i;
    uint64_t got=0,want;memcpy(&want,page+32,8);
    assert(sweet_pmap_sample(p,32,&got,8)==0x4403&&got==want&&!locked);
    assert(sweet_pmap_sample(p,16384,NULL,0)==0&&!locked);
    assert(sweet_pmap_sample(p,16380,&got,8)==0&&!locked);
    for(unsigned attr=1;attr<8;attr++){
        p->entries[3]=0x4403|(attr<<2);got=0;
        assert(sweet_pmap_sample(p,32,&got,8)==0&&got==0&&!locked);
        assert(sweet_pmap_sample(p,32,NULL,0)==p->entries[3]);
    }
    p->entries[3]=0;assert(sweet_pmap_sample(p,32,&got,8)==0&&!locked);
    p->entries[3]=0x8403;assert(sweet_pmap_sample(p,32,&got,8)==0&&!locked);
    p->entries[3]=0x4403;mutate=1;got=0;
    assert(sweet_pmap_sample(p,32,&got,8)==0&&got==0&&!locked);mutate=0;
    p->entries[2]=0x4401;
    assert(sweet_pmap_sample(p,32,NULL,0)==0x4401);
    assert(sweet_pmap_sample(p,32,&got,8)==0&&!locked);
}
'''
        self.compile_and_run(harness)

    def compile_and_run(self, source):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'check.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', str(work / 'check.c'),
                            '-o', str(work / 'check')], check=True)
            subprocess.run([str(work / 'check')], check=True)


if __name__ == '__main__':
    unittest.main()

"""Replay key patches and exercise their real register reads with host mocks."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FILES = ('bsd/dev/sweet_mt_keys.c', 'bsd/dev/sweet_keys.c',
         'bsd/dev/sweet_spmi.c', 'bsd/dev/sweet_spmi.h')


def function(source, name):
    start = source.index('\n' + name + '(') + 1
    start = source.rfind('\n', 0, start - 1) + 1
    end = source.index('\n}\n', start) + 3
    return source[start:end]


@unittest.skipUnless(shutil.which('cc') and shutil.which('git'), 'needs cc and git')
class MatisseKeysTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='matisse-keys-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        for patch in sorted((ROOT / 'xnu/patches').glob('*.patch')):
            subprocess.run(['git', 'apply', '--whitespace=nowarn',
                            *['--include=' + p for p in FILES], str(patch)],
                           cwd=cls.work, check=True, capture_output=True)
        cls.keys = (cls.work / FILES[0]).read_text()
        cls.dispatch = (cls.work / FILES[1]).read_text()
        cls.spmi = (cls.work / FILES[2]).read_text()

    def compile_run(self, name, source):
        path = self.work / (name + '.c')
        path.write_text(source)
        binary = self.work / name
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(path), '-o', str(binary)],
                       check=True, capture_output=True)
        subprocess.run([str(binary)], check=True, capture_output=True)

    def test_keypad_spacing_and_shared_read_failure(self):
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#define KP_MEM1 4u
#define KP_MEM_COUNT 5
static struct { uintptr_t kp; unsigned sid, voldown_index; bool kp_ok; } mtk;
static uint16_t banks[5];
static unsigned sampled, read_sid, read_addr;
static bool fail;
static uint16_t mtk_rd16(uintptr_t base, uint32_t off) {
    assert(base == 0x1c00e000 && off >= 4 && off <= 20 && (off & 3) == 0);
    sampled |= 1u << ((off - 4) / 4);
    return banks[(off - 4) / 4];
}
static int sweet_spmi_read(unsigned sid, unsigned addr, uint8_t *out) {
    read_sid=sid;read_addr=addr;
    if(fail)return -1;
    *out=0x1e;return 0;
}
''' + function(self.keys, 'mtk_spmi_read') + function(self.keys, 'mtk_kpd_voldown') + r'''
int main(void) {
    mtk.kp=0x1c00e000;mtk.kp_ok=true;mtk.sid=4;
    for(unsigned i=0;i<5;i++)banks[i]=0xffff;
    assert(!mtk_kpd_voldown() && sampled==31);
    banks[0]=0xfffe;assert(mtk_kpd_voldown());
    banks[0]=0xffff;assert(!mtk_kpd_voldown());
    mtk.voldown_index=71;banks[4]=0xff7f;assert(mtk_kpd_voldown());
    for(unsigned i=0;i<5;i++)banks[i]=0;
    assert(!mtk_kpd_voldown());
    mtk.kp_ok=false;sampled=0;assert(!mtk_kpd_voldown() && sampled==0);
    uint8_t value=0;
    assert(mtk_spmi_read(0x1e,&value) && value==0x1e && read_sid==4 && read_addr==0x1e);
    value=0xaa;fail=true;assert(!mtk_spmi_read(0x1e,&value) && value==0xaa);
}
'''
        self.compile_run('keys', source)

    def test_only_two_primary_pmic_key_addresses_are_added(self):
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define SP_NO_APID 0xffffu
static bool sp_ready=true, sp_pmif_ok=true, sp_poisoned[512];
static void *sp_lock;
static unsigned locked, reads;
static uintptr_t sp_obs;
static void IOLockLock(void *l){(void)l;assert(locked++==0);}
static void IOLockUnlock(void *l){(void)l;assert(locked--==1);}
static int sp_pmif_read(unsigned sid,unsigned addr,uint8_t*out){
    assert(locked==1 && sid==4 && (addr==9||addr==0x1e));reads++;*out=0x63;return 0;
}
static bool sp_touch_supply_reg(unsigned sid,unsigned addr){(void)sid;(void)addr;return false;}
static uint16_t sp_find_apid(unsigned p,bool a){(void)p;(void)a;return SP_NO_APID;}
static uint32_t sp_rd(uintptr_t b,unsigned o){(void)b;(void)o;return 0;}
static void delay(unsigned u){(void)u;}
''' + function(self.spmi, 'sweet_spmi_read').replace(
            '__asm__ volatile("dmb oshst" ::: "memory");', '(void)0;').replace(
            '__asm__ volatile("dsb osh" ::: "memory");', '(void)0;') + r'''
int main(void) {
    uint8_t v=0;
    assert(!sweet_spmi_read(4,9,&v) && v==0x63);
    assert(!sweet_spmi_read(4,0x1e,&v));
    unsigned before=reads;v=0xaa;
    assert(sweet_spmi_read(4,0x1f,&v)==-1);
    assert(sweet_spmi_read(5,0x1e,&v)==-1);
    assert(sweet_spmi_read(0,9,&v)==-1);
    assert(sweet_spmi_read(4,0x10009,&v)==-1);
    assert(sweet_spmi_read(4,9,0)==-1);
    assert(v==0xaa && reads==before && locked==0);
    sp_pmif_ok=false;assert(sweet_spmi_read(4,9,&v)==-1 && locked==0);
}
'''
        self.compile_run('allowlist', source)

    def test_touch_patch_keeps_mt_keys_in_fresh_build(self):
        conf = 'bsd/conf/files'
        original = ''.join(line + '\n' for line in (
            'bsd/dev/sweet_ufs.c\t\t\tstandard',
            'bsd/dev/sweet_sample.c\t\t\tstandard',
            'bsd/dev/sweet_keys.c\t\t\tstandard',
            'bsd/dev/sweet_touch.c\t\t\tstandard',
            'bsd/dev/sweet_rpmh.c\t\t\tstandard',
            'bsd/dev/sweet_spmi.c\t\t\tstandard',
            'bsd/dev/sweet_usb.c\t\t\tstandard'))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            path = work / conf
            path.parent.mkdir(parents=True)
            path.write_text(original)
            for name in ('0094-sweet-mt6363-keys.patch', '0099-matisse-st-fts-touch.patch'):
                subprocess.run(['git', 'apply', '--include=' + conf,
                                str(ROOT / 'xnu/patches' / name)], cwd=work,
                               check=True, capture_output=True)
            result = path.read_text()
            for driver in ('sweet_keys.c', 'sweet_mt_keys.c', 'sweet_matisse_touch.c'):
                self.assertEqual(result.count('bsd/dev/' + driver + '\t'), 1)

    def test_matisse_dispatch_precedes_qualcomm_mmio(self):
        init = function(self.dispatch, 'sweet_keys_init')
        self.assertLess(init.index('sweet_mt_keys_start(enabled)'), init.index('ml_io_map(KEYS_CORE_PA'))
        self.assertNotIn('PMIF_CH_ACC', self.keys)
        self.assertNotIn('mtk_wr(', self.keys)
        self.assertIn('chip != 0x63', self.keys)


if __name__ == '__main__':
    unittest.main()

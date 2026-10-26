"""Check composed pixels and panel transfer using the patched present method."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = 'iokit/Kernel/arm/SweetFramebuffer.cpp'


@unittest.skipUnless(shutil.which('clang++') and shutil.which('git'), 'needs clang++ and git')
class PresentTests(unittest.TestCase):
    def test_centered_canvas_is_copied_before_panel_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for patch in sorted((ROOT / 'xnu/patches').glob('*.patch')):
                if '+++ b/' + SOURCE in patch.read_text(errors='replace'):
                    subprocess.run(['git', 'apply', '--whitespace=nowarn',
                                    '--include=' + SOURCE, str(patch)],
                                   cwd=work, check=True)
            source = (work / SOURCE).read_text()
            method = source[source.index('void\nAppleCLCD::present('):
                            source.index('\nbool\nIOMobileFramebufferUserClient::initWithTask')]
            geometry = source[source.index('\tfLogicalW = fWidth;'):
                              source.index('\tfLock = IOLockAlloc();')]
            harness = r'''
#include <cstdint>
#include <cstring>
#include <cassert>
#include <vector>
#define SWEET_FB_LAYERS 4
#define SWEET_FB_LOGICAL_W 1125
#define SWEET_FB_LOGICAL_H 2436
#define IOLog(...) ((void)0)
#define OSSafeReleaseNULL(x) do { x=nullptr; } while(0)
static int sweet_console_muted;
static unsigned sweet_fb_swaps_logged, transfers;
static uint32_t *panel;
static size_t probe;
static uint32_t expected;
static void sweet_display_refresh(int force) {
    assert(force == 1 && sweet_console_muted == 1);
    assert(panel[probe] == expected); // Transfer must follow the pixel copy.
    transfers++;
}
static bool PE_parse_boot_argn(const char*,int* p,size_t) { *p=1;return true; }
struct Memory { size_t size;size_t getLength(){return size;} };
struct SweetSurface {
    uint32_t width,height,pixelFormat=0,bytesPerRow,bytesPerElement=4;
    uint32_t elementWidth=1,elementHeight=1,offset=0;
    Memory* memory;uintptr_t pixels;
    uintptr_t kernelAddress(){return pixels;}void release(){}
};
static SweetSurface* current;
static SweetSurface* sweet_surface_copy(uint32_t){return current;}
static uint32_t get32(const uint8_t*p,uint32_t o){uint32_t n;memcpy(&n,p+o,4);return n;}
static void put32(uint8_t*p,uint32_t o,uint32_t n){memcpy(p+o,&n,4);}
struct AppleCLCD {
    uint32_t fWidth,fHeight,fRowBytes,fLogicalW,fLogicalH;
    int32_t fOffX,fOffY;bool fConsolePaused=false;
    uintptr_t fPixels;SweetSurface* fSurface=nullptr;
    void present(const uint8_t*);
    void geometry(int native) {
''' + geometry + '\n}\n};\n' + method + r'''
static void check(uint32_t pw,uint32_t ph,bool native) {
    AppleCLCD fb;fb.fWidth=pw;fb.fHeight=ph;fb.fRowBytes=(pw+8)*4;fb.geometry(native);
    uint32_t sw=fb.fLogicalW,sh=fb.fLogicalH;
    std::vector<uint32_t> src((size_t)sw*sh);
    for(uint32_t y=0;y<sh;y++)for(uint32_t x=0;x<sw;x++)
        src[(size_t)y*sw+x]=0xff000000u | (y<<12) | x;
    Memory mem{src.size()*4};
    SweetSurface surface{sw,sh,0,sw*4,4,1,1,0,&mem,(uintptr_t)src.data()};
    current=&surface;
    std::vector<uint32_t> dst((size_t)(pw+8)*ph+16,0xdeadbeef);
    fb.fPixels=(uintptr_t)dst.data();panel=dst.data();
    probe=(size_t)(ph/2)*(pw+8)+pw/2;
    expected=src[(size_t)((int)ph/2+fb.fOffY)*sw+(int)pw/2+fb.fOffX];
    uint8_t rec[0x46c]={};put32(rec,0x104,1);put32(rec,0x54,1);
    put32(rec,0xcc,sw);put32(rec,0xd0,sh);
    unsigned before=transfers;fb.present(rec);assert(transfers==before+1);
    for(uint32_t y=0;y<ph;y++)for(uint32_t x=0;x<pw;x++) {
        int sx=(int)x+fb.fOffX,sy=(int)y+fb.fOffY;
        uint32_t want=(sx>=0&&sy>=0&&sx<(int)sw&&sy<(int)sh) ?
            src[(size_t)sy*sw+sx] : ((pw>sw||ph>sh)?0:0xdeadbeef);
        assert(dst[(size_t)y*(pw+8)+x]==want);
    }
    for(size_t i=(size_t)(pw+8)*ph;i<dst.size();i++)assert(dst[i]==0xdeadbeef);
    // A later frame must trigger a transfer with the console already muted.
    src[(size_t)((int)ph/2+fb.fOffY)*sw+(int)pw/2+fb.fOffX]=++expected;
    fb.present(rec);assert(transfers==before+2);
}
int main() {
    check(1080,2400,false);check(1440,3200,false);
    check(1440,3200,true);check(1125,2436,false);
}
'''
            (work / 'present.cpp').write_text(harness)
            subprocess.run(['clang++', '-std=c++17', '-O1', '-fsanitize=address,undefined',
                            str(work / 'present.cpp'), '-o', str(work / 'present')], check=True)
            subprocess.run([str(work / 'present')], check=True)


if __name__ == '__main__':
    unittest.main()

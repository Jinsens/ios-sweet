"""Execute the display driver against W0C status registers and a timed TE line.

Only its platform boundary (MMIO, clock, ARM barriers and device-tree stubs)
is replaced. The acknowledgement, TE measurement and presentation functions
come from xnu/display/sweet_matisse_dsi.c on every run.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "xnu/display/sweet_matisse_dsi.c"


def replace_function(source, name, replacement):
    """Replace one platform function while keeping all driver logic intact."""
    match = re.search(
        r"(?m)^static(?: inline)? (?:uint32_t|uint64_t|void)\s*\n"
        + re.escape(name) + r"\([^;]*?\)\s*\{", source
    )
    if match is None:
        raise AssertionError(f"platform boundary {name} was not found")
    start = source.index("{", match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[:match.start()] + replacement + source[end:]


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SWEET 1
void sweet_display_refresh(int force);
typedef void *DTEntry;
#define kSuccess 0
static int SecureDTFindEntry(const char *a,const char *b,DTEntry *c)
{(void)a;(void)b;(void)c;return -1;}
static int SecureDTGetProperty(DTEntry a,const char *b,const void **c,uint32_t *d)
{(void)a;(void)b;(void)c;(void)d;return -1;}
static uintptr_t ml_io_map(uint64_t a,uint64_t b){(void)b;return (uintptr_t)a;}
static int PE_parse_boot_argn(const char *a,void *b,int c)
{(void)a;(void)b;(void)c;return 0;}

enum { MAX_EVENTS=32, MAX_WRITES=512, REGS=0x18000/4 };
struct write_event { unsigned secondary; uint32_t off,value; uint64_t time; unsigned edges; };
static struct {
    uint32_t primary[REGS], secondary[REGS], status;
    uint64_t now, edge_at[MAX_EVENTS];
    unsigned edge_count, next_edge, edges, writes, acks, starts;
    int stuck_high;
    struct write_event log[MAX_WRITES];
} model;
static uint32_t model_read(unsigned secondary,uint32_t off);
static void model_write(unsigned secondary,uint32_t off,uint32_t value);
static uint64_t model_clock(void);
'''


MODEL = r'''
/* Every hardware access costs 10 us; TE occurs in absolute panel time,
 * independently of whether software is polling the status register. */
static void advance(void)
{
    model.now += 10;
    while(model.next_edge < model.edge_count &&
          model.edge_at[model.next_edge] <= model.now) {
        model.status |= DSI_INT_TE_RDY;
        model.next_edge++;
        model.edges++;
    }
}
static uint64_t model_clock(void){advance();return model.now;}
static uint32_t model_read(unsigned secondary,uint32_t off)
{
    assert(!(off & 3) && off/4 < REGS);
    advance();
    if(!secondary && off==DSI_INTSTA)
        return model.status | (model.stuck_high ? DSI_INT_TE_RDY : 0);
    return (secondary ? model.secondary : model.primary)[off/4];
}
static void model_write(unsigned secondary,uint32_t off,uint32_t value)
{
    assert(!(off & 3) && off/4 < REGS);
    advance();
    assert(model.writes < MAX_WRITES);
    model.log[model.writes++] = (struct write_event){secondary,off,value,model.now,model.edges};
    if(!secondary && off==DSI_INTSTA) {
        /* MediaTek status is write-zero-to-clear. Ones leave other IRQs
         * pending; writing a set bit back does NOT acknowledge that bit. */
        model.status &= value;
        model.acks++;
        return;
    }
    (secondary ? model.secondary : model.primary)[off/4] = value;
    if(!secondary && off==DSI_START && value==1)model.starts++;
}
static void reset_model(void)
{
    memset(&model,0,sizeof(model));
    disp_base=(uintptr_t)model.primary;
    disp_secondary_base=(uintptr_t)model.secondary;
    disp_ready=1;
    disp_busy=0;
    disp_pitch=5760;
    disp_width=1440;
    disp_fb_phys=0xa0000000;
    disp_cur_phys=disp_fb_phys;
    disp_gui=0;
    disp_last=0;
    disp_last_start=0;
    sweet_disp_te_period=0;
    sweet_disp_te_align=1;
    sweet_disp_te_sync=0;
    sweet_disp_frame_sync=0;
    sweet_disp_scan_ok=0;
    sweet_disp_hold=1;
    sweet_disp_hold_us=9500;
    sweet_disp_shadow_on=1;
    sweet_disp_wait_done=0;
    sweet_disp_hold_frames=0;
    sweet_disp_hold_trunc=0;
    sweet_disp_gui_flips=0;
    sweet_disp_pushes=0;
    sweet_disp_te_edges=0;
    sweet_disp_te_missed=0;
    model.primary[MUTEX_EN/4]=1;
    model.secondary[MUTEX_EN/4]=1;
}
static void edge(unsigned n,uint64_t at)
{
    assert(n < MAX_EVENTS);
    model.edge_at[n]=at;
    if(n>=model.edge_count)model.edge_count=n+1;
}
static void regular_edges(void)
{
    for(unsigned i=0;i<12;i++)edge(i,16667ull*(i+1));
}
static unsigned find_write(unsigned secondary,uint32_t off,uint32_t value)
{
    for(unsigned i=0;i<model.writes;i++) {
        struct write_event *w=&model.log[i];
        if(w->secondary==secondary && w->off==off && w->value==value)return i;
    }
    assert(!"required MMIO write missing");
    return MAX_WRITES;
}
static void check_frame_order(uint64_t phys,int addresses)
{
    unsigned left_stop=find_write(0,MUTEX_EN,0);
    unsigned right_stop=find_write(1,MUTEX_EN,0);
    unsigned start=find_write(0,DSI_START,1);
    unsigned left_on=find_write(0,MUTEX_EN,1);
    unsigned right_on=find_write(1,MUTEX_EN,1);
    assert(left_stop < left_on && right_stop < right_on);
    assert(left_on < start && right_on < start);
    assert(model.log[start].edges==1 && model.log[start].time >= 16000);
    if(addresses) {
        unsigned left=find_write(0,OVL_L0_ADDR,(uint32_t)phys);
        unsigned right=find_write(1,OVL_L0_ADDR,(uint32_t)(phys+disp_pitch/2));
        assert(left_stop < left && right_stop < right);
        assert(model.log[left].edges==0 && model.log[right].edges==0);
        assert(model.log[left].time < 16000 && model.log[right].time < 16000);
        assert(left < left_on && right < right_on);
        assert(model.primary[OVL_L0_ADDR_MSB/4]==(uint32_t)(phys>>32));
        assert(model.secondary[OVL_L0_ADDR_MSB/4]==(uint32_t)(phys>>32));
    }
    assert(model.starts==1);
    assert(sweet_disp_hold_frames==1 && !sweet_disp_hold_trunc);
    assert(model.now >= model.log[start].time+9400);
    assert(!model.primary[MUTEX_EN/4] && !model.secondary[MUTEX_EN/4]);
}
'''


CASES = r'''
int main(int argc,char **argv)
{
    assert(argc==2);
    reset_model();
    if(!strcmp(argv[1],"ack")) {
        uint32_t pending=DSI_INT_TE_RDY|DSI_INT_CMD_DONE|DSI_INT_RD_RDY|
                         DSI_INT_FRAME_DONE|DSI_INT_UNDERRUN;
        model.status=pending;
        disp_ack(DSI_INT_TE_RDY);
        assert(model.status==(pending & ~DSI_INT_TE_RDY));
        assert(model.acks==1 && model.log[0].value==(uint32_t)~DSI_INT_TE_RDY);
    } else if(!strcmp(argv[1],"stale")) {
        model.status=DSI_INT_TE_RDY|DSI_INT_RD_RDY|DSI_INT_CMD_DONE;
        edge(0,16000);
        assert(disp_te_wait(20000)==0);
        assert(model.edges==1 && model.now>=16000 && model.now<16500);
        assert(model.status==(DSI_INT_RD_RDY|DSI_INT_CMD_DONE));
    } else if(!strcmp(argv[1],"timeout")) {
        model.status=DSI_INT_TE_RDY|DSI_INT_FRAME_DONE;
        assert(disp_te_wait(20000)!=0);
        assert(model.now>=20000 && model.now<20500);
        assert(model.status==DSI_INT_FRAME_DONE && !model.edges);
    } else if(!strcmp(argv[1],"stuck")) {
        model.status=DSI_INT_CMD_DONE;
        model.stuck_high=1;
        assert(disp_te_wait(20000)!=0);
        assert(model.now<20500 && !model.edges);
        assert(model.status==DSI_INT_CMD_DONE);
    } else if(!strcmp(argv[1],"measure")) {
        assert(sweet_disp_te_int==1);
        model.status=DSI_INT_TE_RDY|DSI_INT_RD_RDY;
        regular_edges();
        disp_te_measure();
        assert(sweet_disp_te_period>=16500 && sweet_disp_te_period<=16800);
        assert(model.edges==7 && model.status==DSI_INT_RD_RDY);
    } else if(!strcmp(argv[1],"irregular")) {
        /* Every interval is in the 30..120 Hz range; their average is
         * ~60 Hz, but alternating 10/23 ms is not a panel frame clock. */
        uint64_t at=16000;
        for(unsigned i=0;i<12;i++) {
            edge(i,at);
            at += (i&1) ? 23334 : 10000;
        }
        disp_te_measure();
        assert(!sweet_disp_te_period);
    } else if(!strcmp(argv[1],"fast")) {
        for(unsigned i=0;i<12;i++)edge(i,1000ull*(i+1));
        disp_te_measure();
        assert(!sweet_disp_te_period);
    } else if(!strcmp(argv[1],"measure_timeout")) {
        sweet_disp_te_period=16667; /* failed re-measurement invalidates it */
        model.status=DSI_INT_TE_RDY|DSI_INT_RD_RDY;
        disp_te_measure();
        assert(!sweet_disp_te_period && !model.edges);
        assert(model.now>=40000 && model.now<40500);
        assert(model.status==DSI_INT_RD_RDY);
    } else if(!strcmp(argv[1],"scanout") || !strcmp(argv[1],"forced")) {
        uint64_t phys=0x240000000ull;
        if(!strcmp(argv[1],"forced"))sweet_disp_te_sync=1;
        else sweet_disp_te_period=16667;
        model.status=DSI_INT_TE_RDY|DSI_INT_RD_RDY|DSI_INT_CMD_DONE;
        edge(0,16000);
        assert(sweet_display_scanout(phys,5760)==0);
        check_frame_order(phys,1);
        assert(model.status==(DSI_INT_RD_RDY|DSI_INT_CMD_DONE));
        assert(sweet_disp_gui_flips==1);
    } else if(!strcmp(argv[1],"scanout_timeout")) {
        uint64_t previous=disp_cur_phys;
        sweet_disp_te_period=16667;
        model.status=DSI_INT_TE_RDY|DSI_INT_CMD_DONE;
        assert(sweet_display_scanout(0x240000000ull,5760)==-4);
        assert(!model.starts && !sweet_disp_hold_frames && !sweet_disp_gui_flips);
        assert(model.now>=40000 && model.now<41000);
        assert(disp_cur_phys==previous);
        assert(model.primary[OVL_L0_ADDR/4]==(uint32_t)previous);
        assert(model.secondary[OVL_L0_ADDR/4]==(uint32_t)(previous+disp_pitch/2));
        assert(!model.primary[MUTEX_EN/4] && !model.secondary[MUTEX_EN/4]);
        assert(model.status==DSI_INT_CMD_DONE);
    } else if(!strcmp(argv[1],"console")) {
        sweet_disp_te_period=16667;
        model.status=DSI_INT_TE_RDY|DSI_INT_RD_RDY;
        edge(0,16000);
        sweet_display_refresh(1);
        check_frame_order(disp_fb_phys,0);
        assert(model.status==DSI_INT_RD_RDY);
    } else if(!strcmp(argv[1],"console_timeout")) {
        sweet_disp_te_period=16667;
        model.status=DSI_INT_TE_RDY|DSI_INT_RD_RDY;
        sweet_display_refresh(1);
        assert(!model.starts && !sweet_disp_hold_frames);
        assert(!model.primary[MUTEX_EN/4] && !model.secondary[MUTEX_EN/4]);
        assert(model.status==DSI_INT_RD_RDY);
    } else {
        assert(!"unknown test case");
    }
    puts("display model passed");
    return 0;
}
'''


class MatisseDisplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("clang") or shutil.which("cc")
        if compiler is None:
            raise unittest.SkipTest("a C compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="matisse-display-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        source = DRIVER.read_text()
        source = re.sub(r"(?m)^#include[^\n]*\n", "", source)
        boundaries = {
            "disp_read": "static uint32_t disp_read(uint32_t off) { return model_read(0,off); }",
            "disp_write": "static void disp_write(uint32_t off,uint32_t value) { model_write(0,off,value); }",
            "secondary_read": "static uint32_t secondary_read(uint32_t off) { return model_read(1,off); }",
            "secondary_write": "static void secondary_write(uint32_t off,uint32_t value) { model_write(1,off,value); }",
            "disp_counter": "static uint64_t disp_counter(void) { return model_clock(); }",
            "disp_frequency": "static uint64_t disp_frequency(void) { return 1000000; }",
        }
        for name, replacement in boundaries.items():
            source = replace_function(source, name, replacement)
        # The clock/MMIO above provide sequencing. ARM-only barriers and AT
        # diagnostics cannot execute in a native host regression harness.
        source = re.sub(r'__asm__\s+volatile\s*\(.*?\);', '(void)0;', source, flags=re.S)
        harness = directory / "display.c"
        harness.write_text(PREAMBLE + source + MODEL + CASES)
        cls.binary = directory / "display"
        compiled = subprocess.run(
            [compiler, "-std=c11", "-O0", "-Wall", "-Wextra",
             "-Wno-unused-function", "-Wno-unused-variable", "-Wno-uninitialized",
             str(harness), "-o", str(cls.binary)],
            capture_output=True, text=True,
        )
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_acknowledges_only_requested_w0c_bits(self):
        self.run_case("ack")

    def test_discards_stale_te_and_waits_for_fresh_panel_edge(self):
        self.run_case("stale")

    def test_missing_te_times_out_without_clearing_other_irqs(self):
        self.run_case("timeout")

    def test_stuck_high_is_not_a_new_edge(self):
        self.run_case("stuck")

    def test_measures_regular_panel_clock_and_enables_te_by_default(self):
        self.run_case("measure")

    def test_rejects_plausible_average_with_irregular_intervals(self):
        self.run_case("irregular")

    def test_rejects_impossibly_fast_te(self):
        self.run_case("fast")

    def test_failed_measurement_invalidates_previous_clock(self):
        self.run_case("measure_timeout")

    def test_scanout_stages_both_overlays_before_fresh_te_then_holds(self):
        self.run_case("scanout")

    def test_forced_te_sync_also_bypasses_shadow_fast_path(self):
        self.run_case("forced")

    def test_missing_te_restores_previous_scanout_without_starting(self):
        self.run_case("scanout_timeout")

    def test_boot_console_also_waits_for_fresh_te(self):
        self.run_case("console")

    def test_boot_console_skips_frame_when_te_is_missing(self):
        self.run_case("console_timeout")


if __name__ == "__main__":
    unittest.main()

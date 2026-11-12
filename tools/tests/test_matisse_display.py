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
#define FALSE 0
typedef int boolean_t;
static boolean_t ml_set_interrupts_enabled(boolean_t enabled);
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
struct write_event { unsigned secondary; uint32_t off,value; uint64_t time; unsigned edges; int irq_enabled; };
static struct {
    uint32_t primary[REGS], secondary[REGS], status;
    uint64_t now, edge_at[MAX_EVENTS];
    unsigned edge_count, next_edge, edges, writes, acks, starts;
    uint64_t pause_at, pause_us, lock_delay, irq_off_at, irq_off_max;
    uint64_t transfer_us, transfer_finished_at, early_cmd_us, early_cmd_at;
    unsigned lock_delays, disables, restores;
    int stuck_high, paused, irq_enabled, transfer_active, transfer_never, transfer_busy;
    struct write_event log[MAX_WRITES];
} model;
static uint32_t model_read(unsigned secondary,uint32_t off);
static void model_write(unsigned secondary,uint32_t off,uint32_t value);
static uint64_t model_clock(void);
'''


MODEL = r'''
/* Every hardware access costs 10 us; TE occurs in absolute panel time,
 * independently of whether software is polling the status register. */
static void advance_by(uint64_t us)
{
    model.now += us;
    while(model.next_edge < model.edge_count &&
          model.edge_at[model.next_edge] <= model.now) {
        model.status |= DSI_INT_TE_RDY;
        model.next_edge++;
        model.edges++;
    }
    if(model.transfer_active && !model.transfer_never &&
       model.now>=model.transfer_finished_at) {
        model.transfer_active=0;
        model.primary[DSI_STATE_DBG6/4]=
            (model.primary[DSI_STATE_DBG6/4]&~DSI_CMCTL_MASK)|1;
        model.status=(model.status&~DSI_INT_BUSY)|DSI_INT_CMD_DONE;
    }
    if(model.transfer_active && model.early_cmd_at && model.now>=model.early_cmd_at) {
        model.status|=DSI_INT_CMD_DONE;
        model.early_cmd_at=0;
    }
}
static void advance(void){advance_by(10);}
static boolean_t ml_set_interrupts_enabled(boolean_t enabled)
{
    boolean_t previous=model.irq_enabled;
    if(!enabled && previous) {
        /* Scheduling can happen just before entering the protected window. */
        if(model.lock_delays) {
            advance_by(model.lock_delay);
            model.lock_delays--;
        }
        model.disables++;
        model.irq_off_at=model.now;
    } else if(enabled && !previous) {
        uint64_t off=model.now-model.irq_off_at;
        if(off>model.irq_off_max)model.irq_off_max=off;
        model.restores++;
    }
    model.irq_enabled=enabled;
    return previous;
}
static uint64_t model_clock(void){advance();return model.now;}
static uint32_t model_read(unsigned secondary,uint32_t off)
{
    assert(!(off & 3) && off/4 < REGS);
    if(!secondary && off==DSI_INTSTA && model.pause_at &&
       model.now>=model.pause_at && !model.paused) {
        assert(model.irq_enabled);
        model.paused=1;
        advance_by(model.pause_us);
    }
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
    model.log[model.writes++] = (struct write_event){secondary,off,value,model.now,model.edges,model.irq_enabled};
    if(!secondary && off==DSI_INTSTA) {
        /* MediaTek status is write-zero-to-clear. Ones leave other IRQs
         * pending; writing a set bit back does NOT acknowledge that bit. */
        model.status &= value;
        model.acks++;
        return;
    }
    (secondary ? model.secondary : model.primary)[off/4] = value;
    if(!secondary && off==DSI_START && value==1) {
        model.starts++;
        if(model.transfer_us) {
            /* A previous completion must have been acknowledged before the
             * new trigger; the new CMD_DONE appears only on completion. */
            assert(!(model.status&(DSI_INT_CMD_DONE|DSI_INT_FRAME_DONE)));
            model.transfer_active=1;
            model.transfer_finished_at=model.now+model.transfer_us;
            if(model.early_cmd_us)model.early_cmd_at=model.now+model.early_cmd_us;
            model.primary[DSI_STATE_DBG6/4]=
                (model.primary[DSI_STATE_DBG6/4]&~DSI_CMCTL_MASK)|0x80;
            if(model.transfer_busy)model.status|=DSI_INT_BUSY;
        }
    }
}
static void reset_model(void)
{
    memset(&model,0,sizeof(model));
    model.irq_enabled=1;
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
    sweet_disp_te_late=0;
    sweet_disp_te_launch_late=0;
    sweet_disp_te_poll_max=0;
    sweet_disp_te_launch_max=0;
    sweet_disp_vendor=0;
    sweet_disp_state_active=0;
    sweet_disp_state_done=0;
    sweet_disp_cmd_done=0;
    sweet_disp_state_last=0;
    sweet_disp_done_max=0;
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
static unsigned count_writes(uint32_t off)
{
    unsigned count=0;
    for(unsigned i=0;i<model.writes;i++)
        if(!model.log[i].secondary && model.log[i].off==off)count++;
    return count;
}
static void vendor_model(void)
{
    sweet_disp_vendor=1;
    sweet_disp_hold_us=14500;
    sweet_disp_te_period=16667;
    model.primary[DSI_CON_CTRL/4]=0x89a542a2u|DSI_CM_WAIT_DATA_LINE_EN;
    model.primary[DSI_CMD_TYPE1_HS/4]=0xa5401234u;
    model.primary[DSI_CMDQ_SIZE/4]=0xcafe5a7fu;
    model.primary[DSI_STATE_DBG6/4]=0x81a50001u;
    model.primary[DSI_PHY_LCCON/4]=0xfedcba98u;
    model.status=DSI_INT_CMD_DONE|DSI_INT_FRAME_DONE|DSI_INT_RD_RDY;
}
static void check_vendor_order(void)
{
    uint32_t ctrl=(0x89a542a2u|DSI_CM_WAIT_DATA_LINE_EN)&~DSI_CM_WAIT_DATA_LINE_EN;
    uint32_t hs=0xa5401234u|DSI_CMD_NULL_EN|DSI_CMD_HS_EN;
    uint32_t size=(0xcafe5a7fu&~(0xffu|CMDQ_SIZE_SEL))|1|CMDQ_SIZE_SEL;
    unsigned config=find_write(0,DSI_CON_CTRL,ctrl);
    unsigned null_hs=find_write(0,DSI_CMD_TYPE1_HS,hs);
    unsigned cmd=find_write(0,DSI_CMDQ0,0x002c3909);
    unsigned size_write=find_write(0,DSI_CMDQ_SIZE,size);
    unsigned reset_on=find_write(0,DSI_CON_CTRL,ctrl|DSI_RESET);
    unsigned reset_off=MAX_WRITES;
    unsigned start=find_write(0,DSI_START,1);
    for(unsigned i=reset_on+1;i<model.writes;i++)
        if(!model.log[i].secondary && model.log[i].off==DSI_CON_CTRL &&
           model.log[i].value==ctrl){reset_off=i;break;}
    assert(config<null_hs && null_hs<cmd && cmd<size_write);
    assert(size_write<reset_on && reset_on<reset_off && reset_off<start);
    unsigned ack=find_write(0,DSI_INTSTA,~(DSI_INT_CMD_DONE|DSI_INT_FRAME_DONE));
    assert(reset_off<ack && ack<start);
    assert(model.primary[DSI_CON_CTRL/4]==ctrl);
    assert(model.primary[DSI_CMDQ_SIZE/4]==size);
    assert(model.primary[DSI_PHY_LCCON/4]==0xfedcba98u);
    assert(!count_writes(DSI_PHY_LCCON));
    assert(model.log[config].edges==1 && !model.log[config].irq_enabled);
}
static void check_frame_order(uint64_t phys,int addresses,unsigned expected_edges)
{
    unsigned left_stop=find_write(0,MUTEX_EN,0);
    unsigned right_stop=find_write(1,MUTEX_EN,0);
    unsigned start=find_write(0,DSI_START,1);
    unsigned left_on=find_write(0,MUTEX_EN,1);
    unsigned right_on=find_write(1,MUTEX_EN,1);
    assert(left_stop < left_on && right_stop < right_on);
    assert(left_on < start && right_on < start);
    assert(model.log[start].edges==expected_edges && model.log[start].time >= 16000);
    assert(!model.log[left_on].irq_enabled && !model.log[right_on].irq_enabled);
    assert(!model.log[start].irq_enabled);
    assert(model.irq_enabled && model.disables==model.restores);
    /* The 9.5 ms frame hold and full TE polling remain interruptible. */
    assert(model.irq_off_max<1000);
    assert(sweet_disp_te_launch_max>=300 && sweet_disp_te_launch_max<1000);
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
    assert(sweet_disp_vendor==0); /* experiments must stay opt-in */
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
        check_frame_order(phys,1,1);
        assert(model.status==(DSI_INT_RD_RDY|DSI_INT_CMD_DONE));
        assert(sweet_disp_gui_flips==1);
        assert(!count_writes(DSI_CON_CTRL) && !count_writes(DSI_CMD_TYPE1_HS));
        assert(!count_writes(DSI_CMDQ0) && !count_writes(DSI_CMDQ_SIZE));
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
        check_frame_order(disp_fb_phys,0,1);
        assert(model.status==DSI_INT_RD_RDY);
    } else if(!strcmp(argv[1],"console_timeout")) {
        sweet_disp_te_period=16667;
        model.status=DSI_INT_TE_RDY|DSI_INT_RD_RDY;
        sweet_display_refresh(1);
        assert(!model.starts && !sweet_disp_hold_frames);
        assert(!model.primary[MUTEX_EN/4] && !model.secondary[MUTEX_EN/4]);
        assert(model.status==DSI_INT_RD_RDY);
    } else if(!strcmp(argv[1],"late_poll")) {
        sweet_disp_te_period=16667;
        model.status=DSI_INT_RD_RDY;
        edge(0,16000);edge(1,32000);
        model.pause_at=15900;model.pause_us=500;
        assert(sweet_display_scanout(0x240000000ull,5760)==0);
        assert(model.paused && sweet_disp_te_late==1);
        assert(sweet_disp_te_poll_max<=100);
        check_frame_order(0x240000000ull,1,2);
        assert(model.log[find_write(0,DSI_START,1)].time>=32000);
        assert(model.status==DSI_INT_RD_RDY);
    } else if(!strcmp(argv[1],"late_poll_deadline")) {
        model.status=DSI_INT_RD_RDY;
        edge(0,16000);
        model.pause_at=15900;model.pause_us=50000;
        assert(disp_te_wait(40000)!=0);
        assert(model.paused && model.edges==1 && !model.starts);
        assert(!disp_te_edge);
        assert(model.status&DSI_INT_RD_RDY);
    } else if(!strcmp(argv[1],"late_launch")) {
        sweet_disp_te_period=16667;
        model.status=DSI_INT_CMD_DONE;
        edge(0,16000);edge(1,32000);
        model.lock_delay=500;model.lock_delays=1;
        assert(sweet_display_scanout(0x240000000ull,5760)==0);
        assert(sweet_disp_te_launch_late==1);
        assert(model.disables==2 && model.restores==2);
        check_frame_order(0x240000000ull,1,2);
        assert(model.log[find_write(0,DSI_START,1)].time>=32000);
        assert(model.status==DSI_INT_CMD_DONE);
    } else if(!strcmp(argv[1],"late_launch_twice")) {
        uint64_t previous=disp_cur_phys;
        sweet_disp_te_period=16667;
        model.status=DSI_INT_CMD_DONE;
        edge(0,16000);edge(1,32000);
        model.lock_delay=500;model.lock_delays=2;
        assert(sweet_display_scanout(0x240000000ull,5760)==-4);
        assert(sweet_disp_te_launch_late==2);
        assert(!model.starts && !sweet_disp_hold_frames && !sweet_disp_gui_flips);
        assert(model.irq_enabled && model.disables==2 && model.restores==2);
        assert(model.irq_off_max<1000 && disp_cur_phys==previous);
        assert(model.primary[OVL_L0_ADDR/4]==(uint32_t)previous);
        assert(model.secondary[OVL_L0_ADDR/4]==(uint32_t)(previous+disp_pitch/2));
        assert(!model.primary[MUTEX_EN/4] && !model.secondary[MUTEX_EN/4]);
        assert(model.status==DSI_INT_CMD_DONE);
    } else if(!strcmp(argv[1],"vendor_order") || !strcmp(argv[1],"vendor_extend") ||
              !strcmp(argv[1],"vendor_busy_extend") || !strcmp(argv[1],"vendor_early_cmd")) {
        vendor_model();
        model.transfer_us=!strcmp(argv[1],"vendor_order") ? 8000 : 17000;
        model.transfer_busy=!strcmp(argv[1],"vendor_busy_extend");
        if(!strcmp(argv[1],"vendor_early_cmd"))model.early_cmd_us=1000;
        edge(0,16000);
        assert(sweet_display_scanout(0x240000000ull,5760)==0);
        check_frame_order(0x240000000ull,1,1);
        check_vendor_order();
        assert(sweet_disp_state_active==1 && sweet_disp_state_done==1);
        /* STATE and INTSTA are sequential reads: completion between them
         * may leave the diagnostic CMD_DONE sample one read behind. */
        assert(sweet_disp_cmd_done<=1 && sweet_disp_state_last==1);
        if(!strcmp(argv[1],"vendor_early_cmd"))assert(sweet_disp_cmd_done==1);
        assert(!model.transfer_active && model.now>=model.transfer_finished_at);
        assert(model.now>=model.log[find_write(0,DSI_START,1)].time+14500);
        assert(sweet_disp_done_max>=model.transfer_us-200);
        uint32_t hs=(0xa5401234u|DSI_CMD_NULL_EN|DSI_CMD_HS_EN)&~DSI_CMD_HS_EN;
        unsigned hs_off=find_write(0,DSI_CMD_TYPE1_HS,hs);
        assert(model.log[hs_off].time>=model.transfer_finished_at);
        assert(model.primary[DSI_CMD_TYPE1_HS/4]==hs);
        assert(model.status==(DSI_INT_RD_RDY|DSI_INT_CMD_DONE));
    } else if(!strcmp(argv[1],"vendor_stuck")) {
        vendor_model();
        model.transfer_us=30000;model.transfer_never=1;model.early_cmd_us=1000;
        edge(0,16000);
        assert(sweet_display_scanout(0x240000000ull,5760)==0);
        check_vendor_order();
        assert(model.starts==1 && sweet_disp_hold_trunc==1 && sweet_disp_hold_frames==1);
        assert(sweet_disp_state_active==1 && !sweet_disp_state_done && sweet_disp_cmd_done==1);
        assert(model.primary[MUTEX_EN/4]==1 && model.secondary[MUTEX_EN/4]==1);
        assert(model.primary[DSI_CMD_TYPE1_HS/4]==(0xa5401234u|DSI_CMD_NULL_EN|DSI_CMD_HS_EN));
        assert(model.now>=model.log[find_write(0,DSI_START,1)].time+24300);
        assert(model.now<model.log[find_write(0,DSI_START,1)].time+25000);
        assert(model.irq_enabled);
        unsigned writes=model.writes;
        uint64_t at=model.now;
        assert(sweet_display_scanout(0x250000000ull,5760)==-4);
        assert(model.writes==writes && model.starts==1 && disp_cur_phys==0x240000000ull);
        assert(model.now>=at+20000 && model.now<at+20500);
        assert(model.primary[MUTEX_EN/4]==1 && model.secondary[MUTEX_EN/4]==1);
    } else if(!strcmp(argv[1],"vendor_idle_stall") || !strcmp(argv[1],"vendor_busy_stall") ||
              !strcmp(argv[1],"vendor_console_stall")) {
        vendor_model();
        if(!strcmp(argv[1],"vendor_busy_stall"))model.status|=DSI_INT_BUSY;
        else model.primary[DSI_STATE_DBG6/4]=0x81a50010u;
        if(!strcmp(argv[1],"vendor_console_stall"))sweet_display_refresh(1);
        else assert(sweet_display_scanout(0x240000000ull,5760)==-4);
        assert(!model.writes && !model.starts && !model.disables);
        assert(model.now>=20000 && model.now<20500);
        assert(model.primary[MUTEX_EN/4]==1 && model.secondary[MUTEX_EN/4]==1);
        assert(model.primary[DSI_CMD_TYPE1_HS/4]==0xa5401234u);
    } else if(!strcmp(argv[1],"vendor_idle_wait")) {
        vendor_model();
        model.primary[DSI_STATE_DBG6/4]=0x81a50010u;
        model.transfer_active=1;model.transfer_finished_at=9000;
        model.transfer_us=8000;
        edge(0,16000);
        assert(sweet_display_scanout(0x240000000ull,5760)==0);
        check_frame_order(0x240000000ull,1,1);
        check_vendor_order();
        assert(model.log[find_write(0,MUTEX_EN,0)].time>=9000);
        assert(model.log[find_write(1,MUTEX_EN,0)].time>=9000);
    } else if(!strcmp(argv[1],"vendor_unknown")) {
        vendor_model();
        model.primary[DSI_STATE_DBG6/4]=0x81a50000u;
        edge(0,16000);
        assert(sweet_display_scanout(0x240000000ull,5760)==0);
        check_frame_order(0x240000000ull,1,1);
        check_vendor_order();
        assert(!sweet_disp_state_active && !sweet_disp_state_done && !sweet_disp_cmd_done);
        assert(sweet_disp_state_last==0);
        assert(model.primary[DSI_CMD_TYPE1_HS/4]==(0xa5401234u|DSI_CMD_NULL_EN|DSI_CMD_HS_EN));
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

    def test_late_poll_rejects_old_latch_and_uses_next_panel_edge(self):
        self.run_case("late_poll")

    def test_late_poll_cannot_extend_original_deadline(self):
        self.run_case("late_poll_deadline")

    def test_late_launch_restores_interrupts_and_rewaits_for_next_edge(self):
        self.run_case("late_launch")

    def test_repeated_late_launch_rolls_back_and_restores_interrupts(self):
        self.run_case("late_launch_twice")

    def test_vendor_sequence_preserves_bits_and_disables_hs_after_completion(self):
        self.run_case("vendor_order")

    def test_vendor_hold_extends_for_active_cmctl_without_busy_bit(self):
        self.run_case("vendor_extend")

    def test_vendor_hold_extends_for_busy_transfer(self):
        self.run_case("vendor_busy_extend")

    def test_early_cmd_done_does_not_complete_active_cmctl(self):
        self.run_case("vendor_early_cmd")

    def test_stuck_transfer_keeps_mutex_and_hs_on_and_blocks_next_present(self):
        self.run_case("vendor_stuck")

    def test_vendor_idle_stall_does_not_stop_or_repoint_active_pipeline(self):
        self.run_case("vendor_idle_stall")

    def test_vendor_busy_stall_does_not_stop_pipeline(self):
        self.run_case("vendor_busy_stall")

    def test_vendor_console_stall_does_not_stop_pipeline(self):
        self.run_case("vendor_console_stall")

    def test_vendor_waits_for_previous_transfer_before_stopping_mutex(self):
        self.run_case("vendor_idle_wait")

    def test_unknown_cmctl_uses_hold_fallback_without_claiming_completion(self):
        self.run_case("vendor_unknown")


if __name__ == "__main__":
    unittest.main()

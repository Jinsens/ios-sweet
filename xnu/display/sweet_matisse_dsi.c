/*
 * MT6983/matisse command-mode DSC refresh for the SWEET framebuffer console.
 *
 * Register map and sequence were measured on the attached K50 Pro at LK
 * handoff and validated by a shim entry probe that made the panel show new
 * frames (2026-10-22). Both upstream pipelines are required; primary-first
 * mutex ordering was verified with a full green frame on 2026-10-25.
 * Vendor reference: Xiaomi_Kernel_OpenSource
 * rubens-s-oss drivers/gpu/drm/mediatek/mediatek_v2/{mtk_dsi.c,mtk_drm_ddp.c,
 * mtk_disp_ovl.c} and drivers/clk/mediatek/clk-mt6983-mm.c.
 *
 * This driver deliberately does not touch panel rails/reset, the PHY PLL or
 * DSC setup: LK has already done that to display the boot logo, and every
 * precondition below is checked before the first write. The XNU framebuffer
 * is the same physical surface the OVL is already scanning.
 */
#include <arm64/sweet_matisse_dsi.h>

#if defined(SWEET)

#include <stdint.h>
#include <machine/machine_routines.h>
#include <pexpert/device_tree.h>
#include <pexpert/pexpert.h>

/* Primary window covers clocks, mutex, OVL0_2L, DSC0 and DSI0.
 * The optional secondary window supplies the right half of the frame. */
#define DISP_WINDOW_MIN		0x18000u

#define DISP_CLK_STA0		0x100u	/* bit=1: gated, bit=0: enabled */
#define DISP_CLK_CLR0		0x108u
#define DISP_CLK_STA1		0x110u
#define DISP_CLK_CLR1		0x118u
#define DISP_CLK_STA2		0x1a0u
#define DISP_CLK_CLR2		0x1a8u
#define GATE0_MUTEX		(1u << 0)
#define GATE0_DSC0		(1u << 27)
#define GATE0_DSI0		(1u << 29)
#define GATE1_OVL0_2L		(1u << 8)
#define GATE2_DSI_CLK		(1u << 0)

#define DISP_MUTEX		0x1000u
/* LK already programmed MOD0/MOD1 and SOF for OVL -> DSC -> DSI.  The
 * refresh path only toggles EN; do not replace the live routing mask. */
#define MUTEX_EN		(DISP_MUTEX + 0x20u)

#define DISP_OVL0_2L		0x4000u
#define OVL_EN			(DISP_OVL0_2L + 0x0cu)
/* mtk_disp_ovl.c DISP_OVL_BYPASS_SHADOW, bit22 of OVL_EN: with the bit set
 * the module takes every write at once; with it clear it reads the shadow
 * bank that the mutex copies in at the start of a frame. */
#define OVL_BYPASS_SHADOW	(1u << 22)
#define OVL_INTSTA		(DISP_OVL0_2L + 0x08u)
/* mtk_disp_ovl.c INTEN_FLD_FME_CPL_INTEN: the overlay finished the frame it
 * was fetching. This, not DSI_BUSY, says when a fetch is over. */
#define OVL_INT_FME_CPL		(1u << 1)
#define OVL_L0_PITCH		(DISP_OVL0_2L + 0x44u)
#define OVL_L0_ADDR		(DISP_OVL0_2L + 0xf40u)
/* The layer address is 34 bits wide: the upper four live at 0xf4c
 * (mtk_disp_ovl.c DISP_REG_OVL_ADDR_MSB, written when is_support_34bits).
 * Compositor surfaces are allocated above 4 GiB, so this half is required. */
#define OVL_L0_ADDR_MSB		(DISP_OVL0_2L + 0xf4cu)

#define DISP_DSC0		0x15000u
#define DSC_IN_SRC_SEL		(1u << 3)

#define DISP_DSI0		0x17000u
#define DSI_START		(DISP_DSI0 + 0x00u)
#define DSI_INTEN		(DISP_DSI0 + 0x08u)
#define DSI_INTSTA		(DISP_DSI0 + 0x0cu)
#define DSI_CON_CTRL		(DISP_DSI0 + 0x10u)
#define DSI_MODE_CTRL		(DISP_DSI0 + 0x14u)
#define DSI_TXRX_CTRL		(DISP_DSI0 + 0x18u)
#define DSI_PSCTRL		(DISP_DSI0 + 0x1cu)
#define DSI_VACT_NL		(DISP_DSI0 + 0x2cu)
#define DSI_SIZE_CON		(DISP_DSI0 + 0x38u)
#define DSI_BUF_CON0		(DISP_DSI0 + 0x400u)
#define DSI_PHY_LCPAT		(DISP_DSI0 + 0x100u)
#define DSI_PHY_LCCON		(DISP_DSI0 + 0x104u)
#define DSI_MEM_CONTI		(DISP_DSI0 + 0x90u)
#define DSI_CMDQ_SIZE		(DISP_DSI0 + 0x60u)
/* 0xd00: mt6983_dsi_driver_data in the vendor tree has .reg_cmdq0_ofs = 0xd00
 * and .reg_vm_cmd_con_ofs = 0x200, so 0x200 is the VM command block, not the
 * DCS queue. (2507 was built with 0x200 on the opposite reading; it sent no
 * DCS, so nothing depended on it.) The 2502-2504 write did reach the panel
 * through 0xd00 - it moved the tear line once - which is why that offset is
 * the one kept. What those probes could not do was read an answer back. */
#define DSI_CMDQ0		(DISP_DSI0 + 0xd00u)
#define DSI_RX_DATA0		(DISP_DSI0 + 0x74u)
#define CMDQ_SIZE_SEL		(1u << 15)

#define DSI_EN			(1u << 1)
#define DSI_CM_WAIT_DATA_LINE_EN (1u << 24)
#define DSI_CM_WAIT_FIFO_FULL_EN (1u << 27)
#define DSI_INT_TE_RDY		(1u << 2)
#define DSI_INT_FRAME_DONE	(1u << 4)
#define DSI_INT_BUSY		(1u << 31)
#define DSI_INT_CMD_DONE	(1u << 1)
#define DSI_INT_RD_RDY		(1u << 0)
/* The DSIM ran out of data mid-frame: the panel gets a frame with a hole
 * in it, which is what a flash on every new frame would look like. */
#define DSI_INT_UNDERRUN	(1u << 12)
#define DSI_PHY_LC_HS_TX_EN	(1u << 0)
#define DSI_PS_SEL_MASK		(0xfu << 16)
#define DSI_PS_SEL_DSC		(5u << 16)

/* Values LK programs for this 1440x3200 DSC command panel (2x720 slices). */
#define EXPECT_SIZE_CON		((3200u << 16) | 480u)
#define EXPECT_VACT		3200u

static uintptr_t disp_base, disp_secondary_base;
static uint32_t disp_con_ctrl;
static uint64_t disp_last;
static uint64_t disp_last_start;        /* when the last transfer was started */
static uint64_t disp_fb_phys;           /* the surface LK left the OVL on */
static int disp_gui;                    /* a compositor frame is on the panel */

/* Counters the framebuffer prints with its vsync statistics. */
uint32_t sweet_disp_console_flips;      /* console refreshes kept off the panel */
uint32_t sweet_disp_gui_flips;          /* frames put on the panel by scanout */
uint64_t sweet_disp_wait_ticks;         /* ... and how long each waited for the last */
uint32_t sweet_disp_waits;
uint64_t sweet_disp_send_ticks;         /* ... and how long the frame took to go out */
uint64_t sweet_disp_send_max;
uint32_t sweet_disp_sends;
uint32_t sweet_disp_sof_started;        /* frames the mutex's start-of-frame began */
uint32_t sweet_disp_sof_missed;         /* ... and the ones it did not */
uint32_t sweet_disp_te_seen;            /* transfers started at the panel's tearing effect */
uint32_t sweet_disp_te_missed;          /* ... and the ones that waited for it in vain */
uint64_t sweet_disp_te_ticks;           /* time from one tearing effect to the next */
uint32_t sweet_disp_te_gaps;
uint32_t sweet_disp_te_stale;           /* edges older than the frame sent */
uint32_t sweet_disp_te_waits;
int sweet_disp_te_sync;                 /* send only on a fresh edge */
int sweet_disp_wait_done;               /* do not return until the panel has it */
int sweet_disp_frame_sync;              /* move the overlay at a frame boundary */
/* Keep the frame source stopped once a frame is out (sweetfb_hold=1): the
 * panel then holds the last frame until the compositor presents the next
 * one, instead of the hardware re-fetching the same surface every panel
 * frame. The bounded hold also keeps the submitted surface alive until the
 * transfer window has ended, before the framebuffer releases its old one. */
int sweet_disp_hold = 1;
/* Use the overlay's shadow bank instead of LK's bypass (sweetfb_shadow=1):
 * the layer address then lands at the next frame boundary, the mutex can
 * keep running, and nothing has to be stopped around a present. */
int sweet_disp_shadow = 1;
uint32_t sweet_disp_shadow_on;          /* the switch actually happened */
/* sweetfb_teoff=1 sends DCS 0x34 (tearing effect line off) once, so the
 * panel is free to take a frame when it is complete instead of waiting for
 * a line we cannot read. */
int sweet_disp_te_off;
/* sweetfb_teext=1 turns the DSIM's external tearing-effect input on
 * (TXRX_CTRL bit9, edge select bit10). The vendor sets bit9 for this panel
 * (writel(0x0001023c) in mtk_dsi.c); if LK left it clear, TE_RDY is not
 * the panel's line at all and every wait on it is meaningless. */
int sweet_disp_te_ext;
/* sweetfb_teon=1 sends DCS 0x35 0x00 (tearing effect line on, v-blank
 * only) once; if LK never sent it the panel drives no tear line at all,
 * which is what 2500 saw (te edges 0, lows 0). */
int sweet_disp_te_on;
/* sweetfb_sline=1 asks the panel for its scan line (DCS 0x45) a few times
 * at init and keeps the answers. A value that ramps is a phase reference
 * we can actually use; 0 or a constant says the panel does not answer. */
int sweet_disp_sline;
/* sweetfb_teint=1 enables the DSIM's TE interrupt bit. mtk_dsi.c turns it
 * on in mtk_dsi_set_interrupt_enable(); without it the status bit may not
 * latch at all, which is what "te edges 0" would look like. */
int sweet_disp_te_int = 1;
uint32_t sweet_disp_scan[5];
/* What DCS 0x45 (get scan line) says. A value that climbs and wraps once a
 * frame is the panel's own clock: the transfer can start on the wrap instead
 * of at whatever phase the compositor happened to present. */
uint32_t sweet_disp_scan_line;          /* last value read */
uint32_t sweet_disp_scan_max;           /* highest line seen */
uint32_t sweet_disp_scan_wraps;         /* frame boundaries counted */
uint64_t sweet_disp_scan_period;        /* ticks between two of them */
uint32_t sweet_disp_scan_ok;            /* the value behaves like a scan line */
uint32_t sweet_disp_phase_n;            /* presents sampled for their phase */
uint32_t sweet_disp_phase_min;
uint32_t sweet_disp_phase_max;
uint64_t sweet_disp_phase_sum;
/* Whether the mutex alone had already started a transfer when the strobe was
 * written: if it had, the strobe starts a second one over the top of the
 * first, and the panel sees a frame that restarts half way down. */
/* How long the frame source is given to fill the DSC/DSIM before the DSI
 * strobe goes out (sweetfb_lead, microseconds). At 0 the strobe arrives
 * before the pipeline has a line to send; 2506 showed that 300 us of head
 * start is what turned "the whole upper half flashes" into an occasional
 * band, so this is the lever for the rest of it. */
uint32_t sweet_disp_lead_us = 300;
uint32_t sweet_disp_pre_busy;
uint32_t sweet_disp_pre_idle;
uint64_t sweet_disp_pre_ticks;
/* How long the frame source is left running after the strobe before it is
 * stopped again (sweetfb_hold_us, microseconds). 9500 came from a measured
 * 8.8 ms frame with a margin; if a frame actually takes longer, stopping at
 * 9500 cuts its tail and the rows below the cut keep the previous frame -
 * a fixed band that only shows while the picture moves. hold_trunc counts
 * the presents that ended with the DSIM still busy, which is that cut. */
uint32_t sweet_disp_hold_us = 9500;
uint32_t sweet_disp_hold_trunc;
uint32_t sweet_disp_hold_frames;
uint64_t sweet_disp_hold_ticks;
uint32_t sweet_disp_frame_seen;         /* boundaries waited for */
uint32_t sweet_disp_frame_missed;       /* ... and the ones that did not come */
uint64_t sweet_disp_frame_wait_ticks;
uint64_t sweet_disp_frame_wait_max;
uint64_t sweet_disp_te_wait_ticks;
uint64_t sweet_disp_te_wait_max;
static uint64_t disp_cur_phys;          /* what layer 0 points at now */
static uint32_t disp_pitch, disp_width;
static int disp_ready;
static int disp_busy;
/* Whether the mutex was found running when a frame was about to be put on
 * the panel, and how many times it had to be stopped for it. The mutex
 * keeps triggering frames on its own once it is enabled (2494: the panel
 * followed a paint with nothing sent), so the address has to be written
 * while it is off, otherwise the new surface is picked up in the middle of
 * a frame that is already being fetched: the band, and the text that jumps
 * with it. */
uint32_t sweet_disp_underruns;
uint32_t sweet_disp_te_edges;
uint32_t sweet_disp_te_lows;
uint64_t sweet_disp_te_period;          /* one panel frame, measured */
uint32_t sweet_disp_te_sync_hits;
uint32_t sweet_disp_te_sync_missed;
uint64_t sweet_disp_te_sync_ticks;
int sweet_disp_te_align = 1;            /* start a frame on the panel's TE */
uint32_t sweet_disp_te_late;           /* edges observed after a scheduling gap */
uint32_t sweet_disp_te_launch_late;    /* rescheduled between TE and trigger */
uint64_t sweet_disp_te_poll_max;       /* upper bound on accepted edge age */
uint64_t sweet_disp_te_launch_max;     /* accepted TE observation to DSI_START */
static uint64_t disp_te_edge;
uint32_t sweet_disp_dcs;
uint32_t sweet_disp_mutex_stops;
uint32_t sweet_disp_mutex_running;
uint32_t sweet_disp_mutex_last;

/*
 * Why the panel is dark: sweet_display_init() is a chain of "LK's state must
 * still match the measured working configuration, otherwise leave the panel
 * alone" checks, and every one of them used to return silently. There is no
 * console to complain on, and this runs long before the UFS progress marks
 * exist, so the decision is recorded here and dumped later by
 * bsd/dev/sweet_ufs.c (marks 70..85) once the mark channel is up.
 *
 * [0] stage reached, [1] width<<16|height, [2] stride, [3..5] clock gates,
 * [6] DSI mode, [7] PSCTRL, [8] SIZE_CON, [9] VACT, [10] BUF_CON0,
 * [11] INTSTA, [12] OVL_EN, [13] OVL_L0_ADDR, [14] OVL_L0_PITCH,
 * [15] framebuffer phys.
 */
uint32_t sweet_disp_trace[16];
uint32_t sweet_disp_calls;      /* sweet_display_refresh() invocations */
uint32_t sweet_disp_pushes;     /* refreshes that actually pushed a frame */
uint32_t sweet_disp_busy_skips; /* triggers skipped while DSI was busy */
uint32_t sweet_disp_status;     /* last DSI_INTSTA observed by refresh */
/* Cached until UFS is ready: VA, PA, PAR(first), PAR(last), stride/height. */
uint64_t sweet_disp_target[5];
uint32_t sweet_disp_status_before;
uint32_t sweet_disp_cmdq[4]; /* CON_CTRL, TXRX_CTRL, CMDQ_SIZE, CMDQ0 */

static inline uint32_t
disp_read(uint32_t off)
{
	return *(volatile uint32_t *)(disp_base + off);
}

static inline void
disp_write(uint32_t off, uint32_t value)
{
	*(volatile uint32_t *)(disp_base + off) = value;
	__asm__ volatile("dsb sy" ::: "memory");
}

/* MT6983 status latches are WRITE ZERO TO CLEAR, as in the vendor
 * mtk_dsi_irq_status(): writel(~status, DSI_INTSTA). Writing the event mask
 * itself preserves that stale event and clears every unrelated one. Never
 * read/modify/write here: a different event can arrive after the read. */
static void
disp_ack(uint32_t flags)
{
	disp_write(DSI_INTSTA, ~flags);
}

static uint32_t
secondary_read(uint32_t off)
{
	return *(volatile uint32_t *)(disp_secondary_base + off);
}

static void
secondary_write(uint32_t off, uint32_t value)
{
	*(volatile uint32_t *)(disp_secondary_base + off) = value;
	__asm__ volatile("dsb sy" ::: "memory");
}

/* Darwin ARM64 treats semicolons as comments: separate instructions with
 * newlines so the timebase read is emitted, including in polling loops. */
static uint64_t
disp_counter(void)
{
	uint64_t v;
	__asm__ volatile("isb\n\tmrs %0, cntpct_el0" : "=r"(v));
	return v;
}

static uint64_t
disp_frequency(void)
{
	uint64_t v;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
	return v;
}

/* Enable or stop the frame source. Both display pipelines have their own
 * mutex; the right half must be stopped with the left. */
static void
disp_mutex(uint32_t on)
{
	uint32_t was = disp_read(MUTEX_EN) & 1u;

	sweet_disp_mutex_last = was;
	if (on == 0 && was) {
		sweet_disp_mutex_stops++;
		sweet_disp_mutex_running++;
	}
	disp_write(MUTEX_EN, on);
	if (disp_secondary_base) {
		secondary_write(MUTEX_EN, on);
	}
}

/* Let the frame that was just triggered finish going out, then stop the
 * frame source: nothing reads the surface again until the next present, so
 * there is no fetch in flight to catch a change made between two.
 *
 * Nothing here can be trusted as "the frame is out" on its own. The
 * overlay's completion (FME_CPL) fires while the frame is still leaving:
 * 2496 used it and every present returned after 1.3 ms when one frame takes
 * 8.8 ms, so the rest of the frame was cut away - the flash seen while
 * scrolling, with a still picture perfectly clean. DSI_BUSY is clear when a
 * frame is started, so it cannot be waited for either. Wait out the measured
 * frame and only then stop; BUSY, when it does show, extends the wait. */
static void
disp_hold_frame(uint64_t began)
{
	uint64_t done = began + disp_frequency() * sweet_disp_hold_us / 1000000;
	uint64_t due = done + disp_frequency() / 100;           /* +10 ms cap */
	uint32_t status;
	uint64_t now;

	do {
		status = disp_read(DSI_INTSTA);
		now = disp_counter();
	} while (now < done || ((status & DSI_INT_BUSY) && now < due));
	if (status & DSI_INT_BUSY) {
		sweet_disp_hold_trunc++;
	}
	sweet_disp_hold_ticks += now - began;
	sweet_disp_hold_frames++;
	disp_mutex(0);
}


/* One DCS read out of the DSIM's own command queue: the vendor encodes it as
 * type 0x06 (DCS read, no parameter) with BTA in the config byte, and the
 * answer lands in the RX FIFO as a MIPI packet - type, length high, length
 * low, then the payload (mtk_dsi_recv_cnt() reads the FIFO with readb).
 *
 * Two things made the earlier attempts read nothing: the ready bit was
 * already set from a previous answer, so every poll passed at once and every
 * read returned the same stale word, and the queue was written at 0xd00
 * instead of 0x200 so no command was ever sent. */
static int
disp_dcs_read(uint32_t cmd, uint32_t *raw, uint32_t *payload)
{
	uint64_t limit;
	uint32_t status, w, type;
	volatile uint8_t *rx = (volatile uint8_t *)(disp_base + DSI_RX_DATA0);

	disp_ack(DSI_INT_RD_RDY | DSI_INT_CMD_DONE);
	limit = disp_counter() + disp_frequency() / 200;   /* 5 ms */
	while ((disp_read(DSI_INTSTA) & DSI_INT_RD_RDY) &&
	    disp_counter() < limit) {
		__asm__ volatile("yield");
	}
	disp_write(DSI_CMDQ0, (cmd << 16) | (0x06u << 8) | 0x04u);
	disp_write(DSI_CMDQ_SIZE, 1u | CMDQ_SIZE_SEL);
	disp_write(DSI_START, 0);
	disp_write(DSI_START, 1);
	limit = disp_counter() + disp_frequency() / 50;    /* 20 ms */
	do {
		status = disp_read(DSI_INTSTA);
	} while ((status & DSI_INT_RD_RDY) == 0 && disp_counter() < limit);
	if ((status & DSI_INT_RD_RDY) == 0) {
		disp_ack(DSI_INT_RD_RDY);
		return -1;
	}
	w = disp_read(DSI_RX_DATA0);
	type = rx[0];
	if (raw != NULL) {
		*raw = w;
	}
	if (payload != NULL) {
		/* DCS long read 0x1c, generic long read 0x1a, and the two short
		 * forms all carry their bytes from offset 3. */
		if (type == 0x1cu || type == 0x1au || type == 0x22u ||
		    type == 0x21u) {
			*payload = ((uint32_t)rx[3] << 8) | (uint32_t)rx[4];
		} else {
			*payload = 0xffffffffu;
		}
	}
	disp_ack(DSI_INT_RD_RDY | DSI_INT_CMD_DONE);
	return 0;
}

/* One DCS short write out of the DSIM's own command queue (mtk_dsi_cmdq:
 * CONFIG|DATA_ID in the first word, the payload bytes above it, size in
 * words plus the size-select bit). Used to talk to the panel itself rather
 * than to the pipeline.
 *
 * The data type is not decoration: 0x05 is "short write, no parameter" and
 * 0x15 is "short write, one parameter". 2504 sent 0x35 with a parameter
 * under type 0x05, and the panel stopped taking frames - the parameter byte
 * was read as a second command. Everything with a value goes out as 0x15. */
static int
disp_dcs(uint32_t cmd, uint32_t value, int has_value)
{
	uint64_t limit = disp_counter() + disp_frequency() / 20;	/* 50 ms */
	uint32_t status;

	disp_write(DSI_CMDQ0, (has_value ? (value << 24) : 0) | (cmd << 16) |
	    ((has_value ? 0x15u : 0x05u) << 8));
	disp_write(DSI_CMDQ_SIZE, 1u | CMDQ_SIZE_SEL);
	disp_ack(DSI_INT_CMD_DONE);
	disp_write(DSI_START, 0);
	disp_write(DSI_START, 1);
	sweet_disp_dcs++;
	do {
		status = disp_read(DSI_INTSTA);
	} while ((status & DSI_INT_CMD_DONE) == 0 && disp_counter() < limit);
	disp_ack(DSI_INT_CMD_DONE);
	return (status & DSI_INT_CMD_DONE) ? 0 : -1;
}

/* Point both overlays at phys. Callers hold disp_busy. */
static void
disp_point(uint64_t phys, uint32_t pitch)
{
	disp_write(OVL_L0_ADDR_MSB, (uint32_t)(phys >> 32));
	disp_write(OVL_L0_ADDR, (uint32_t)phys);
	disp_write(OVL_L0_PITCH, pitch);
	if (disp_secondary_base) {
		secondary_write(OVL_L0_ADDR_MSB, (uint32_t)(phys >> 32));
		secondary_write(OVL_L0_ADDR, (uint32_t)(phys + (pitch >> 1)));
		secondary_write(OVL_L0_PITCH, pitch);
	}
	disp_cur_phys = phys;
}

/* Wait for one panel frame boundary out of the DSIM's TE status.
 *
 * The bit is an interrupt latch, not a level: it goes high on the tearing
 * effect and stays high until a ZERO is written to that bit. Previous probes
 * wrote one, preserving the stale latch: their instant "edges" did not say
 * anything about the panel's actual TE signal. */
static int
disp_te_wait(uint64_t ticks)
{
	uint64_t end = disp_counter() + ticks;
	uint64_t last_low, now;
	uint64_t freshness = disp_frequency() / 10000; /* at most 100 us old */

	disp_te_edge = 0;
	disp_ack(DSI_INT_TE_RDY);
	last_low = disp_counter();
	if (disp_read(DSI_INTSTA) & DSI_INT_TE_RDY) {
		/* A latch that did not clear is not a fresh panel boundary. */
		return -1;
	}
	sweet_disp_te_lows++;
	for (;;) {
		/* Timestamp BEFORE the low observation. A preemption anywhere
		 * between that sample and the high sample is included in its age. */
		uint64_t before = disp_counter();
		uint32_t status = disp_read(DSI_INTSTA);

		now = disp_counter();
		if (now > end) {
			return -1;
		}
		if (status & DSI_INT_TE_RDY) {
			uint64_t age = now - last_low;

			disp_ack(DSI_INT_TE_RDY);
			if (age <= freshness) {
				if (age > sweet_disp_te_poll_max) {
					sweet_disp_te_poll_max = age;
				}
				disp_te_edge = now;
				return 0;
			}
			/* A latched edge survived a long scheduling gap. It is
			 * real, but too old to start a frame on: wait for another. */
			sweet_disp_te_late++;
			if (disp_read(DSI_INTSTA) & DSI_INT_TE_RDY) {
				return -1;
			}
			sweet_disp_te_lows++;
			last_low = now;
		} else {
			last_low = before;
		}
		__asm__ volatile("yield");
	}
}

/* Establish a clock from seven real edges, checking each of six intervals.
 * An average alone can turn stale flags mixed with timeouts into a plausible
 * frame period. No panel command or PHY/timing change is needed. */
static void
disp_te_measure(void)
{
	uint64_t last = 0, sum = 0, reference = 0;
	uint64_t freq = disp_frequency();
	uint32_t i;

	sweet_disp_te_period = 0;
	for (i = 0; i < 7; i++) {
		uint64_t now, interval;

		if (disp_te_wait(freq / 25) != 0) {    /* 40 ms, including 30 Hz */
			sweet_disp_te_missed++;
			return;
		}
		now = disp_counter();
		sweet_disp_te_edges++;
		if (i != 0) {
			interval = now - last;
			if (interval < freq / 120 || interval > freq / 30) {
				return;
			}
			if (reference == 0) {
				reference = interval;
			} else if (interval < reference * 3 / 4 ||
			    interval > reference * 5 / 4) {
				return;
			}
			sum += interval;
		}
		last = now;
	}
	sweet_disp_te_period = sum / 6;
}

/* Called after both overlay addresses have been staged, before either
 * pipeline is triggered. The old fShown remains retained during this wait. */
static int
disp_sync_frame(void)
{
	static uint64_t last_te;
	uint64_t began, at, waited;

	if (!sweet_disp_te_sync &&
	    !(sweet_disp_te_period != 0 && sweet_disp_te_align)) {
		disp_te_edge = 0;
		return 0;
	}
	began = disp_counter();
	if (disp_te_wait(disp_frequency() / 25) != 0) {
		sweet_disp_te_sync_missed++;
		sweet_disp_te_missed++;
		return -1;                  /* keep the previous frame on the panel */
	}
	at = disp_counter();
	waited = at - began;
	sweet_disp_te_sync_hits++;
	sweet_disp_te_sync_ticks += waited;
	sweet_disp_te_seen++;
	sweet_disp_te_waits++;
	sweet_disp_te_wait_ticks += waited;
	if (waited > sweet_disp_te_wait_max) {
		sweet_disp_te_wait_max = waited;
	}
	if (last_te != 0) {
		sweet_disp_te_ticks += at - last_te;
		sweet_disp_te_gaps++;
	}
	last_te = at;
	return 0;
}

/* The polling thread may be rescheduled after it saw TE. Protect only the
 * short launch window, never the full wait for the next panel frame. */
static int
disp_start_frame(uint64_t *began)
{
	uint32_t attempt;

	for (attempt = 0; attempt < 2; attempt++) {
		boolean_t irq_state;
		uint64_t now, lead, until, start;
		uint32_t status;

		if (disp_sync_frame() != 0) {
			return -1;
		}
		irq_state = ml_set_interrupts_enabled(FALSE);
		now = disp_counter();
		if (disp_te_edge != 0 && now - disp_te_edge > disp_frequency() / 10000) {
			(void)ml_set_interrupts_enabled(irq_state);
			sweet_disp_te_launch_late++;
			continue;
		}
		disp_write(MUTEX_EN, 0);
		if (disp_secondary_base) {
			secondary_write(MUTEX_EN, 0);
		}
		disp_write(MUTEX_EN, 1);
		if (disp_secondary_base) {
			secondary_write(MUTEX_EN, 1);
		}
		lead = disp_frequency() * sweet_disp_lead_us / 1000000;
		start = disp_counter();
		until = start + lead;
		status = disp_read(DSI_INTSTA);
		while (!(status & DSI_INT_BUSY) && lead != 0 && disp_counter() < until) {
			__asm__ volatile("yield");
			status = disp_read(DSI_INTSTA);
		}
		if (status & DSI_INT_BUSY) {
			sweet_disp_pre_busy++;
			sweet_disp_pre_ticks += disp_counter() - start;
		} else {
			sweet_disp_pre_idle++;
		}
		*began = disp_counter();
		disp_write(DSI_START, 0);
		disp_write(DSI_START, 1);
		if (disp_te_edge != 0) {
			uint64_t latency = disp_counter() - disp_te_edge;

			if (latency > sweet_disp_te_launch_max) {
				sweet_disp_te_launch_max = latency;
			}
		}
		(void)ml_set_interrupts_enabled(irq_state);
		return 0;
	}
	return -1;
}

/* LK leaves DISP_OVL_BYPASS_SHADOW (OVL_EN bit22) set, so every register
 * write reaches the module at once - including a layer address written while
 * the overlay is half way through a frame, which is the band. With the bit
 * clear the module reads its shadow bank, and the mutex copies that bank in
 * at the start of each frame, so an address written between two frames takes
 * effect at the next one and nothing has to be stopped.
 *
 * The shadow bank was never written while the bypass was on, so the live
 * configuration is snapshotted first and written back after the switch;
 * otherwise the first frame would come from whatever the bank held. */
static void
ovl_shadow_enable(uintptr_t base)
{
	uint32_t cfg[0x40], layer[0x20];
	uint32_t i, off;

	for (i = 0; i < 0x40; i++) {
		off = i * 4u;
		/* INTSTA (0x08) clears on write, TRIG/RST (0x10/0x14) act on
		 * write: not part of the configuration. */
		if (off == 0x08u || off == 0x10u || off == 0x14u) {
			cfg[i] = 0;
			continue;
		}
		cfg[i] = *(volatile uint32_t *)(base + off);
	}
	for (i = 0; i < 0x20; i++) {
		layer[i] = *(volatile uint32_t *)(base + 0xf00u + i * 4u);
	}

	*(volatile uint32_t *)(base + 0x0cu) =
	    cfg[0x0c / 4] & ~OVL_BYPASS_SHADOW;
	for (i = 0; i < 0x40; i++) {
		off = i * 4u;
		if (off == 0x0cu || off == 0x08u || off == 0x10u || off == 0x14u)
			continue;
		*(volatile uint32_t *)(base + off) = cfg[i];
	}
	for (i = 0; i < 0x20; i++) {
		*(volatile uint32_t *)(base + 0xf00u + i * 4u) = layer[i];
	}
}

void
sweet_display_init(uint64_t fb_phys, uint32_t width, uint32_t height,
    uint32_t stride)
{
	DTEntry entry;
	uint64_t const *reg;
	uint32_t size;
	uint32_t ctl, mode, ps, size_con, vact, buf, gates0, gates1, gates2;
	uint32_t ovl_en, ovl_addr, ovl_pitch, intsta;

	/* These decide what init does to the panel, and SweetFramebuffer only
	 * parses them in start() - long after this runs - so read them here. */
	(void)PE_parse_boot_argn("sweetfb_hold", &sweet_disp_hold,
	    sizeof(sweet_disp_hold));
	(void)PE_parse_boot_argn("sweetfb_hold_us", &sweet_disp_hold_us,
	    sizeof(sweet_disp_hold_us));
	if (sweet_disp_hold_us > 50000) {
		sweet_disp_hold_us = 50000;
	}
	(void)PE_parse_boot_argn("sweetfb_tealign", &sweet_disp_te_align,
	    sizeof(sweet_disp_te_align));
	(void)PE_parse_boot_argn("sweetfbtesync", &sweet_disp_te_sync,
	    sizeof(sweet_disp_te_sync));
	(void)PE_parse_boot_argn("sweetfb_teon", &sweet_disp_te_on,
	    sizeof(sweet_disp_te_on));
	(void)PE_parse_boot_argn("sweetfb_teoff", &sweet_disp_te_off,
	    sizeof(sweet_disp_te_off));
	(void)PE_parse_boot_argn("sweetfb_teext", &sweet_disp_te_ext,
	    sizeof(sweet_disp_te_ext));
	(void)PE_parse_boot_argn("sweetfb_sline", &sweet_disp_sline,
	    sizeof(sweet_disp_sline));
	(void)PE_parse_boot_argn("sweetfb_teint", &sweet_disp_te_int,
	    sizeof(sweet_disp_te_int));
	(void)PE_parse_boot_argn("sweetfb_lead", &sweet_disp_lead_us,
	    sizeof(sweet_disp_lead_us));
	if (sweet_disp_lead_us > 300) {
		sweet_disp_lead_us = 300; /* bound the interrupt-disabled launch */
	}
	(void)PE_parse_boot_argn("sweetfb_shadow", &sweet_disp_shadow,
	    sizeof(sweet_disp_shadow));
	disp_ready = 0;
	disp_secondary_base = 0;
	sweet_disp_trace[0] = 1;
	sweet_disp_trace[1] = (width << 16) | (height & 0xffff);
	sweet_disp_trace[2] = stride;
	sweet_disp_trace[15] = (uint32_t)fb_phys;
	if (SecureDTFindEntry("name", "sweet-display", &entry) != kSuccess) {
		return;
	}
	if (SecureDTGetProperty(entry, "reg", (void const **)&reg, &size) != kSuccess ||
	    size < 16 || !reg[0] || (reg[0] & 3) ||
	    reg[1] < DISP_WINDOW_MIN) {
		sweet_disp_trace[0] = 2;
		return;
	}
	if (!width || !height || !stride || stride < width * 4 ||
	    height != EXPECT_VACT || stride > 32768) {
		sweet_disp_trace[0] = 3;
		return;
	}

	disp_base = ml_io_map(reg[0], reg[1]);
	if (!disp_base) {
		sweet_disp_trace[0] = 4;
		return;
	}

	/* Refuse to write unless the LK state matches the measured, working
	 * configuration. A different panel or a re-initialized bus stays
	 * headless instead of pushing bogus frames. */
	gates0 = disp_read(DISP_CLK_STA0);
	gates1 = disp_read(DISP_CLK_STA1);
	gates2 = disp_read(DISP_CLK_STA2);
	ctl = disp_read(DSI_CON_CTRL);
	mode = disp_read(DSI_MODE_CTRL);
	ps = disp_read(DSI_PSCTRL);
	size_con = disp_read(DSI_SIZE_CON);
	vact = disp_read(DSI_VACT_NL);
	buf = disp_read(DSI_BUF_CON0);
	ovl_en = disp_read(OVL_EN);
	ovl_addr = disp_read(OVL_L0_ADDR);
	ovl_pitch = disp_read(OVL_L0_PITCH);
	intsta = disp_read(DSI_INTSTA);

	sweet_disp_trace[3] = gates0;
	sweet_disp_trace[4] = gates1;
	sweet_disp_trace[5] = gates2;
	sweet_disp_trace[6] = mode;
	sweet_disp_trace[7] = ps;
	sweet_disp_trace[8] = size_con;
	sweet_disp_trace[9] = vact;
	sweet_disp_trace[10] = buf;
	sweet_disp_trace[11] = intsta;
	sweet_disp_trace[12] = ovl_en;
	sweet_disp_trace[13] = ovl_addr;
	sweet_disp_trace[14] = ovl_pitch;
	sweet_disp_cmdq[0] = ctl;
	sweet_disp_cmdq[1] = disp_read(DSI_TXRX_CTRL);
	sweet_disp_cmdq[2] = disp_read(DISP_DSI0 + 0x60u);
	sweet_disp_cmdq[3] = disp_read(DISP_DSI0 + 0xd00u);

	if ((gates0 & (GATE0_MUTEX | GATE0_DSC0 | GATE0_DSI0)) ||
	    (gates1 & GATE1_OVL0_2L) || (gates2 & GATE2_DSI_CLK)) {
		sweet_disp_trace[0] = 5;
		return;		/* clock gate status 1 = gated off */
	}
	if ((mode & 3u) != 0 || (ps & DSI_PS_SEL_MASK) != DSI_PS_SEL_DSC ||
	    size_con != EXPECT_SIZE_CON || vact != EXPECT_VACT ||
	    (buf & 1u) == 0) {
		sweet_disp_trace[0] = 6;
		return;
	}
	if ((ovl_en & 1u) == 0 || ovl_addr != (uint32_t)fb_phys ||
	    ovl_pitch != stride) {
		sweet_disp_trace[0] = 7;
		return;
	}
	/* TE_RDY is an edge status, not a stable readiness predicate. Keep it
	 * for diagnostics; command transfers are gated by DSI_BUSY below. */
	if ((intsta & DSI_INT_TE_RDY) == 0) {
		sweet_disp_trace[0] = 8;
	} else {
		sweet_disp_trace[0] = 9;
	}

	/* DSC_IN_SRC_SEL uses both 720-pixel display pipelines. LK leaves
	 * the right pipeline configured but stopped; it needs its own mutex. */
	if (disp_read(DISP_DSC0) & DSC_IN_SRC_SEL) {
		if (SecureDTGetProperty(entry, "secondary-reg", (void const **)&reg,
		    &size) != kSuccess || size < 16 || !reg[0] ||
		    (reg[0] & 3) || reg[1] < 0x5000 || (width & 1)) {
			sweet_disp_trace[0] = 10;
			return;
		}
		disp_secondary_base = ml_io_map(reg[0], reg[1]);
		if (!disp_secondary_base) {
			sweet_disp_trace[0] = 11;
			return;
		}
		if ((secondary_read(DISP_CLK_STA0) & GATE0_MUTEX) ||
		    (secondary_read(DISP_CLK_STA1) & GATE1_OVL0_2L) ||
		    !(secondary_read(OVL_EN) & 1) ||
		    secondary_read(OVL_L0_ADDR) != (uint32_t)(fb_phys + width * 2) ||
		    secondary_read(OVL_L0_PITCH) != stride) {
			sweet_disp_trace[0] = 12;
			disp_secondary_base = 0;
			return;
		}
	}

	disp_con_ctrl = ctl | DSI_EN | DSI_CM_WAIT_DATA_LINE_EN |
	    DSI_CM_WAIT_FIFO_FULL_EN;
	disp_fb_phys = fb_phys;
	disp_cur_phys = fb_phys;
	disp_pitch = stride;
	disp_width = width;
	disp_write(DSI_PHY_LCPAT, 0x55);
	disp_write(DSI_PHY_LCCON, disp_read(DSI_PHY_LCCON) |
	    DSI_PHY_LC_HS_TX_EN);
	disp_write(DSI_CON_CTRL, disp_con_ctrl);

	/* Take the overlay out of LK's bypass mode while nothing is being
	 * fetched, so the first frame after this comes from the shadow bank
	 * we just filled. */
	if (sweet_disp_shadow && (ovl_en & OVL_BYPASS_SHADOW)) {
		uint64_t limit = disp_counter() + disp_frequency() / 50;
		uint32_t st;

		disp_write(MUTEX_EN, 0);
		if (disp_secondary_base) {
			secondary_write(MUTEX_EN, 0);
		}
		do {
			st = disp_read(DSI_INTSTA);
		} while ((st & DSI_INT_BUSY) && disp_counter() < limit);
		ovl_shadow_enable(disp_base + DISP_OVL0_2L);
		if (disp_secondary_base) {
			ovl_shadow_enable(disp_secondary_base + DISP_OVL0_2L);
		}
		sweet_disp_shadow_on = 1;
	}
	if (sweet_disp_te_ext) {
		disp_write(DSI_TXRX_CTRL, disp_read(DSI_TXRX_CTRL) |
		    (1u << 9));              /* preserve LK's edge polarity */
	}
	sweet_disp_cmdq[1] = disp_read(DSI_TXRX_CTRL);
	sweet_disp_cmdq[0] = disp_read(DSI_CON_CTRL);
	disp_last = 0;
	disp_ready = 1;
	if (sweet_disp_te_int) {
		disp_write(DSI_INTEN, disp_read(DSI_INTEN) | DSI_INT_TE_RDY);
		disp_te_measure();
	}
	if (sweet_disp_te_on) {
		uint64_t until = disp_counter() + disp_frequency() / 5; /* 200 ms */
		uint32_t prev = disp_read(DSI_INTSTA);

		(void)disp_dcs(0x35u, 0x00u, 1);
		/* Watch the tear line itself for 50 ms: three frames at 60 Hz
		 * are enough to see whether turning it on changed anything. */
		while (disp_counter() < until) {
			uint32_t cur = disp_read(DSI_INTSTA);

			if ((prev & DSI_INT_TE_RDY) && !(cur & DSI_INT_TE_RDY)) {
				sweet_disp_te_lows++;
			}
			if (!(prev & DSI_INT_TE_RDY) && (cur & DSI_INT_TE_RDY)) {
				sweet_disp_te_edges++;
			}
			prev = cur;
		}
	}
	if (sweet_disp_sline) {
		/* Only while the bus is quiet: a command written into the queue
		 * in the middle of a frame transfer corrupts that frame. */
		uint64_t idle = disp_counter() + disp_frequency() / 50;
		uint32_t st = disp_read(DSI_INTSTA);

		while ((st & DSI_INT_BUSY) && disp_counter() < idle) {
			__asm__ volatile("yield");
			st = disp_read(DSI_INTSTA);
		}
		if (st & DSI_INT_BUSY) {
			sweet_disp_scan_ok = 2;   /* never got a quiet bus */
		}
	}
	if (sweet_disp_sline && sweet_disp_scan_ok == 0) {
		/* Ask the panel where it is scanning, until the answer wraps:
		 * that wrap is the frame boundary, and starting the transfer
		 * on it is what keeps a new frame from landing half way down
		 * the one the panel is already showing. */
		uint32_t prev = 0, maxl = 0, wraps = 0, ok = 0;
		uint64_t end = disp_counter() + disp_frequency() / 20;  /* 50 ms */
		uint64_t first = 0, last = 0;

		while (disp_counter() < end && wraps < 3) {
			uint32_t v = 0;

			if (disp_dcs_read(0x45u, &sweet_disp_scan[ok < 5 ? ok : 4],
			    &v) != 0 || v == 0xffffffffu) {
				break;          /* no answer, or not a read response */
			}
			if (ok == 0 || v > maxl) {
				maxl = v;
			}
			if (ok != 0 && v + 32u < prev) {
				uint64_t at = disp_counter();

				if (first == 0) {
					first = at;
				}
				last = at;
				wraps++;
			}
			prev = v;
			sweet_disp_scan_line = v;
			ok++;
		}
		sweet_disp_scan_max = maxl;
		sweet_disp_scan_wraps = wraps;
		if (wraps >= 2 && last > first) {
			uint64_t period = (last - first) / (wraps - 1);

			/* Anything between 30 and 120 Hz is a plausible
			 * panel; outside that the value is not a scan line. */
			if (period > disp_frequency() / 200 &&
			    period < disp_frequency() / 20) {
				sweet_disp_scan_period = period;
				sweet_disp_scan_ok = 1;
			}
		}
	}
	if (sweet_disp_te_off) {
		int rc = disp_dcs(0x34u, 0, 0);

		sweet_disp_trace[11] = (sweet_disp_trace[11] & 0xffff0000u) |
		    (0x3400u | (uint32_t)(rc == 0));
	}
	sweet_display_refresh(1);
}


void
sweet_display_refresh(int force)
{
	uint64_t now, limit;
	uint32_t status;

	sweet_disp_calls++;
	if (!disp_ready) {
		return;
	}
	now = disp_counter();
	/* Five attempts/s, including timeouts: a stuck engine must not stall
	 * early boot for 20 ms on every console character. */
	if (!force && disp_last && now - disp_last < disp_frequency() / 5) {
		return;
	}
	if (__atomic_test_and_set(&disp_busy, __ATOMIC_ACQUIRE)) {
		return;
	}

	/* Recheck under the lock in case another CPU just refreshed. */
	now = disp_counter();
	if (!force && disp_last && now - disp_last < disp_frequency() / 5) {
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return;
	}
	disp_last = now;

	/* Once the compositor's own frame is on the panel, this buffer is not
	 * to go back on it: every console refresh would blank the screen for a
	 * frame, which is what the black flicker was. */
	if (disp_gui) {
		sweet_disp_console_flips++;
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return;
	}

	/* rubens-s-oss mtk_dsi_poll_for_idle(): DSI_BUSY, not FRAME_DONE, is
	 * the command-mode completion indication. Do not retrigger an active
	 * transfer. Console writers still share one buffer; this is not a
	 * double-buffering or tear-free guarantee. */
	limit = now + disp_frequency() / 50;	/* 20 ms */
	/* No new frame may start while the overlay is being pointed
	 * somewhere else: an address taken up mid-fetch is half of one frame
	 * and half of the next. */
	disp_mutex(0);
	status = disp_read(DSI_INTSTA);
	sweet_disp_status_before = status;
	while ((status & DSI_INT_BUSY) && disp_counter() < limit) {
		__asm__ volatile("yield");
		status = disp_read(DSI_INTSTA);
	}
	if (status & DSI_INT_BUSY) {
		sweet_disp_busy_skips++;
		sweet_disp_status = status;
		disp_mutex(1);
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return;
	}

	/* Keep LK's MOD/SOF route and DSC configuration.  mtk_dsi_start() is a
	 * strobe: write 0 then 1 after the mutex 0 -> 1 transition. The console
	 * mappings are Normal-NC: publish their stores without dereferencing
	 * the physical framebuffer after TTBR0 has been removed. */
	__asm__ volatile("dsb sy" ::: "memory");
	if (disp_cur_phys != disp_fb_phys) {
		/* A compositor frame was shown directly; this refresh is of the
		 * kernel's own buffer, so point the overlays back at it. */
		disp_point(disp_fb_phys, disp_pitch);
	}
	uint64_t began;
	if (disp_start_frame(&began) != 0) {
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return;
	}
	sweet_disp_pushes++;
	if (sweet_disp_hold) {
		disp_hold_frame(began);
	}
	status = disp_read(DSI_INTSTA);
	if (status & DSI_INT_UNDERRUN) {
		sweet_disp_underruns++;
		disp_ack(DSI_INT_UNDERRUN);
	}
	sweet_disp_status = status;

	__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
}

/*
 * Show a surface the OVL was not started on. The panel is in command mode:
 * nothing is fetched until a transfer is triggered, so repointing layer 0
 * between frames cannot tear a frame that is already leaving, and a full
 * 1440x3200 CPU copy (36.8 MB of traffic, in the vsync callback) is not
 * needed to present what the compositor just drew. Both overlay windows are
 * moved: the right half is fetched by the secondary one, the same half-a-row
 * offset LK used (720 pixels of a 5760-byte row).
 */
int
sweet_display_scanout(uint64_t phys, uint32_t pitch)
{
	uint64_t now, limit;
	uint32_t status;

	if (!disp_ready || !phys || (phys & 0x3f) || pitch != disp_pitch ||
	    (phys >> 36) != 0) {
		return -1;		/* not ready, or not a panel-shaped surface */
	}
	sweet_disp_calls++;
	if (__atomic_test_and_set(&disp_busy, __ATOMIC_ACQUIRE)) {
		return -1;
	}
	/* Keep the old asynchronous path only when synchronization is disabled
	 * or no periodic TE was found. With a measured TE, stop and stage BOTH
	 * pipelines before waiting: writing one address after SOF can split a
	 * frame, even if OVL shadow registers are enabled. */
	if (sweet_disp_shadow_on && !sweet_disp_te_sync &&
	    !(sweet_disp_te_period != 0 && sweet_disp_te_align)) {
		disp_point(phys, pitch);
		if ((disp_read(MUTEX_EN) & 1u) == 0) {
			disp_write(MUTEX_EN, 1);
			if (disp_secondary_base) {
				secondary_write(MUTEX_EN, 1);
			}
			disp_write(DSI_START, 0);
			disp_write(DSI_START, 1);
			sweet_disp_pushes++;
		}
		sweet_disp_gui_flips++;
		disp_gui = 1;
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return 0;
	}
	now = disp_counter();
	limit = now + disp_frequency() / 50;	/* 20 ms, as refresh() */
	/* Nothing may start a frame from here to the trigger below. The
	 * mutex runs on between presents and starts one every panel frame on
	 * its own, so any address written while it is up lands inside a fetch
	 * that is already going out. Stop it first, let the frame in flight
	 * finish, then move the overlay. */
	disp_mutex(0);
	status = disp_read(DSI_INTSTA);
	sweet_disp_status_before = status;
	bool was_busy = (status & DSI_INT_BUSY) != 0;
	while ((status & DSI_INT_BUSY) && disp_counter() < limit) {
		__asm__ volatile("yield");
		status = disp_read(DSI_INTSTA);
	}
	if (was_busy && disp_last_start != 0) {
		/* How long the panel took to receive the frame that was still
		 * leaving when this one was ready. */
		uint64_t took = disp_counter() - disp_last_start;

		sweet_disp_send_ticks += took;
		if (took > sweet_disp_send_max) {
			sweet_disp_send_max = took;
		}
		sweet_disp_sends++;
	}
	disp_last_start = 0;
	/* How long the previous frame's transfer was still running: this is the
	 * time the panel spent reading a surface the compositor may have been
	 * drawing the next frame into. */
	sweet_disp_wait_ticks += disp_counter() - now;
	sweet_disp_waits++;
	if (status & DSI_INT_BUSY) {
		sweet_disp_busy_skips++;
		sweet_disp_status = status;
		disp_mutex(1);		/* carry on showing what it was showing */
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return -2;		/* still transferring the previous frame */
	}
	disp_last = now;

	/* The overlay fetches the surface all the time, not only when a frame is
	 * triggered: painting that memory with no transfer at all puts the paint
	 * on the glass (2494). So an address written between two frames takes
	 * effect where it is written, and the frame above that line is the last
	 * one and the frame below is this one - the band, and the text that
	 * jumps with it. Move the address at a frame boundary instead. */
	if (sweet_disp_frame_sync) {
		uint64_t began, edge, waited;

		status = disp_read(DSI_INTSTA);
		if (status & DSI_INT_FRAME_DONE) {
			disp_ack(DSI_INT_FRAME_DONE);
		}
		began = disp_counter();
		edge = began + disp_frequency() / 50;   /* 20 ms */
		while (((status = disp_read(DSI_INTSTA)) & DSI_INT_FRAME_DONE) == 0 &&
		    disp_counter() < edge) {
			__asm__ volatile("yield");
		}
		waited = disp_counter() - began;
		if (status & DSI_INT_FRAME_DONE) {
			sweet_disp_frame_seen++;
			sweet_disp_frame_wait_ticks += waited;
			if (waited > sweet_disp_frame_wait_max) {
				sweet_disp_frame_wait_max = waited;
			}
			disp_ack(DSI_INT_FRAME_DONE);
		} else {
			sweet_disp_frame_missed++;
		}
	}
	if (sweet_disp_scan_ok) {
		/* Where the panel is when the compositor hands over a frame:
		 * if these numbers are spread across the whole frame, the
		 * transfer starts at a random phase every time and the tear
		 * line can only be where the panel happened to be. */
		uint32_t v = 0;

		if (disp_dcs_read(0x45u, NULL, &v) == 0 && v < 0x10000u) {
			sweet_disp_scan_line = v;
			sweet_disp_phase_sum += v;
			if (sweet_disp_phase_n == 0 || v < sweet_disp_phase_min) {
				sweet_disp_phase_min = v;
			}
			if (v > sweet_disp_phase_max) {
				sweet_disp_phase_max = v;
			}
			sweet_disp_phase_n++;
		}
	}
	uint64_t previous_phys = disp_cur_phys;
	__asm__ volatile("dsb sy" ::: "memory");
	disp_point(phys, pitch);
	/* The overlays latch their address at the trigger; a write that did not
	 * arrive would show whatever the previous frame left, so read it back
	 * and let the caller copy instead. */
	if (disp_read(OVL_L0_ADDR) != (uint32_t)phys ||
	    disp_read(OVL_L0_ADDR_MSB) != (uint32_t)(phys >> 32)) {
		disp_point(disp_fb_phys, disp_pitch);
		disp_mutex(1);
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return -3;		/* the overlay did not take the address */
	}

	uint64_t began;
	if (disp_start_frame(&began) != 0) {
		/* An old or missing TE must not turn into an unsynchronized flash. */
		disp_point(previous_phys, pitch);
		__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
		return -4;
	}
	disp_last_start = began;
	sweet_disp_pushes++;
	sweet_disp_gui_flips++;
	disp_gui = 1;
	status = disp_read(DSI_INTSTA);
	if (sweet_disp_wait_done) {
		/* Whether a frame that is still leaving when the compositor starts
		 * the next one is what puts a band across the picture: hold the
		 * caller until the panel has all of it, and see if the band goes. */
		uint64_t stop = began + disp_frequency() / 20;   /* 50 ms */

		while ((status & DSI_INT_BUSY) && disp_counter() < stop) {
			__asm__ volatile("yield");
			status = disp_read(DSI_INTSTA);
		}
	}
	if (sweet_disp_hold) {
		disp_hold_frame(began);
	}
	status = disp_read(DSI_INTSTA);
	if (status & DSI_INT_UNDERRUN) {
		sweet_disp_underruns++;
		disp_ack(DSI_INT_UNDERRUN);
	}
	sweet_disp_status = status;
	__atomic_clear(&disp_busy, __ATOMIC_RELEASE);
	return 0;
}

/* AT checks translations without reading pixels or faulting on an unmapped
 * VA. Save the results now; sweet_mark() cannot persist them before UFS init.
 * All framebuffer aliases must be Normal-NC (PAR attributes 0x44). */
void
sweet_display_set_target(uint64_t va, uint64_t phys, uint32_t stride,
    uint32_t height)
{
	sweet_disp_target[0] = va;
	sweet_disp_target[1] = phys;
	sweet_disp_target[2] = sweet_disp_target[3] = 1;
	sweet_disp_target[4] = ((uint64_t)stride << 32) | height;
	if (!va || !stride || !height || va + (uint64_t)stride * height < va) {
		return;
	}
	uint64_t first, last, end = va + (uint64_t)stride * height - 1;
	__asm__ volatile("at s1e1w, %1\n\tisb\n\tmrs %0, par_el1"
	    : "=r"(first) : "r"(va) : "memory");
	__asm__ volatile("at s1e1w, %1\n\tisb\n\tmrs %0, par_el1"
	    : "=r"(last) : "r"(end) : "memory");
	sweet_disp_target[2] = first;
	sweet_disp_target[3] = last;
}

#endif /* SWEET */

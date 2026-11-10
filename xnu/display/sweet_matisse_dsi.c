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

#define DSI_EN			(1u << 1)
#define DSI_CM_WAIT_DATA_LINE_EN (1u << 24)
#define DSI_CM_WAIT_FIFO_FULL_EN (1u << 27)
#define DSI_INT_TE_RDY		(1u << 2)
#define DSI_INT_FRAME_DONE	(1u << 4)
#define DSI_INT_BUSY		(1u << 31)
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
 * frame. Not the default: if this panel needs a stream to stay lit, this
 * is what would leave it dark, so it is a trial lever and not the path. */
int sweet_disp_hold = 1;
uint32_t sweet_disp_hold_frames;
uint32_t sweet_disp_hold_missed;
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

/* Let the frame that was just triggered finish being fetched, then stop the
 * frame source: nothing reads the surface again until the next present, so
 * there is no fetch in flight to catch a change made between two. The wait
 * is the overlay's own completion bit, bounded above one measured frame
 * (8.8 ms) - if the bit never comes, the frame has gone out anyway. */
static void
disp_hold_frame(uint64_t began)
{
	uint64_t due = began + disp_frequency() * 12 / 1000;	/* 12 ms */

	disp_write(OVL_INTSTA, OVL_INT_FME_CPL);
	while ((disp_read(OVL_INTSTA) & OVL_INT_FME_CPL) == 0 &&
	    disp_counter() < due) {
		__asm__ volatile("yield");
	}
	if (disp_read(OVL_INTSTA) & OVL_INT_FME_CPL) {
		sweet_disp_hold_frames++;
	} else {
		sweet_disp_hold_missed++;
	}
	disp_write(OVL_INTSTA, OVL_INT_FME_CPL);
	disp_mutex(0);
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

void
sweet_display_init(uint64_t fb_phys, uint32_t width, uint32_t height,
    uint32_t stride)
{
	DTEntry entry;
	uint64_t const *reg;
	uint32_t size;
	uint32_t ctl, mode, ps, size_con, vact, buf, gates0, gates1, gates2;
	uint32_t ovl_en, ovl_addr, ovl_pitch, intsta;

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
	disp_last = 0;
	disp_ready = 1;
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
	/* MT6983: start the primary path before the secondary. Reversing
	 * these writes stalls both OVL outputs and leaves DSI waiting for
	 * data. This ordering completed a full frame on matisse (0146). */
	disp_write(MUTEX_EN, 1);
	if (disp_secondary_base) {
		secondary_write(MUTEX_EN, 1);
	}
	uint64_t began = disp_counter();
	disp_write(DSI_START, 0);
	disp_write(DSI_START, 1);
	sweet_disp_pushes++;
	if (sweet_disp_hold) {
		disp_hold_frame(began);
	}
	sweet_disp_status = disp_read(DSI_INTSTA);

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

	/* The panel's tearing effect is the only sign of where its own scan is.
	 * A frame it is given in the middle of one is shown half old and half
	 * new: the horizontal band, and the text that jumps with it. An edge
	 * that is already set when the frame is ready says only that the scan
	 * has started at some point since the last one, not where in it we are,
	 * so it is dropped and the next real edge is waited for. That is the
	 * difference between sending a frame and sending it at a boundary. */
	{
		static uint64_t lastTe;
		uint64_t began, edge;

		status = disp_read(DSI_INTSTA);
		if (sweet_disp_te_sync && (status & DSI_INT_TE_RDY)) {
			disp_write(DSI_INTSTA, status & ~DSI_INT_BUSY);
			status &= ~DSI_INT_TE_RDY;
			sweet_disp_te_stale++;
		}
		began = disp_counter();
		/* A whole frame from now, not from when this call started: the
		 * wait for the previous transfer may already have used it up. */
		edge = began + disp_frequency() / 50;   /* 20 ms */
		while ((status & DSI_INT_TE_RDY) == 0 && disp_counter() < edge) {
			__asm__ volatile("yield");
			status = disp_read(DSI_INTSTA);
		}
		if (status & DSI_INT_TE_RDY) {
			uint64_t at = disp_counter();
			uint64_t waited = at - began;

			sweet_disp_te_seen++;
			sweet_disp_te_waits++;
			sweet_disp_te_wait_ticks += waited;
			if (waited > sweet_disp_te_wait_max) {
				sweet_disp_te_wait_max = waited;
			}
			if (lastTe != 0 && at > lastTe) {
				sweet_disp_te_ticks += at - lastTe;
				sweet_disp_te_gaps++;
			}
			lastTe = at;
			/* MTK clears the status bits by writing them back. */
			disp_write(DSI_INTSTA, status & ~DSI_INT_BUSY);
		} else {
			sweet_disp_te_missed++;
		}
	}

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
			disp_write(DSI_INTSTA, status & ~DSI_INT_BUSY);
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
			disp_write(DSI_INTSTA, status & ~DSI_INT_BUSY);
		} else {
			sweet_disp_frame_missed++;
		}
	}
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

	/* The address is in place and nothing has fetched it yet: let the
	 * mutex run again and start one frame from it. */
	disp_write(MUTEX_EN, 0);
	if (disp_secondary_base) {
		secondary_write(MUTEX_EN, 0);
	}
	disp_write(MUTEX_EN, 1);
	if (disp_secondary_base) {
		secondary_write(MUTEX_EN, 1);
	}
	/* Started right after the panel's own frame boundary (see above). The
	 * transfer is not waited for: the compositor can draw the next frame
	 * while the panel reads this one, and the wait at the top of the next
	 * call is where a transfer that is still running shows up. Measured
	 * there, a "send" sample is a frame that was late, not every frame. */
	uint64_t began = disp_counter();
	disp_write(DSI_START, 0);
	disp_write(DSI_START, 1);
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

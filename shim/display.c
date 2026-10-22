/*
 * Push the shim console to an MT6983 command-mode DSC panel.
 *
 * LK leaves the panel initialized and the OVL scanning the boot framebuffer;
 * only the DSI engine is off and the mutex stopped. Sequence and guards were
 * validated on hardware with the entry probe (2026-10-22): the panel showed
 * new frames while DSI INTSTA FRAME_DONE stayed clear, so no completion bit
 * is polled. All bases come from the live FDT; without a matching state this
 * stays a no-op and the panel keeps showing whatever LK left there.
 */
#include <stdint.h>
#include "board.h"
#include "console.h"
#include "display.h"
#include "fdt.h"

#define rsr(r) ({ uint64_t _v; __asm__ volatile("mrs %0, " r : "=r"(_v)); _v; })

static uint64_t
now(void)
{
	return rsr("cntpct_el0");
}

#define DSI_START	0x00
#define DSI_INTSTA	0x0c
#define DSI_CON_CTRL	0x10
#define DSI_MODE_CTRL	0x14
#define DSI_PSCTRL	0x1c
#define DSI_VACT_NL	0x2c
#define DSI_SIZE_CON	0x38
#define DSI_MEM_CONTI	0x90
#define DSI_BUF_CON0	0x400
#define DSI_BUF_CON1	0x404
#define DSI_TX_BUF_RW_TIMES 0x410
#define DSI_DEBUG_SEL	0x170
#define DSI_PHY_LCPAT	0x100
#define DSI_PHY_LCCON	0x104

#define DSI_EN			(1u << 1)
#define DSI_CM_WAIT_DATA_LINE_EN (1u << 24)
#define DSI_CM_WAIT_FIFO_FULL_EN (1u << 27)
#define DSI_INT_TE_RDY		(1u << 2)
#define DSI_PHY_LC_HS_TX_EN	(1u << 0)
#define DSI_PS_SEL_MASK		(0xfu << 16)
#define DSI_PS_SEL_DSC		(5u << 16)

#define EXPECT_SIZE_CON		((3200u << 16) | 480u)
#define EXPECT_VACT		3200u

#define GATE0_MUTEX		(1u << 0)
#define GATE0_DSC0		(1u << 27)
#define GATE0_DSI0		(1u << 29)
#define GATE1_APB		(1u << 1)
#define GATE1_OVL0_2L		(1u << 8)
#define GATE2_DSI_CLK		(1u << 0)

static uintptr_t dsi, mutex, ovl, clk;
static uint32_t con_ctrl;
static int ready;
static uint32_t error;

/* Match the display-kick entry probe's status codes. */
#define ERR_NODES	1
#define ERR_CLOCKS	2
#define ERR_DSI		3
#define ERR_OVL		4
#define ERR_BUF		5
#define ERR_TE		6

static inline uint32_t rd(uintptr_t a)
{
	return *(volatile uint32_t *)a;
}

static inline void wr(uintptr_t a, uint32_t v)
{
	*(volatile uint32_t *)a = v;
	__asm__ volatile("dsb sy" ::: "memory");
}

static int resolve(uint64_t dtb, const char *compatible, uintptr_t *base)
{
	char path[160];
	uint64_t a, size;
	if (!fdt_find_compatible(dtb, compatible, path, sizeof(path)) ||
	    !fdt_reg_n(dtb, path, 0, &a, &size) || !a || size < 0x1000)
		return 0;
	*base = (uintptr_t)a;
	return 1;
}

/* Measured matisse display0 addresses. Used only when the live FDT lookup
 * fails on a board already identified as matisse; every precondition below
 * still has to match, so a wrong base refuses to write. */
static int resolve_disp(uint64_t dtb, const char *compatible, uintptr_t *base,
    uintptr_t measured)
{
	if (resolve(dtb, compatible, base))
		return 1;
	if (!measured)
		return 0;
	*base = measured;
	return 1;
}

int display_probe(uint64_t dtb, uint64_t fb, uint32_t stride)
{
	uintptr_t dsi0, mutex0, ovl0, clk0;
	uint32_t ctl, mode, ps, size_con, vact, buf, g0, g1, g2;

	ready = 0;
	error = 0;
	if (brd.name[0] != 'm') {
		error = ERR_NODES;
		return 0;
	}
	if (!resolve_disp(dtb, "mediatek,dsi0", &dsi0, 0x14017000) ||
	    !resolve_disp(dtb, "mediatek,disp_mutex0", &mutex0, 0x14001000) ||
	    !resolve_disp(dtb, "mediatek,disp_ovl0_2l", &ovl0, 0x14004000)) {
		error = ERR_NODES;
		return 0;
	}
	if (!resolve_disp(dtb, "mediatek,mt6983-dispsys_config", &clk0, 0) &&
	    !resolve_disp(dtb, "mediatek,mt6983-disp", &clk0, 0x14000000)) {
		error = ERR_NODES;
		return 0;
	}

	/* Keep the bases even if a later check fails: the forced-refresh
	 * diagnostic still needs them to push the error line on screen. */
	dsi = dsi0;
	mutex = mutex0;
	ovl = ovl0;
	clk = clk0;
	con_ctrl = rd(dsi0 + DSI_CON_CTRL);

	g0 = rd(clk0 + 0x100);
	g1 = rd(clk0 + 0x110);
	g2 = rd(clk0 + 0x1a0);

	/* MTK gate status: 0 = clock enabled, 1 = gated. The SET register
	 * gates a clock; the CLR register at +8 enables it. LK leaves some
	 * display clocks gated on a normal boot, so enable them first. */
	if (g0 & (GATE0_MUTEX | GATE0_DSC0 | GATE0_DSI0))
		wr(clk0 + 0x108, g0 & (GATE0_MUTEX | GATE0_DSC0 | GATE0_DSI0));
	if (g1 & (GATE1_APB | GATE1_OVL0_2L))
		wr(clk0 + 0x118, g1 & (GATE1_APB | GATE1_OVL0_2L));
	if (g2 & GATE2_DSI_CLK)
		wr(clk0 + 0x1a8, g2 & GATE2_DSI_CLK);
	__asm__ volatile("dsb sy" ::: "memory");

	g0 = rd(clk0 + 0x100);
	g1 = rd(clk0 + 0x110);
	g2 = rd(clk0 + 0x1a0);
	if ((g0 & (GATE0_MUTEX | GATE0_DSC0 | GATE0_DSI0)) ||
	    (g1 & GATE1_OVL0_2L) || (g2 & GATE2_DSI_CLK)) {
		error = ERR_CLOCKS;
		return 0;
	}

	ctl = rd(dsi0 + DSI_CON_CTRL);
	mode = rd(dsi0 + DSI_MODE_CTRL);
	wr(dsi0 + DSI_MEM_CONTI, 0x3cu);
	ps = rd(dsi0 + DSI_PSCTRL);
	size_con = rd(dsi0 + DSI_SIZE_CON);
	vact = rd(dsi0 + DSI_VACT_NL);
	buf = rd(dsi0 + DSI_BUF_CON0);

	/* LK may leave the command-mode buffer disabled on this boot path.
	 * Rebuild the parts whose values are known; keep LK's thresholds. */
	if ((buf & 1u) == 0) {
		uint32_t bc1 = rd(dsi0 + DSI_BUF_CON1);
		wr(dsi0 + DSI_BUF_CON1, (bc1 & 0xffff0000u) | 0xb1u);
		wr(dsi0 + DSI_TX_BUF_RW_TIMES, 512000u);
		wr(dsi0 + DSI_DEBUG_SEL,
		    rd(dsi0 + DSI_DEBUG_SEL) | (1u << 10));
		wr(dsi0 + DSI_BUF_CON0, buf | 1u);
		wr(dsi0 + DSI_MEM_CONTI, 0x3cu);
		buf = rd(dsi0 + DSI_BUF_CON0);
	}

	if ((mode & 3u) != 0 || (ps & DSI_PS_SEL_MASK) != DSI_PS_SEL_DSC ||
	    size_con != EXPECT_SIZE_CON || vact != EXPECT_VACT) {
		error = ERR_DSI;
		return 0;
	}
	if ((buf & 1u) == 0) {
		error = ERR_BUF;
		return 0;
	}
	/* LK cycles through three scanout frames. Read the live OVL address
	 * only now that the display clocks are enabled, and move the console
	 * there instead of assuming the first frame is visible. */
	uint32_t scan = rd(ovl0 + 0xf40);
	uint64_t frame = (uint64_t)brd.fb_stride * brd.fb_h;
	if (scan >= 0x40000000u && (scan & 3u) == 0 &&
	    scan >= brd.fb_base && scan + frame <= brd.fb_base + brd.fb_size &&
	    scan != (uint32_t)fb) {
		brd.fb_base = scan;
		fb = scan;
		if (con_fb) {
			con_target((volatile uint32_t *)(uintptr_t)scan);
			con_redraw(RED);
		}
	}
	if ((rd(ovl0 + 0x0c) & 1u) == 0 || rd(ovl0 + 0xf40) != (uint32_t)fb ||
	    rd(ovl0 + 0x44) != stride) {
		error = ERR_OVL;
		return 0;
	}

	/* Wait for one TE pulse; the kick probe proved this is present after
	 * the display clocks are enabled. */
	uint64_t limit = now() + rsr("cntfrq_el0") / 30;	/* 33 ms */
	while ((rd(dsi0 + DSI_INTSTA) & DSI_INT_TE_RDY) == 0 && now() < limit)
		__asm__ volatile("yield");
	if ((rd(dsi0 + DSI_INTSTA) & DSI_INT_TE_RDY) == 0) {
		error = ERR_TE;
		return 0;
	}

	con_ctrl = ctl | DSI_EN | DSI_CM_WAIT_DATA_LINE_EN |
	    DSI_CM_WAIT_FIFO_FULL_EN;
	wr(dsi + DSI_PHY_LCPAT, 0x55);
	wr(dsi + DSI_PHY_LCCON, rd(dsi + DSI_PHY_LCCON) | DSI_PHY_LC_HS_TX_EN);
	wr(dsi + DSI_CON_CTRL, con_ctrl);
	ready = 1;
	return 1;
}

void display_refresh(void)
{
	if (!ready)
		return;
	/* W0C status: retire the stale TE flag before waiting for the next. */
	wr(dsi + DSI_INTSTA, ~DSI_INT_TE_RDY);
	uint64_t limit = now() + rsr("cntfrq_el0") / 100;	/* 10 ms */
	while ((rd(dsi + DSI_INTSTA) & DSI_INT_TE_RDY) == 0 && now() < limit)
		__asm__ volatile("yield");
	wr(mutex + 0x08, 0);
	wr(mutex + 0x20, 0);
	wr(mutex + 0x20, 1);
	wr(dsi + DSI_START, 1);
}

/* Diagnostic-only push that ignores the precondition checks. Used after
 * drawing display_probe's error code so the panel can show why it failed. */
void display_force_refresh(void)
{
	if (!dsi || !mutex)
		return;
	wr(dsi + DSI_MEM_CONTI, 0x3cu);
	wr(dsi + DSI_PHY_LCPAT, 0x55);
	wr(dsi + DSI_PHY_LCCON, rd(dsi + DSI_PHY_LCCON) | DSI_PHY_LC_HS_TX_EN);
	wr(dsi + DSI_CON_CTRL, con_ctrl | DSI_EN | DSI_CM_WAIT_DATA_LINE_EN |
	    DSI_CM_WAIT_FIFO_FULL_EN);
	wr(dsi + DSI_INTSTA, ~DSI_INT_TE_RDY);
	uint64_t limit = now() + rsr("cntfrq_el0") / 25;	/* 40 ms */
	while ((rd(dsi + DSI_INTSTA) & DSI_INT_TE_RDY) == 0 && now() < limit)
		__asm__ volatile("yield");
	wr(mutex + 0x08, 0);
	wr(mutex + 0x20, 0);
	wr(mutex + 0x20, 1);
	wr(dsi + DSI_START, 1);
}

int display_active(void)
{
	return ready;
}

uint32_t display_error(void)
{
	return error;
}

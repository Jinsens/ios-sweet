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
#define DSI_BUF_CON0	0x400
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
#define GATE1_OVL0_2L		(1u << 8)
#define GATE2_DSI_CLK		(1u << 0)

static uintptr_t dsi, mutex, ovl, clk;
static uint32_t con_ctrl;
static int ready;

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

int display_probe(uint64_t dtb, uint64_t fb, uint32_t stride)
{
	uintptr_t dsi0, mutex0, ovl0, clk0;
	uint32_t ctl, mode, ps, size_con, vact, buf, g0, g1, g2;

	ready = 0;
	if (brd.name[0] != 'm')
		return 0;
	if (!resolve(dtb, "mediatek,dsi0", &dsi0) ||
	    !resolve(dtb, "mediatek,disp_mutex0", &mutex0) ||
	    !resolve(dtb, "mediatek,disp_ovl0_2l", &ovl0))
		return 0;
	if (!resolve(dtb, "mediatek,mt6983-dispsys_config", &clk0) &&
	    !resolve(dtb, "mediatek,mt6983-disp", &clk0))
		return 0;

	g0 = rd(clk0 + 0x100);
	g1 = rd(clk0 + 0x110);
	g2 = rd(clk0 + 0x1a0);
	ctl = rd(dsi0 + DSI_CON_CTRL);
	mode = rd(dsi0 + DSI_MODE_CTRL);
	ps = rd(dsi0 + DSI_PSCTRL);
	size_con = rd(dsi0 + DSI_SIZE_CON);
	vact = rd(dsi0 + DSI_VACT_NL);
	buf = rd(dsi0 + DSI_BUF_CON0);

	/* MTK gate status: 0 = clock enabled. */
	if ((g0 & (GATE0_MUTEX | GATE0_DSC0 | GATE0_DSI0)) ||
	    (g1 & GATE1_OVL0_2L) || (g2 & GATE2_DSI_CLK))
		return 0;
	if ((mode & 3u) != 0 || (ps & DSI_PS_SEL_MASK) != DSI_PS_SEL_DSC ||
	    size_con != EXPECT_SIZE_CON || vact != EXPECT_VACT || (buf & 1u) == 0)
		return 0;
	if ((rd(ovl0 + 0x0c) & 1u) == 0 || rd(ovl0 + 0xf40) != (uint32_t)fb ||
	    rd(ovl0 + 0x44) != stride)
		return 0;
	if ((rd(dsi0 + DSI_INTSTA) & DSI_INT_TE_RDY) == 0)
		return 0;

	dsi = dsi0;
	mutex = mutex0;
	ovl = ovl0;
	clk = clk0;
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

int display_active(void)
{
	return ready;
}

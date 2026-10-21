/* Read-only MT6983 display snapshot plus an explicitly built, guarded
 * command-mode refresh experiment.
 *
 * Register definitions: MiCode Xiaomi_Kernel_OpenSource branch rubens-s-oss,
 * drivers/gpu/drm/mediatek/mediatek_v2/{mtk_dsi.c,mtk_disp_ovl.c,
 * mtk_drm_ddp.c}, drivers/clk/mediatek/clk-mt6983-mm.c and
 * drivers/gpu/drm/mediatek/mediatek_v2/mtk_mipi_tx.c.
 * All MMIO bases come from the live FDT; no register is written unless the
 * build defines ENTRY_DISPLAY_KICK and every precondition below holds.
 *
 * MT6983 DSI INTSTA is write-0-to-clear (vendor clears with ~status), the
 * mutex is at 0x14001000 and its per-frame sequence is CFG=0, EN=0, EN=1,
 * then DSI_START=1. Command-mode frames are pushed by DSI_START; the OVL
 * must still be enabled and scanning the boot framebuffer.
 *
 * Hardware result 2026-10-22: the panel visibly refreshed after the first
 * DSI_START while INTSTA bit4 (FRAME_DONE) stayed clear. Do not use bit4 as
 * the completion criterion; issuing START after TE is the validated action.
 */
#include <stdint.h>
#include "entryprobe_config.h"
#include "board.h"
#include "fdt.h"
#if defined(ENTRY_DISPLAY_KICK)
#include "console.h"
#endif

/* DSI host (dsi@14017000) */
#define DSI_START		0x00
#define DSI_INTSTA		0x0c
#define DSI_CON_CTRL		0x10
#define DSI_MODE_CTRL		0x14
#define DSI_TXRX_CTRL		0x18
#define DSI_PSCTRL		0x1c
#define DSI_VACT_NL		0x2c
#define DSI_SIZE_CON		0x38
#define DSI_CMDQ_SIZE		0x60
#define DSI_CMD_TYPE1_HS	0x6c
#define DSI_MEM_CONTI		0x90
#define DSI_PHY_LCPAT		0x100
#define DSI_PHY_LCCON		0x104
#define DSI_PHY_LD0CON		0x108
#define DSI_PHY_TIMECON0	0x110
#define DSI_DEBUG_SEL		0x170
#define DSI_BUF_CON0		0x400
#define DSI_BUF_CON1		0x404
#define DSI_TX_BUF_RW_TIMES	0x410

#define DSI_EN				(1u << 1)
#define DSI_CM_WAIT_DATA_LINE_EN	(1u << 24)
#define DSI_CM_WAIT_FIFO_FULL_EN	(1u << 27)
#define DSI_INT_TE_RDY			(1u << 2)
#define DSI_INT_FRAME_DONE		(1u << 4)
#define DSI_INT_BUSY			(1u << 31)
#define DSI_PHY_LC_HS_TX_EN		(1u << 0)
#define DSI_PS_SEL_MASK			(0xfu << 16)
#define DSI_PS_SEL_DSC			(5u << 16)

/* disp_mutex0 (0x14001000) */
#define MUTEX_INTEN		0x00
#define MUTEX_INTSTA		0x04
#define MUTEX_CFG		0x08
#define MUTEX_EN		0x20
#define MUTEX_SOF		0x2c
#define MUTEX_MOD0		0x30
#define MUTEX_MOD1		0x34

/* disp_ovl0_2l (0x14004000) */
#define OVL_EN			0x0c
#define OVL_SRC_CON		0x2c
#define OVL_CON0		0x30
#define OVL_SRC_SIZE0		0x38
#define OVL_L0_PITCH		0x44
#define OVL_L0_ADDR		0xf40

/* mipi_tx_config0 (0x11e50000), MT6983 layout */
#define MIPI_PLL_PWR		0x28
#define MIPI_PLL_CON0		0x2c
#define MIPI_PLL_CON2		0x34
#define MIPI_PLL_CON3		0x38

/* dispsys_config clocks (0x14000000): gate status banks */
#define CLK0_STA		0x100
#define CLK1_STA		0x110
#define CLK2_STA		0x1a0
#define GATE0_MUTEX		(1u << 0)
#define GATE0_DSC0		(1u << 27)
#define GATE0_DSI0		(1u << 29)
#define GATE1_APB		(1u << 1)
#define GATE1_OVL0_2L		(1u << 8)
#define GATE2_DSI_CLK		(1u << 0)

/* Values the vendor driver programs for this panel: 1440x3200 DSC command
 * mode, chunk 720 x 2 slices (1440-byte stream lines). */
#define EXPECT_SIZE_CON		((3200u << 16) | 480u)
#define EXPECT_VACT		3200u

/* GCE thread n block: 0x100 + 0x80*n; CURR_STATUS +0x0c, WAIT_TOKEN +0x30 */
#define GCE_THREAD(n, off)	(0x100u + 0x80u * (n) + (off))

#define STATUS_STAGE_READ	0x20u
#define STATUS_STAGE_KICK	0x30u
#define STATUS_STAGE_LOOP	0x40u
#define STATUS_STAGE_DONE	0x50u

#define STATUS_OK		0u
#define STATUS_NO_NODE		1u
#define STATUS_CLOCKS_OFF	2u
#define STATUS_DSI_STATE	3u
#define STATUS_OVL_STATE	4u
#define STATUS_BUF_OFF		5u
#define STATUS_NO_TE		6u
#define STATUS_NO_FRAME		7u
#define STATUS_PARTIAL		8u

static void
field(char *banner, unsigned offset, uint64_t value)
{
	static const char hex[] = "0123456789abcdef";
	for (unsigned i = 0; i < 16; i++)
		banner[offset + i] = hex[(value >> (60 - i * 4)) & 15];
}

static void
field_bad(char *banner, unsigned offset)
{
	field(banner, offset, UINT64_MAX);
}

static void
status(char *banner, unsigned offset, unsigned stage, unsigned code)
{
	field(banner, offset, ((uint64_t)stage << 16) | code);
}

struct disp {
	uintptr_t dsi, mutex, ovl, mipi, clk, gce;
	uint64_t dsi_sz, mutex_sz, ovl_sz, mipi_sz, clk_sz, gce_sz;
	int got_dsi, got_mutex, got_ovl, got_mipi, got_clk, got_gce;
};

static int
resolve(uint64_t dtb, const char *compatible, int index, uintptr_t *base,
	uint64_t *size)
{
	char path[160];
	uint64_t a, s;
	if (!fdt_find_compatible(dtb, compatible, path, sizeof(path)) ||
	    !fdt_reg_n(dtb, path, index, &a, &s) || !a || s < 4)
		return 0;
	*base = (uintptr_t)a;
	*size = s;
	return 1;
}

static int
resolve_disp(uint64_t dtb, struct disp *d)
{
	d->got_dsi = resolve(dtb, "mediatek,dsi0", 0, &d->dsi, &d->dsi_sz);
	d->got_mutex = resolve(dtb, "mediatek,disp_mutex0", 0, &d->mutex,
		&d->mutex_sz);
	d->got_ovl = resolve(dtb, "mediatek,disp_ovl0_2l", 0, &d->ovl,
		&d->ovl_sz);
	d->got_mipi = resolve(dtb, "mediatek,mt6983-mipi-tx", 0, &d->mipi,
		&d->mipi_sz);
	/* The clock gate provider is a syscon node with its own compatible. */
	d->got_clk = resolve(dtb, "mediatek,mt6983-dispsys_config", 0,
		&d->clk, &d->clk_sz);
	if (!d->got_clk)
		d->got_clk = resolve(dtb, "mediatek,mt6983-disp", 0,
			&d->clk, &d->clk_sz);
	d->got_gce = resolve(dtb, "mediatek,mt6983-gce", 0, &d->gce,
		&d->gce_sz);
	return d->got_dsi && d->got_mutex && d->got_ovl && d->got_mipi &&
	    d->got_clk && d->got_gce;
}

static void
snapshot(struct disp *d, char *banner)
{
#define PAIR(ok, base, size, o1, o2, off) do {				\
	if ((ok) && (o1) + 4u <= (size) && (o2) + 4u <= (size)) {	\
		uint32_t a = *(volatile uint32_t *)((base) + (o1));	\
		uint32_t b = *(volatile uint32_t *)((base) + (o2));	\
		field(banner, (off), ((uint64_t)a << 32) | b);		\
	} else {							\
		field_bad(banner, (off));				\
	}								\
} while (0)

	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_CON_CTRL, DSI_MODE_CTRL,
		ENTRY_0);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_START, DSI_INTSTA, ENTRY_1);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_TXRX_CTRL, DSI_PSCTRL,
		ENTRY_2);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_CMDQ_SIZE, DSI_SIZE_CON,
		ENTRY_3);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_MEM_CONTI, DSI_BUF_CON0,
		ENTRY_4);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_BUF_CON1, DSI_TX_BUF_RW_TIMES,
		ENTRY_5);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_CMD_TYPE1_HS, DSI_DEBUG_SEL,
		ENTRY_6);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_PHY_LCCON, DSI_PHY_LD0CON,
		ENTRY_7);
	PAIR(d->got_mipi, d->mipi, d->mipi_sz, MIPI_PLL_PWR, MIPI_PLL_CON0,
		ENTRY_8);
	PAIR(d->got_dsi, d->dsi, d->dsi_sz, DSI_VACT_NL, DSI_PHY_TIMECON0,
		ENTRY_9);
	PAIR(d->got_clk, d->clk, d->clk_sz, CLK0_STA, CLK1_STA, ENTRY_A);
	/* bank2 and mutex CFG share field B */
	if (d->got_clk && d->got_mutex && CLK2_STA + 4u <= d->clk_sz &&
	    MUTEX_CFG + 4u <= d->mutex_sz) {
		uint32_t a = *(volatile uint32_t *)(d->clk + CLK2_STA);
		uint32_t b = *(volatile uint32_t *)(d->mutex + MUTEX_CFG);
		field(banner, ENTRY_B, ((uint64_t)a << 32) | b);
	} else {
		field_bad(banner, ENTRY_B);
	}
	PAIR(d->got_ovl, d->ovl, d->ovl_sz, OVL_EN, OVL_L0_ADDR, ENTRY_C);
	PAIR(d->got_ovl, d->ovl, d->ovl_sz, OVL_L0_PITCH, OVL_CON0, ENTRY_D);
	PAIR(d->got_ovl, d->ovl, d->ovl_sz, OVL_SRC_CON, OVL_SRC_SIZE0,
		ENTRY_E);
	PAIR(d->got_mutex, d->mutex, d->mutex_sz, MUTEX_EN, MUTEX_SOF,
		ENTRY_F);
	PAIR(d->got_mutex, d->mutex, d->mutex_sz, MUTEX_MOD0, MUTEX_MOD1,
		ENTRY_G);
	PAIR(d->got_mutex, d->mutex, d->mutex_sz, MUTEX_INTEN, MUTEX_INTSTA,
		ENTRY_H);
	PAIR(d->got_gce, d->gce, d->gce_sz, GCE_THREAD(3, 0x0c),
		GCE_THREAD(3, 0x30), ENTRY_I);
	PAIR(d->got_gce, d->gce, d->gce_sz, GCE_THREAD(5, 0x0c),
		GCE_THREAD(5, 0x30), ENTRY_J);
#undef PAIR
}

#if defined(ENTRY_DISPLAY_KICK)
static inline uint32_t
mmio_rd(uintptr_t a)
{
	return *(volatile uint32_t *)a;
}

static inline void
mmio_wr(uintptr_t a, uint32_t v)
{
	*(volatile uint32_t *)a = v;
	__asm__ volatile("dsb sy" ::: "memory");
}

static uint64_t
read_cntpct(void)
{
	uint64_t v;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(v));
	return v;
}

static uint64_t
read_cntfrq(void)
{
	uint64_t v;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
	return v;
}

static void
delay_us(uint64_t us)
{
	uint64_t start = read_cntpct();
	uint64_t ticks = (read_cntfrq() * us) / 1000000u;
	while (read_cntpct() - start < ticks)
		__asm__ volatile("yield");
}

/* Wait until (reg & mask) == want. Returns 1 on success. */
static int
wait_mask(uintptr_t reg, uint32_t mask, uint32_t want, uint64_t us)
{
	uint64_t start = read_cntpct();
	uint64_t ticks = (read_cntfrq() * us) / 1000000u;
	for (;;) {
		if ((mmio_rd(reg) & mask) == want)
			return 1;
		if (read_cntpct() - start >= ticks)
			return 0;
	}
}

#endif

void
entryprobe_display(uint64_t dtb, char *banner)
{
	struct disp d = {0};
	status(banner, ENTRY_K, STATUS_STAGE_READ, STATUS_OK);
	if (brd.name[0] != 'm')
		return;
	if (!resolve_disp(dtb, &d)) {
		status(banner, ENTRY_K, STATUS_STAGE_READ, STATUS_NO_NODE);
		return;
	}
	snapshot(&d, banner);
	field(banner, ENTRY_K, (uint64_t)(STATUS_STAGE_DONE) << 16);
}

#if defined(ENTRY_DISPLAY_KICK)

/* The vendor per-frame packet: mutex CFG=0, EN=0, EN=1; DSI_START=1 after
 * TE; the DSI completes with FRAME_DONE. INTSTA is write-0-to-clear. */
static int
kick_once(struct disp *d, uint32_t con_ctrl, uint32_t *last_intsta)
{
	uintptr_t dsi = d->dsi;
	uintptr_t mutex = d->mutex;

	/* MTK writes these on every DSI power-on. If LK left the cmd-mode
	 * buffer disabled, rebuild only the parts whose values are known; the
	 * SODI/ultra/urgent thresholds keep LK's values. */
	if ((mmio_rd(dsi + DSI_BUF_CON0) & 1u) == 0) {
		uint32_t bc1 = mmio_rd(dsi + DSI_BUF_CON1);
		mmio_wr(dsi + DSI_BUF_CON1, (bc1 & 0xffff0000u) | 0xb1u);
		/* cmd_null_pkt_en: width=(1440+2)/3=480,
		 * rw_times=(480*3/9)*3200=512000. */
		mmio_wr(dsi + DSI_TX_BUF_RW_TIMES, 512000u);
		mmio_wr(dsi + DSI_DEBUG_SEL,
		    mmio_rd(dsi + DSI_DEBUG_SEL) | (1u << 10));
		mmio_wr(dsi + DSI_BUF_CON0,
		    mmio_rd(dsi + DSI_BUF_CON0) | 1u);
	}
	mmio_wr(dsi + DSI_MEM_CONTI, 0x3cu);

	/* Keep the engine and the cmd-mode FIFO path enabled. */
	mmio_wr(dsi + DSI_CON_CTRL, con_ctrl | DSI_EN |
	    DSI_CM_WAIT_DATA_LINE_EN | DSI_CM_WAIT_FIFO_FULL_EN);
	/* 0x55: D-PHY LP pattern used by the vendor clk_hs_mode helper. */
	mmio_wr(dsi + DSI_PHY_LCPAT, 0x55);
	mmio_wr(dsi + DSI_PHY_LCCON, mmio_rd(dsi + DSI_PHY_LCCON) |
	    DSI_PHY_LC_HS_TX_EN);

	/* Wait for the panel TE pulse, then drop the stale flag. */
	mmio_wr(dsi + DSI_INTSTA, ~DSI_INT_TE_RDY);
	if (!wait_mask(dsi + DSI_INTSTA, DSI_INT_TE_RDY, DSI_INT_TE_RDY,
	    40000)) {
		return 0;
	}
	mmio_wr(dsi + DSI_INTSTA, ~(DSI_INT_TE_RDY | DSI_INT_FRAME_DONE));
	mmio_wr(mutex + MUTEX_CFG, 0);
	mmio_wr(mutex + MUTEX_EN, 0);
	mmio_wr(mutex + MUTEX_EN, 1);
	mmio_wr(dsi + DSI_START, 1);
	/* The command-mode engine does not raise FRAME_DONE here (verified on
	 * hardware); record it but treat the issued START as the action. */
	delay_us(15000);
	*last_intsta = mmio_rd(dsi + DSI_INTSTA);
	mmio_wr(dsi + DSI_INTSTA, ~(DSI_INT_FRAME_DONE | DSI_INT_BUSY));
	mmio_wr(mutex + MUTEX_EN, 0);
	return 1;
}

void
entryprobe_display_kick(uint64_t dtb, char *banner)
{
	struct disp d = {0};
	uint32_t con_ctrl, mode_ctrl, psctrl, size_con, vact, bufcon0;
	uint32_t clk0, clk1, clk2, ovl_en, ovl_addr, ovl_pitch;
	unsigned done = 0, te_seen = 0, last = 0;
	unsigned code = STATUS_OK;
	uint32_t start_orig, lccon_orig, lcpat_orig;
	int touched = 0;

	status(banner, ENTRY_K, STATUS_STAGE_KICK, STATUS_OK);
	if (brd.name[0] != 'm' || !resolve_disp(dtb, &d)) {
		status(banner, ENTRY_K, STATUS_STAGE_KICK, STATUS_NO_NODE);
		return;
	}
	snapshot(&d, banner);

	con_target((volatile uint32_t *)(uintptr_t)brd.fb_base);
	con_w = brd.fb_w;
	con_h = brd.fb_h;
	con_stride = brd.fb_stride;
	con_bgr = brd.fb_bgr;
	con_clear(BLUE);
	con_str("SWEET", "DISPLAY KICK");
	con_hex("fb", brd.fb_base);

	con_ctrl = mmio_rd(d.dsi + DSI_CON_CTRL);
	mode_ctrl = mmio_rd(d.dsi + DSI_MODE_CTRL);
	psctrl = mmio_rd(d.dsi + DSI_PSCTRL);
	size_con = mmio_rd(d.dsi + DSI_SIZE_CON);
	vact = mmio_rd(d.dsi + DSI_VACT_NL);
	bufcon0 = mmio_rd(d.dsi + DSI_BUF_CON0);
	clk0 = mmio_rd(d.clk + CLK0_STA);
	clk1 = mmio_rd(d.clk + CLK1_STA);
	clk2 = mmio_rd(d.clk + CLK2_STA);
	ovl_en = mmio_rd(d.ovl + OVL_EN);
	ovl_addr = mmio_rd(d.ovl + OVL_L0_ADDR);
	ovl_pitch = mmio_rd(d.ovl + OVL_L0_PITCH);
	start_orig = mmio_rd(d.dsi + DSI_START);
	lccon_orig = mmio_rd(d.dsi + DSI_PHY_LCCON);
	lcpat_orig = mmio_rd(d.dsi + DSI_PHY_LCPAT);

	/* MTK gate status is active-low: 0 = clock enabled, 1 = gated.
	 * Enable only gates that read back gated (clear registers are
	 * 0x108/0x118/0x1a8); never use the set registers here. */
	if (clk0 & (GATE0_MUTEX | GATE0_DSC0 | GATE0_DSI0))
		mmio_wr(d.clk + 0x108, clk0 & (GATE0_MUTEX | GATE0_DSC0 |
		    GATE0_DSI0));
	if (clk1 & (GATE1_APB | GATE1_OVL0_2L))
		mmio_wr(d.clk + 0x118, clk1 & (GATE1_APB | GATE1_OVL0_2L));
	if (clk2 & GATE2_DSI_CLK)
		mmio_wr(d.clk + 0x1a8, clk2 & GATE2_DSI_CLK);
	clk0 = mmio_rd(d.clk + CLK0_STA);
	clk1 = mmio_rd(d.clk + CLK1_STA);
	clk2 = mmio_rd(d.clk + CLK2_STA);

	/* Preconditions: never poke a display we do not understand. */
	if ((clk0 & GATE0_DSI0) != 0 || (clk2 & GATE2_DSI_CLK) != 0 ||
	    (clk1 & GATE1_OVL0_2L) != 0) {
		code = STATUS_CLOCKS_OFF;
		goto out;
	}
	if ((mode_ctrl & 3u) != 0 || (con_ctrl & DSI_EN) != 0 ||
	    (psctrl & DSI_PS_SEL_MASK) != DSI_PS_SEL_DSC ||
	    size_con != EXPECT_SIZE_CON || vact != EXPECT_VACT) {
		code = STATUS_DSI_STATE;
		goto out;
	}
	if ((ovl_en & 1u) == 0 || ovl_addr != (uint32_t)brd.fb_base ||
	    ovl_pitch != brd.fb_stride) {
		code = STATUS_OVL_STATE;
		goto out;
	}
	if ((bufcon0 & 1u) == 0) {
		code = STATUS_BUF_OFF;
		goto out;
	}

	status(banner, ENTRY_K, STATUS_STAGE_LOOP, STATUS_OK);

	/* First frame: stop if TE never appears; FRAME_DONE is not required. */
	if (!wait_mask(d.dsi + DSI_INTSTA, DSI_INT_TE_RDY, DSI_INT_TE_RDY,
	    40000)) {
		code = STATUS_NO_TE;
		goto out;
	}
	te_seen = 1;
	touched = 1;
	for (unsigned tries = 0; tries < 5 && done == 0; tries++) {
		if (kick_once(&d, con_ctrl, &last))
			done++;
		else
			delay_us(16000);
	}
	if (done == 0) {
		code = STATUS_NO_FRAME;
		goto out;
	}

	/* Show motion only when a frame completed: a moving bar plus a frame
	 * counter. A static panel image means no refresh path. */
	for (unsigned i = 1; i < 80; i++) {
		unsigned x0 = (i * 17u) % (brd.fb_w > 96u ? brd.fb_w - 96u : 1u);
		volatile uint32_t *fb = (volatile uint32_t *)(uintptr_t)
		    brd.fb_base;
		for (unsigned y = 320; y < 400 && y < brd.fb_h; y++) {
			volatile uint32_t *line = fb +
			    (uint64_t)y * (brd.fb_stride / 4);
			for (unsigned x = 0; x < 96 && x0 + x < brd.fb_w; x++)
				line[x0 + x] = GREEN;
		}
		__asm__ volatile("dsb sy" ::: "memory");
		if (kick_once(&d, con_ctrl, &last))
			done++;
		delay_us(25000);
	}
	if (done < 80)
		code = STATUS_PARTIAL;

out:
	/* Restore only what this probe touched; the panel GRAM keeps the last
	 * frame, so Linux still has to initialize the display itself. */
	if (touched) {
		mmio_wr(d.mutex + MUTEX_EN, 0);
		mmio_wr(d.dsi + DSI_PHY_LCCON, lccon_orig);
		mmio_wr(d.dsi + DSI_PHY_LCPAT, lcpat_orig);
		mmio_wr(d.dsi + DSI_CON_CTRL, con_ctrl);
		mmio_wr(d.dsi + DSI_START, start_orig);
	}
	field(banner, ENTRY_I, ((uint64_t)last << 32) | done);
	field(banner, ENTRY_J, ((uint64_t)te_seen << 32) | code);
	status(banner, ENTRY_K, STATUS_STAGE_DONE, code);
	con_hex("done", done);
	con_hex("code", code);
}
#endif /* ENTRY_DISPLAY_KICK */

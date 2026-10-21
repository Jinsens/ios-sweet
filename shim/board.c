/*
 * Board discovery from the live device tree.
 *
 * Two bootloaders, one contract:
 *
 *   sweet (Qualcomm ABL, SM7150)
 *     - no videolfb ATAG; the splash framebuffer sits in the
 *       reserved-memory node "cont_splash_region" at 0x9c000000
 *     - GICD 0x17a00000, GICR 0x17a60000 (one 0x20000 frame per core)
 *     - virtual timer is PPI 3 (INTID 19), not the architectural 27
 *     - UFS host at 0x1d84000
 *
 *   matisse (MediaTek LK, MT6983)
 *     - /chosen/atag,videolfb-fb_base_l/_h and -vramSize carry the frame
 *       buffer; the panel is 1440x3200 RGBA8888 (the live DRM connector
 *       reports 1440x3200, and vramSize is three such buffers)
 *     - GICv3 in /interrupt-controller: distributor then redistributor
 *     - virtual timer is PPI 11 (INTID 27), the architectural value
 *     - UFS host at 0x112b0000 (mediatek,mt8183-ufshci)
 *
 * Everything else (initrd, /memory, PSCI) is identical.
 */
#include <stdint.h>
#include "board.h"
#include "fdt.h"
#include "console.h"

struct board brd;

static int has_str(const char *list, uint32_t len, const char *want)
{
	uintptr_t p = (uintptr_t)list, end = p + len;

	while (p < end) {
		const char *s = (const char *)p;
		uintptr_t q = p;
		while (q < end && *(const char *)q)
			q++;
		if (q - p == 0)
			break;
		if (__builtin_strcmp(s, want) == 0)
			return 1;
		p = q + 1;
	}
	return 0;
}

static int compatible_has(uintptr_t dtb, const char *want)
{
	const void *v;
	uint32_t len;

	if (!fdt_prop(dtb, "/", "compatible", &v, &len))
		return 0;
	return has_str(v, len, want);
}

static void sweet_defaults(void)
{
	brd.name = "sweet";
	brd.fb_base = 0x9c000000UL;
	brd.fb_size = 0x1700000UL;
	brd.fb_w = 1080;
	brd.fb_h = 2400;
	brd.fb_stride = 1080 * 4;
	brd.fb_bgr = 0;
	brd.fb_count = 1;
	brd.gicd = 0x17a00000UL;
	brd.gicr = 0x17a60000UL;
	brd.gicr_size = 0x100000UL;
	brd.gicr_stride = 0x20000UL;
	brd.vtimer_intid = 19;
	brd.ptimer_intid = 18;
	brd.sgi_intid = 1;
	brd.ufs_base = 0x01d84000UL;
	brd.dram_base = 0x100000000ULL;
	brd.dram_size = 0xfcc00000UL;
	brd.kernel_base = 0x100000000ULL;
	brd.kernel_size = 0xfcc00000UL;
	brd.log_part = 0;
	brd.log_off_blocks = 0;
	brd.log_blocks = 0;
}

static void matisse_defaults(void)
{
	brd.name = "matisse";
	brd.fb_base = 0xfcb3f000UL;
	brd.fb_size = 0x34c0000UL;
	brd.fb_w = 1440;
	brd.fb_h = 3200;
	brd.fb_stride = 1440 * 4;
	brd.fb_bgr = 0;
	brd.fb_count = 3;
	brd.gicd = 0x0c400000UL;
	brd.gicr = 0x0c440000UL;
	brd.gicr_size = 0x200000UL;
	brd.gicr_stride = 0x20000UL;
	brd.vtimer_intid = 27;
	brd.ptimer_intid = 30;
	brd.sgi_intid = 1;
	brd.ufs_base = 0x112b0000UL;
	brd.dram_base = 0x40000000UL;
	brd.dram_size = 0x200000000ULL;
	/* Above every MediaTek carve-out, below the 0x22e000000 block */
	brd.kernel_base = 0x100000000ULL;
	brd.kernel_size = 0x12e000000ULL;
	/* Raw partition logging is destructive and requires explicit opt-in. */
#ifdef SHIM_STORAGE_LOG
	brd.log_part = "oops";
#else
	brd.log_part = 0;
#endif
	brd.log_off_blocks = 0;
	brd.log_blocks = 8;
}

/* Pull the framebuffer, GIC and UFS description out of the live tree. */
static void refine_from_dtb(uintptr_t dtb)
{
	uint64_t a, s;
	const void *v;
	uint32_t len;

	/* MediaTek LK: framebuffer base and size in /chosen ATAGs */
	if (fdt_prop(dtb, "/chosen", "atag,videolfb-fb_base_l", &v, &len) && len >= 4) {
		uint64_t base = fdt_be32((uintptr_t)v);
		if (fdt_prop(dtb, "/chosen", "atag,videolfb-fb_base_h", &v, &len) && len >= 4)
			base |= (uint64_t)fdt_be32((uintptr_t)v) << 32;
		brd.fb_base = base;
	}
	if (fdt_prop(dtb, "/chosen", "atag,videolfb-vramSize", &v, &len) && len >= 4)
		brd.fb_size = fdt_be32((uintptr_t)v);

	/* GICv3: distributor then redistributor (both cell layouts handled) */
	if (fdt_reg_n(dtb, "/interrupt-controller", 0, &a, &s)) {
		brd.gicd = a;
		if (fdt_reg_n(dtb, "/interrupt-controller", 1, &a, &s)) {
			brd.gicr = a;
			brd.gicr_size = s;
		}
	} else {
		char path[192];
		if (fdt_find_compatible(dtb, "arm,gic-v3", path, sizeof(path)) &&
		    fdt_reg_n(dtb, path, 0, &a, &s)) {
			brd.gicd = a;
			if (fdt_reg_n(dtb, path, 1, &a, &s)) {
				brd.gicr = a;
				brd.gicr_size = s;
			}
		}
	}

	/* UFS host */
	{
		char path[192];
		if (fdt_find_compatible(dtb, "mediatek,mt8183-ufshci", path, sizeof(path)) &&
		    fdt_reg(dtb, path, &a, &s))
			brd.ufs_base = a;
		else if (fdt_find_compatible(dtb, "qcom,ufshc", path, sizeof(path)) &&
			 fdt_reg(dtb, path, &a, &s))
			brd.ufs_base = a;
	}

	/* DRAM log buffers: <addr size ...> in /chosen */
	if (fdt_prop(dtb, "/chosen", "ram_console", &v, &len) && len >= 8) {
		brd.ram_console = fdt_be32((uintptr_t)v);
		brd.ram_console_size = fdt_be32((uintptr_t)v + 4);
	}
	if (fdt_prop(dtb, "/chosen", "log_store", &v, &len) && len >= 8) {
		brd.log_store = fdt_be32((uintptr_t)v);
		brd.log_store_size = fdt_be32((uintptr_t)v + 4);
	}

	/* RAM */
	if (fdt_reg(dtb, "/memory", &a, &s)) {
		brd.dram_base = a;
		brd.dram_size = s;
	}

	/* Timer PPIs: the arm,armv8-timer order is secure, non-secure, virtual, hyp */
	{
		char path[192];
		if (fdt_find_compatible(dtb, "arm,armv8-timer", path, sizeof(path))) {
			if (fdt_prop(dtb, path, "interrupts", &v, &len) && len >= 48) {
				uint32_t virt = fdt_be32((uintptr_t)v + 32) & 0xffff;
				uint32_t phys = fdt_be32((uintptr_t)v + 16) & 0xffff;
				brd.vtimer_intid = 16 + virt;
				brd.ptimer_intid = 16 + phys;
			}
		}
	}
}

int board_probe(uintptr_t dtb)
{
	if (!dtb || fdt_be32(dtb) != 0xd00dfeed)
		return 0;

	if (compatible_has(dtb, "mediatek,MT6983") || compatible_has(dtb, "mediatek,mt6983"))
		matisse_defaults();
	else
		sweet_defaults();

	refine_from_dtb(dtb);

	/* How many whole frames fit in the reservation? LK allocates three
	 * (the DSI driver cycles through them), so the one being scanned out
	 * when we start is not necessarily the first. */
	{
		uint64_t frame = (uint64_t)brd.fb_stride * brd.fb_h;
		int n = frame ? (int)(brd.fb_size / frame) : 0;
		brd.fb_count = n < 1 ? 1 : n > 3 ? 3 : n;
	}

	/* Refuse a framebuffer that cannot be one: the console draws into it
	 * before anything else runs, and a bogus base would scribble on RAM. */
	uint64_t need = (uint64_t)brd.fb_w * brd.fb_h * 4;
	if (brd.fb_base < 0x40000000ULL || brd.fb_base >= 0x100000000ULL ||
	    brd.fb_size < need || brd.fb_stride < brd.fb_w * 4) {
		brd.fb_base = 0;
		brd.fb_size = 0;
	}
	return 1;
}

const char *board_name(void)
{
	return brd.name;
}

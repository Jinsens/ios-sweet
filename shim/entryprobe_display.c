/* Read-only LK display snapshot. No display, clock or PHY writes.
 * Register definitions: MiCode Xiaomi_Kernel_OpenSource/rubens-s-oss,
 * drivers/gpu/drm/mediatek/mediatek_v2/{mtk_dsi,mtk_disp_ovl}.c.
 * MMIO bases come exclusively from the live FDT, and only on matisse.
 * Layout in mkentryprobe.py DISPLAY_FIELDS; fault vectors return to Linux.
 */
#include <stdint.h>
#include "entryprobe_config.h"
#include "board.h"
#include "fdt.h"

static void field(char *banner, unsigned offset, uint64_t value)
{
	static const char hex[] = "0123456789abcdef";
	for (unsigned i = 0; i < 16; i++)
		banner[offset + i] = hex[(value >> (60 - i * 4)) & 15];
}

static void pair(uint64_t dtb, char *banner, unsigned field_offset,
	const char *compatible, unsigned off1, unsigned off2)
{
	char path[160];
	uint64_t base, size;
	if (!fdt_find_compatible(dtb, compatible, path, sizeof(path)) ||
	    !fdt_reg_n(dtb, path, 0, &base, &size) || !base ||
	    off1 + 4 > size || off2 + 4 > size) {
		field(banner, field_offset, UINT64_MAX);
		return;
	}
	uint32_t a = *(volatile uint32_t *)(uintptr_t)(base + off1);
	uint32_t b = *(volatile uint32_t *)(uintptr_t)(base + off2);
	field(banner, field_offset, ((uint64_t)a << 32) | b);
}

void entryprobe_display(uint64_t dtb, char *banner)
{
	if (brd.name[0] != 'm') {
		field(banner, ENTRY_STAGE, 0xfd);
		return;
	}
	field(banner, ENTRY_STAGE, 10);
	pair(dtb, banner, ENTRY_EL, "mediatek,dsi0", 0x10, 0x14);
	pair(dtb, banner, ENTRY_MIDR, "mediatek,dsi0", 0, 0x0c);
	pair(dtb, banner, ENTRY_MPIDR, "mediatek,dsi0", 0x18, 0x1c);
	pair(dtb, banner, ENTRY_FREQ, "mediatek,dsi0", 0x60, 0x38);
	field(banner, ENTRY_STAGE, 11);
	pair(dtb, banner, ENTRY_DTB, "mediatek,dsi1", 0x10, 0x14);
	pair(dtb, banner, ENTRY_SCTLR, "mediatek,dsi1", 0, 0x0c);
	field(banner, ENTRY_STAGE, 12);
	pair(dtb, banner, ENTRY_ESR, "mediatek,disp_ovl0", 0x0c, 0x2c);
	pair(dtb, banner, ENTRY_FAR, "mediatek,disp_ovl0", 0xf40, 0x44);
	field(banner, ENTRY_STAGE, 13);
	pair(dtb, banner, ENTRY_PC, "mediatek,disp_ovl0_2l", 0x0c, 0x2c);
	pair(dtb, banner, ENTRY_GICD, "mediatek,disp_ovl0_2l", 0xf40, 0x44);
	field(banner, ENTRY_STAGE, 14);
	pair(dtb, banner, ENTRY_VTIMER, "mediatek,disp_mutex0", 0x20, 0x2c);
	pair(dtb, banner, ENTRY_FB, "mediatek,disp_mutex0", 0x30, 0x34);
	field(banner, ENTRY_STAGE, 15);
}

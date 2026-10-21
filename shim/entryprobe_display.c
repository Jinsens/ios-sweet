/* Read-only LK display snapshot. No display, clock or PHY writes.
 * Register definitions: MiCode Xiaomi_Kernel_OpenSource/rubens-s-oss,
 * drivers/gpu/drm/mediatek/mediatek_v2/{mtk_dsi,mtk_disp_ovl}.c,
 * drivers/gpu/drm/mediatek/mediatek_v2/mtk_disp_mutex.c, and
 * drivers/mailbox/mtk-cmdq-mailbox.c. Thread ids come from the live MT6983 DTS.
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

/* Two-bit status values: 0=compatible missing, 1=reg/range invalid,
 * 2=both reads completed (including a legitimate 0xffffffff value).
 */
static unsigned pair(uint64_t dtb, char *banner, unsigned field_offset,
	const char *compatible, int reg_index, unsigned off1, unsigned off2)
{
	char path[160];
	uint64_t base, size;
	if (!fdt_find_compatible(dtb, compatible, path, sizeof(path))) {
		field(banner, field_offset, UINT64_MAX);
		return 0;
	}
	if (!fdt_reg_n(dtb, path, reg_index, &base, &size) || !base ||
	    (base & 3) || size < 4 || off1 > size - 4 || off2 > size - 4 ||
	    base > UINT64_MAX - off1 || base > UINT64_MAX - off2) {
		field(banner, field_offset, UINT64_MAX);
		return 1;
	}
	uint32_t a = *(volatile uint32_t *)(uintptr_t)(base + off1);
	uint32_t b = *(volatile uint32_t *)(uintptr_t)(base + off2);
	field(banner, field_offset, ((uint64_t)a << 32) | b);
	return 2;
}

static void capture_pair(uint64_t dtb, char *banner, unsigned field_offset,
	unsigned slot, uint64_t *status, const char *compatible, int reg_index,
	unsigned off1, unsigned off2)
{
	unsigned result = pair(dtb, banner, field_offset, compatible, reg_index,
		off1, off2);
	*status |= (uint64_t)result << (slot * 2);
	field(banner, ENTRY_DISPLAY_STATUS, *status);
}

void entryprobe_display(uint64_t dtb, char *banner)
{
	if (brd.name[0] != 'm') {
		field(banner, ENTRY_STAGE, 0xfd);
		return;
	}
	uint64_t status = 0;
	field(banner, ENTRY_DISPLAY_STATUS, status);
	field(banner, ENTRY_STAGE, 10);
	capture_pair(dtb, banner, ENTRY_EL, 0, &status,
		"mediatek,dsi0", 0, 0x10, 0x14);
	capture_pair(dtb, banner, ENTRY_MIDR, 1, &status,
		"mediatek,dsi0", 0, 0, 0x0c);
	capture_pair(dtb, banner, ENTRY_MPIDR, 2, &status,
		"mediatek,dsi0", 0, 0x18, 0x1c);
	capture_pair(dtb, banner, ENTRY_FREQ, 3, &status,
		"mediatek,dsi0", 0, 0x60, 0x38);
	field(banner, ENTRY_STAGE, 11);
	/* MT6983 DISP CLIENT_TRIG_LOOP0/1 are GCE threads 3 and 5.
	 * Each thread block is 0x80 bytes from GCE+0x100; these are read-only
	 * CURR_STATUS and WAIT_TOKEN snapshots, never writes/doorbells.
	 */
	capture_pair(dtb, banner, ENTRY_GCE3, 4, &status,
		"mediatek,mt6983-gce", 0, 0x28c, 0x2b0);
	capture_pair(dtb, banner, ENTRY_GCE5, 5, &status,
		"mediatek,mt6983-gce", 0, 0x38c, 0x3b0);
	field(banner, ENTRY_STAGE, 12);
	capture_pair(dtb, banner, ENTRY_ESR, 6, &status,
		"mediatek,disp_ovl0", 0, 0x0c, 0x2c);
	capture_pair(dtb, banner, ENTRY_FAR, 7, &status,
		"mediatek,disp_ovl0", 0, 0xf40, 0x44);
	field(banner, ENTRY_STAGE, 13);
	capture_pair(dtb, banner, ENTRY_PC, 8, &status,
		"mediatek,disp_ovl0_2l", 0, 0x0c, 0x2c);
	capture_pair(dtb, banner, ENTRY_GICD, 9, &status,
		"mediatek,disp_ovl0_2l", 0, 0xf40, 0x44);
	field(banner, ENTRY_STAGE, 14);
	capture_pair(dtb, banner, ENTRY_VTIMER, 10, &status,
		"mediatek,disp_mutex0", 0, 0x20, 0x2c);
	capture_pair(dtb, banner, ENTRY_FB, 11, &status,
		"mediatek,disp_mutex0", 0, 0x30, 0x34);
	capture_pair(dtb, banner, ENTRY_MUTEX1, 12, &status,
		"mediatek,disp_mutex0", 1, 0x20, 0x2c);
	capture_pair(dtb, banner, ENTRY_MUTEX1_MOD, 13, &status,
		"mediatek,disp_mutex0", 1, 0x30, 0x34);
	field(banner, ENTRY_STAGE, 15);
}

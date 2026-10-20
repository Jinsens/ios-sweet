#pragma once
#include <stdint.h>

/*
 * Everything the shim needs to know about the board, discovered from the
 * live device tree the bootloader hands us. No SoC constants in the rest of
 * the code: both Qualcomm ABL (sweet) and MediaTek LK (matisse) leave the
 * same /chosen contract and describe their GIC/UFS/memory in the tree.
 */
struct board {
	const char *name;	/* "sweet" or "matisse" */

	/* Framebuffer the bootloader left scanning out */
	uint64_t fb_base, fb_size;
	uint32_t fb_w, fb_h, fb_stride;
	int fb_bgr;		/* 1: memory order B,G,R,A (a8r8g8b8) */
	int fb_count;		/* scanout buffers inside fb_size (1..3) */

	/* GICv3 */
	uint64_t gicd, gicr, gicr_size, gicr_stride;
	uint32_t vtimer_intid, ptimer_intid, sgi_intid;

	/* Storage: UFS host controller */
	uint64_t ufs_base;

	/* RAM the bootloader told us about */
	uint64_t dram_base, dram_size;

	/* Where the payload goes (kernel-managed RAM handed to XNU) */
	uint64_t kernel_base, kernel_size;

	/* DRAM log channels the bootloader leaves in /chosen. LK dumps them
	 * with `fastboot oem lpmsg` (ram_console) and `oem lkmsg` (log_store),
	 * so a boot that never reaches the panel is still readable. */
	uint64_t ram_console, ram_console_size;
	uint64_t log_store, log_store_size;

	/* Console log: raw blocks inside a partition, past the boot image */
	const char *log_part;
	uint32_t log_off_blocks;
	uint32_t log_blocks;
};

extern struct board brd;

/* Fill `brd` from the live FDT. Returns 0 if the tree is unusable. */
int board_probe(uintptr_t dtb);

/* Human-readable board name for the console. */
const char *board_name(void);

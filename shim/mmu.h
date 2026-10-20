#pragma once
#include <stdint.h>

struct mmu_info {
	uint64_t img_lo, img_hi, ttbr;
	uint64_t sctlr_before, sctlr_after;
	int n_l3;
};

/* fb_phys: block that gets Normal non-cacheable attributes (write-combining) */
int mmu_enable(struct mmu_info *info, uint64_t fb_phys);
uint64_t mmu_probe(uintptr_t va);

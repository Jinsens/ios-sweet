/* Read-only GIC discovery + masked virtual timer/PPI pending probe.
 * No GIC distributor/redistributor configuration is changed. DAIF stays
 * masked. The original timer control/compare value is restored for Linux.
 */
#include <stdint.h>
#include "board.h"

#define rsr(r) ({ uint64_t v; __asm__ volatile("mrs %0, " r : "=r"(v)); v; })
#define wsr(r, v) __asm__ volatile("msr " r ", %0" : : "r"((uint64_t)(v)) : "memory")
#define isb() __asm__ volatile("isb" ::: "memory")

static uint32_t rd32(uint64_t a) { return *(volatile uint32_t *)(uintptr_t)a; }

/* dctlr, redistributor PA, timer/PPI observation flags */
void entryprobe_timer(uint64_t *dctlr, uint64_t *redist, uint64_t *flags)
{
	uint64_t mpidr = rsr("mpidr_el1");
	uint32_t want = (mpidr & 0xffffff) | ((mpidr >> 8) & 0xff000000);
	uint64_t r = 0;
	*dctlr = rd32(brd.gicd);
	*redist = 0;
	*flags = 0;
	for (uint64_t off = 0; off + 0x20000 <= brd.gicr_size; off += 0x20000) {
		/* Keep the 64-bit register as aligned word reads for Device memory. */
		uint32_t lo = rd32(brd.gicr + off + 8);
		uint32_t hi = rd32(brd.gicr + off + 12);
		if (hi == want) {
			r = brd.gicr + off;
			break;
		}
		if (lo & (1U << 4))
			break;
		if (lo & (1U << 1))
			off += 0x20000; /* GICv4 redistributor includes VLPI frames */
	}
	*redist = r;
	if (!r || brd.vtimer_intid >= 32)
		return;
	uint32_t bit = 1U << brd.vtimer_intid;
	uint32_t before = rd32(r + 0x10200);
	uint64_t old_ctl = rsr("cntv_ctl_el0");
	uint64_t old_cval = rsr("cntv_cval_el0");
	uint64_t freq = rsr("cntfrq_el0");
	uint64_t start = rsr("cntvct_el0");
	wsr("cntv_ctl_el0", 0);
	wsr("cntv_cval_el0", start + freq / 10000); /* 100 microseconds */
	wsr("cntv_ctl_el0", 1); /* timer output unmasked, CPU interrupts masked */
	isb();
	uint64_t ctl = rsr("cntv_ctl_el0");
	while (!(ctl & 4) && rsr("cntvct_el0") - start < freq / 100) {
		isb();
		ctl = rsr("cntv_ctl_el0");
	}
	isb();
	uint32_t after = rd32(r + 0x10200);
	uint64_t elapsed = rsr("cntvct_el0") - start;
	wsr("cntv_ctl_el0", 0);
	wsr("cntv_cval_el0", old_cval);
	wsr("cntv_ctl_el0", old_ctl);
	isb();
	*flags = ((uint64_t)(rd32(r + 0x14)) << 32) |
		 ((elapsed & 0xffffff) << 8) | 1 | /* bit0: counter advanced/read */
		 ((ctl & 4) ? 2 : 0) |           /* bit1: timer ISTATUS asserted */
		 ((before & bit) ? 4 : 0) |      /* bit2: PPI pending before */
		 ((after & bit) ? 8 : 0);        /* bit3: PPI pending after */
}

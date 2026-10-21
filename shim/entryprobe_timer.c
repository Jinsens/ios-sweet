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

volatile uint64_t entryprobe_irq_count;
volatile uint64_t entryprobe_irq_last;

static void wr32(uint64_t a, uint32_t v)
{
	*(volatile uint32_t *)(uintptr_t)a = v;
	__asm__ volatile("dsb sy" ::: "memory");
}

static int wait_distributor(void)
{
	uint64_t start = rsr("cntpct_el0"), freq = rsr("cntfrq_el0");
	while (rd32(brd.gicd) & (1U << 31))
		if (rsr("cntpct_el0") - start > freq / 10)
			return 0;
	return 1;
}

/* Actual IRQ-vector/EOI test. Unlike entryprobe_timer this temporarily
 * configures GIC state, so save and restore every register we modify. */
void entryprobe_irq(uint64_t redist, uint64_t *count, uint64_t *last, uint64_t *rc)
{
	*count = *last = 0;
	*rc = 1;
	if (!redist || brd.vtimer_intid != 27 || (rd32(redist + 0x14) & 6))
		return;
	uint64_t sgi = redist + 0x10000;
	uint32_t bit = 1U << 27;
	uint32_t dctlr = rd32(brd.gicd);
	uint32_t group = rd32(sgi + 0x80), enabled = rd32(sgi + 0x100);
	uint32_t pending = rd32(sgi + 0x200);
	volatile uint8_t *priority = (volatile uint8_t *)(uintptr_t)(sgi + 0x400 + 27);
	uint8_t old_priority = *priority;
	uint64_t old_sre = rsr("icc_sre_el1");
	wsr("icc_sre_el1", old_sre | 1);
	isb();
	uint64_t old_ctlr = rsr("icc_ctlr_el1"), old_pmr = rsr("icc_pmr_el1");
	uint64_t old_bpr = rsr("icc_bpr1_el1"), old_grp = rsr("icc_igrpen1_el1");
	uint64_t old_tctl = rsr("cntv_ctl_el0"), old_cval = rsr("cntv_cval_el0");
	wsr("icc_igrpen1_el1", 0);
	wsr("cntv_ctl_el0", 0);
	isb();
	wr32(sgi + 0x180, 0xffffffff); /* disable local SGI/PPI, preserve SPI state */
	wr32(sgi + 0x80, group | bit);
	*priority = 0xa0;
	wr32(sgi + 0x100, bit);
	wr32(brd.gicd, dctlr | 0x13);
	int distributor_ready = wait_distributor();
	entryprobe_irq_count = entryprobe_irq_last = 0;
	wsr("icc_ctlr_el1", old_ctlr & ~2ULL); /* EOImode0: EOI deactivates */
	wsr("icc_pmr_el1", 0xb0);
	wsr("icc_bpr1_el1", 0);
	wsr("icc_igrpen1_el1", 1);
	uint64_t freq = rsr("cntfrq_el0"), start = rsr("cntpct_el0");
	wsr("cntv_tval_el0", freq / 1000); /* 1ms */
	wsr("cntv_ctl_el0", 1);
	isb();
	if (distributor_ready) {
		__asm__ volatile("msr daifclr, #2; isb" ::: "memory");
		while (!entryprobe_irq_count && rsr("cntpct_el0") - start < freq / 10)
			;
		__asm__ volatile("msr daifset, #2; isb" ::: "memory");
	}
	wsr("cntv_ctl_el0", 0);
	wsr("icc_igrpen1_el1", 0);
	isb();
	*count = entryprobe_irq_count;
	*last = entryprobe_irq_last;
	*rc = !distributor_ready ? 2 : (*count && *last == 27 ? 0 : 3);
	wr32(sgi + 0x180, 0xffffffff);
	wr32(sgi + 0x80, group);
	*priority = old_priority;
	if (!(pending & bit))
		wr32(sgi + 0x280, bit); /* don't leave our test's pending timer behind */
	wr32(sgi + 0x100, enabled);
	wr32(brd.gicd, dctlr);
	(void)wait_distributor();
	wsr("icc_ctlr_el1", old_ctlr);
	wsr("icc_pmr_el1", old_pmr);
	wsr("icc_bpr1_el1", old_bpr);
	wsr("icc_igrpen1_el1", old_grp);
	wsr("cntv_cval_el0", old_cval);
	wsr("cntv_ctl_el0", old_tctl);
	isb();
	wsr("icc_sre_el1", old_sre);
	isb();
}

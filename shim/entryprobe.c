/* Optional stages of entryprobe.S, using the real shim platform reader. */
#include <stdint.h>
#include "entryprobe_config.h"
#include "board.h"
#include "logstore.h"
#include "mmu.h"

static void field(char *banner, unsigned offset, uint64_t value)
{
	static const char hex[] = "0123456789abcdef";
	for (unsigned i = 0; i < 16; i++)
		banner[offset + i] = hex[(value >> (60 - i * 4)) & 15];
}

void entryprobe_display(uint64_t dtb, char *banner);

void entryprobe_timer(uint64_t *dctlr, uint64_t *redist, uint64_t *flags);
void entryprobe_irq(uint64_t redist, uint64_t *count, uint64_t *last, uint64_t *rc);

void entryprobe_board(uint64_t dtb, char *banner)
{
	if (!board_probe((uintptr_t)dtb)) {
		field(banner, ENTRY_STAGE, 0xff);
		return;
	}
	field(banner, ENTRY_GICD, brd.gicd);
	field(banner, ENTRY_VTIMER, brd.vtimer_intid);
	field(banner, ENTRY_FB, brd.fb_base);
	field(banner, ENTRY_STAGE, 2);
#if ENTRY_LEVEL == 6
	entryprobe_display(dtb, banner);
	/* The display snapshot is intentionally read-only and does not progress
	 * into the MMU/timer/IRQ stages used by the other probe levels. */
#else
#if ENTRY_LEVEL == 2
	/* Unlike reboot-based experiments, /proc/pl_lk is read by Linux in
	 * THIS boot, before another preloader/LK run can replace its buffer. */
	logstore_mark("entry-probe", dtb, brd.vtimer_intid);
	field(banner, ENTRY_STAGE, 3);
#endif
#if ENTRY_LEVEL >= 3
	uint64_t el, sctlr, mmfr0;
	__asm__ volatile("mrs %0, CurrentEL" : "=r"(el));
	__asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));
	__asm__ volatile("mrs %0, id_aa64mmfr0_el1" : "=r"(mmfr0));
	field(banner, ENTRY_FAR, mmfr0);
	if (el != 4 || (sctlr & 1) || ((mmfr0 >> 20) & 15) == 0) {
		field(banner, ENTRY_STAGE, 0xfe);
		return;
	}
	struct mmu_info info;
	field(banner, ENTRY_STAGE, 4);
	int rc = mmu_enable(&info, brd.fb_base);
	field(banner, ENTRY_ESR, (uint32_t)rc);
	if (rc)
		return;
	field(banner, ENTRY_STAGE, 5);
	field(banner, ENTRY_FAR, mmu_probe((uintptr_t)entryprobe_board));
	field(banner, ENTRY_PC, info.sctlr_after);
#if ENTRY_LEVEL >= 4
	uint64_t dctlr, redist, flags;
	field(banner, ENTRY_STAGE, 6);
	entryprobe_timer(&dctlr, &redist, &flags);
	field(banner, ENTRY_ESR, dctlr);
	field(banner, ENTRY_FAR, redist);
	field(banner, ENTRY_PC, flags);
	field(banner, ENTRY_STAGE, 7);
#if ENTRY_LEVEL >= 5
	uint64_t irq_count, irq_last, irq_rc;
	field(banner, ENTRY_STAGE, 8);
	entryprobe_irq(redist, &irq_count, &irq_last, &irq_rc);
	field(banner, ENTRY_ESR, irq_rc);
	field(banner, ENTRY_FAR, irq_count);
	field(banner, ENTRY_PC, irq_last);
	field(banner, ENTRY_STAGE, 9);
#endif
#endif
#endif
#endif
}

/* Optional stages of entryprobe.S, using the real shim platform reader. */
#include <stdint.h>
#include "entryprobe_config.h"
#include "board.h"
#include "logstore.h"
#include "mmu.h"
#if defined(ENTRY_EL1_DISPLAY)
#include "console.h"
#include "display.h"
#endif
#if defined(ENTRY_LOADER_PROBE)
#include "loader.h"
#include "diag.h"
static char *loader_banner;

void diag_stage(const char *name, uint64_t a, uint64_t b)
{
	/* Preserve the loader's last completed checkpoint in Linux's banner.
	 * The XNU entry replaces VBAR; after that, this fallback is unavailable. */
	unsigned n = name[0] == 'S' && name[1] >= '0' && name[1] <= '9' ?
	    (unsigned)(name[1] - '0') : 0;
	static const char hex[] = "0123456789abcdef";
	unsigned offsets[] = { ENTRY_STAGE, ENTRY_FAR, ENTRY_PC };
	uint64_t values[] = { 0x80u + n, a, b };
	for (unsigned j = 0; j < 3; j++)
		for (unsigned i = 0; i < 16; i++)
			loader_banner[offsets[j] + i] = hex[(values[j] >> (60 - i * 4)) & 15];
}

void diag_arm_sentinels(uint64_t virt, uint64_t phys, uint64_t size)
{
	(void)virt; (void)phys; (void)size;
#ifdef ENTRY_XNU_VECTOR_PA
	/* Diagnostic only, before MMU enable: keep XNU's VBAR write, but
	 * redirect each early spin vector to the same slot of our recorder.
	 * No stack is needed; XNU's SP may already hold an unmapped KVA. */
	extern const unsigned char probe_vectors[];
	volatile uint32_t *vectors = (volatile uint32_t *)ENTRY_XNU_VECTOR_PA;
	for (unsigned slot = 0; slot < 16; slot++) {
		uint64_t target = (uintptr_t)probe_vectors + slot * 128;
		volatile uint32_t *v = vectors + slot * 32;
		v[0] = 0xd2800010 | ((target & 0xffff) << 5); /* movz x16 */
		v[1] = 0xf2a00010 | (((target >> 16) & 0xffff) << 5);
		v[2] = 0xf2c00010 | (((target >> 32) & 0xffff) << 5);
		v[3] = 0xf2e00010 | (((target >> 48) & 0xffff) << 5);
		v[4] = 0xd61f0200; /* br x16 */
	}
#endif
#if defined(ENTRY_XNU_HIGH_VECTOR_PA) && defined(ENTRY_XNU_SELECTIVE)
	/* Keep every normal XNU vector. Only the EL1h synchronous slot gets a
	 * stub that handles our BRK and forwards all other exceptions to the
	 * original handler. The relay body is copied to slot 12 (AArch32 EL0
	 * sync), which arm64 userspace never uses. */
	extern const uint32_t probe_sel_stub[], probe_sel_stub_end[];
	extern const uint32_t probe_sel_forward[];
	extern const uint32_t probe_mmu_vector[], probe_mmu_identity[], probe_mmu_physical[];
	extern const uint32_t probe_mmu_recorder[], probe_mmu_literals_end[];
	extern const unsigned char probe_vectors[];
	uintptr_t hv = ENTRY_XNU_HIGH_VECTOR_PA;
	uintptr_t slot4 = hv + 4 * 128;
	uint32_t insn = *(volatile uint32_t *)slot4;
	if ((insn & 0xfc000000) == 0x14000000) {
		/* Decode the original B target in virtual-address space and patch
		 * the stub's own B. A direct branch preserves every guest GPR on
		 * the forward path; a br through x16 would corrupt the
		 * interrupted context. */
		int64_t off = ((int64_t)(insn & 0x03ffffff) << 38) >> 36;
		uint64_t target = ENTRY_XNU_HIGH_VECTOR_VA + 4 * 128 + (uint64_t)off;
		uintptr_t fwd_off = (uintptr_t)probe_sel_forward - (uintptr_t)probe_sel_stub;
		uintptr_t fwd = slot4 + fwd_off;
		uint64_t fwd_va = ENTRY_XNU_HIGH_VECTOR_VA + 4 * 128 + fwd_off;
		int64_t delta = (int64_t)target - (int64_t)fwd_va;
		if ((delta & 3) == 0 && delta >= -(1 << 27) && delta < (1 << 27)) {
			unsigned words = ((uintptr_t)probe_sel_stub_end - (uintptr_t)probe_sel_stub) / 4;
			for (unsigned i = 0; i < words; i++)
				((volatile uint32_t *)slot4)[i] = probe_sel_stub[i];
			*(volatile uint32_t *)fwd = 0x14000000u | (((uint32_t)(delta >> 2)) & 0x03ffffffu);
		}
	}
	uintptr_t relay = hv + 12 * 128;
	unsigned rwords = ((uintptr_t)probe_mmu_literals_end - (uintptr_t)probe_mmu_vector) / 4;
	for (unsigned i = 0; i < rwords; i++)
		((volatile uint32_t *)relay)[i] = probe_mmu_vector[i];
	*(volatile uint64_t *)(relay +
	    ((uintptr_t)probe_mmu_physical - (uintptr_t)probe_mmu_vector)) =
	    relay + ((uintptr_t)probe_mmu_identity - (uintptr_t)probe_mmu_vector);
	*(volatile uint64_t *)(relay +
	    ((uintptr_t)probe_mmu_recorder - (uintptr_t)probe_mmu_vector)) =
	    (uintptr_t)probe_vectors;
#elif defined(ENTRY_XNU_HIGH_VECTOR_PA)
	/* Shared-table mode restores the still-intact bootstrap TTBR0 before
	 * switching to the physical alias. The per-slot mode requires TTBR0
	 * to remain active. Configured cache cleanup precedes the recorder. */
	extern const uint32_t probe_mmu_vector[], probe_mmu_vector_end[];
	extern const uint32_t probe_mmu_identity[], probe_mmu_physical[], probe_mmu_recorder[];
	unsigned words = ((uintptr_t)probe_mmu_vector_end - (uintptr_t)probe_mmu_vector) / 4;
#ifdef ENTRY_XNU_RESTORE_TTBR0
	uintptr_t dst = ENTRY_XNU_HIGH_VECTOR_PA;
	volatile uint32_t *v = (volatile uint32_t *)dst;
	for (unsigned i = 0; i < words; i++)
		v[i] = probe_mmu_vector[i];
	*(volatile uint64_t *)(dst + (uintptr_t)probe_mmu_physical - (uintptr_t)probe_mmu_vector) =
	    dst + (uintptr_t)probe_mmu_identity - (uintptr_t)probe_mmu_vector;
	*(volatile uint64_t *)(dst + (uintptr_t)probe_mmu_recorder - (uintptr_t)probe_mmu_vector) =
	    (uintptr_t)probe_vectors;
#else
	for (unsigned slot = 0; slot < 16; slot++) {
		uintptr_t dst = ENTRY_XNU_HIGH_VECTOR_PA + slot * 128;
		volatile uint32_t *v = (volatile uint32_t *)dst;
		for (unsigned i = 0; i < words; i++)
			v[i] = probe_mmu_vector[i];
		*(volatile uint64_t *)(dst + (uintptr_t)probe_mmu_physical - (uintptr_t)probe_mmu_vector) =
		    dst + (uintptr_t)probe_mmu_identity - (uintptr_t)probe_mmu_vector;
		*(volatile uint64_t *)(dst + (uintptr_t)probe_mmu_recorder - (uintptr_t)probe_mmu_vector) =
		    (uintptr_t)probe_vectors + slot * 128;
	}
#endif
#endif
}
#endif
#if defined(ENTRY_XNU)
#include "loader.h"
#endif

static void field(char *banner, unsigned offset, uint64_t value)
{
	static const char hex[] = "0123456789abcdef";
	for (unsigned i = 0; i < 16; i++)
		banner[offset + i] = hex[(value >> (60 - i * 4)) & 15];
}

void entryprobe_display(uint64_t dtb, char *banner);
#if defined(ENTRY_DISPLAY_KICK)
void entryprobe_display_kick(uint64_t dtb, char *banner);
#endif

void entryprobe_timer(uint64_t *dctlr, uint64_t *redist, uint64_t *flags);
void entryprobe_irq(uint64_t redist, uint64_t *count, uint64_t *last, uint64_t *rc);

void entryprobe_board(uint64_t dtb, char *banner)
{
#if defined(ENTRY_EL1_DISPLAY)
	field(banner, ENTRY_STAGE, 0x70);
#endif
	if (!board_probe((uintptr_t)dtb)) {
		field(banner, ENTRY_STAGE, 0xff);
		return;
	}
	field(banner, ENTRY_GICD, brd.gicd);
	field(banner, ENTRY_VTIMER, brd.vtimer_intid);
	field(banner, ENTRY_FB, brd.fb_base);
	field(banner, ENTRY_STAGE, 2);
#if defined(ENTRY_EL1_DISPLAY)
	/* Exercise the full shim's console/display at EL1, then return through
	 * HVC to the saved EL2 Linux entry. Exceptions carry ESR/FAR/PC into
	 * this same-boot banner instead of disappearing behind the LK logo. */
	uint64_t sctlr;
	__asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));
	field(banner, ENTRY_SCTLR, sctlr);
	field(banner, ENTRY_STAGE, 0x71);
	con_target((volatile uint32_t *)(uintptr_t)brd.fb_base);
	con_w = brd.fb_w;
	con_h = brd.fb_h;
	con_stride = brd.fb_stride;
	con_bgr = brd.fb_bgr;
	con_clear(BLUE);
	con_str("SWEET", "EL1 DISPLAY PROBE");
	field(banner, ENTRY_STAGE, 0x72);
	int rc = display_probe(dtb, brd.fb_base, brd.fb_stride);
	field(banner, ENTRY_ESR, display_error());
	field(banner, ENTRY_FAR, rc);
	field(banner, ENTRY_STAGE, 0x73);
	if (rc) {
		display_refresh();
		field(banner, ENTRY_STAGE, 0x74);
#if defined(ENTRY_LOADER_PROBE)
		loader_banner = banner;
		const char *why = loader_boot(dtb);
		con_str("LOADER", why);
		display_refresh();
		field(banner, ENTRY_STAGE, 0x7f);
#endif
	}
#else
#if ENTRY_LEVEL == 6
#if defined(ENTRY_XNU)
	/* Use the hardware-validated kick path to put a known image on the
	 * panel, then load the XNU payload with the real shim loader instead
	 * of returning to the stock Linux kernel. */
	entryprobe_display_kick(dtb, banner);
	loader_boot((uintptr_t)dtb);
	for (;;)
		__asm__ volatile("wfe");
#elif defined(ENTRY_DISPLAY_KICK)
	entryprobe_display_kick(dtb, banner);
#else
	entryprobe_display(dtb, banner);
#endif
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
#endif /* !ENTRY_EL1_DISPLAY */
}

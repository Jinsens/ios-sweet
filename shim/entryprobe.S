/*
 * Non-destructive entry probe for bootloaders without `fastboot boot` or UART.
 * Record LK's actual entry registers in the bundled Linux kernel's banner,
 * optionally exercise the real shim FDT reader, then boot that Linux kernel
 * in the SAME boot. Recovery's dmesg is the reader; no reset-persistence,
 * display, UFS driver, fixed MMIO address or partition write is involved here.
 *
 * mkentryprobe.py supplies offsets for a caller-provided stock Image. The
 * stock Image must permit placement anywhere and have text_offset == 0.
 */
#include "entryprobe_config.h"

	.section .text.head, "ax"
	.globl _head
_head:
	b	probe_start
	.long	0
	.quad	0
	.quad	ENTRY_IMAGE_SIZE
	.quad	0xa
	.quad	0, 0, 0
	.ascii	"ARM\x64"
	.long	0

/* All address construction is PC-relative: LK chooses the load address. */
.macro banner_field field
	adrp	x1, stock_image
	add	x1, x1, :lo12:stock_image
	mov	x2, #((ENTRY_BANNER_OFFSET + \field) & 0xffff)
	movk	x2, #((ENTRY_BANNER_OFFSET + \field) >> 16), lsl #16
	add	x1, x1, x2
	bl	probe_hex
.endm

	.text
probe_start:
	msr	daifset, #0xf
	adr	x9, probe_saved
	stp	x0, x1, [x9]
	stp	x2, x3, [x9, #16]
	mov	x10, sp
	str	x10, [x9, #32]
	mrs	x10, spsel
	str	x10, [x9, #128]
	mrs	x10, CurrentEL
	str	x10, [x9, #40]
	cmp	x10, #8
	b.eq	1f
	cmp	x10, #4
	b.ne	probe_stock	/* do not touch sysregs at an unknown EL */
	mrs	x11, vbar_el1
	mrs	x12, sctlr_el1
	b	2f
1:
	mrs	x11, vbar_el2
	mrs	x12, sctlr_el2
2:
	str	x11, [x9, #48]
	str	x12, [x9, #56]
	adr	x11, probe_vectors
	cmp	x10, #8
	b.eq	3f
	msr	vbar_el1, x11
	b	4f
3:
	msr	vbar_el2, x11
4:
	isb
	lsr	x0, x10, #2
	banner_field ENTRY_EL
	mrs	x0, midr_el1
	banner_field ENTRY_MIDR
	mrs	x0, mpidr_el1
	banner_field ENTRY_MPIDR
	mrs	x0, cntfrq_el0
	banner_field ENTRY_FREQ
	adr	x9, probe_saved
	ldr	x0, [x9]
#if ENTRY_LEVEL != 6
	banner_field ENTRY_DTB
	adr	x9, probe_saved
	ldr	x0, [x9, #56]
	banner_field ENTRY_SCTLR
#endif
	mov	x0, #1
	banner_field ENTRY_STAGE

#if ENTRY_LEVEL > 0
	msr	spsel, #1
	adr	x9, probe_stack_top
	mov	sp, x9
	adr	x9, probe_saved
	ldr	x0, [x9]
	adrp	x1, stock_image
	add	x1, x1, :lo12:stock_image
	mov	x2, #(ENTRY_BANNER_OFFSET & 0xffff)
	movk	x2, #(ENTRY_BANNER_OFFSET >> 16), lsl #16
	add	x1, x1, x2
#if defined(ENTRY_EL1_DISPLAY)
	mrs	x10, CurrentEL
	cmp	x10, #8
	b.ne	probe_call_c
	/* Save the EL1 and EL2 controls before reproducing start.S's drop.
	 * The EL2 vectors stay installed: HVC returns us to Linux even if the
	 * EL1 C probe faults before its display can show an error. */
	adr	x9, probe_saved
	mrs	x10, sctlr_el1
	str	x10, [x9, #64]
	mrs	x10, vbar_el1
	str	x10, [x9, #72]
	mrs	x10, sp_el1
	str	x10, [x9, #80]
	mrs	x10, hcr_el2
	str	x10, [x9, #88]
	mrs	x10, cnthctl_el2
	str	x10, [x9, #96]
	mrs	x10, cntvoff_el2
	str	x10, [x9, #104]
	mrs	x10, cptr_el2
	str	x10, [x9, #112]
	mrs	x10, mdcr_el2
	str	x10, [x9, #120]
	adr	x10, probe_vectors
	msr	vbar_el1, x10
	adr	x10, probe_stack_top
	msr	sp_el1, x10
	mov	x10, #3
	msr	cnthctl_el2, x10
	msr	cntvoff_el2, xzr
	mov	x10, #0x33ff
	msr	cptr_el2, x10
	msr	mdcr_el2, xzr
	mov	x10, #(1 << 31)
	msr	hcr_el2, x10
	adr	x10, probe_el1
	msr	elr_el2, x10
	mov	x10, #0x3c5
	msr	spsr_el2, x10
	isb
	eret
probe_el1:
	/* Match the production shim: disable EL1 translation/caches only
	 * when LK left an active EL1 map. */
	mrs	x10, sctlr_el1
	tbz	x10, #0, probe_call_c
	bic	x10, x10, #1
	bic	x10, x10, #4
	bic	x10, x10, #(1 << 12)
	dsb	sy
	msr	sctlr_el1, x10
	isb
probe_call_c:
#endif
	bl	entryprobe_board
#endif
	b	probe_finish

/* A failed optional C/DRAM probe must still return to the stock kernel. */
probe_exception:
#if defined(ENTRY_EL1_DISPLAY)
	mrs	x10, CurrentEL
	cmp	x10, #8
	b.ne	probe_record_fault
	mrs	x10, esr_el2
	ubfx	x11, x10, #26, #6
	cmp	x11, #0x16		/* HVC64 */
	b.ne	probe_record_fault
	and	x10, x10, #0xffff
	mov	x11, #0x5357
	cmp	x10, x11
	b.eq	probe_resume_el2
probe_record_fault:
#endif
	add	x0, x13, #1
	banner_field ENTRY_FAULT
	mrs	x10, CurrentEL
	cmp	x10, #8
	b.eq	5f
	mrs	x0, esr_el1
	banner_field ENTRY_ESR
	mrs	x0, far_el1
	banner_field ENTRY_FAR
	mrs	x0, elr_el1
	banner_field ENTRY_PC
	b	probe_finish
5:
	mrs	x0, esr_el2
	banner_field ENTRY_ESR
	mrs	x0, far_el2
	banner_field ENTRY_FAR
	mrs	x0, elr_el2
	banner_field ENTRY_PC

probe_finish:
#if defined(ENTRY_EL1_DISPLAY)
	mrs	x10, CurrentEL
	cmp	x10, #4
	b.ne	probe_finish_el2
	adr	x9, probe_saved
	ldr	x10, [x9, #40]
	cmp	x10, #8
	b.ne	probe_finish_el2
	hvc	#0x5357
	b	.
probe_resume_el2:
	adr	x9, probe_saved
	ldr	x10, [x9, #64]
	msr	sctlr_el1, x10
	ldr	x10, [x9, #72]
	msr	vbar_el1, x10
	ldr	x10, [x9, #80]
	msr	sp_el1, x10
	ldr	x10, [x9, #88]
	msr	hcr_el2, x10
	ldr	x10, [x9, #96]
	msr	cnthctl_el2, x10
	ldr	x10, [x9, #104]
	msr	cntvoff_el2, x10
	ldr	x10, [x9, #112]
	msr	cptr_el2, x10
	ldr	x10, [x9, #120]
	msr	mdcr_el2, x10
	isb
probe_finish_el2:
#endif
#if ENTRY_LEVEL >= 3 && ENTRY_LEVEL < 6
	/* The MMU stage maps only our own prefix WB. Clean all its dirty
	 * lines (including tables, saved registers and stack), plus the banner
	 * below, before restoring the original MMU-off SCTLR for Linux. */
	adr	x1, _head
	adr	x2, _end
	mrs	x10, ctr_el0
	ubfx	x10, x10, #16, #4
	mov	x11, #4
	lsl	x11, x11, x10
	sub	x10, x11, #1
	bic	x1, x1, x10
11:
	dc	cvac, x1
	add	x1, x1, x11
	cmp	x1, x2
	b.lo	11b
	dsb	sy
#endif
	/* Linux may invalidate data caches early. Clean our banner if LK left
	 * caches on, but otherwise preserve the entry state for stock Linux. */
	adr	x9, probe_saved
	ldr	x10, [x9, #56]
	tbz	x10, #2, 7f
	adrp	x1, stock_image
	add	x1, x1, :lo12:stock_image
	mov	x2, #(ENTRY_BANNER_OFFSET & 0xffff)
	movk	x2, #(ENTRY_BANNER_OFFSET >> 16), lsl #16
	add	x1, x1, x2
	mrs	x10, ctr_el0
	ubfx	x10, x10, #16, #4
	mov	x11, #4
	lsl	x11, x11, x10
	sub	x10, x11, #1
	bic	x1, x1, x10
	add	x2, x1, #(ENTRY_BANNER_SIZE + 256)
6:
	dc	cvac, x1
	add	x1, x1, x11
	cmp	x1, x2
	b.lo	6b
7:
	dsb	sy
#if (ENTRY_LEVEL >= 3 && ENTRY_LEVEL < 6) || defined(ENTRY_LOADER_PROBE)
	adr	x9, probe_saved
	ldr	x10, [x9, #56]
	msr	sctlr_el1, x10
	isb
	tlbi	vmalle1
	dsb	sy
	isb
#endif
	adr	x9, probe_saved
	ldr	x10, [x9, #40]
	ldr	x11, [x9, #48]
	cmp	x10, #8
	b.eq	8f
	msr	vbar_el1, x11
	b	9f
8:
	msr	vbar_el2, x11
9:
	isb
	/* XNU early startup can select SP_EL0 and put an unmapped kernel VA
	 * in either stack register. Our recorder is stackless; restore the
	 * original selection before restoring the original stack pointer. */
	ldr	x10, [x9, #128]
	tbz	x10, #0, 12f
	msr	spsel, #1
	b	13f
12:
	msr	spsel, #0
13:
	ldr	x10, [x9, #32]
	mov	sp, x10
probe_stock:
	adr	x9, probe_saved
	ldp	x0, x1, [x9]
	ldp	x2, x3, [x9, #16]
	b	stock_image

/* x0=value, x1=16-byte destination; no stack or other dependencies. */
probe_hex:
	mov	x8, #60
10:
	lsrv	x9, x0, x8
	and	x9, x9, #15
	cmp	x9, #10
	add	x10, x9, #48
	add	x11, x9, #87
	csel	x9, x10, x11, lo
	strb	w9, [x1], #1
	subs	x8, x8, #4
	b.ge	10b
	ret

#if ENTRY_LEVEL == 5
probe_irq:
	stp	x0, x1, [sp, #-48]!
	stp	x2, x3, [sp, #16]
	stp	x4, x5, [sp, #32]
	mrs	x0, icc_iar1_el1
	and	x1, x0, #0xffffff
	cmp	x1, #1020
	b.hs	irq_return
	cmp	x1, #27
	b.ne	irq_record
	msr	cntv_ctl_el0, xzr
	isb
irq_record:
	adrp	x2, entryprobe_irq_count
	add	x2, x2, :lo12:entryprobe_irq_count
	ldr	x3, [x2]
	add	x3, x3, #1
	str	x3, [x2]
	adrp	x2, entryprobe_irq_last
	add	x2, x2, :lo12:entryprobe_irq_last
	str	x1, [x2]
	msr	icc_eoir1_el1, x0
	isb
irq_return:
	ldp	x2, x3, [sp, #16]
	ldp	x4, x5, [sp, #32]
	ldp	x0, x1, [sp], #48
	eret
#endif

.macro probe_vector slot
	.balign 128
#if ENTRY_LEVEL == 5
	.if \slot == 5
	b	probe_irq
	.else
#endif
	mov	x13, #\slot
	b	probe_exception
#if ENTRY_LEVEL == 5
	.endif
#endif
.endm
	.balign 2048
	.globl probe_vectors
probe_vectors:
	probe_vector 0
	probe_vector 1
	probe_vector 2
	probe_vector 3
	probe_vector 4
	probe_vector 5
	probe_vector 6
	probe_vector 7
	probe_vector 8
	probe_vector 9
	probe_vector 10
	probe_vector 11
	probe_vector 12
	probe_vector 13
	probe_vector 14
	probe_vector 15

#ifdef ENTRY_XNU_HIGH_VECTOR_PA
	/* Copied verbatim into each XNU high vector slot. All references are
	 * local PC-relative literals, valid at both physical and virtual aliases.
	 * Later early-C checkpoints require the bounded cache-clean option. */
	.balign 8
	.globl probe_mmu_vector, probe_mmu_vector_end, probe_mmu_identity
	.globl probe_mmu_physical, probe_mmu_recorder
probe_mmu_vector:
	ldr	x16, probe_mmu_physical
	br	x16
probe_mmu_identity:
#ifdef ENTRY_XNU_CLEAN_START
	/* Early C may dirty kernel globals/stacks. All such storage is still
	 * inside the loader's kernel/ADT/boot_args range at this checkpoint.
	 * Clean/invalidate via its bootstrap identity alias before MMU-off. */
	mrs	x15, ctr_el0
	ubfx	x15, x15, #16, #4
	mov	x14, #4
	lsl	x15, x14, x15
	ldr	x16, probe_mmu_clean_start
	ldr	x17, probe_mmu_clean_end
14:
	dc	civac, x16
	add	x16, x16, x15
	cmp	x16, x17
	b.lo	14b
	dsb	sy
#endif
	mrs	x16, sctlr_el1
	bic	x16, x16, #1
	bic	x16, x16, #4
	bic	x16, x16, #(1 << 12)
	dsb	sy
	msr	sctlr_el1, x16
	isb
	ldr	x16, probe_mmu_recorder
	br	x16
	.balign 8
probe_mmu_physical:
	.quad 0
probe_mmu_recorder:
	.quad 0
#ifdef ENTRY_XNU_CLEAN_START
probe_mmu_clean_start:
	.quad ENTRY_XNU_CLEAN_START
probe_mmu_clean_end:
	.quad ENTRY_XNU_CLEAN_END
#endif
probe_mmu_vector_end:
#endif

	.data
	.balign 16
probe_saved:
	.zero 144
	.bss
	.balign 16
	.space 0x8000
probe_stack_top:

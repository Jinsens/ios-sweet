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
	banner_field ENTRY_DTB
	adr	x9, probe_saved
	ldr	x0, [x9, #56]
	banner_field ENTRY_SCTLR
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
	bl	entryprobe_board
#endif
	b	probe_finish

/* A failed optional C/DRAM probe must still return to the stock kernel. */
probe_exception:
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
#if ENTRY_LEVEL >= 3
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
#if ENTRY_LEVEL >= 3
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

.macro probe_vector slot
	.balign 128
	mov	x13, #\slot
	b	probe_exception
.endm
	.balign 2048
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

	.data
	.balign 16
probe_saved:
	.zero 64
	.bss
	.balign 16
	.space 0x8000
probe_stack_top:

/*
 * Act as iBoot for xnu.
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the bootloader's initrd,
 * copy the segments to phys_base + (vmaddr - virt_base), place the ADT and
 * boot_args behind the kernel, and jump to the entry point with the MMU and
 * caches off, x0 = boot_args (physical).
 *
 * Boot v4 embeds the payload immediately after the linker's _end in the
 * inflated kernel Image. Recovery's ramdisk is in vendor_boot; boot.img's
 * compressed generic ramdisk is preserved without a raw payload trailer.
 * Legacy boot images still scan the initrd for an aligned payload magic.
 */
#include <stdint.h>
#include "board.h"
#include "console.h"
#ifdef EMBEDDED_XNU_BYTES
#include "display.h"
#endif
#include "fdt.h"
#include "loader.h"

#define BOOT_LINE_LENGTH	608
#define PAGE_16K		0x4000UL

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x2a8, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 736, "boot_args layout");

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}

static void zero64(uintptr_t dst, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	for (uint64_t i = 0; i < n / 8; i++)
		d[i] = 0;
}

static int magic_ok(const char *m)
{
	static const char want[] = "SWEETXNU";
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

#ifdef EMBEDDED_XNU_BYTES
extern const unsigned char _end[];

/* _end is 4 KiB aligned by shim.ld; build.sh appends marker, length, payload. */
static const struct payload *embedded_payload(void)
{
	static const char marker[8] = "SWEETAPP";
	const unsigned char *data = _end;
	for (int i = 0; i < 8; i++)
		if (data[i] != marker[i])
			return 0;
	uint64_t len = *(const volatile uint64_t *)(const void *)(data + 8);
	if (len != EMBEDDED_XNU_BYTES || len < sizeof(struct payload))
		return 0;
	const struct payload *p = (const struct payload *)(const void *)(data + 16);
	return magic_ok(p->magic) ? p : 0;
}
#endif

#ifndef EMBEDDED_XNU_BYTES
/* Find the payload anywhere in [start, end), 8-byte aligned. */
static const struct payload *find_payload(uint64_t start, uint64_t end)
{
	for (uint64_t p = (start + 7) & ~7UL; p + sizeof(struct payload) <= end; p += 8)
		if (magic_ok((const char *)p))
			return (const struct payload *)p;
	return 0;
}
#endif

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

const char *loader_boot(uintptr_t dtb)
{
	uint64_t rd_start = 0, rd_end = 0, payload_bytes;
	const struct payload *p;
	int embedded = 0;

#ifdef EMBEDDED_XNU_BYTES
	(void)dtb;
	p = embedded_payload();
	if (!p)
		return "embedded SWEETXNU missing or corrupt";
	payload_bytes = EMBEDDED_XNU_BYTES;
	embedded = 1;
	con_str("PAYLOAD", "embedded in kernel Image");
#else
	if (!fdt_initrd(dtb, &rd_start, &rd_end))
		return "no initrd in /chosen";
	con_hex2("INITRD", "lo=", (uint32_t)rd_start, "hi=", (uint32_t)rd_end);
	p = find_payload(rd_start, rd_end);
	if (!p)
		return "no SWEETXNU payload in initrd";
	payload_bytes = rd_end - (uintptr_t)p;
#endif
	if (p->version != 1 || (p->adt_off & 7) || ((uintptr_t)p & 7))
		return "bad payload header";
	if (!p->nsegs || p->nsegs > (payload_bytes - sizeof(*p)) / sizeof(struct payload_seg) ||
	    p->adt_off > payload_bytes || p->adt_size > payload_bytes - p->adt_off)
		return "payload exceeds source bounds";
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		if (s->file_off > payload_bytes || s->file_size > payload_bytes - s->file_off ||
		    s->file_size > s->vmsize)
			return "payload segment exceeds source bounds";
	}

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex2("PHYS", "base=", (uint32_t)p->phys_base, "size=", (uint32_t)p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);

	/* The payload must not overlap where the kernel goes. */
	if (!embedded && rd_end > p->phys_base && rd_start < p->phys_base + p->mem_size)
		return "ramdisk overlaps kernel memory";

	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		uintptr_t dst = s->vmaddr + pa_of_va_base;
		copy64(dst, (uintptr_t)p + s->file_off, s->file_size);
		if (s->vmsize > s->file_size)
			zero64(dst + ((s->file_size + 7) & ~7UL), s->vmsize - ((s->file_size + 7) & ~7UL));
	}
	con_hex2("SEGS", "n=", p->nsegs, "end=", (uint32_t)(p->kernel_end + pa_of_va_base));

	/* ADT, then boot_args, behind the kernel; everything below topOfKernelData is ours. */
	uintptr_t adt_pa = p->kernel_end + pa_of_va_base;
	copy64(adt_pa, (uintptr_t)p + p->adt_off, p->adt_size);
	uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uintptr_t top = ba_pa + PAGE_16K;

	zero64(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = brd.fb_base;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = brd.fb_stride;
	ba->Video.v_width = brd.fb_w;
	ba->Video.v_height = brd.fb_h;
	ba->Video.v_depth = 32;
	ba->deviceTreeP = adt_pa - pa_of_va_base;	/* xnu reads it with the MMU on: a VA */
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->memSizeActual = p->mem_size;
	con_hex2("BOOTARG", "pa=", (uint32_t)ba_pa, "top=", (uint32_t)top);

	uintptr_t entry_pa = p->entry_va + pa_of_va_base;
	con_hex("JUMP", entry_pa);
	con_str("XNU", "jumping (fbcon clears screen)");
	/* Command-mode panels need an explicit push. Keep the handoff marker
	 * visible briefly so a dark XNU frame is distinguishable from a loader
	 * failure when inspecting the device without a serial console. */
#ifdef EMBEDDED_XNU_BYTES
	if (display_active()) {
		uint64_t freq, begin, tick;
		display_refresh();
		__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
		__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(begin));
		do {
			__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(tick));
		} while (tick - begin < freq / 5); /* ~200 ms */
	}
#endif

	/* Everything was written with the MMU and caches off; drop any stale I-cache lines too. */
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);
	return "xnu returned";
}

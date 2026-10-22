/*
 * Diagnostic trace for the blind MediaTek boot path.
 *
 * The command-mode panel is the only output until XNU owns the console.
 * -DXNU_DIAG draws every shim stage and plants sentinels in three globals
 * that XNU writes at known early stages; the next boot reports which sentinel
 * is still intact. -DDIAG_LOGSTORE additionally mirrors the trace into
 * MediaTek LK's log_store for `fastboot oem lkmsg`. It is off by default:
 * hardware attempts hung on the first log_store access from the shim, before
 * any frame was pushed. The log store also cannot survive LK rewriting its
 * own copy on some boots, so the panel is the primary channel.
 */
#include <stdint.h>
#include "board.h"
#include "console.h"
#include "diag.h"
#include "display.h"
#include "fdt.h"

#ifdef XNU_DIAG
#include "diag_syms.h"

#define SENT_MAGIC		0x584e55454e545259ULL	/* XNUENTRY */

static uint64_t boot_t0;
static uint64_t boot_freq;

static uint64_t
now(void)
{
	uint64_t v;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(v));
	return v;
}

static uint64_t
rd64(uint64_t pa)
{
	return *(volatile uint64_t *)(uintptr_t)pa;
}

static void
wr64(uint64_t pa, uint64_t v)
{
	*(volatile uint64_t *)(uintptr_t)pa = v;
	__asm__ volatile("dsb sy" ::: "memory");
}

static char *
put_hex(char *p, uint64_t v)
{
	static const char h[] = "0123456789ABCDEF";
	for (int i = 60; i >= 0; i -= 4)
		*p++ = h[(v >> i) & 15];
	return p;
}

static char *
put_dec(char *p, uint64_t v)
{
	char tmp[20];
	int n = 0;
	do {
		tmp[n++] = (char)('0' + v % 10);
		v /= 10;
	} while (v);
	while (n)
		*p++ = tmp[--n];
	return p;
}

static char *
put_str(char *p, const char *s)
{
	while (*s)
		*p++ = *s++;
	return p;
}

/*
 * Log-store trace (opt-in).
 */
#ifdef DIAG_LOGSTORE

#define LS_FALLBACK		0x7ffbf000ULL
#define LS_FALLBACK_SIZE	0x40000ULL
#define LS_TEXT_MAX		0x3000U
#define LS_MAGIC		"SWX2BOOT\n"
#define LS_MAGIC_LEN		9U

/* LK appends its own boot log to the same store; keep three copies so one
 * overwrite cannot hide the trace. The reporter reads the longest one. */
static const uint32_t ls_mirror[] = { 0x1000U, 0x10000U, 0x30000U };
#define LS_MIRRORS		(sizeof(ls_mirror) / sizeof(ls_mirror[0]))

static uint64_t ls_base, ls_size;
static uint32_t ls_text_off, ls_len;
static int reporting;

static uint8_t
rd8(uint64_t pa)
{
	return *(volatile uint8_t *)(uintptr_t)pa;
}

static void
ls_write(uint32_t off, const char *s, uint32_t n)
{
	volatile uint8_t *d = (volatile uint8_t *)(uintptr_t)(ls_base + off);
	for (uint32_t i = 0; i < n; i++)
		d[i] = (uint8_t)s[i];
	__asm__ volatile("dsb sy" ::: "memory");
}

static void
sentinel_summary(void)
{
	char out[192];
	char *p = out;

	if (ls_size < 0x800)
		return;
	p = put_str(p, "SWX2SENT BS=");
	p = put_hex(p, rd64(XNU_DIAG_PA_BOOTSTRAP));
	p = put_str(p, " BA=");
	p = put_hex(p, rd64(XNU_DIAG_PA_BOOT_ARGS));
	p = put_str(p, " GP=");
	p = put_hex(p, rd64(XNU_DIAG_PA_GPHYS));
	p = put_str(p, " GV=");
	p = put_hex(p, rd64(XNU_DIAG_PA_GVIRT));
	p = put_str(p, "\n");
	ls_write((uint32_t)(ls_size - 0x800), out, (uint32_t)(p - out));
}

static void
ls_init(uintptr_t dtb)
{
	char path[192];
	uint64_t a = 0, s = 0;

	ls_base = LS_FALLBACK;
	ls_size = LS_FALLBACK_SIZE;
	if (fdt_find_compatible(dtb, "mediatek,log_store", path, sizeof path) &&
	    fdt_reg(dtb, path, &a, &s) && a >= 0x1000 && s >= LS_TEXT_MAX) {
		ls_base = a;
		ls_size = s;
	}
	sentinel_summary();

	reporting = 0;
	ls_text_off = ls_mirror[0];
	{
		uint32_t best = 0;

		for (uint32_t m = 0; m < LS_MIRRORS; m++) {
			uint32_t off = ls_mirror[m];
			int ok = 1;

			if (off + LS_TEXT_MAX > ls_size)
				continue;
			for (uint32_t i = 0; i < LS_MAGIC_LEN - 1; i++)
				if (rd8(ls_base + off + i) != (uint8_t)LS_MAGIC[i]) {
					ok = 0;
					break;
				}
			if (!ok)
				continue;
			uint32_t n = 0;
			while (n < LS_TEXT_MAX &&
			    rd8(ls_base + off + LS_MAGIC_LEN + n))
				n++;
			if (!reporting || n > best) {
				reporting = 1;
				best = n;
				ls_text_off = off;
			}
		}
	}
	ls_len = 0;
	if (!reporting) {
		for (uint32_t m = 0; m < LS_MIRRORS; m++)
			if (ls_mirror[m] + LS_TEXT_MAX <= ls_size)
				ls_write(ls_mirror[m], LS_MAGIC, LS_MAGIC_LEN);
	}
}

static void
ls_stage(const char *line, uint32_t n)
{
	if (!ls_base || reporting)
		return;
	if (LS_MAGIC_LEN + ls_len + n + 1 >= LS_TEXT_MAX)
		return;
	for (uint32_t m = 0; m < LS_MIRRORS; m++) {
		uint32_t off = ls_mirror[m];

		if (off + LS_TEXT_MAX > ls_size)
			continue;
		ls_write(off + LS_MAGIC_LEN + ls_len, line, n);
	}
	ls_len += n;
}

#else

#define reporting 0
static inline void ls_init(uintptr_t dtb) { (void)dtb; }
static inline void ls_stage(const char *line, uint32_t n)
{
	(void)line; (void)n;
}

#endif /* DIAG_LOGSTORE */

void
diag_early(uintptr_t dtb)
{
	ls_init(dtb);
	__asm__ volatile("isb; mrs %0, cntfrq_el0" : "=r"(boot_freq));
	boot_t0 = now();
}

int
diag_reporting(void)
{
	return reporting;
}

void
diag_stage(const char *name, uint64_t a, uint64_t b)
{
	char line[96];
	char *p = line;
	uint32_t ms;
	uint32_t n;

	if (reporting)
		return;
	ms = (uint32_t)((now() - boot_t0) / (boot_freq / 1000));
	p = put_str(p, name);
	*p++ = ' ';
	p = put_hex(p, a);
	*p++ = ' ';
	p = put_hex(p, b);
	p = put_str(p, " t=");
	p = put_dec(p, ms);
	*p++ = '\n';
	*p = 0;
	n = (uint32_t)(p - line);
	ls_stage(line, n);

	if (con_fb) {
		char draw[64];
		char *d = draw;
		d = put_str(d, "a=");
		d = put_hex(d, a);
		d = put_str(d, " b=");
		d = put_hex(d, b);
		*d = 0;
		con_str(name, draw);
		if (display_active())
			display_refresh();
	}
}

void
diag_arm_sentinels(uint64_t virt_base, uint64_t phys_base, uint64_t mem_size)
{
	if (reporting)
		return;
	if (virt_base != XNU_DIAG_VIRT_BASE || phys_base != XNU_DIAG_PHYS_BASE ||
	    mem_size != XNU_DIAG_MEM_SIZE) {
		diag_stage("SENT", virt_base, phys_base);
		return;
	}
	wr64(XNU_DIAG_PA_BOOTSTRAP, SENT_MAGIC);
	wr64(XNU_DIAG_PA_BOOT_ARGS, SENT_MAGIC);
	wr64(XNU_DIAG_PA_GPHYS, SENT_MAGIC);
	wr64(XNU_DIAG_PA_GVIRT, SENT_MAGIC);
	diag_stage("SENT", XNU_DIAG_PA_BOOTSTRAP, SENT_MAGIC);
}

/* Report the previous boot's sentinel words on the panel before this boot's
 * loader overwrites them. A word still equal to SENT_MAGIC means XNU never
 * executed the write for that stage. */
void
diag_prev_report(void)
{
	uint64_t bs = rd64(XNU_DIAG_PA_BOOTSTRAP);
	uint64_t ba = rd64(XNU_DIAG_PA_BOOT_ARGS);
	uint64_t gp = rd64(XNU_DIAG_PA_GPHYS);
	uint64_t gv = rd64(XNU_DIAG_PA_GVIRT);

	if (!con_fb)
		return;
	con_str("PREV", "sentinels from last boot");
	con_hex("BSENT", bs);
	con_hex("BASENT", ba);
	con_hex("GPSENT", gp);
	con_hex("GVSENT", gv);
	con_str("ENTRY", bs == SENT_MAGIC ? "XNU did not run start.s" :
	    "bootstrap tables written");
	con_str("ARM_INIT", ba == SENT_MAGIC ? "not reached" :
	    "boot_args copied");
	con_str("ARM_VM", gp == SENT_MAGIC ? "not reached" : "gPhysBase set");
	if (display_active())
		display_refresh();
}

void
diag_report(void)
{
#ifdef DIAG_LOGSTORE
	char line[80];
	int n = 0;

	con_str("DIAG", "previous boot trace");
	for (uint32_t i = LS_MAGIC_LEN; i < LS_TEXT_MAX; i++) {
		char c = (char)rd8(ls_base + ls_text_off + i);
		if (!c)
			break;
		if (c == '\n') {
			line[n] = 0;
			if (n)
				con_str(line, "");
			n = 0;
		} else if (n < (int)sizeof(line) - 1) {
			line[n++] = c;
		}
	}
	if (n) {
		line[n] = 0;
		con_str(line, "");
	}
	if (display_active())
		display_refresh();
#endif
}

#else /* !XNU_DIAG */

void diag_early(uintptr_t dtb) { (void)dtb; }
int diag_reporting(void) { return 0; }
void diag_report(void) { }
void diag_prev_report(void) { }
void diag_stage(const char *name, uint64_t a, uint64_t b)
{
	(void)name; (void)a; (void)b;
}
void diag_arm_sentinels(uint64_t v, uint64_t p, uint64_t m)
{
	(void)v; (void)p; (void)m;
}

#endif

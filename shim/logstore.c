/* MediaTek PL/LK log_store append-only writer. No guessed physical address. */
#include <stdint.h>
#include "board.h"
#include "logstore.h"

#define SRAM_SIG       0xabcd1234U
#define PL_LK_SIG      0xcdab3412U
#define DRAM_BUF_ADDR  0x14 /* packed u64: read as two aligned words */
#define DRAM_BUF_SIZE  0x1c
#define PL_LK_OFF      0x40
#define PL_LK_SIZE     0x44
#define PL_LK_OFF_PL   0x48
#define PL_LK_SZ_PL    0x4c
#define PL_LK_OFF_LK   0x54
#define PL_LK_SZ_LK    0x58
#define HEADER_SIZE    0x60
#define MAX_BUF        (16U << 20)
#define MIN_BUF        0x1000U

static uint32_t read32(volatile uint8_t *header, unsigned offset)
{
	return *(volatile uint32_t *)(header + offset);
}

static void barrier(void)
{
#ifndef LOGSTORE_HOST_TEST
	__asm__ volatile("dsb sy" ::: "memory");
#else
	__asm__ volatile("" ::: "memory");
#endif
}

int logstore_write(const char *text, uint32_t n)
{
	if (!text || !n || !brd.log_store || (brd.log_store & 3) ||
	    brd.log_store_size < HEADER_SIZE)
		return 0;
	volatile uint8_t *h = (volatile uint8_t *)(uintptr_t)brd.log_store;
	if (read32(h, 0) != SRAM_SIG || read32(h, PL_LK_OFF) != PL_LK_SIG)
		return 0;
	uint64_t buf = read32(h, DRAM_BUF_ADDR);
	buf |= (uint64_t)read32(h, DRAM_BUF_ADDR + 4) << 32;
	uint32_t allocated = read32(h, DRAM_BUF_SIZE);
	uint32_t size = read32(h, PL_LK_SIZE);
	if (buf < 0x1000 || allocated < MIN_BUF || allocated > MAX_BUF ||
	    size < MIN_BUF || size > allocated || buf > UINTPTR_MAX - allocated)
		return 0;
	uint32_t off_pl = read32(h, PL_LK_OFF_PL);
	uint32_t sz_pl = read32(h, PL_LK_SZ_PL);
	uint32_t off_lk = read32(h, PL_LK_OFF_LK);
	uint32_t sz_lk = read32(h, PL_LK_SZ_LK);
	/* Never overlap the preloader text, its in-buffer header, or the end.
	 * Subtractions are ordered to reject integer overflow as well. */
	if (off_pl < 0x20 || off_pl > size || sz_pl > size - off_pl ||
	    off_lk < off_pl + sz_pl || off_lk > size || sz_lk > size - off_lk ||
	    n > size - off_lk - sz_lk)
		return 0;
	volatile uint8_t *dst = (volatile uint8_t *)(uintptr_t)(buf + off_lk + sz_lk);
	for (uint32_t i = 0; i < n; i++)
		dst[i] = (uint8_t)text[i];
	barrier();
	*(volatile uint32_t *)(h + PL_LK_SZ_LK) = sz_lk + n;
	barrier();
	return 1;
}

static char *put_hex(char *p, uint64_t v)
{
	static const char hex[] = "0123456789abcdef";
	for (int i = 60; i >= 0; i -= 4)
		*p++ = hex[(v >> i) & 15];
	return p;
}

void logstore_mark(const char *tag, uint64_t a, uint64_t b)
{
	if (!tag)
		return;
	char line[96];
	char *p = line;
	static const char prefix[] = "SWEET-SHIM ";
	for (unsigned i = 0; i < sizeof(prefix) - 1; i++)
		*p++ = prefix[i];
	/* Tags are bounded even when a caller supplies an unexpected string. */
	for (unsigned i = 0; i < 32 && tag[i]; i++)
		*p++ = tag[i];
	*p++ = ' '; *p++ = 'a'; *p++ = '=';
	p = put_hex(p, a);
	*p++ = ' '; *p++ = 'b'; *p++ = '=';
	p = put_hex(p, b);
	*p++ = '\n';
	logstore_write(line, (uint32_t)(p - line));
}

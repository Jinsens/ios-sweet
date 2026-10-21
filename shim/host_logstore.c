/* cc -DLOGSTORE_HOST_TEST -Ishim shim/host_logstore.c shim/logstore.c -o ... */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "board.h"
#include "logstore.h"

struct board brd;
static uint32_t header[64];
static unsigned char text[0x1000];
static unsigned char before[sizeof(text)];
static uint32_t header_before[64];

static void setup(void)
{
	memset(header, 0, sizeof(header));
	memset(text, 0x5a, sizeof(text));
	brd.log_store = (uintptr_t)header;
	brd.log_store_size = sizeof(header);
	header[0] = 0xabcd1234;
	header[0x14 / 4] = (uint32_t)(uintptr_t)text;
	header[0x18 / 4] = (uint32_t)((uint64_t)(uintptr_t)text >> 32);
	header[0x1c / 4] = sizeof(text);
	header[0x40 / 4] = 0xcdab3412;
	header[0x44 / 4] = sizeof(text);
	header[0x48 / 4] = 0x400;
	header[0x4c / 4] = 0x100;
	header[0x54 / 4] = 0x500;
	header[0x58 / 4] = 0x10;
}

static void reject(void)
{
	memcpy(before, text, sizeof(text));
	memcpy(header_before, header, sizeof(header));
	assert(!logstore_write("FAIL", 4));
	assert(!memcmp(before, text, sizeof(text)));
	assert(!memcmp(header_before, header, sizeof(header)));
}

int main(void)
{
	setup();
	memcpy(header_before, header, sizeof(header));
	assert(logstore_write("hello", 5));
	assert(!memcmp(text + 0x510, "hello", 5));
	assert(text[0x50f] == 0x5a && text[0x515] == 0x5a);
	header_before[0x58 / 4] += 5;
	assert(!memcmp(header_before, header, sizeof(header)));
	logstore_mark("test", 1, 2);
	const char *want = "SWEET-SHIM test a=0000000000000001 b=0000000000000002\n";
	assert(!memcmp(text + 0x515, want, strlen(want)));
	char long_tag[1000]; memset(long_tag, 'x', sizeof(long_tag));
	long_tag[sizeof(long_tag)-1] = 0;
	logstore_mark(long_tag, 0, 0); /* stack bounds under ASan */
	setup(); brd.log_store = 0; reject();
	setup(); brd.log_store_size = 0x58; reject();
	setup(); header[0] = 0; reject();
	setup(); header[0x40 / 4] = 0; reject();
	setup(); header[0x44 / 4] = 0x2000; reject();
	setup(); header[0x54 / 4] = 0x4ff; reject();
	setup(); header[0x58 / 4] = 0xffffffff; reject();
	setup(); header[0x4c / 4] = 0xffffffff; reject();
	setup(); header[0x58 / 4] = sizeof(text) - 0x500; reject();
	setup(); header[0x14 / 4] = header[0x18 / 4] = 0xffffffff; reject();
	setup(); assert(!logstore_write(NULL, 1));
	assert(!logstore_write("", 0));
	puts("logstore: append-only and malformed/full/overflow/tag-bounds cases PASS");
	return 0;
}

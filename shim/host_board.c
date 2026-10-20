/* Host check of the device-tree reader against a real dumped FDT:
 *   gcc -O1 -Wall -I. -o out/host_board host_board.c board.c fdt.c && \
 *   ./out/host_board research/matisse/matisse-recovery.dtb
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "board.h"
#include "fdt.h"

int main(int argc, char **argv)
{
	setvbuf(stdout, NULL, _IONBF, 0);
	if (argc < 2) {
		fprintf(stderr, "usage: %s DTB\n", argv[0]);
		return 2;
	}
	FILE *f = fopen(argv[1], "rb");
	if (!f) {
		perror(argv[1]);
		return 2;
	}
	fseek(f, 0, SEEK_END);
	long n = ftell(f);
	fseek(f, 0, SEEK_SET);
	void *buf = malloc(n);
	if (fread(buf, 1, n, f) != (size_t)n) {
		fprintf(stderr, "short read\n");
		return 2;
	}
	fclose(f);

	printf("magic %08x size %ld\n", fdt_be32((uintptr_t)buf), n);
	if (!board_probe((uintptr_t)buf)) {
		printf("board_probe failed\n");
		return 1;
	}
	printf("board      %s\n", brd.name);
	printf("fb         %#llx size %#llx %ux%u stride %u\n",
	       (unsigned long long)brd.fb_base, (unsigned long long)brd.fb_size,
	       brd.fb_w, brd.fb_h, brd.fb_stride);
	printf("gic        d %#llx r %#llx size %#llx stride %#llx\n",
	       (unsigned long long)brd.gicd, (unsigned long long)brd.gicr,
	       (unsigned long long)brd.gicr_size, (unsigned long long)brd.gicr_stride);
	printf("timer      virt %u phys %u sgi %u\n", brd.vtimer_intid, brd.ptimer_intid,
	       brd.sgi_intid);
	printf("ufs        %#llx\n", (unsigned long long)brd.ufs_base);
	printf("dram       %#llx size %#llx\n", (unsigned long long)brd.dram_base,
	       (unsigned long long)brd.dram_size);

	uint64_t a, b;
	if (fdt_initrd((uintptr_t)buf, &a, &b))
		printf("initrd     %#llx..%#llx\n", (unsigned long long)a,
		       (unsigned long long)b);
	else
		printf("initrd     absent\n");

	const void *v;
	uint32_t len;
	if (fdt_prop((uintptr_t)buf, "/chosen", "atag,videolfb-lcmname", &v, &len))
		printf("lcm        %s\n", (const char *)v);
	if (fdt_prop((uintptr_t)buf, "/chosen", "bootargs", &v, &len))
		printf("bootargs   %.120s\n", (const char *)v);
	if (fdt_reg((uintptr_t)buf, "/reserved-memory/mblock-14-framebuffer", &a, &b))
		printf("fb rsv     %#llx + %#llx\n", (unsigned long long)a,
		       (unsigned long long)b);
	return 0;
}

/* Render the console layout on the host: ./out/host_test && view out/host.ppm */
#include <stdio.h>
#include "console.h"

#define HW 1440
#define HH 3200
#define HS (HW * 4)

static uint32_t host_fb[HH * HS / 4];

int main(void)
{
	con_fb = host_fb;
	con_w = HW;
	con_h = HH;
	con_stride = HS;
	con_bgr = 0;
	con_clear(RED);
	con_str("SHIM", "sweet/matisse xnu loader");
	con_str("BOARD", "matisse");
	con_hex2("FB", "base=", 0xfcb3f000, "size=", 0x34c0000);
	con_hex("MMFR0", 0x0000000000101122ULL);
	con_hex2("PSCI", "ver=", 0x10001, "smccc=", 0x10002);
	con_hex2("CPU7", "ret=", 0, "mpidr=", 0x81000700);
	con_clear(BLUE);
	con_str("PANIC", "sync exception");
	con_hex("ESR", 0x96000010);
	FILE *f = fopen("out/host.ppm", "wb");
	fprintf(f, "P6 %d %d 255\n", HW, HH);
	for (int i = 0; i < HW * HH; i++) {
		unsigned char px[3] = { host_fb[i] >> 16, host_fb[i] >> 8, host_fb[i] };
		fwrite(px, 1, 3, f);
	}
	fclose(f);
	return 0;
}

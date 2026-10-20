/*
 * The few libc bits the compiler may call. Compiled with -fno-builtin so the
 * loops below are not themselves turned into calls.
 */
#include <stdint.h>

int strcmp(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return (unsigned char)*a - (unsigned char)*b;
}

unsigned long strlen(const char *s)
{
	const char *p = s;

	while (*p)
		p++;
	return (unsigned long)(p - s);
}

void *memcpy(void *d, const void *s, unsigned long n)
{
	uint8_t *dp = d;
	const uint8_t *sp = s;

	while (n--)
		*dp++ = *sp++;
	return d;
}

void *memset(void *d, int c, unsigned long n)
{
	uint8_t *dp = d;

	while (n--)
		*dp++ = (uint8_t)c;
	return d;
}

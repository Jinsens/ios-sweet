/*
 * Minimal text console on the framebuffer the bootloader left scanning out.
 * With the MMU off every access is Device memory, so stay aligned
 * (-mstrict-align) and only touch 32-bit words.
 *
 * The same text is kept in a flat buffer so the shim can also drop it into a
 * raw partition when the panel turns out not to be live (MediaTek LK does
 * not always leave the DSI link up for a non-Android kernel).
 */
#include "console.h"
#include "font.h"

#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	8
#define TEXT_MAX	8192

volatile uint32_t *con_fb;
uint32_t con_w = 1080, con_h = 2400, con_stride = 1080 * 4;
int con_bgr;

static uint32_t con_bg;
static int row;
static char text[TEXT_MAX];
static uint32_t text_len;

static void text_put(const char *s)
{
	while (*s && text_len < TEXT_MAX - 1)
		text[text_len++] = *s++;
	text[text_len] = 0;
}

void con_text_reset(void)
{
	text_len = 0;
	text[0] = 0;
}

const char *con_text(uint32_t *len)
{
	*len = text_len;
	return text;
}

/* Memory order for a logical colour. */
static uint32_t pix(uint32_t c)
{
	if (!con_bgr)
		return c;
	return (c & 0xff00ff00U) | ((c & 0xffU) << 16) | ((c >> 16) & 0xffU);
}

void con_clear(uint32_t bg)
{
	uint32_t c = pix(bg);

	if (!con_fb)
		return;

	for (uint32_t y = 0; y < con_h; y++) {
		volatile uint32_t *line = con_fb + (uint64_t)y * (con_stride / 4);
		for (uint32_t x = 0; x < con_w; x++)
			line[x] = c;
	}
	con_bg = bg;
	row = 0;
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (!con_fb)
		return;
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	uint32_t cf = pix(fg), cb = pix(con_bg);

	if (x0 + CH_W > (int)con_w || y0 + CH_H > (int)con_h)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? cf : cb;
			for (int sy = 0; sy < SCALE; sy++) {
				volatile uint32_t *p = con_fb +
					(uint64_t)(y0 + gy * SCALE + sy) * (con_stride / 4) +
					x0 + gx * SCALE;
				for (int sx = 0; sx < SCALE; sx++)
					p[sx] = c;
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

void con_skip(void)
{
	row++;
	text_put("\n");
}

void con_str(const char *label, const char *s)
{
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	row++;
	text_put(label);
	text_put(" ");
	text_put(s);
	text_put("\n");
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	row++;
	text_put(label);
	text_put(" ");
	text_put(a);
	fmt_hex(buf, va, 8);
	text_put(buf);
	if (b) {
		text_put(" ");
		text_put(b);
		fmt_hex(buf, vb, 8);
		text_put(buf);
	}
	text_put("\n");
}

void con_target(volatile uint32_t *fb)
{
	con_fb = fb;
}

/*
 * Clear and draw the stored text. Labels (the first word of a line) are
 * yellow, the rest white, matching con_str/con_hex2 closely enough to read.
 */
void con_redraw(uint32_t bg)
{
	con_clear(bg);
	for (uint32_t i = 0; i < text_len;) {
		uint32_t start = i, split = i;

		while (i < text_len && text[i] != '\n')
			i++;
		uint32_t end = i;
		if (i < text_len)
			i++;
		if (end == start)
			continue;
		for (uint32_t k = start; k < end; k++)
			if (text[k] == ' ') {
				split = k;
				break;
			}
		int col = 0;
		for (uint32_t k = start; k < split; k++)
			put_char(col++, text[k], YELLOW);
		if (split < end) {
			col = col < LABEL_COLS ? LABEL_COLS : col + 1;
			for (uint32_t k = split + 1; k < end; k++)
				put_char(col++, text[k], WHITE);
		}
		row++;
	}
}

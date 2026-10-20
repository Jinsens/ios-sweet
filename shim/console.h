#pragma once
#include <stdint.h>

#define RED	0xffff0000U
#define BLUE	0xff0000a0U
#define WHITE	0xffffffffU
#define YELLOW	0xffffff00U
#define GREEN	0xff00ff00U

/* Set by board_probe() before the first con_* call. */
extern volatile uint32_t *con_fb;
extern uint32_t con_w, con_h, con_stride;
extern int con_bgr;		/* 1 = memory order B,G,R,A */

void con_clear(uint32_t bg);
void con_skip(void);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
/* label, then "a=xxxxxxxx b=xxxxxxxx" style pairs of 32-bit values */
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);

/* Plain text copy of everything printed (for the UFS log ring). */
const char *con_text(uint32_t *len);
void con_text_reset(void);

/* Draw the stored text again into another framebuffer (MTK rotates between
 * three scanout buffers, so the visible one is not necessarily the first). */
void con_target(volatile uint32_t *fb);
void con_redraw(uint32_t bg);

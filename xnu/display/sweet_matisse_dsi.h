/*
 * MT6983 (matisse) command-mode DSI refresh.
 *
 * LK leaves the panel powered and initialized: rails on, PLL/HS clock on,
 * DSC command-mode PSCTRL/SIZE_CON/TXRX_CTRL programmed, the DSI cmd-mode
 * buffer enabled, the OVL scanning the boot framebuffer and the mutex MOD
 * bits configured. Only the DSI engine is disabled and the mutex is stopped.
 *
 * A frame is pushed by pulsing the mutex and writing DSI_START (a 0->1
 * strobe). MUTEX_EN is a level, not a strobe: while it is 1 the mutex keeps
 * triggering frames on its own, which is why the driver stops it before it
 * moves the overlay and (sweetfb_hold) stops it again once a frame is out.
 * Command-mode completion is the DSIM busy bit (DSI_INTSTA bit31), not
 * FRAME_DONE: rubens-s-oss mtk_dsi_poll_for_idle() waits on DSI_BUSY.
 * The refresh path therefore polls DSI_BUSY with a bound and skips the frame
 * if the previous transfer is still leaving the DSIM, instead of overwriting
 * an in-flight frame (which produces a partial/corrupt panel).
 */
#ifndef _ARM64_SWEET_MATISSE_DSI_H_
#define _ARM64_SWEET_MATISSE_DSI_H_

#include <stdint.h>
#include <pexpert/arm64/board_config.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(SWEET)
/* fb_phys/width/height/stride describe the Boot_Video surface the console
 * draws into. Must be called after ml_io_map() is usable (serial_init). */
void sweet_display_init(uint64_t fb_phys, uint32_t width, uint32_t height,
    uint32_t stride);
/* Push the current framebuffer to the panel. Cheap no-op when inactive or
 * called again within 200 ms unless force is non-zero. */
void sweet_display_refresh(int force);

/* Show another surface instead of the boot framebuffer: point layer 0 of both
 * overlays at phys (pitch bytes a row, the right half starting half a row in)
 * and push it. Returns 0 when the transfer was started; non-zero when the
 * engine was busy, the address did not read back, or the driver never came
 * up, in which case the caller still has to copy. */
int sweet_display_scanout(uint64_t phys, uint32_t pitch);

/* Record the console's live mapping and translation attributes for later
 * diagnostics. Does not read pixels or trigger a transfer. */
void sweet_display_set_target(uint64_t va, uint64_t phys, uint32_t stride,
    uint32_t height);

/* Why the panel is (or is not) live: filled by sweet_display_init() long
 * before the UFS progress-mark channel exists, so bsd/dev/sweet_ufs.c dumps
 * it once that channel is up. See the table in sweet_matisse_dsi.c. */
extern uint32_t sweet_disp_trace[16];
extern uint32_t sweet_disp_calls;
extern uint32_t sweet_disp_pushes;
extern uint32_t sweet_disp_busy_skips;
extern uint32_t sweet_disp_console_flips;
extern uint32_t sweet_disp_gui_flips;
extern uint32_t sweet_disp_waits;
extern uint64_t sweet_disp_wait_ticks;
extern uint64_t sweet_disp_send_ticks;
extern uint64_t sweet_disp_send_max;
extern uint32_t sweet_disp_sends;
extern uint32_t sweet_disp_sof_started;
extern uint32_t sweet_disp_sof_missed;
extern uint32_t sweet_disp_te_seen;
extern uint32_t sweet_disp_te_missed;
extern uint64_t sweet_disp_te_ticks;
extern uint32_t sweet_disp_te_gaps;
/* Edges dropped because they were older than the frame being sent, and how
 * long the wait for a fresh one took: the first says the panel was already
 * scanning when the frame was ready, the second what waiting costs. */
extern uint32_t sweet_disp_te_stale;
extern uint32_t sweet_disp_te_waits;
extern uint64_t sweet_disp_te_wait_ticks;
extern uint64_t sweet_disp_te_wait_max;
/* Zero sends a frame whether or not the panel is between scans; one (the
 * default, and what keeps the picture whole) waits for a fresh edge. */
extern int sweet_disp_te_sync;
/* One makes every scanout wait for the panel to finish receiving the frame
 * before returning; zero (the default) lets the compositor carry on while
 * the panel reads. */
extern int sweet_disp_wait_done;
/* One moves the overlay to the new surface at a frame boundary, which is
 * what keeps one frame out of the next; zero moves it as soon as it is
 * asked. */
extern int sweet_disp_frame_sync;
extern uint32_t sweet_disp_frame_seen;
extern uint32_t sweet_disp_frame_missed;
extern uint64_t sweet_disp_frame_wait_ticks;
extern uint64_t sweet_disp_frame_wait_max;
/* Frames that would have started while the overlay was being moved, and how
 * many times the frame source had to be stopped for that. A non-zero first
 * count is what the 2494 probe showed from the other side: the mutex keeps
 * triggering frames on its own. */
extern uint32_t sweet_disp_underruns;
extern uint32_t sweet_disp_te_edges;
extern uint32_t sweet_disp_te_lows;
extern uint32_t sweet_disp_dcs;
/* sweetfb_teoff=1 sends DCS 0x34 once, turning the panel's tear line off. */
extern int sweet_disp_te_off;
/* sweetfb_teext=1 enables the DSIM's external TE input. */
extern int sweet_disp_te_ext;
/* sweetfb_teon=1 sends DCS 0x35 once; sweetfb_sline=1 samples DCS 0x45. */
extern int sweet_disp_te_on;
extern int sweet_disp_sline;
extern uint32_t sweet_disp_scan[5];
/* sweetfb_teint=1 enables the DSIM TE interrupt before sampling it. */
extern int sweet_disp_te_int;
extern uint32_t sweet_disp_mutex_stops;
extern uint32_t sweet_disp_mutex_running;
extern uint32_t sweet_disp_mutex_last;
/* sweetfb_hold=1 keeps the frame source stopped between presents. */
extern int sweet_disp_hold;
extern uint32_t sweet_disp_hold_frames;
extern uint64_t sweet_disp_hold_ticks;
extern uint32_t sweet_disp_status;
extern uint32_t sweet_disp_status_before;
extern uint32_t sweet_disp_cmdq[4];
extern uint64_t sweet_disp_target[5];
#else
static inline void sweet_display_init(uint64_t fb_phys, uint32_t width,
    uint32_t height, uint32_t stride)
{
	(void)fb_phys; (void)width; (void)height; (void)stride;
}
static inline void sweet_display_refresh(int force) { (void)force; }
static inline int sweet_display_scanout(uint64_t phys, uint32_t pitch)
{
	(void)phys; (void)pitch; return -1;
}
static inline void sweet_display_set_target(uint64_t va, uint64_t phys,
    uint32_t stride, uint32_t height)
{
	(void)va; (void)phys; (void)stride; (void)height;
}
#endif

#ifdef __cplusplus
}
#endif

#endif /* _ARM64_SWEET_MATISSE_DSI_H_ */

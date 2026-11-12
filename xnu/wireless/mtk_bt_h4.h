/* MT6983 BTIF carries raw H:4; no STP envelope is used by this platform. */
#ifndef SWEET_MTK_BT_H4_H
#define SWEET_MTK_BT_H4_H

#include <stddef.h>
#include <stdint.h>

enum mtk_bt_h4_result {
    MTK_BT_H4_OK = 0,
    MTK_BT_H4_ARGUMENT = -1,
    MTK_BT_H4_INDICATOR = -2,
    MTK_BT_H4_OVERSIZE = -3,
    MTK_BT_H4_TRUNCATED = -4
};

/* The callback borrows a complete frame INCLUDING its H:4 indicator until it
 * returns. It must neither retain that pointer nor reenter this decoder.
 * The owner serializes feed/reset, and supplies storage outside incoming data.
 */
typedef void (*mtk_bt_h4_receive)(void *, const uint8_t *, size_t);

struct mtk_bt_h4 {
    uint8_t *storage;
    size_t capacity;
    size_t used;
    size_t expected;
    size_t header_bytes;
    int error;
    mtk_bt_h4_receive receive;
    void *context;
};

int mtk_bt_h4_init(struct mtk_bt_h4 *, uint8_t *, size_t,
    mtk_bt_h4_receive, void *);
void mtk_bt_h4_reset(struct mtk_bt_h4 *);
/* Frames larger than capacity and unknown indicators latch an error. No bytes
 * following an invalid header are searched for a new frame: reset only after
 * resetting/draining the underlying transport. consumed may be NULL.
 */
int mtk_bt_h4_feed(struct mtk_bt_h4 *, const uint8_t *, size_t, size_t *consumed);
/* Call only at an actual end of stream, not between BTIF fragments. */
int mtk_bt_h4_finish(struct mtk_bt_h4 *);

/* WMT FUNC_CTRL command and event include the H:4 indicator. */
#define MTK_BT_WMT_POWER_COMMAND_BYTES 10u
int mtk_bt_wmt_power_command(uint8_t *, size_t, unsigned on);
/* A well-formed event returns 0 and writes controller status (0 = success).
 * Nonmatching/malformed events return -1 and leave status untouched.
 */
int mtk_bt_wmt_power_event(const uint8_t *, size_t, uint8_t *status);

#endif

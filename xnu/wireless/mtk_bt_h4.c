/* Protocol references: MiCode/MTK_kernel_modules, rubens-s-oss,
 * connectivity/bt/mt66xx/{btmtk_main.c,include/btmtk_define.h,
 * btif/btmtk_mt66xx.c,btif/btmtk_btif_main.c}.
 * H:4 length fields and ISO's 14-bit length follow h4_recv_buf().
 */
#include "mtk_bt_h4.h"

static void
clear_frame(struct mtk_bt_h4 *decoder)
{
    decoder->used = 0;
    decoder->expected = 0;
    decoder->header_bytes = 0;
}

int
mtk_bt_h4_init(struct mtk_bt_h4 *decoder, uint8_t *storage, size_t capacity,
    mtk_bt_h4_receive receive, void *context)
{
    if (!decoder)
        return MTK_BT_H4_ARGUMENT;
    decoder->storage = storage;
    decoder->capacity = capacity;
    decoder->receive = receive;
    decoder->context = context;
    clear_frame(decoder);
    decoder->error = (!storage || capacity < 5 || !receive) ?
        MTK_BT_H4_ARGUMENT : MTK_BT_H4_OK;
    return decoder->error;
}

void
mtk_bt_h4_reset(struct mtk_bt_h4 *decoder)
{
    if (!decoder)
        return;
    clear_frame(decoder);
    decoder->error = (!decoder->storage || decoder->capacity < 5 ||
        !decoder->receive) ? MTK_BT_H4_ARGUMENT : MTK_BT_H4_OK;
}

static size_t
payload_bytes(const struct mtk_bt_h4 *decoder)
{
    const uint8_t *frame = decoder->storage;
    switch (frame[0]) {
    case 0x02: /* ACL */
        return (size_t)frame[3] | ((size_t)frame[4] << 8);
    case 0x03: /* SCO */
        return frame[3];
    case 0x04: /* Event */
        return frame[2];
    case 0x05: /* ISO, length excludes upper two bits */
        return (size_t)frame[3] | ((size_t)(frame[4] & 0x3f) << 8);
    default:
        return 0; /* feed already rejected unknown indicators */
    }
}

int
mtk_bt_h4_feed(struct mtk_bt_h4 *decoder, const uint8_t *data, size_t length,
    size_t *consumed)
{
    size_t position = 0;
    if (consumed)
        *consumed = 0;
    if (!decoder)
        return MTK_BT_H4_ARGUMENT;
    if (decoder->error)
        return decoder->error;
    if (!data && length) {
        decoder->error = MTK_BT_H4_ARGUMENT;
        return decoder->error;
    }
    while (position < length) {
        if (decoder->used == 0) {
            uint8_t indicator = data[position++];
            switch (indicator) {
            case 0x02: case 0x05: decoder->header_bytes = 5; break;
            case 0x03: decoder->header_bytes = 4; break;
            case 0x04: decoder->header_bytes = 3; break;
            default:
                decoder->error = MTK_BT_H4_INDICATOR;
                goto done;
            }
            decoder->storage[0] = indicator;
            decoder->used = 1;
            decoder->expected = decoder->header_bytes;
        }
        while (position < length && decoder->used < decoder->expected)
            decoder->storage[decoder->used++] = data[position++];
        if (decoder->used != decoder->expected)
            break;
        if (decoder->expected == decoder->header_bytes) {
            size_t payload = payload_bytes(decoder);
            if (payload > decoder->capacity - decoder->header_bytes) {
                decoder->error = MTK_BT_H4_OVERSIZE;
                goto done;
            }
            decoder->expected = decoder->header_bytes + payload;
            if (payload)
                continue;
        }
        decoder->receive(decoder->context, decoder->storage, decoder->used);
        clear_frame(decoder);
    }
done:
    if (consumed)
        *consumed = position;
    return decoder->error;
}

int
mtk_bt_h4_finish(struct mtk_bt_h4 *decoder)
{
    if (!decoder)
        return MTK_BT_H4_ARGUMENT;
    if (!decoder->error && decoder->used)
        decoder->error = MTK_BT_H4_TRUNCATED;
    return decoder->error;
}

int
mtk_bt_wmt_power_command(uint8_t *output, size_t capacity, unsigned on)
{
    static const uint8_t command[MTK_BT_WMT_POWER_COMMAND_BYTES] =
        {0x01, 0x6f, 0xfc, 0x06, 0x01, 0x06, 0x02, 0x00, 0x00, 0x00};
    size_t i;
    if (!output || capacity < sizeof(command) || on > 1)
        return -1;
    for (i = 0; i < sizeof(command); i++)
        output[i] = command[i];
    output[9] = (uint8_t)on;
    return 0;
}

int
mtk_bt_wmt_power_event(const uint8_t *frame, size_t length, uint8_t *status)
{
    if (!frame || !status || length != 8 ||
        frame[0] != 0x04 || frame[1] != 0xe4 || frame[2] != 0x05 ||
        frame[3] != 0x02 || frame[4] != 0x06 ||
        frame[5] != 0x01 || frame[6] != 0x00)
        return -1;
    *status = frame[7];
    return 0;
}

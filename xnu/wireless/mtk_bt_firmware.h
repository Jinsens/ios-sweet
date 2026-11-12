#ifndef SWEET_MTK_BT_FIRMWARE_H
#define SWEET_MTK_BT_FIRMWARE_H

#include <stddef.h>
#include <stdint.h>

#define MTK_BT_EMI_HEADER_BYTES 48u
enum mtk_bt_patch_result {
    MTK_BT_PATCH_OK = 0,
    MTK_BT_PATCH_ARGUMENT = -1,
    MTK_BT_PATCH_TRUNCATED = -2,
    MTK_BT_PATCH_ADDRESS = -3,
    MTK_BT_PATCH_RANGE = -4,
    MTK_BT_PATCH_CHECKSUM = -5
};

struct mtk_bt_patch {
    /* Borrowed bytes after the outer EMI header, INCLUDING the inner 48-byte
     * MCU/BT header. No allocation, mapping, copy or register write is done.
     */
    const uint8_t *payload;
    size_t payload_bytes;
    uint32_t emi_offset;
    uint32_t firmware_address;
    uint32_t subsystem;
    /* Kept raw: vendor logs byte-swapped versions but selection must inspect
     * the exact board/ECO contract rather than infer it from these bytes. */
    uint8_t hardware_version[2];
    uint8_t software_version[2];
    uint8_t datetime[16];
    uint8_t platform[4];
};

/* bytes must reference length bytes (NULL is valid only for length zero). */
uint16_t mtk_bt_patch_checksum(const uint8_t *, size_t);
/* Validates format, vendor checksum, and the specific subsystem's
 * caller-approved window inside its reserved Conninfra EMI allocation. This checksum
 * is consistency checking, not firmware authentication. The owner must keep
 * the validated image immutable through copying it into EMI.
 * output is untouched on failure. emi_bytes is allocation size, not PA.
 * window_offset/window_bytes must come from the board's firmware load plan.
 * The old vendor's disabled MCU/BT window constants do not describe the actual
 * matisse BT blobs (their offset is 0x5bc00, not 0x100000).
 */
int mtk_bt_patch_parse(const uint8_t *, size_t, size_t window_offset,
    size_t window_bytes, size_t emi_bytes, struct mtk_bt_patch *output);

#endif

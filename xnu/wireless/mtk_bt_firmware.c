/* EMI format and checksum: MiCode/MTK_kernel_modules,
 * rubens-s-oss, connectivity/bt/mt66xx/btif/btmtk_mt66xx.c.
 * The vendor copies bytes after the outer 48-byte header to EMI. Its disabled
 * region checks are replaced by caller-approved bounds before any copy.
 */
#include "mtk_bt_firmware.h"

static uint32_t
signed_high_fold(uint32_t sum)
{
    /* The vendor uses int32_t and arithmetic >>16 after the sign bit sets.
     * Match its on-disk checksum without C signed-overflow/shift assumptions.
     * Plain unsigned Internet-checksum folding differs on actual BT images.
     */
    int32_t high = (int32_t)(sum >> 16);
    if (sum & 0x80000000u)
        high -= 0x10000;
    return (sum & 0xffffu) + (uint32_t)high;
}

uint16_t
mtk_bt_patch_checksum(const uint8_t *bytes, size_t length)
{
    uint32_t sum = 0;
    while (length > 1) {
        sum += (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8);
        if (sum & 0x80000000u)
            sum = signed_high_fold(sum);
        bytes += 2;
        length -= 2;
    }
    if (length)
        sum += bytes[0];
    while (sum >> 16)
        sum = signed_high_fold(sum);
    return (uint16_t)~sum;
}

int
mtk_bt_patch_parse(const uint8_t *image, size_t length,
    size_t window_offset, size_t window_bytes, size_t emi_bytes,
    struct mtk_bt_patch *output)
{
    struct mtk_bt_patch patch = {0};
    uint16_t checksum;
    size_t i;
    if (!image || !output || window_offset > emi_bytes ||
        window_bytes > emi_bytes - window_offset)
        return MTK_BT_PATCH_ARGUMENT;
    /* Documented format contains an outer EMI and an inner MCU/BT header. */
    if (length < 2 * MTK_BT_EMI_HEADER_BYTES)
        return MTK_BT_PATCH_TRUNCATED;
    /* Firmware address is 0xFXXXXXXX. Only its middle two bytes are an EMI
     * offset in this protocol; reject discarded bits instead of aliasing an
     * unrepresentable address to an unrelated location in reserved memory.
     */
    if (image[24] != 0 || image[27] != 0xf0)
        return MTK_BT_PATCH_ADDRESS;
    patch.emi_offset = (uint32_t)image[25] << 8 |
        (uint32_t)image[26] << 16;
    patch.firmware_address = (uint32_t)image[24] | (uint32_t)image[25] << 8 |
        (uint32_t)image[26] << 16 | (uint32_t)image[27] << 24;
    patch.payload_bytes = length - MTK_BT_EMI_HEADER_BYTES;
    if (patch.emi_offset < window_offset ||
        patch.emi_offset - window_offset > window_bytes ||
        patch.payload_bytes > window_bytes - (patch.emi_offset - window_offset))
        return MTK_BT_PATCH_RANGE;
    patch.payload = image + MTK_BT_EMI_HEADER_BYTES;
    checksum = (uint16_t)image[46] | ((uint16_t)image[47] << 8);
    if (mtk_bt_patch_checksum(patch.payload, patch.payload_bytes) != checksum)
        return MTK_BT_PATCH_CHECKSUM;
    patch.subsystem = (uint32_t)image[28] | (uint32_t)image[29] << 8 |
        (uint32_t)image[30] << 16 | (uint32_t)image[31] << 24;
    for (i = 0; i < sizeof(patch.datetime); i++)
        patch.datetime[i] = image[i];
    for (i = 0; i < sizeof(patch.platform); i++)
        patch.platform[i] = image[16 + i];
    for (i = 0; i < sizeof(patch.hardware_version); i++) {
        patch.hardware_version[i] = image[20 + i];
        patch.software_version[i] = image[22 + i];
    }
    *output = patch;
    return MTK_BT_PATCH_OK;
}

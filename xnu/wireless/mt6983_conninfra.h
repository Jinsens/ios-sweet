#ifndef SWEET_MT6983_CONNINFRA_H
#define SWEET_MT6983_CONNINFRA_H

#include <stdint.h>

/* AP-side prerequisites shared by MT6983 WLAN and Bluetooth.
 * Independently written from MediaTek's published register descriptions:
 * MiCode/MTK_kernel_modules, rubens-s-oss,
 * c66b3c9ce7b5c0bc3d72a829f6542200deaa8a9c:
 * connectivity/conninfra/platform/mt6983/mt6983_pos_gen.c:
 *   consys_conninfra_on_power_ctrl_mt6983_gen() (power/reset/clock bits)
 * connectivity/conninfra/platform/mt6983/mt6983_consys_reg.c:
 *   consys_check_conninfra_on_domain() (AP-to-CONN RX/TX protection)
 * connectivity/conninfra/platform/mt6983/include/mt6983_pos_gen.h:
 *   CONN_PWR_CON=SPM+0xe04; protection=INFRACFG_AO+0xc9c/+0xc5c.
 *
 * Capture never writes and never accesses 0x18000000..0x18ffffff. Open AP
 * gates do NOT establish that CONN off-domain clocks, firmware or radios
 * work. The vendor off-domain readability check resets a clock detector;
 * a read-only snapshot cannot replace that fresh clock check.
 */
#define MT6983_CONNINFRA_PWR_CON_PA UINT64_C(0x1c001e04)
#define MT6983_CONNINFRA_RX_PROTECT_PA UINT64_C(0x10001c9c)
#define MT6983_CONNINFRA_TX_PROTECT_PA UINT64_C(0x10001c5c)

#define MT6983_CONNINFRA_VALID_POWER (1u << 0)
#define MT6983_CONNINFRA_VALID_RX (1u << 1)
#define MT6983_CONNINFRA_VALID_TX (1u << 2)
#define MT6983_CONNINFRA_VALID_ALL (MT6983_CONNINFRA_VALID_POWER | \
    MT6983_CONNINFRA_VALID_RX | MT6983_CONNINFRA_VALID_TX)

#define MT6983_CONNINFRA_BLOCK_POWER (1u << 0)
#define MT6983_CONNINFRA_BLOCK_RESET (1u << 1)
#define MT6983_CONNINFRA_BLOCK_ISOLATION (1u << 2)
#define MT6983_CONNINFRA_BLOCK_CLOCK (1u << 3)
#define MT6983_CONNINFRA_BLOCK_RX (1u << 4)
#define MT6983_CONNINFRA_BLOCK_TX (1u << 5)

enum mt6983_conninfra_state {
    MT6983_CONNINFRA_INVALID = 0,
    MT6983_CONNINFRA_READ_ERROR = 1,
    MT6983_CONNINFRA_HOST_BLOCKED = 2,
    MT6983_CONNINFRA_HOST_GATES_OPEN = 3
};

enum mt6983_conninfra_result {
    MT6983_CONNINFRA_OK = 0,
    MT6983_CONNINFRA_ARGUMENT = -1,
    MT6983_CONNINFRA_IO = -2
};

struct mt6983_conninfra_io {
    void *opaque;
    /* Return zero only after storing a successfully read 32-bit value.
     * The caller owns mapping, ordering, and the hardware's lifetime.
     * Failed reads stop capture immediately; callback error codes are
     * preserved in read_error. Capture makes at most three calls.
     */
    int (*read32)(void *opaque, uint64_t physical, uint32_t *value);
};

struct mt6983_conninfra_report {
    uint32_t state;
    uint32_t valid_mask;
    uint32_t blockers;
    uint32_t conn_pwr_con;
    uint32_t rx_protect;
    uint32_t tx_protect;
    uint32_t firmware_ready; /* Always zero: firmware is not inspected. */
    int32_t read_error;
    uint64_t failed_address;
};

/* The output is initialized on every call when report is non-NULL.
 * Complete capture returns OK, even when host gates are blocked.
 * Only valid_mask fields contain successfully observed register data.
 * io/report must not overlap and the callback must not modify report.
 */
int mt6983_conninfra_capture(const struct mt6983_conninfra_io *io,
    struct mt6983_conninfra_report *report);

#endif

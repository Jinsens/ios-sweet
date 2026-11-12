/* MT6983 wireless bring-up entry. No power/clock/EMI or radio writes. */
#if defined(ARM64_BOARD_CONFIG_SWEET)
#include <sys/param.h>
#include <sys/systm.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include "mt6983_conninfra.h"

extern vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, unsigned int);
extern void ml_io_unmap(vm_offset_t, vm_size_t);
void SweetWirelessStart(void);

#define WIRELESS_IO_PAGE 0x4000u
#define WIRELESS_AP_PA 0x10000000u
#define WIRELESS_SPM_PA 0x1c000000u
#define WIRELESS_VM_WIMG_IO 0x7u

struct wireless_maps {
    vm_offset_t ap;
    vm_offset_t spm;
};

static uint32_t
wireless_mmio_read32(vm_offset_t address)
{
    uint32_t value = *(volatile uint32_t *)address;
    __asm__ volatile("dmb oshld" ::: "memory");
    return value;
}

static int
wireless_read32(void *opaque, uint64_t physical, uint32_t *value)
{
    struct wireless_maps *maps = opaque;
    vm_offset_t address;

    /* A second allowlist at the MMIO boundary. No CONN bus mapping exists. */
    switch (physical) {
    case MT6983_CONNINFRA_PWR_CON_PA:
        address = maps->spm + (physical - WIRELESS_SPM_PA);
        break;
    case MT6983_CONNINFRA_RX_PROTECT_PA:
    case MT6983_CONNINFRA_TX_PROTECT_PA:
        address = maps->ap + (physical - WIRELESS_AP_PA);
        break;
    default:
        return -1;
    }
    *value = wireless_mmio_read32(address);
    return 0;
}

static bool
wireless_is_matisse(void)
{
    DTEntry root;
    const char *list;
    unsigned int bytes;
    bool board = false, soc = false;

    if (SecureDTLookupEntry(NULL, "/", &root) != kSuccess ||
        SecureDTGetProperty(root, "compatible", (const void **)&list,
        &bytes) != kSuccess || bytes == 0 || bytes > 512) {
        return false;
    }
    while (bytes != 0) {
        size_t length = strnlen(list, bytes);
        if (length == bytes) {
            return false;
        }
        if (length == sizeof("xiaomi,matisse") - 1 &&
            memcmp(list, "xiaomi,matisse", length) == 0) {
            board = true;
        }
        if (length == sizeof("mediatek,mt6983") - 1 &&
            memcmp(list, "mediatek,mt6983", length) == 0) {
            soc = true;
        }
        list += length + 1;
        bytes -= (unsigned int)(length + 1);
    }
    return board && soc;
}

void
SweetWirelessStart(void)
{
    unsigned int mode = 0;
    struct wireless_maps maps = {0};
    struct mt6983_conninfra_report report;
    struct mt6983_conninfra_io io = { &maps, wireless_read32 };
    int error;

    /* Called exactly once by the platform expert. Disabled unless requested. */
    if (!PE_parse_boot_argn("sweetconn", &mode, sizeof(mode)) || mode == 0) {
        return;
    }
    if (mode != 1 || !wireless_is_matisse()) {
        printf("SWEET CONN: unsupported diagnostic mode/board\n");
        return;
    }
    maps.ap = ml_io_map_unmappable(WIRELESS_AP_PA, WIRELESS_IO_PAGE,
        WIRELESS_VM_WIMG_IO);
    maps.spm = ml_io_map_unmappable(WIRELESS_SPM_PA, WIRELESS_IO_PAGE,
        WIRELESS_VM_WIMG_IO);
    if (maps.ap == 0 || maps.spm == 0) {
        printf("SWEET CONN: AP register mapping failed\n");
    } else {
        error = mt6983_conninfra_capture(&io, &report);
        printf("SWEET CONN: AP-only rc=%d state=%u valid=%x blockers=%x "
            "pwr=%08x rx=%08x tx=%08x firmware=unprobed\n", error,
            report.state, report.valid_mask, report.blockers,
            report.conn_pwr_con, report.rx_protect, report.tx_protect);
    }
    if (maps.spm != 0) {
        ml_io_unmap(maps.spm, WIRELESS_IO_PAGE);
    }
    if (maps.ap != 0) {
        ml_io_unmap(maps.ap, WIRELESS_IO_PAGE);
    }
}
#endif

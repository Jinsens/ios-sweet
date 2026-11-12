#include "mt6983_conninfra.h"

int
mt6983_conninfra_capture(const struct mt6983_conninfra_io *io,
    struct mt6983_conninfra_report *report)
{
    struct mt6983_conninfra_report result = {0};
    static const uint64_t addresses[3] = {
        MT6983_CONNINFRA_PWR_CON_PA,
        MT6983_CONNINFRA_RX_PROTECT_PA,
        MT6983_CONNINFRA_TX_PROTECT_PA
    };
    uint32_t values[3] = {0};
    unsigned i;

    if (report == 0)
        return MT6983_CONNINFRA_ARGUMENT;
    *report = result;
    if (io == 0 || io->read32 == 0)
        return MT6983_CONNINFRA_ARGUMENT;

    for (i = 0; i < 3; i++) {
        int error = io->read32(io->opaque, addresses[i], &values[i]);
        if (error != 0) {
            result.state = MT6983_CONNINFRA_READ_ERROR;
            result.read_error = error;
            result.failed_address = addresses[i];
            *report = result;
            return MT6983_CONNINFRA_IO;
        }
        result.valid_mask |= 1u << i;
        if (i == 0)
            result.conn_pwr_con = values[i];
        else if (i == 1)
            result.rx_protect = values[i];
        else
            result.tx_protect = values[i];
    }

    /* Require both requests and both acknowledgements, plus released
     * reset/isolation and an enabled AP-to-CONN bus clock. Transitions are
     * reported conservatively as blocked; nothing here changes power.
     */
    if ((result.conn_pwr_con & 0xc000000cu) != 0xc000000cu)
        result.blockers |= MT6983_CONNINFRA_BLOCK_POWER;
    if ((result.conn_pwr_con & (1u << 0)) == 0)
        result.blockers |= MT6983_CONNINFRA_BLOCK_RESET;
    if ((result.conn_pwr_con & (1u << 1)) != 0)
        result.blockers |= MT6983_CONNINFRA_BLOCK_ISOLATION;
    if ((result.conn_pwr_con & (1u << 4)) != 0)
        result.blockers |= MT6983_CONNINFRA_BLOCK_CLOCK;
    if ((result.rx_protect & (1u << 0)) != 0)
        result.blockers |= MT6983_CONNINFRA_BLOCK_RX;
    if ((result.tx_protect & (1u << 12)) != 0)
        result.blockers |= MT6983_CONNINFRA_BLOCK_TX;
    result.state = result.blockers != 0 ? MT6983_CONNINFRA_HOST_BLOCKED :
        MT6983_CONNINFRA_HOST_GATES_OPEN;
    *report = result;
    return MT6983_CONNINFRA_OK;
}

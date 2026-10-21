#pragma once
#include <stdint.h>

/*
 * Append to MediaTek's PL/LK text using the live /chosen/log_store header:
 *   +0x00 u32 sig=0xabcd1234
 *   +0x14 packed u64 buffer address, +0x1c u32 allocation size
 *   +0x40 pl_lk_log {sig=0xcdab3412, size, off_pl, sz_pl, pl_flag,
 *                    off_lk, sz_lk, lk_flag}
 *
 * board_probe must run first. Missing/malformed/full buffers are no-ops;
 * never fall back to a physical address from another board. Only sz_lk and
 * previously unused LK text bytes are changed. /proc/pl_lk is the verified
 * reader on matisse; `oem lkmsg` reads a DIFFERENT kernel/UART log buffer.
 * Reboot persistence is NOT established: prefer a same-boot entryprobe.
 */
int logstore_write(const char *text, uint32_t n);
void logstore_mark(const char *tag, uint64_t a, uint64_t b);

#pragma once
#include <stdint.h>

/*
 * XNU_DIAG boot trace: every shim stage is drawn on the panel, and before
 * jumping sentinels are planted in globals that XNU writes at known early
 * stages. The next boot draws their state (diag_prev_report) before the
 * loader overwrites them.
 *
 * -DDIAG_LOGSTORE additionally mirrors the trace into MediaTek LK's
 * log_store for `fastboot oem lkmsg`. It is off by default: two hardware
 * attempts hung on the first log_store access from the shim, before any
 * frame was pushed. Without -DXNU_DIAG every call is a no-op.
 */
void diag_early(uintptr_t dtb);
int diag_reporting(void);
void diag_report(void);
void diag_prev_report(void);
void diag_stage(const char *name, uint64_t a, uint64_t b);
void diag_arm_sentinels(uint64_t virt_base, uint64_t phys_base,
    uint64_t mem_size);

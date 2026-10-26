# Matisse MMU and UFS performance audit, 2026-10-27

The running userland baseline is build 0x230d, commit 9b6c6d6, using
`xnu/out/local/xnu` and the frozen shim from 8dc3bce. Source inspection does
not show ordinary kernel/user RAM mapped globally as Device or Normal-NC.
Hardware readbacks are required to verify that conclusion at runtime.

## Memory attributes

| Path | MAIR index | Attribute byte | Use |
| --- | --- | --- | --- |
| shim diagnostic MMU | 0 | 0x00 | Device-nGnRnE, default identity blocks |
| shim diagnostic MMU | 1 | 0xff | Normal, Inner/Outer WB, read/write allocate; shim image |
| shim diagnostic MMU | 2 | 0x44 | Normal-NC; framebuffer block |
| XNU | 0 | 0xff | Normal, Inner/Outer WB, read/write allocate; default RAM |
| XNU | 1 | 0x44 | Normal-NC; explicit write-combining mappings |
| XNU | 2 | 0xbb | Normal WT |
| XNU | 3 | 0x00 | Device-nGnRnE |
| XNU | 4 | 0x4f | Normal, Inner WB, Outer NC |
| XNU | 5/6/7 | 0x04/0x08/0x0c | Other Device attributes |

The index numbers differ between shim and XNU, but each matches its own
MAIR. The shim's `MAIR_EL1=0x44ff00` does **not** persist into userland:

- `shim/shim.c` calls `loader_boot()` before `mmu_enable()`. Successful
  embedded XNU boots jump from the loader with MMU/caches off. On matisse,
  the no-payload failure path stops before the Qualcomm diagnostic MMU path.
- `shim/start.S` disables the incoming MMU/caches. `shim/loader.c` copies
  the kernel and boot arguments, invalidates I-cache, then jumps to XNU.
- `osfmk/arm64/start.s` rebuilds MAIR on CPU start/resume, expected value
  `0x0c08044f00bb44ff`, and writes `SCTLR_EL1_DEFAULT` enabling M/C/I.
- `osfmk/arm64/proc_reg.h` defines both TTBR0/TTBR1 walks as Inner/Outer WB
  in `TCR_EL1_BASE`. Its `CACHE_ATTRINDX_DEFAULT` and `WRITEBACK` are both 0.
- `osfmk/arm64/arm_vm_init.c` uses index 0 for ordinary RAM L2 blocks/L3
  pages and explicitly separate NC mappings for framebuffer memory.
- `osfmk/arm/pmap/pmap.c`: managed RAM defaults to `VM_WIMG_DEFAULT` in
  `pmap_cache_attributes()`. `pmap_enter_options_internal()` applies
  `wimg_to_pte()`, whose ordinary/default case selects index 0. Explicit IO
  mappings can select Device; that is not the default user allocation path.
- UFS register windows use `ml_io_map()`, while DMA/bounce RAM is allocated
  with `kmem_alloc_contig(...KMA_DATA...)` and uses ordinary RAM mappings.

## Per-read waiting and other costs

`bsd/dev/sweet_ufs.c:ufs_scsi()` submits a slot-0 command, then tests the
doorbell until it clears. `delay(1)` is reached only while still pending.
`osfmk/kern/clock.c:delay(int usec)` confirms its unit is microseconds.
The five-second constant is an error deadline, not a fixed sleep per read.

`ufswait=1` optionally spins for 50 microseconds then sleeps in intervals
up to 200 microseconds. Build 0x230d's command line does not set `ufswait`,
so that branch is disabled. Larger delays in device reset, link startup and
device-init polling are outside the normal block-read path.

Potential costs that still require measurement:

- All I/O uses one mutex, one slot and a 1 MiB bounce buffer.
- Each read performs DMA cache maintenance before/after the command and a
  bounce-to-destination memcpy. `ufscache=1` narrows descriptor maintenance
  but still maintains read-buffer coherency.
- No explicit transition to a fast HS gear was found after link startup.
  This alone does not establish the actual negotiated mode. Read-only
  DME_GET of gear, active lanes, power mode and HS series will establish it.
- Previous process sampling held `task_lock` across `vm_map_read_user()`,
  which may fault/page in. This can delay the observed task; sampling must
  avoid that observer effect before attributing all slowness to storage.

## Diagnostic build 0x230e

Patch `0113-sweet-mmu-ufs-performance-diagnostics.patch` enables diagnostics
only with `sweetperf=1`: actual per-CPU MAIR/SCTLR/TCR, kernel text/DMA and
user header/PC/SP entries, DMA AT/PAR readback, a 32 MiB cached memcpy
benchmark in dedicated bounce RAM, negotiated link attributes, cumulative
READ/WRITE/SYNC phase times, command counts/bytes, polls/sleeps/errors,
strategy mutex waiting and memcpy time. No new storage benchmark writes or
gear/power changes are introduced. Existing filesystem/log writes remain.

The sampler now snapshots registers under `task_lock`, releases it before
printing/reading, and reads only resident managed WB pages through a
bounded pmap/PVH-locked helper. Missing/NC/Device pages are rejected without
faulting. Backtraces are best effort because target threads keep running.

Validation: full CLT kernel build exit 0; 16,723,824 bytes, SHA256
`946f623065a621e1de3d892579e506599ec781fae78879a9c6bca7f18d8f76a8`.
Patch applies cleanly to saved pre-230e sources. Host ASan+UBSan test passes
for WB copies, missing/unmanaged pages, cross-page rejection, all seven
other AttrIndx values, changed entries under PVH lock, block probing and
lock release. No new GitHub Actions run. Runtime trial is pending.

User additionally identified `Documents/lk.img`, `vcp.img` and `dtbo.img`
as display references. Inspect these read-only and compare with existing
panel/refresh evidence; their presence does not prove UI has rendered.

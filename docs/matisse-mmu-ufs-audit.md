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

## 0x230e trial and diagnostic correction

The user returned FASTBOOT after 247.41 seconds after reporting the combined
Apple/frozen/black-screen choice. Original boot_a was fully restored and its
64 MiB SHA256 verified as
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`.
Both recovered marks and console are byte-identical to 0x230d. There is no
valid 0x230e MMU or performance result; failure occurred before new marks
were committed, and the exact failing instruction is unknown.

Source review found a concrete diagnostic context error: `SweetCPU::initCPU`
runs at splsched with interrupts disabled, while `_IOLogv()` in
`iokit/Kernel/IOLib.cpp` asserts interrupts enabled when CPUs are running.
This is a plausible early failure cause, not a captured panic diagnosis.
Patch 0114 / build 0x230f uses `kprintf`, as the adjacent existing CPU log
does, and moves optional PTE/AT/memcpy/DME checks into the first background
snapshot after persistent logging is active. It acquires the UFS mutex for
the bounce-buffer benchmark and releases it before printing.

Read-only display comparison confirms the supplied DTBO and actual live DT
select `l11_38_0a_0a_dsc_cmd,lcm`. LK contains the matching driver name, and
LK's embedded DT / live DT agree on DSI0 `0x14017000`, DSC0 `0x14015000`
and the two mutex windows `0x14001000` / `0x14401000`. These agree with the
current XNU display window. VCP identifies itself as `tinysys-vcp-RV55_A`;
this static inspection establishes no new display dependency. Raw JSON and
string evidence is retained in the ignored userland research directory.

0x230f validation: full CLT build exit 0, 16,723,880 bytes, SHA256
`3ccc1849d8e421e0f01d79a99e17461de6d985c94fc4288090529c948e4b02f3`. Patch 0114 applies cleanly and the resident-sampler ASan/UBSan test passes.

# Matisse MMU and UFS performance audit, 2026-10-27

Corrected hardware probes in 0x2310/2311 confirm Normal Inner/Outer WB RAM
and enabled MMU/data/instruction caches. Opt-in HS Gear1 removes the measured
PWM storage bottleneck. ADT network identity resolves lsd seeding; the
flashlight-only startup correction in 0x2314 reaches a user-confirmed visible
iOS lock screen. Trial 0x2314 logs confirm SpringBoard in its UIApplication event loop,
GUI swaps through 40 and no mediaserverd crash; full stock boot_a restoration
is independently hash verified. Touch was disabled in that trial. Native
1440x3200 display and touch/key interaction are being tested in 0x2315.

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

## Recovered 0x230f results, reviewed October 31

The user saw boot text updating and returned the device to FASTBOOT.
Original boot_a was restored and the entire 64 MiB verified against the
backup. Fresh marks identify build 0x230f; these are not stale 0x230d logs.
The ignored evidence is `research/matisse/20261027-userland/ui230f-*`.

MAIR readback is `0x0c08044f00bb44ff`, with index0=`0xff`. Every valid
sampled PTE for kernel text, DMA, launchd, SpringBoard, backboardd, logd and
logd_helper uses AttrIndx0. Some nonresident pages return zero; those must
not be decoded as valid WB entries. The cached 32 MiB copy took 4081 us,
about 7.8 GiB/s, repeatedly copying between two 512 KiB bounce-buffer halves.
This is not sustained DRAM bandwidth.

**The old DMA PAR and SCTLR values are invalid.** Darwin's ARM64 assembler
treats `;` as a comment. The combined inline-assembly string emitted only
`mrs x9, MAIR_EL1`, then stored uninitialized registers for SCTLR/PAR.
Actual 0x230f kernel disassembly proves that the intended SCTLR read,
AT/ISB and PAR read are missing. Do not infer disabled caches from these
values. Startup disassembly does set SCTLR_EL1=`0x3454593d` (M/C/I set),
but valid runtime SCTLR/TCR/PAR sampling is still required. Early per-CPU
kprintf records were not recovered through the persistent console.

| Successful DME_GET | Value |
| --- | --- |
| PA_TXGEAR / PA_RXGEAR | 1 / 1 |
| PA_ACTIVETXDATALANES / PA_ACTIVERXDATALANES | 1 / 1 |
| PA_PWRMODE | 0x55 (SLOWAUTO / SLOWAUTO, PWM) |
| PA_HSSERIES | 1 |

At uptime707920ms, 4792 reads transferred446234624B. Cumulative preparation,
hardware-wait and postprocessing times were20336us,652093493us,19409us;
errors0, sleeps0. Command throughput was about0.65MiB/s. Bounce copies
across strategies totaled107090us. Mutex waiting accumulates across
callers and is not elapsed wall time. Hardware waiting dominates; the
pending-loop delay is1us and is not a fixed millisecond read delay.

## Opt-in HS Gear1 trial, build 0x2310

Patch0115 fixes the probe with explicit newline-separated AT/ISB/MRS
instructions and builtin MAIR/SCTLR/TCR/MPIDR reads. Interrupts stay disabled
only across this short register snapshot; printing happens afterwards.
The `MMU v2` marker distinguishes the corrected probe from invalid legacy
records. The sampler bounds console lines so a lost newline cannot join
two thread records.

`ufshs=1` checks local/peer HS capabilities and connected lanes, requests
HS Gear1/one lane/seriesA/FAST in both directions and applies UniPro timeout
defaults. It follows Linux v6.6 `ufshcd_change_power_mode` and MTK's
no-adaptation setting for gears below4. The transition requires both UIC
command completion and the independent power-mode completion, then
HCS.UPMCRS=PWR_LOCAL and exact DME readback. Initialization aborts on any
failure; no automatic fallback continues with uncertain link state.

After partition discovery and before enabling writes, the driver times
one1MiB READ(10) in PWM and HS modes and exactly compares the first4KiB.
This is a read-only check, not a full filesystem integrity check. Existing
filesystem/log writes become possible only after it succeeds. Default
boots do not negotiate HS unless explicitly opted in.

Host tests execute the actual patched functions against a UFSHCI/DME
model with delayed/missing power completion, stale status, capability
failure, failed commands, bad status/readback and corrupt data. ASan/UBSan
passes. The Darwin assembler test verifies emitted AT/ISB/PAR instructions;
the existing resident WB-only sampler test also passes. Hardware outcome
is still pending.

Full local CLT build exit0: kernel16723920B, SHA256
`d8f1c7d126ca07932b268c1994d39c37de680fb136f15e1cd4d03ba69f5aa3c2`.
Linked-kernel disassembly at0xfffffe00074dccfc confirms all four register
reads followed by AT S1E1R, ISB, MRS PAR and interrupt-state restoration.
The five targeted tests pass. These checks establish the emitted code,
not the device's resulting HS mode or runtime cache state.

## Hardware result 0x2310, October 31

HS Gear1/one lane/seriesA succeeded. Local and peer advertise HS Gear4 and
two connected lanes; the conservative requested settings all read back
exactly, including PA_PWRMODE=`0x11`. The same1MiB read fell from1519023us
in PWM to8870us in HS; its first4KiB matched exactly. At uptime345485ms,
20674 reads transferred1591185408B in17539226us including preparation and
postprocessing, about86.52MiB/s (including the initial slow PWM benchmark).
R/W/S error counters are all zero. This removes the measured PWM bottleneck.

Valid runtime register probe: MPIDR=`0x81000100`,
SCTLR=`0x3454599d` (M/C/I all enabled), TCR=`0x226511a511` (TTBR0/1 Inner
and Outer walk policies all WB), MAIR=`0x0c08044f00bb44ff`, and
PAR=`0xff00000104f04b00` (successful DMA translation, Normal WB attribute
`0xff`). Valid sampled user/kernel PTEs continue to use index0. These are
now runtime evidence, unlike the invalid legacy SCTLR/PAR record.

Userland advances past logd cache harvesting. Backboardd opens AppleCLCD
and IOCoreSurfaceRoot, creates two1125x2436 BGRA surfaces and presents
swaps2..6. The user still reported Apple/frozen/black, so visible UI is not
confirmed. SpringBoard now waits on a LaunchServices context lock while
a worker awaits a synchronous reply in
`__LAUNCH_SERVICES_IS_RESEEDING_ITS_DATABASE_AND_MAY_BLOCK`. Sampling lsd
is the next concrete step. Its role is established; the server's cause is
not yet known. Backboardd's main thread is in its ordinary CFRunLoop.

The user returned FASTBOOT at393.12s. Original64MiB boot_a restored and
full SHA256 verified; `boot-ui-2310-result.json` has restore_pending=false.
Watchdog is after the forced return, not evidence of a spontaneous reset.

## Hardware result 0x2311 and device-identity correction

The user returned FASTBOOT at243.02s after seeing Apple/black. Original
boot_a was restored and the entire64MiB hash independently verified again
in Recovery; restore_pending=false. Fresh marks identify2311. All three
complete framebuffer thumbnails (frames2/3/4) contain a white Apple logo
on black. These describe framebuffer RAM, not physical-panel output.
Display transfer counters are unchanged after the first105s sample.

With actual shared-cache slide0x1b02c000, lsd102's seeding worker is stable
at105/165/225s in this chain:

```
MIInstallationIdentity installationIdentityForBundle
  GenerateBuiltInAppInstallSessionID
    MGCopyAnswer
      libMobileGestalt func_19c5673dc / func_19c568910
        func_19c5716e8: Wi-Fi address
          func_19c57c084: IONetworkController match
            dispatch_semaphore_wait
```

The corresponding worker runs MobileGestaltRunLoopMode. Lsd's main waits
in performJournalRecovery; its database-fetch service waits for seeding.
SpringBoard and a backboardd worker consequently await the server store.
Backboardd's main remains in an ordinary CFRunLoop wait.

Disassembly of the actual20H392 cache establishes that func_19c5716e8 first
reads the six-byte `IODeviceTree:/chosen/mac-address-wifi0`. The trial ADT
has no such property; after missing sdio/wlan/marconi-wifi paths the code
waits for an IONetworkController, which this port does not publish. The
same device-ID routine next asks for Bluetooth; func_19c56883c first reads
`/chosen/mac-address-bluetooth0`. This is a demonstrated dependency path,
not an assumption that resetting the LaunchServices database will help.
Raw disassembly and complete symbols remain in the ignored research tree.

`tools/mkadt.py` now publishes locally administered, synthetic Wi-Fi and
Bluetooth addresses alongside the existing synthetic ECID. This supplies
identity inputs only; no radio functionality is added. Trial2312 reuses
exact kernel2311 and adds only these two ADT properties, preserving all
original ADT bytes otherwise, plus `sweettrial=2312` for log attribution.
Seven existing board-profile tests pass; no kernel rebuild or new CI run
is needed. The rebuilt image preserves stock ramdisk/signature and its
embedded payload, ADT and command line were independently verified.
Hardware result for this identity correction is pending.

Trial2312 confirmed that the identity correction clears the observed lsd
blocker: at105/165s the server has completed seeding and its main is in
`runServerMainRunLoop`; installd now executes, and the previous LS waits
are gone. SpringBoard now blocks establishing `com.apple.coremedia.flashlight`
through `FigFlashlightRemoteCreate` -> `FigXPCRemoteClientCreate` -> synchronous
XPC message. The stock service belongs to mediaserverd. This establishes
the next dependency, not whether mediaserverd is stalled or its flashlight
server is disabled. Capture mediaserverd threads before changing that service.
The first three framebuffer shots are still Apple logos; no visible UI.
Original boot_a restored and full hash verified after208.11s forced return.

### 0x2313: flashlight startup dependency (2026-10-31)

The user manually returned to FASTBOOT at 157.15 seconds with Apple/black
screen. Full stock boot_a was restored and independently SHA256 verified;
boot_a_restore_pending=false. Recovery's subsequent Watchdog reason is
not evidence of a spontaneous iOS crash. No visible UI is confirmed.

Actual 20H392 disassembly shows MediaToolbox _FigMediaServerStart gates
FigStartCaptureServers on StartRecorderServer. CMCaptureCore starts a list
including FigPreloadShaders and FigFlashlightServerStart. The latter calls
FigXPCServerStart for com.apple.coremedia.flashlight, the synchronous
SpringBoard dependency. Mediaserverd's sampled main thread is in CFRunLoop.

The existing recorder-off GPU workaround also suppresses flashlight startup.
The next opt-in experiment uses tools/sweet-media-flashlight.py: exact
cache25 UUID/SHA256 checks guard two ARM64 instruction changes, making the
startup array contain only flashlight followed by nil. Offsets 0x36a08b4
and 0x36a08bc change; the existing name CFString is 0x1dabc9560. The XML
boolean becomes true without changing file size. All camera servers and
shader prewarm remain skipped. This is diagnostic compatibility, not camera
or flashlight hardware support, nor evidence of visible UI. No Apple binary
is committed. Validation against pristine root passes.

Recovery lacks HFS support, and live journal start/end differ. Before any
root update: pull the current 16 GiB root, let native macOS HFS replay the
journal, patch files in place preserving hardlinks/permissions, unmount and
verify, then apply only a preimage-checked block delta to the offline device.
Keep original and changed preimage blocks locally for rollback.

0x2314 update: the current root was pulled, natively journal-replayed and
patched; offline fsck_hfs -fn passed. A 573440-byte delta in 34 runs matched
all device preimages and was fully readback verified. Numeric uid/gid,
modes, hardlinks, data sizes and extents were checked. The actual patched
disassembly confirms the nil terminator and flashlight first object.

The user reported “出现锁屏、设置或主屏幕” for 0x2314. This is the first
positive visible-UI observation in this matisse trial series. The exact
screen is still being clarified, touch remains disabled, and fresh runtime
logs have not yet been recovered. Original boot_a restoration is armed and
pending FASTBOOT/Recovery return; do not call this fully verified yet.

0x2314 recovered evidence: fresh sweettrial=2314; SpringBoard PID26 main
is UIApplicationMain/GSEventRunModal/CFRunLoop at105.499s, not the flashlight
sync wait. GUI swaps reach40 and SpringBoard creates additional surfaces.
Mediaserverd28 is idle without a recorded crash. PosterBoard crashes once
in first switcher configuration, then replacement233 reaches UIApplicationMain;
this does not block the observed lock screen. The user manually returned
FASTBOOT at176.77s. Full stock64MiB boot_a SHA256 matches backup; Recovery
is online. UFS read throughput87.84MiB/s with R/W/S errors0, valid MMU v2
again confirms M/C/I, WB walks and sampled PTE index0/MAIR0xff.

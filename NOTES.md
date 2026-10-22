# iOS 16 on sweet (Redmi Note 10 Pro, SM7150) - working notes

## >>> ACTIVE 2026-10-23: 0085 result is static logo; boot_a restored live backup (verified); PID 1 EL0 probe prepared <<<

**Current device (read-only adb + approved restore this turn): Recovery, root,
slot `_a`.** The partition was initially the stock template
`7a6a462d5e8858c6f7ecd77655363dd84964d44ea6e7c239e961632996a96e94`
(`backup/boot_a-stock.img`), not the live backup; the first 64 KiB also matched
the template. The live backup
`backup/recovery-20261021/boot_a-live-20261021.img` was pushed, written with
`dd ... conv=fsync`, and the full 67,108,864-byte readback now equals
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`.
`boot_b` is a separate image
(`8775a30a95a44ca5c8d83c38df4d77e2c4c8bad315ac54fb9d1dae60495d9cb7`). No
`userdata` write. Each new `boot_a` write still needs explicit approval.

**The logo and the BSD result are separate.** Diagnostic BRK records prove
`bsd_init` ran through `vfs_mountroot` (x0=0) and reached `load_init_program`;
mockfs/md0 was mounted. The panel showing LK's first logo says only that the
command-mode DSI path is not pushing new frames, not that the kernel stopped.
PID 1 execution is still unproven.

**Watchdog fact check (device evidence, correcting the `WDT_AUTO_RESET`
theory).** MediaTek does have a WDT here: the live DTB has
`watchdog@1c007000` compatible `mediatek,mt6983-wdt`, `/sys/class/watchdog/watchdog0`
exists, and Recovery dmesg says `mtk-wdt 1c007000.watchdog: Watchdog enabled
(timeout=31 sec, nowayout=0)`; the current boot command line carries
`aee_aed.pureason=Watchdog`. The WDT is real. However, current
`tools/mkentryprobe.py` does not define `WDT_AUTO_RESET` or `XNU_DIAG`
(confirmed by `llvm-nm`: no `shim_wdt_restore` in the diagnostic ELF), so the
shim leaves the WDT disabled in these probe builds. A probe that misses will
hang with no automatic Recovery return; recover with Power + Volume Down.

**Display constants audit.** For matisse the active values are correct:
`shim/board.c` and `tools/mkadt.py` select `0xfcb3f000`, 1440x3200, stride
5760, and the live FDT overrides them. `0x9c000000` / 1080x2400 are only the
sweet/unknown-board fallbacks. Patch `0082` replaced the old early-console
`0x9c000000` writes from `0010`/`0012` with Boot_Video arguments populated by
`shim/loader.c`. Patches `0061`/`0046` still carry iPhone-X logical sizing for
later userland (`1125x2436`, dot pitch 458); that does not explain the
pre-userspace logo. No stale matisse framebuffer hardcode was found.

**Next evidence: EL0 syscall checkpoints (host-built, NOT flashed).** The new
selective vector also redirects slot 8 (AArch64 EL0 synchronous). It forwards
ordinary syscalls unchanged and aborts to the proven Recovery relay at the
first EL0 SVC whose x16 matches `--el0-syscall`. This directly proves whether
`load_init_program` produced a running PID 1, and whether `open("/dev/console")`
returned, without a display or UART.

- `shim/out/entryprobe-el1-ramdisk-syscall-open.img`, 26,566,656 B, SHA256
  `162320a626c3ddc0baf819bafa2ce72251aa580bbc1c232ea1a5efa5e315de30`.
  `--el0-syscall 5`. Expected record: `esr=0000000056000080`,
  `pc=00000000100000420`, `x0=000000001000004c0` (path pointer),
  `x16=0000000000000005`, `spsr=0000000000000000`.
- `shim/out/entryprobe-el1-ramdisk-syscall-write.img`, 26,566,656 B, SHA256
  `23270c33bfedb861a2ffcb20fab639c80981d47d95dbb93aa29a282c4ea94f92`.
  `--el0-syscall 4`. Expected record: `esr=0000000056000080`,
  `pc=000000001000004a8`, `x0=0000000000000003` (console fd),
  `x16=0000000000000004`, `spsr=0000000000000000`.
- Both embed payload `shim/out/matisse-xnu-ramdisk-rtcfast.payload`, 16,622,400 B,
  SHA256 `2ff27355d4c416516760243811ef6bec2bf71815dfbf18346060f36dd07d4a44`.
  It is the hardware-passed RAM-disk kernel with the RTC and IOMedia 30 s waits
  bypassed and the old `load_init_program` BRK removed (the only four words
  differing from `matisse-xnu-ramdisk-init.payload` are the two waits, the
  timeout branch and that BRK).
- Build validation: banner record 384 B (cap 406), slot 8 original `b` kept,
  stub 100 B (<128), embedded payload hash matched, key words checked
  (`0x77ee118: d104c3ff`, `0x79ad070: d503201f`, `0x7a41e70: aa1f03e0`,
  `0x7a41eb4: 14000007`, slot 8 `140003b3`). All 44 tools tests pass.
- Suggested order: flash **open** first; a hit proves PID 1 executed. Then
  flash **write**; a hit proves `open("/dev/console")` returned and the first
  userspace write was issued. A miss on open means no EL0 execution (or a
  dead vector), not merely a dark panel.

## >>> ACTIVE 2026-10-23 (Asia/Shanghai): BSD mounts md0; display refresh fix prepared <<<

**Current device (verified): Recovery, boot_a fully restored.** The latest
full 67,108,864-byte readback (`thread-brk-v2-restored.img`) matches original SHA256
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`.
Online hardware is **matisse / Redmi K50 Pro / MT6983**, not README's
sweet / Qualcomm target. XNU's original initialization through formal page-table
construction and the first switch to those tables inside arm_vm_init are
hardware-proven, as are TTBR0 teardown, TLB invalidation and early console
mark 10 using the relocated framebuffer, and the complete arm_vm_init return.
Serial_init also returned success (x0=1) on hardware, followed by startup
phase 4, console/printf setup, idle init and PE_init_platform(TRUE). CPU/timer/
GIC setup and early_random also passed up to the machine_startup call.
machine_startup and kernel_bootstrap have now reached vm_mem_bootstrap;
vm_page_bootstrap, zone_bootstrap, vm_object_bootstrap, vm_map_init,
kmem_init (with the KMEM and ZALLOC startup phases, including zone_init) and
vm_fault_init have all returned on hardware: the **complete
vm_mem_bootstrap returned** to kernel_bootstrap. `sched_init`, task/thread
init and `load_context` then ran, and the **bootstrap thread executed on
hardware** (`kernel_bootstrap_thread` after `current_processor`).
`idle_thread_create`, `sched_startup` (first real `thread_block` round trip)
and `thread_daemon_init` have also returned on hardware. The kernel then
reached `spllo()` with interrupts enabled and returned from
`initialize_screen`. Selective diagnostic vectors are hardware-proven, and a
500 ms CNTVCT busy-wait with interrupts enabled advanced `sched_tick` by 3:
timer-interrupt delivery and scheduler maintenance are proven. A direct jump
to the `bl bsd_init` call failed into an infinite reboot loop; `boot_a` was
fully restored. The post-lockdown checkpoint then passed; the XNUPOST-skip
diagnostic still looped, and a payload test confirmed the post-lockdown hang
is `kperf_init_early()` -> `ml_wait_max_cpus()`. Returning 1 from that barrier
on the single-CPU topology let kperf, the full CONFIG_XNUPOST suite and
`PE_lockdown_iokit` pass; execution reached the `bl bsd_init` site. The
permanent CI kernel with that fix was then hardware-validated end-to-end and
reached the `bl bsd_init` site with XNUPOST and kperf untouched. Later RAM-disk
diagnostics executed `bsd_init`: `vfs_mountroot` returned success (`x0=0`),
`bsd_utaskbootstrap` ran, and execution reached `load_init_program`. Thus BSD
initialization and mounting the mockfs/md0 root are proven on hardware.
Successful PID 1 execution and full kernel boot remain unproven; the
production screen stayed dark. The display path has now been traced to two
gaps: `sweet_display_init()` treated transient DSI `TE_RDY` as a hard
readiness gate, and the command-mode refresh hook covered `pe_serial.c` but
not XNU's graphical `video_console.c`. New patch
`xnu/patches/0085-matisse-video-console-refresh.patch` removes that gate and
refreshes after graphical console output. It applies to the CI XNU source and
both affected files pass local ARM64 Mach-O object compilation (0 failures)
using the recorded DEVELOPMENT/SWEET flags. All 44 tools tests pass. CI and
hardware validation are still pending; the screen fix is not yet proven.
Device state is last verified as
Recovery with the full original `boot_a` restored and hash
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`.

**Completed: `sched_startup` return (hardware PASS).**
`shim/out/entryprobe-el1-sched-startup-brk.img`, 26,562,560 B, SHA256
`c1859ef9626ff2a66689c0b63d25aa10e23645e987db1c851435a43f0c5bf5b9`;
payload SHA256 `196c4de70325c597e6c80089dcc45048eb30a72f6f0488f27e34f33b4ed14a72`.
Same dual-slot selective vectors, 128 MiB cleanup range
`[0x100000000, 0x108000000)`, bootstrap TTBR0 restore `0x10117c000`, debug
payload base `b6eafe9a...` and stock template `7a6a462d...` as the
hardware-passed thread image. BRK at entry offset `0x62334c`, payload offset
`0xaabbc4`, VA `0xfffffe0007aaf7d4` / PA `0x101aaf7d4`, immediately after
`bl sched_startup`; it replaces `ldrb w8,[x21,#0x20c]` (`0x394832a8`).

Same-boot `sched-startup-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aaf7d4`, x0 `1`. Thus
`idle_thread_create` and `sched_startup` returned on hardware, including the
first real `thread_block` round trip (`sched_init_thread` blocks, bootstrap
resumes). The ESR is the inserted BRK. The device returned to Recovery about
30 s after `adb reboot recovery`. The predicted written-partition SHA256
`259d0ae9...` matched the full 67,108,864-byte readback, and the complete
original was written back and verified as `2931647a...`. Evidence:
sched-startup-dmesg.txt, before/written/restored images and prepared-build.txt
in `research/matisse/20261023-sched-startup/`.

**Completed: `thread_daemon_init` return (hardware PASS).**
`shim/out/entryprobe-el1-thread-daemon-brk.img`, 26,562,560 B, SHA256
`f16c296dd10705f0434c29c0cc0e6460411730d1be1a0899a88809c9aea4f4e8`;
payload SHA256 `d41aec2a63d98734a89d1a5935de37e568f0d3b5cd6a6b18d363199f432f30f7`.
BRK at entry offset `0x623384`, payload offset `0xaabbfc`, VA
`0xfffffe0007aaf80c` / PA `0x101aaf80c`, after `bl thread_daemon_init`;
replaces `mov w0,#0xe` (`0x528001c0`) immediately before
`kernel_startup_initialize_upto(STARTUP_SUB_THREAD_CALL)`. Same selective,
cleanup and TTBR0 setup as the passed images.

Same-boot `thread-daemon-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aaf80c`, x0 `0`. Thus
`thread_daemon_init` returned as well: `idle_thread_create`, `sched_startup`
and `thread_daemon_init` have all completed on hardware. The device returned
to Recovery about 30 s after `adb reboot recovery`; predicted written-partition
SHA256 `a20d347f...` matched the full readback, and the complete original was
written back and verified as `2931647a...`. Evidence: thread-daemon-dmesg.txt,
written/restored images and prepared-build.txt.

The build recipe is proven: rebuilding the hardware-passed thread image from
the same template/payload/offsets (`--xnu-vector-offset 0xb78`,
`--xnu-high-vector-offset=-0x4488`, `--xnu-clean --xnu-clean-limit
0x108000000`, `--xnu-restore-ttbr0 0x10117c000`, `--xnu-selective`, NDK
28.2.13676358) reproduced SHA256 `154449c1...` byte-for-byte. Each new payload
differs from the base by exactly one BRK word. All 37 tools tests pass. The
stale `entryprobe-el1-sched-init-brk.img` / payload `e7c026b4...` remains
slot-4-only and must not be used.

Device state after this test (observed): root ADB in Recovery, slot `_a`,
`boot_a` fully restored to SHA256
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`. Each
further `boot_a` write needs separate approval. After the BRK record, write
back the full original and verify the hash; on failure, restore the trimmed
stock prefix via fastboot, boot Recovery, then write the full backup. Never
leave a test image in `boot_a`.

**Docs:** `docs/matisse-xnu-ios-roadmap-zh.md` updated on 2026-10-23 with the scheduler, timer-IRQ, lockdown, kperf and `bsd_init` results.

**ACTIVE: production RAM-disk boot reaches load_init but console is dark
(2026-10-23).** Production images `fa6607a8...` (original waits) and
`236b9828...` (RTC/IOMedia waits bypassed) were flashed; both stayed static
with a black/logo screen and no Recovery, so the AP watchdog remained off and
the kernel did not reset. A third production image `996197e5...` also NOPed the
`bl sweet_display_init` in `serial_init` (`0xfffffe0007a7c73c`); the screen
stayed dark. Diagnostic BRK evidence proves `bsd_init` executed through
`vfs_mountroot` (x0=0) and `load_init_program` entry. `mountroot` success means
mockfs/md0 is mounted. What remains unproven is PID 1 execution/console: the
framebuffer console shows nothing, and the init's first action
`open("/dev/console")` may block or the console may be headless. Next evidence
path should be the XNU msgbuf/serial, not more blind framebuffer boots.
Final state after this run: `boot_a` was restored from the full 64 MiB backup and verified as `2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`; device is in Recovery, slot `_a`. No `userdata` write.

**ACTIVE: diagnostic watchdog artifact found (2026-10-23).**
`mkentryprobe.py` compiles the shim with `WDT_AUTO_RESET`, which re-arms the
MT6983 31 s AP watchdog so a hung XNU resets. Every "infinite reboot" after a
BRK checkpoint or no-BRK diagnostic image is therefore partly an artifact. The
production `shim/build.sh` path calls `wdt_disable_raw()` at shim entry and
leaves the watchdog off. Built production image `shim/out/shim-boot.img`
(7,008,256 B, SHA256 `fa6607a8ee617fa3da8e816180e43f8ac867465d986556face0ba56533b3686c`)
from the original RAM-disk payload and flashed it; predicted partition
`e08af82b...` matched. This boot, not the diagnostic images, is the real test
of the 30 s RTC + 30 s IOMedia waits and the minimal init.

**ACTIVE: bsd_init bisection found two 30 s waits (2026-10-23).**
Checkpoints after `kauth_init`, `kmem_suballoc`, `bsd_bufferinit`, and
(once the waits were patched) `vfsinit`, `IOKitBSDInit`, `vfs_mountroot`
(x0=0), `bsd_utaskbootstrap`, and `load_init_program` all returned. The two
blockers were `IOKitInitializeTime` waiting 30 s for an RTC service at
`0xfffffe00079ad070`, and `IOFindBSDRoot` waiting 30 s for IOMedia at
`0xfffffe0007a41e70` before `mdevadd` creates md0. The hardware watchdog
resets at ~30 s, so both waits look like a boot loop. Diagnostic payloads skip
both (`nop waitForService`, `mov x0,xzr` + `b` over the timeout sleep) and
reach `load_init_program` (`pc=fffffe00077ee118`). A checkpoint at the first
`load_init_program_at_path` return (`0x77ee214`) did not hit, so exec likely
succeeded; `tools/sweet-init.S` then hangs forever if `open("/dev/console")`
fails, which also triggers the watchdog. Built `research/sweet-init-diag`
(SHA256 `2917403c...`) that falls back to fd 1 and never spins, payload
`shim/out/matisse-xnu-0084-diaginit-rtcfast.payload` (`fdef6e15...`) and image
`entryprobe-el1-diaginit-rtcfast.img` (`4f0376b3...`), not yet booted.

**ACTIVE: RAM-disk bsd_init diagnostics (2026-10-23).** Built
`research/sweet-init` (16,635 B, SHA256 `f5de3714...`), payload
`shim/out/matisse-xnu-0084-ramdisk.payload` (SHA256 `4df6b274...`, ADT
`RAMDisk=<0x1024d0000,0x8000>`, cmdline `rd=md0`), and a no-BRK image
`entryprobe-el1-ramdisk.img` (`b337bfbf...`). Hardware: no-BRK boot loops with
`aee_aed.pureason=Watchdog`, so `bsd_init` hangs, not panics. `bsd_init` entry
checkpoint `entryprobe-el1-bsd-entry-ramdisk-brk.img` (`e6d4bf7e...`) returned
`pc=fffffe000776e048 x0=0`, proving the RAM-disk payload enters `bsd_init`.
Checkpoints at `bsd_utaskbootstrap` entry (`679fa200...`), `IOFindBSDRoot`
return (`0f1be906...`), and a no-wait variant (`060d436c...`) all looped.
Disassembly: `IOFindBSDRoot` calls `IOService::waitForMatchingService` with a
30 s timeout before `mdevadd` (which prints "Added memory device md0"); the
watchdog resets around the same time. Next diagnostic:
`entryprobe-el1-fastroot-ramdisk-brk.img` (`7ee792f1...`) patches the wait to
return 0 and replaces the timeout `cbz` with `b 0x...1ed0`, then BRKs after
`IOFindBSDRoot`; expected to reach `mdevadd`/return x0. Device is currently in
the no-wait loop; waiting for manual fastboot to restore full `boot_a`.

**Next:** execute `bsd_init` itself with a RAM-disk root (prepared, NOT
flashed). Host-only build on 2026-10-23 reused CI kernel 39828405447
(`28190f5b...`, patch 0084), `shim/out/matisse.adt` and the static init
`research/sweet-init` (rebuilt from `tools/sweet-init.S` with NDK clang +
`tools/mkinit.py`, byte-identical SHA256
`f5de3714ff95e6a3b9ece30723f677de0f3f74ec5eb985a00c5ae872f0ac9bf1`).

- `shim/out/matisse-xnu-ramdisk-init.payload`, 16,622,400 B, SHA256
  `4df6b274603e3897e9699bb7bc11791556f1067868e772a0fdf1592d5354f9b9`.
  Cmdline: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam rd=md0
  sweetkeys=0 sweettouch=0 sweetbattery=0 sweetusb=0 ufsrw=0 -noprogress`
  (135 B, `rd=md0` preserved by `platform_cmdline`).
- RAM disk segment VA `0xfffffe00084d0000`, 32 KiB, PA `0x1024d0000`;
  ADT `/chosen/memory-map/RAMDisk = <0x1024d0000, 0x8000>`. ADT is copied at
  PA `0x1024d8000` (4508 B), boot_args at `0x1024dc000`, topOfKernelData
  `0x1024e0000`; all inside the proven 128 MiB cleanup range
  `[0x100000000, 0x108000000)`.
- `shim/out/entryprobe-el1-ramdisk-init.img`, 26,562,560 B, SHA256
  `b337bfbf86bbe62c769bf6521df0331f65e7cf7d0c4d9973d83b0ae0cafcbe75`
  (no BRK; selective dual-slot vectors, stock-template SHA `7a6a462d...`).
  Expected panel output: `BSD root: md0`, `hfs: mounted SweetRoot...` is not
  used here (mockfs), `load_init_program: attempting to load /sbin/launchd`,
  then `*** SWEET userspace: init running as PID 1 ***` and a
  `SWEET init: still alive` line every 5 s.
- Checkpoints (same payload plus one `BRK #0x5357`):
  `entryprobe-el1-ramdisk-bsdinit-return-brk.img` SHA256
  `9a8c43dbe6b2c13bf9ad2136c21320bc7bf23df878759b63bd4b7d57d40c1821`,
  BRK at VA `0xfffffe0007aafc04` (immediately after `bl bsd_init` returns);
  `entryprobe-el1-ramdisk-load-init-brk.img` SHA256
  `b4af6752d90b82a46b814de428f92e1eeabc39073f15dfdf1a44c6bcc68928e8`,
  BRK at `_load_init_program` VA `0xfffffe00077ee118`.
- Validation: re-parsed both compressed boot Images, matched the embedded
  `SWEETAPP` payload SHA, the init bytes and `MH_MAGIC_64`, the ADT RAMDisk
  pair, the cmdline length, and that only the checkpoint payloads contain the
  BRK word. All 44 tools tests pass.
- Device baseline read-only check this turn: Recovery, root ADB, slot `_a`,
  `boot_a` SHA256 `2931647a...` matches the full backup. No flash was
  performed. Each `boot_a` write still requires explicit approval.

Next action: flash the no-BRK image, boot it, and photograph/observe the
panel. If it does not reach PID 1, use the `bsdinit-return` checkpoint next;
if that passes but userland does not, use the `load-init` checkpoint.

**Failed: XNUPOST-skip diagnostic (hardware, infinite reboot).**
`shim/out/entryprobe-el1-bsd-init-skiptest-brk.img`, 26,562,560 B, SHA256
`ce02efbf38a55aac1186e7b653e53505836cc4a4e29d0271fec1456c5283021a`;
payload SHA256 `29beb4b8bfd0508764101d9b5b4628a3ae6c33bfb34912edf9dfa7477969e10e`.
It NOPed `kernel_list_tests`, `kernel_do_post` and the `cbnz w0` panic
branch, then inserted BRK at `bl bsd_init`. The written partition matched the
predicted SHA256 `13524b46...`. The device still entered an infinite reboot
loop; no SWEET-ENTRY. Recovery used the same manual-fastboot → trimmed-prefix
→ full-backup path; `boot_a` verified `2931647a...`. CONFIG_XNUPOST is
therefore not the blocker.

**Completed: NOP-kperf checkpoint (hardware PASS; kperf confirmed).**
`shim/out/entryprobe-el1-bsd-init-nokperf-brk.img`, 26,562,560 B, SHA256
`14bef24d4f369ea13bbcb854eeedb1c7ba540cd3601250a6c8e6bac4fe183cf1`;
payload SHA256 `b2ae537f5e791b6441c780708a2598d2165b588d3d240188a9847c3cabc0c4f6`
(`research/.../nokperf-build.py`). It NOPs both XNUPOST calls and the panic
branch, NOPs `bl kperf_init_early` (`0x7aafbdc`), and breaks at the
`bl bsd_init` site (`0x7aafbe0`).

Same-boot `nokperf-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aafbe0`, x0 `0`. With
kperf skipped, execution reached `bl bsd_init`; `PE_lockdown_iokit` and
`vm_set_restrictions` returned. Written-partition SHA256 `c4921d05...`
matched the readback; the full original was restored and verified as
`2931647a...`.

Root cause (source): kperf calls `ml_wait_max_cpus()`, which blocks until
`ml_set_max_cpus()` sets `max_cpus_initialized = MAX_CPUS_SET`. On matisse the
ADT CPU node is `arm,cortex-a510` + `ARM,v8`, while the SweetCPU personality
matches only `qcom,kryo470`; the CPU driver never instantiates, so the setter
is never called and the barrier sleeps forever. `machine_info.max_cpus` is 1
because the matisse ADT has a single CPU node.

**Completed: payload max_cpus fix (hardware PASS; pre-bsd path cleared).**
`shim/out/entryprobe-el1-bsd-init-maxcpu-fix.img`, 26,562,560 B, SHA256
`8783158b9d5ba83e5154b6817da60175020778d7743a14f1f85837d2ff066ee2`;
payload SHA256 `6d45243bc5fd475ff434df217b7516eee33a4a0f8b6596655d61d4cae6956f1a`.
It replaced the first two instructions of `ml_wait_max_cpus()` (VA
`0xfffffe000748fe28`) with `mov w0,#1; ret`, kept CONFIG_XNUPOST and kperf
enabled, and broke at `bl bsd_init` (`0x7aafbe0`). Only 12 payload bytes
differed from the debug payload.

Same-boot `maxcpu-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aafbe0`, x0 `0`. The
single-CPU `ml_wait_max_cpus` return let `kperf_init_early()` and the full
CONFIG_XNUPOST suite pass; `PE_lockdown_iokit` and `vm_set_restrictions` also
returned. Written-partition SHA256 `ad487813...` matched the readback; the
full original was restored and verified as `2931647a...`.

Permanent source fix: `xnu/patches/0084-sweet-single-cpu-max-cpus.patch`
makes `ml_wait_max_cpus()` in `osfmk/arm/machine_routines_common.c` return
immediately when `machine_info.max_cpus <= 1`; the sole CPU is already
executing and the IOKit CPU driver may never publish the flag (SweetCPU
matches `qcom,kryo470`, matisse uses `arm,cortex-a510`). Local single-file
compile with `tools/xnu-cc.py` passes (0 of 1 failed on the ci-39158169212
tree). Committed as `b616374`, pushed to `Jinsens/ios-sweet`, and built by CI
run `39828405447` (the first dispatch on `main`, run `39826038020`, was
cancelled before any step; the retry on `codex/matisse-xnu-screen-log`
succeeded). New kernel
`xnu/out/ci-39828405447/kernel.development.sweet` SHA256
`28190f5b4c274b4d5c1501f08b776873c5a9190ec67b74021bd4f42b795267f2`;
payload `shim/out/matisse-xnu-ci39828405447.payload` SHA256
`e39237a808568658c363b8d10fbac9916b4093c2f8b0992cebfbc870c13f887c`.

**Completed: permanent-fix validation (hardware PASS).**
`shim/out/entryprobe-el1-bsd-init-0084-brk.img`, 26,562,560 B, SHA256
`1cd72d232491a871296d611720a8ac55774f4c743a286131841b2dff1da93599`;
payload SHA256 `d634f707bc0aecccd438fa312d0f69254706a7cb2b04ddba6ac886183835b425`.
The new kernel's `_kernel_bootstrap_thread` is at `0xfffffe0007aaf754`; the
BRK is at VA `0xfffffe0007aafc00` / PA `0x101aafc00` (entry offset
`0x623778`), replacing `bl bsd_init` (`0x97f2f912`). XNUPOST and kperf were
untouched. Same vector/cleanup/TTBR0 parameters as before: the early and high
vector tables and `bootstrap_pagetables` did not move.

Same-boot `ci0084-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aafc00`, x0 `0`.
Predicted written-partition SHA256 `65c410be...` matched the full readback;
the complete original was restored and verified as `2931647a...`. Patch 0084
is hardware-validated end-to-end: the real CONFIG_XNUPOST suite and
`kperf_init_early()` returned and execution reached `bl bsd_init`. 44 tools
tests pass.

**Completed: first post-`spllo` checkpoint (hardware PASS).**
`shim/out/entryprobe-el1-spllo-brk.img`, 26,562,560 B, SHA256
`ff8e21bfdd891cb86ccf6de2ec85fb82ab148e8877e598e74b8728059b80b02a`;
payload SHA256 `c9622ac8894e638f1f269189a7ddee8adf30cf83f9d5619116110de1bb2ecc55`.
BRK at entry offset `0x623564`, payload offset `0xaabddc`, VA
`0xfffffe0007aaf9ec` / PA `0x101aaf9ec`, immediately after
`bl initialize_screen`, i.e. after `spllo()` enabled interrupts and
`initialize_screen(NULL, kPEAcquireScreen)` returned; it replaced
`bl vm_commpage_init` (`0x97e63a74`). Same dual-slot selective vectors,
128 MiB cleanup range and TTBR0 restore as the passed images.

Same-boot `spllo-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aaf9ec`, x0
`fffffe0007c57408`. Thus `kernel_startup_initialize_upto` through
`STARTUP_SUB_EARLY_BOOT` (16), `PE_init_iokit`, `spllo()` and
`initialize_screen` returned with interrupts enabled. The device returned to
Recovery about 30 s after `adb reboot recovery`; predicted written-partition
SHA256 `8ac9faaa...` matched the full readback, and the complete original was
written back and verified as `2931647a...`.

That checkpoint alone did not prove a timer IRQ was delivered (the scheduler
tick interval is 125 ms and the code between `spllo()` and it may be shorter).
The dedicated probe below did.

**Completed: timer-tick proof (hardware PASS).**
`shim/out/entryprobe-el1-tick-brk.img`, 26,562,560 B, SHA256
`80c8f80fc924c94900c11f3c37b35a2a116dd728d81c21e1b80075c22a731a64`;
payload SHA256 `a9dcaf248023333832276bffab285cacb5fc8f7ca43789ed3647b332ba9ca9fb`.
Instead of a plain BRK after `initialize_screen`, the checkpoint instruction
at VA `0xfffffe0007aaf9ec` branches to a cave at VA `0xfffffe000748d714`
(early-vector slot 14 plus 20 bytes; `diag_arm_sentinels()` rewrites only the
first five words of each early slot). The cave calls `vm_commpage_init`,
reads `sched_tick` (VA `0xfffffe0007b94698`), busy-waits 6,500,000
`CNTVCT_EL0` ticks (500 ms at the measured 13 MHz) with interrupts left
enabled, re-reads `sched_tick`, subtracts, and executes BRK #0x5357.

Same-boot `tick-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe000748d744`, x0 `3`. The
`sched_tick` delta of 3 over 500 ms proves timer interrupts were delivered,
XNU's normal IRQ path ran, and the scheduler maintenance continuation ran
three times. The wait uses CNTVCT directly, so a broken interrupt path would
still have returned with x0 0. Predicted written-partition SHA256
`2f895215...` matched the full readback; the complete original was written
back and verified as `2931647a...`. Tool `tools/xnu_tickprobe.py`; 44 tools
tests pass. The image uses the same dual-slot selective vectors, 128 MiB
cleanup range and TTBR0 restore.

**Failed: `bsd_init`-entry big jump (hardware, infinite reboot).**
`shim/out/entryprobe-el1-bsd-init-brk.img`, 26,562,560 B, SHA256
`7a5b60b59c21fa4d69b4b0f1f6a13e0ea230c7e10ec2eecb8f94bd9136badee0`,
payload `cc0ec308...`, BRK at entry offset `0x623758` / VA
`0xfffffe0007aafbe0` / PA `0x101aafbe0`, replacing the `bl bsd_init`
(`0x97f2f912`) at the end of `kernel_bootstrap_thread`. The written partition
readback matched the predicted SHA256 `b07b8a78...`. The device did **not**
return; it entered an infinite reboot loop and no SWEET-ENTRY record was
captured. Do not reuse this image.

Recovery (completed): user entered fastboot manually;
`fastboot flash boot_a backup/boot_a-stock-trim.img` (20,873,216 B, SHA256
`fe6e8fbf...`) returned Sending/Writing OKAY. `fastboot reboot recovery`
reported a USB status failure, but Recovery came up in 27 s. The complete
67,108,864-byte original was then written and read back as
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`.

The cause was narrowed by the next test: the post-lockdown checkpoint
passed, so lockdown and the diagnostic relay are not the failure. The
remaining candidates are the CONFIG_XNUPOST tests (`kernel_list_tests` /
`kernel_do_post`), `PE_lockdown_iokit`, `vm_set_restrictions`,
`kperf_init_early`, or the final return from the `bl bsd_init` site.

**Completed: post-lockdown checkpoint (hardware PASS).**
`shim/out/entryprobe-el1-lockdown-brk.img`, 26,562,560 B, SHA256
`f68a28a8f3c6c564e0b89ea540ef59828175ab20f311996a80a4c51c5e6b074a`;
payload SHA256 `c5b99715dc5886e327d2d1ef9cefa3d8db2550c901b4ce3949a5b66c0014a93e`.
BRK at entry offset `0x6236cc`, payload offset `0xaabf44`, VA
`0xfffffe0007aafb54` / PA `0x101aafb54`, immediately after
`bl machine_lockdown`, before `bl kernel_list_tests`; it replaced
`bl kernel_list_tests` (`0x97e41107`).

Same-boot `lockdown-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aafb54`, x0
`fffffe00072e0000`. `OSKextRemoveKextBootstrap` and `machine_lockdown`
returned, and the selective vector relay, 128 MiB cache cleanup and bootstrap
TTBR0 restore still work after the ROX lockdown. Written-partition SHA256
`d0537227...` matched the readback; the full original was restored and
verified as `2931647a...`.

Memory review: `bootstrap_pagetables` (VA `0xfffffe000717c000`, PA
`0x10117c000`, the TTBR0 restore page) is in `__DATA_CONST`;
`arm_vm_prot_finalize()` frees `__BOOTDATA` at VA `0xfffffe0008080000`
(PA `0x102080000`), not this page. The vector stubs are in `__TEXT_EXEC`
and stay executable after the ROX lockdown. This is now hardware-proven.

**Completed: bootstrap-thread checkpoint (hardware PASS).**
`shim/out/entryprobe-el1-thread-brk.img`, rebuilt with dual-slot selective
vectors, 26,562,560 B, SHA256
`154449c1e72284e4111a353c969549d7a810c9a7906cafe6b2160191a39be872`.
Payload SHA256 `3b44b2bf052a865074a19b51ad0b447e0f5552f2b5cda370233fc2ef75d08d06`.
BRK offset `0x6232c4` at VA `0xfffffe0007aaf74c` / PA `0x101aaf74c`, after
`current_processor` returns inside `kernel_bootstrap_thread`. Cleanup range
stayed `[0x100000000, 0x108000000)` (128 MiB).

Same-boot `thread-brk-v2-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007aaf74c`, with
x0 `fffffe0007b88180`. Kernel thread creation, `load_context` and the
bootstrap thread's first C call therefore ran on hardware. The ESR is the
inserted BRK. Automatic Recovery return and the complete original
67,108,864-byte boot_a restoration/readback SHA256 `2931647a...` were
verified; the written partition matched the predicted `c57c8252...` hash.
Per user direction this image was tested directly, without host emulation;
evidence is the BRK record plus the full restoration check.

**Completed: selective diagnostic vectors (hardware PASS; EL1t root cause).**
- XNU executes with SPSel=0 (EL1t), so a BRK from kernel C code takes vector
  slot 0, not slot 4. The first selective image patched only slot 4; the BRK
  reached XNU's original EL1t handler instead, which reset the device. The
  bootstrap-thread checkpoint itself was never implicated.
- Fix: the stub is installed in **both** synchronous slots 0 and 4; each
  copy has its forward B and its relay B patched at install time. The stub
  saves/restores x16/x17 and preserves every GPR on the forward path.
  IRQ/FIQ/SError and EL0 synchronous slots stay original; the diagnostic
  relay lives in unused AArch32 slots 12/13.
- Hardware PASS on the known-good vm-return checkpoint:
  `shim/out/entryprobe-el1-vm-return-sel.img`, SHA256
  `81652ccf2912acb70630fe781985386eedce35c12cf90ad13f9f7d8fb50ceced`,
  partition `281a4a26...`. `sel-vm-return-v2-dmesg.txt` records
  `stage=87 fault=1 esr=f2005357 pc=fffffe00073644d8`, x0
  `fffffe00080bbbc0`; automatic return and full restore verified.
- 23 tool tests and `selective-stub-microtest.txt` pass. Commits `5f3ba55`
  (initial) and `42d6582` (dual-slot fix).

**Historical (superseded): first bootstrap-thread attempt with slot-4-only vectors.**
`shim/out/entryprobe-el1-thread-brk.img`, 26,562,560 B, SHA256
`1787a79128f3eb39edabcbdbadbc0fd56ab44203806f5835f7685ac6151f0dcc`.
Payload SHA256 `3b44b2bf052a865074a19b51ad0b447e0f5552f2b5cda370233fc2ef75d08d06`.
BRK offset `0x6232c4` at VA `0xfffffe0007aaf74c` / PA `0x101aaf74c`, after
`current_processor` returns inside `kernel_bootstrap_thread`. Selective
vectors; cleanup range stayed `[0x100000000, 0x108000000)` (128 MiB).
Expected record: `pc=fffffe0007aaf74c`.

The device did not return to Recovery within 45 s and entered a reboot loop;
no SWEET-ENTRY record was captured. The user entered fastboot manually. The
20,873,216-byte trimmed stock prefix was flashed (fastboot printed pipe
errors, but `Sending` and `Writing` both returned OKAY), and
`fastboot reboot recovery` reported "Command failed" yet the device booted
Recovery. The complete original 67,108,864-byte boot_a was then written and
read back; SHA256 `2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`
verified. The device is in Recovery. Evidence: `thread-brk-restore.log` and
`thread-brk-restored.img`.

The cause is established by the later dual-slot hardware pass: XNU executes
with SPSel=0, so the BRK took vector slot 0 (EL1t) while this image patched
only slot 4. It reached XNU's original EL1t handler and reset. The
bootstrap-thread checkpoint itself was never implicated.

The earlier slot-4-only selective image `4d67b49b...` and its model evidence
are superseded. The `sched_init` fallback image `e7fe406e...` was also built
with slot-4-only vectors; do not use it without rebuilding.

**Completed: full vm_mem_bootstrap return (hardware PASS).**
`shim/out/entryprobe-el1-vm-return-brk.img`, 26,562,560 B, SHA256
`99d68ed6d5c870070a116e223b7ba29bd76b1e619c34e444f05146c92b1106fa`.
Payload SHA256 `fac094b493eb2156f627c4ab96247db4db314e8bbef644fed38d39b9b35b1a2d`.
BRK offset `-0x127fb0` at VA `0xfffffe00073644d8` / PA `0x1013644d8`, the
first instruction after `bl vm_mem_bootstrap` in `kernel_bootstrap`. Same
shared relay, high vectors, TTBR0 restore and x0 recorder; cleanup range
stayed `[0x100000000, 0x108000000)` (128 MiB).

Same-boot `vm-return-brk-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe00073644d8`, with
x0 `fffffe00080bbbc0` — identical to the host model. vm_retire_boot_pages,
vm_map_init, kmem_init (including the KMEM and ZALLOC startup phases and
zone_init) and vm_fault_init therefore all returned, and kernel_bootstrap
resumed after the complete VM bootstrap. The ESR is the inserted BRK, not
an unexpected kernel fault. Automatic Recovery return and the complete
original 67,108,864-byte boot_a restoration/readback SHA256 `2931647a...`
were verified; the written partition matched the predicted `376eb489...`
hash. Evidence: manifest, same-boot dmesg, before/written/restored images
and the emulation log. Preparation commit `3d638e1`. No source changes and
no new CI.

Host model full-path PASS (`vm-return-brk-emulation.txt`). At the trap:
TTBR0 empty `0x1024d8000`, TTBR1 `0x1024dc000`, DAIF masked, VBAR still
`0xfffffe0007488000`, GIC state unchanged. `vm_pages` still maps PA
`0x102ac4000..0x1038c16cf`, and `avail_start` stays `0x102838000`. True
translated-PA stores (the hook reports physical addresses) total 20,021,604
with max PA `0x1039e3fff`, inside the 128 MiB range. Bootstrap-TTBR0
identity aliases cover the whole 128 MiB window and were probed OK through
`0x220000000`; `0x22e000000` is **unmapped**, so do not extend the clean
limit beyond the validated window without new evidence. Six high kernel-map
VAs fault in `mmu_kvtop` (AT/PAR.F), expected for unmapped probes. The
bootstrap-TTBR0 restore, the 128 MiB clean loop and the return to stock
Linux with original SCTLR/SP/SPSel/DTB all pass in the model. These are
host-model results; the hardware result is the BRK record plus the full
restoration check.

**Completed: zone/object bootstrap checkpoint (hardware PASS).**
`shim/out/entryprobe-el1-zone-object-brk.img`, 26,562,560 B, SHA256
`e4231b9071f0a559c72a8d4961acfe895e2a91eb2ebae525647be1704018695a`.
Payload SHA256 `878148b67d4b242e294b716322ae87d60a2a8da99c2dfbfc8acb0e18d8233fc4`.
BRK offset `0x633648` at VA `0xfffffe0007abfad0` / PA `0x101abfad0`
replaces `bl vm_retire_boot_pages`, so it stops only after both
`zone_bootstrap` and `vm_object_bootstrap` return; that single-instruction
empty retire call is deliberately not executed. Same shared relay, high
vectors, TTBR0 restore and x0 recorder; cleanup range stays
`[0x100000000, 0x108000000)` (128 MiB).

Same-boot `zone-object-brk-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007abfad0`, with
x0 `fffffe0007b482a0`. vm_page_bootstrap, zone_bootstrap and
vm_object_bootstrap therefore all returned inside vm_mem_bootstrap; the
ESR is the inserted BRK, not an unexpected kernel fault. Automatic Recovery
return and the complete original 67,108,864-byte boot_a restoration/readback
SHA256 `2931647a...` were verified. The written partition matched the
predicted `ddef7f6a...` hash. Evidence: manifest, same-boot dmesg,
before/written/restored images and the emulation log in the evidence
directory. Preparation commit `1e233f4`. No source changes and no new CI.

Host model `zone-object-brk-emulation.txt` had predicted the same x0 and
PC. At the trap: TTBR0 empty `0x1024d8000`, TTBR1 `0x1024dc000`, DAIF
masked, GIC model unchanged. `vm_pages` still spans VA
`0xfffffe000eb38000..0xfffffe000f9356d0`; sampled first/last mappings are PA
`0x102ac4000` and `0x1038c16cf`, inside the cleanup range. `avail_start`
stayed `0x102838000`. The earlier `0x1024fbfff` store figure came from a
hook that treated physical addresses as virtual and is superseded: the
later state run measures true translated-PA stores through a deeper point
at max `0x1039e3fff`, so this checkpoint's 128 MiB range was sufficient.
Bootstrap-TTBR0 identity aliases across the whole 128 MiB window, the
high-vector page, the cleanup loop and the return to stock Linux with
original SCTLR/SP/SPSel/DTB all passed. These remain host-model results, not
hardware execution; the hardware result above is the BRK record plus the
full restoration check.

Evidence is ignored under `research/matisse/20261022-xnu-resume/`.
Each test below had explicit user approval including restoration. This LK
has no working `fastboot boot`; tests temporarily replace boot_a. A new
boot_a test requires separate confirmation per AGENTS.md. Do not write
vendor_boot, userdata, GPT, bootloaders, recovery, modem or persist.

**Observed results, in order:**

- Exact display control `entryprobe-display-kick-hw-ok.img`, 20,836,352 B,
  SHA256 `b8cec21a912dc978b7b78fddd343253e9a93b95b3d91e275bca0fe0bc0eeee3f`:
  user saw test screen and moving green bar; same-boot Recovery log had no
  OVL/CMDQ timeout errors. The old control reports `0x500007` despite visible
  refresh (known FRAME_DONE-check limitation).
- `entryprobe-el1-display.img`, 20,836,352 B, SHA256
  `a3131110a6a7876d08ade3518e87fdcf9a557b7500eecb2f43722f060e9dd4f0`:
  user saw EL1 text; same-boot stage `0x74`, fault 0, display_error 0,
  display result 1. **Actual initial entry is already EL1** despite LK's
  misleading `via EL2` message. MIDR `0x410fd462`, MPIDR `0x81000000`,
  CNTFRQ 13 MHz, SCTLR `0x30d00980`. EL2/HVC return branch remains untested.
- Full shim `shim-boot-xnu-resume.img`, 7,008,256 B, SHA256
  `b89fcc7c29cd9faa9c200c4ba7713bf1ad55c7bd9196d0e3271d0fd448eefafb`:
  user saw only Redmi logo; forced fastboot entry and full recovery completed.
- Direct loader/jump `entryprobe-el1-xnu.img`, 26,562,560 B, SHA256
  `bee6ee32f76f7faa6c1df8736f4eef54b81f1f5978bc087507cfabe10ec73c42`:
  user saw only logo, no Recovery; host later observed preloader USB.
  Full boot_a restored. The logo alone does not establish an execution stage.
- **Load-only `entryprobe-el1-load.img`, 26,562,560 B, SHA256
  `9e6c2bec44ccef8dc9e4681ca3c360f9930805b4003be4a0ebd80eb52a568c52`:**
  returned automatically to Recovery. Same-boot `el1-load-dmesg.txt` has
  `stage=7f fault=0 esr=0 far=410fd462 pc=81000000`, timer INTID 27.
  The loader reached its last S7 ID checkpoint and returned through
  LOADER_NO_JUMP, after copying XNU segments, ADT and boot_args. FAR/PC here
  are MIDR/MPIDR diagnostic values, not exception addresses. Full boot_a
  restoration and the 64 MiB SHA256 above were verified afterward.

All tests restored the full original partition. A trimmed fastboot restore
alone previously left one 4 KiB block of test data beyond the prefix; always
follow it with a full Recovery write and complete readback hash. Recovery
/tmp is cleared each reboot: re-push the backup before each restoration.
Use `dd ... && sync` and accept adb state `recovery` as online. Recovery dd
can append statistics to stdout or silently truncate streaming output: use an
on-device temporary readback file plus adb pull and verify its exact length.

**Build and implementation:** latest successful kernel CI remains
`Jinsens/ios-sweet` run **39640336293**, commit `dd9752e`; no new CI run.
`xnu/out/ci-39640336293/kernel.development.sweet` SHA256
`bc308789486ee96b380ce374bc7fe4c1894f4a115da9662b4cb0cf9a016b543c`.
Debug payload `shim/out/matisse-xnu-debug.payload` SHA256
`b6eafe9a953aea1d18a34d579e43e172f1bb0f60a289a51b303d463e13e8fe95`
adds only `-enable_kprintf_spam`. XNU entry PA `0x10148c488`, boot_args
`0x1024d4000`, top `0x1024d8000`; physBase `0x100000000`, memSize
`0x12e000000`. Probe Image 65,552,896 B; marker offset 48,947,200;
stock Linux +2 MiB and live DTB `0x47c80000` remain intact.

The timer reader now handles GIC interrupt tuples of **four cells on
MT6983 and three on sweet**, yielding virtual INTID 27 and physical 30
against the live tree. 27 host tests pass, all nine original diagnostic
levels plus el1-xnu/el1-load cross-compile. XNU video mapping checks pass
11 cases. Full repo validation still fails on six pre-existing CRLF shell
files; do not report it as passing.

Commits: `712b44f` (display-control/restoration handoff), `031d329` (EL1
probe, hardware-tested display refresh, initial timer fix). Commit `850e1d2` records the load-only control and four-cell timer work. Pre-existing
`shim/build.sh` --kick and `shim/shim.c` KICK_ONLY work remains untested;
the old mkentryprobe `xnu` level is likewise untested. Preserve these edits.

**Next concrete action:** extend the bootstrap-thread checkpoint to
`sched_startup`/`idle_thread_create` and then to `thread_daemon_init`, still
with the dual-slot selective vectors. After that, verify timer interrupt
delivery with normal XNU vectors and a running thread. Per user direction,
go directly to device tests; do not spend time on full-boot emulation.
Re-check the dirty physical range at each deeper checkpoint; the 128 MiB
window covered this thread entry but is not proven for later allocations.
The bootstrap-thread image `154449c1` passed and was fully restored;
selective vectors `81652ccf` passed at the vm-return checkpoint; the earlier
full-return `99d68ed6`, zone/object `e4231b90` and post-vm_page `e9b81b88`
also passed. Rebuild the stale `sched_init` image before any use. No device
test is currently authorized.
The requested standalone Chinese report is
[docs/matisse-xnu-ios-roadmap-zh.md](docs/matisse-xnu-ios-roadmap-zh.md):
complete progress, minimal userspace, real iOS, UFS, touch, USB and acceptance
criteria. The latest user request is to receive this report after the test.

**Source/Recovery corrections established while preparing the report:**
- This exact CI kernel has only RET at vm_retire_boot_pages
  (`0xfffffe0007439678`); the source implementation is empty. Earlier warnings
  treating that function itself as a page-table teardown are too conservative.
  Later allocations/mapping changes still require lifetime/cleanup review.
- The diagnostic vector table must not replace normal XNU IRQ/SVC/page-fault
  handling when proceeding into interrupts and userspace. Preserve/restore
  normal vectors or implement a selective diagnostic handler first.
- Touch is **ST FTS**, not the historical FocalTech label: live spi6.0
  compatible is `st,spi`, bound driver `fts`, Recovery module
  `fts_touch_spi.ko`, firmware `st_fts_L11.ftb`. Exact IC still needs ID
  confirmation. SPI6/CS0/mode0/12 MHz and 14400x32000 coordinate range agree
  with device logs and vendor DTS. Evidence: `touch-driver-binding.txt`.
- Pending shim UFS review: GPT LE fields are read using be32 (entry LBA is
  also 64-bit), write UPIU lacks W flag, timeout assumes 19.2 MHz instead of
  measured 13 MHz. These are source findings, not a proven explanation of
  earlier host-takeover failure; no UFS source changes made this turn.
- Existing XNU UFS init also launches keys/touch/USB; decouple these for
  independent RAM-disk driver tests. Current payload has no RAMDisk/root arg.

Do not reuse the bootstrap-table relay at later stages without separately
proving its tables, vectors, saved state and cleanup mappings survive.
The shared relay restores bootstrap TTBR0 only on exception; the older
per-slot relay cannot be used after identity-map teardown.

**Completed entry BRK test:** `entryprobe-el1-entry-brk.img`, 26,562,560 B,
SHA256 `386420a822f8e7e4c368d5882b1509cdcbdbe70ffd08bf32e1d96cc84a824ded`.
Same-boot Recovery captured `stage=87 fault=5 esr=f2005357 far=0 pc=10148c488`.
It returned automatically; full 64 MiB restoration verified. The first
exec-out readback silently truncated at 59,225,600 B; a full on-device tmp
read plus adb pull completed and matched the original hash. Always validate
length; use tmp readback + pull if streaming is truncated.

**Completed pre-VBAR test:** `entryprobe-el1-vbar-brk.img`, 26,562,560 B,
SHA256 `1291e310667744208e5328d72ca9c7c293e3ea2859dbddf86856725f57dd4660`.
Same-boot Recovery captured `stage=87 fault=5 esr=f2005357 far=0 pc=10148e104`.
The original entry branch and first six start_first_cpu instructions ran.
Automatic Recovery return and complete original boot_a hash verified.
Commits: `6c9772e` (bounded breakpoint tooling), `1c7dc60` (entry result),
`286923e` (pre-VBAR result). Exact manifests/logs/readbacks are in the evidence
directory above. Neither test proves the remaining original initialization.

**Completed: early-vector relay + page-table checkpoint**
`shim/out/entryprobe-el1-tables-brk.img`, 26562560 B, SHA256
`276ffdd70208c8190f46614b6f09893fb3e9d822ec09ed81f1d8b2f5dfcead4d`.
Payload SHA256 `69f1676576e11b2f5bcac36c49a255c4a5b7fa6d63442c360fe9ee657eca4c06`.
One payload BRK replaces common_start's first instruction at `0x10148e444`
(entry offset `0x1fbc`), after bootstrap/video page tables are prepared and
before TCR/TTBR/MMU configuration. `--xnu-vector-offset 0xb78` validates the
16 early spin-vector slots at `0x10148d000` and enables the loader to replace
each slot with a 64-bit register-indirect branch to the corresponding probe
vector. XNU's original VBAR write remains intact. Diagnostic use is limited
to MMU-off checkpoints; the probe prefix is not in XNU's page tables.
The stackless exception recorder now restores the initial SPSel before SP,
because XNU switches to SP_EL0 with an unmapped KVA before this checkpoint.
Expected ESR `0xf2005357`, PC `0x10148e444`, fault slot **1** (EL1t sync).
An earlier real exception will instead record its own ESR/PC.

31 host tests pass. Full image executed in Unicorn with modeled display/
clock and manually modeled BRK exception entry: actual loader, XNU code up
to common_start, generated vector trampolines and probe fallback ran; original
SP, SPSel and DTB argument restored at stock Linux entry. This is host-only
evidence, NOT hardware success. Log `tables-brk-emulation.txt`.
Predicted written-partition SHA256 `a5c00b3edbc254030c4d962bd6b9ccfb830aea6d1fff7ed443703680091f8ce0`.
**Hardware PASS (explicitly approved):** same-boot `tables-brk-dmesg.txt`
recorded `stage=87 fault=1 esr=f2005357 far=0 pc=10148e444`. The kernel
executed boot_args loads, stack changes and page-table initialization up to
common_start. The relay handled an EL1t exception and returned automatically
to Recovery. Full 64 MiB original boot_a hash verified after restoration.
Next prepare a breakpoint before the high-VA VBAR switch at `0x10148e4f8`,
after TCR/TTBR/MAIR and TLB/cache operations, still before MMU enable.

**Completed: MMU configuration checkpoint**
`shim/out/entryprobe-el1-mmu-config-brk.img`, 26562560 B, SHA256
`36228bdb46ade73cb03dc5fc628c5cf675b5ac87956398d4c17f08e26c334f51`.
Payload SHA256 `3f7c78fbc5b7f1ab25a18d88cc3afc4f64f57d61010721fe4fed6aca181d2f58`.
BRK offset `0x2070` replaces MSR VBAR_EL1 at `0x10148e4f8`, just before
XNU changes from low physical vectors to its high virtual vectors.
The same early-vector relay remains enabled. Host execution with modeled
MMIO/BRK reached `0x10148e4f8`, then returned to stock Linux with original
SP/SPSel/DTB restored (`mmu-config-brk-emulation.txt`). Hardware PASS with explicit approval: same-boot Recovery recorded
`stage=87 fault=1 esr=f2005357 far=0 pc=10148e4f8`. All pre-MMU
TCR/TTBR/MAIR and TLB/cache operations executed. Automatic return and full
64 MiB restoration verified (`mmu-config-brk-dmesg.txt` and readback). Predicted partition SHA256
`d99e68b1110f8b421ecd8bf88a0f6ff0de8872962381de66d391761f22b4ce00`.
Device remains in restored Recovery; no new write yet.

**Completed: immediate MMU-on checkpoint**
`shim/out/entryprobe-el1-mmu-on-brk.img`, 26562560 B, SHA256
`7937d19c9ef74c5c7c9557098596902c054010b4553d6a7a6e2f9d152fc15f16`.
Payload SHA256 `38ac4bda2a6a711c45adc09ecd97488313415c14efddb0cfbac68aef463aaa9e`.
BRK at entry+`0x20a8` = `0x10148e530` replaces RET to arm_init after
MMU enable, CPACR and TPIDR initialization. No kernel C code runs.
The low vector relay is retained. `--xnu-high-vector-offset=-0x4488`
validates branch/NOP slots and installs a copied stackless assembly relay
in the high vector table at PA `0x101488000`. It first branches from the
high virtual mapping to its own physical identity alias, then disables
M/C/I and jumps back to the probe. The probe restores original SCTLR and
SPSel/SP. **Only safe for the immediate checkpoint before cached C writes
or bootstrap identity-map teardown; do not use for arbitrary later stages.**
XNU's original high VBAR and SCTLR writes execute unchanged.

32 host tests pass. Unicorn's CPU model advertises MMFR0 `0x1124` (no 16K
granule), so CPU-mode emulation faults at MMU enable and cannot validate
this target. With a modeled 16K translation callback walking XNU's actual
L1/L2 descriptors, actual startup/relay code reaches `0x10148e530` and
returns to stock Linux with original SCTLR/SP/SPSel/DTB restored. This is
host model evidence only, not a real CPU transition. Logs:
`mmu-on-brk-emulation.txt`, `mmu-on-brk-modeled-emulation.txt`.
The earlier independent hardware MMU probe did verify 16K support.
Predicted partition SHA256 `557bdf81d00c77f5d81ee683de2989a4257cce30cccfe329193ae3a66fc8ae56`.
**Hardware PASS (explicitly approved):** same-boot Recovery captured
`stage=87 fault=1 esr=f2005357 far=0 pc=10148e530`. XNU's real high VBAR
and SCTLR writes executed, then CPACR/TPIDR and boot_args VA conversion.
The high-vector relay returned automatically to Recovery. Complete original
64 MiB boot_a hash verified. Evidence: `mmu-on-brk-dmesg.txt`, manifest and
restored image. Next: a bounded early-C checkpoint with explicit cache
clean/invalidate before leaving XNU's bootstrap identity mapping.

**Completed: bounded early-C checkpoint**
`shim/out/entryprobe-el1-early-c-brk.img`, 26562560 B, SHA256
`98e8829d84edeabf476c0c837cfefe1a132d993e6baee39082e3bf54eaa835ff`.
Payload SHA256 `37ba85594c75f1ec7b4260a6ce20a4bfc56d44ee0385cfdbab6510cf3acdba6e`.
BRK offset `0x649428` replaces the BL to arm_set_kernel_tbi at
VA `0xfffffe0007ad58b0` / PA `0x101ad58b0`. It stops after arm_init's
prologue, sweet_early_init and sweet_early_mark; before later initialization.
The high-vector relay now has optional `--xnu-clean`: after switching to
its physical alias it cleans/invalidates each D-cache line in
`[0x100000000, 0x1024d8000)` using CTR_EL0's line size, then disables MMU
and returns to the probe. This covers the kernel's static globals/stacks,
ADT and boot_args used here. It still requires bootstrap identity mappings
and must not be reused after VM remapping/allocation without a new review.
The copied relay is 120 bytes and linker-checked to fit each 128-byte slot.

33 host tests pass. With modeled MMIO/16K translation/BRK entry, actual XNU
C code reached the high-VA checkpoint, the cache-maintenance loop executed,
and original SCTLR/SP/SPSel/DTB were restored at Linux entry. Emulation does
not model cache coherency or real hardware timing. Evidence:
`early-c-brk-emulation.txt`. Predicted partition SHA256
`370a7bc51f1cb74fce3d0b966851664927707668eb8ce06d83a7882ff25a398e`. Device remains restored in Recovery;
**Hardware PASS (explicitly approved):** `early-c-brk-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007ad58b0`. arm_init's
C prologue, sweet_early_init and first sweet_early_mark ran. Cache cleanup
and automatic return to Recovery succeeded. Full 64 MiB original boot_a
hash verified. MMU-on milestone `9161917`; bounded cleanup tooling `ccb1d03`.
Next: stop before arm_vm_init while bootstrap identity mappings still exist.

**Completed: pre-VM checkpoint**
`shim/out/entryprobe-el1-pre-vm-brk.img`, 26562560 B, SHA256
`25d5d260f4eb2f7ebb215062d81b8e97c5eabaebfa84aed9a17f1f015e0aef63`.
Payload SHA256 `b7a0fe56181f8ae2104b2258c08f1bf7f6aedfcaf58e55dd3737cf7d17117a38`.
BRK offset `0x649c9c` at VA `0xfffffe0007ad6124` / PA `0x101ad6124`
replaces the call to arm_vm_init. Same bounded cache cleanup and low/high
exception relays as the hardware-passed early-C test. No VM-map teardown
or later device driver initialization executes. Static bootstrap storage
remains inside the cleanup range.

Host emulation reached this checkpoint and restored original SCTLR/SP/SPSel/
DTB using Unicorn MAX (LSE atomics supported), modeled 16K translation,
MMIO and exception entry. Default A72 model stops at LDADD; that is an
emulator feature limitation, not evidence of a device failure. Log:
`pre-vm-brk-max-emulation.txt`. Existing 33 host tests remain passing;
no new source changes for this image. Predicted partition SHA256
`f42873e5419cdc5a786c4f933f09d947775294c48333f11d042c800f9f90d43c`.
**Hardware PASS (explicitly approved):** same-boot `pre-vm-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007ad6124`.
Platform setup, CPU topology, bootstrap thread, CPU/timer initialization and
early startup reached arm_vm_init. Automatic Recovery return and full
64 MiB original boot_a hash verified. Early-C result commit `ec4d84f`.
Next: stop just before the first TTBR1 switch inside arm_vm_init; expand
cache cleanup to cover newly allocated page tables.

**Completed: final-table construction checkpoint**
`shim/out/entryprobe-el1-final-tables-brk.img`, 26562560 B, SHA256
`340b962813b4bc3cd6cf699525c5003b7643087becc3915dba0390e42de5ec52`.
Payload SHA256 `9ccf10dd33d42a9d79588de2374b8bcbbff759f56e757d2ce5345ea4813f62fc`.
BRK entry-relative offset `-0x86b0` at VA `0xfffffe0007483dd8` /
PA `0x101483dd8` replaces BL set_mmu_ttb_alternate. Original bootstrap
TTBR0/TTBR1 are still active, preserving the validated fallback.
`--xnu-clean-limit 0x104000000` extends cleanup to the first 64 MiB of
managed RAM to include arm_vm_init's new page tables. Generator validates
the extension is aligned, covers the original bootstrap range, and stays
inside the declared managed RAM. Signed offsets now allow checkpoints in
functions located before __start.

34 host tests pass. Unicorn MAX with modeled 16K translations and AT/PAR
operations executes through this checkpoint and returns to Linux with
original SCTLR/SP/SPSel/DTB. Allocation and final table pointers are recorded
in `final-tables-brk-emulation.txt`; alloc end is within the 64 MiB cleanup.
CPU-mode AT cannot handle this model's missing 16K support, so AT/PAR is
explicitly modeled. Predicted partition
SHA256 `23320b4789d653d980e8c1d5320333cff4ca94da62ed4ab61f8f224e24b0c218`.
**Hardware PASS (explicitly approved):** same-boot `final-tables-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007483dd8`.
arm_vm_init built the formal tables and reached its first TTBR1 switch.
The ESR is the inserted BRK, not an unexpected kernel exception. Automatic
Recovery return and the complete 64 MiB original boot_a hash were verified
(`final-tables-brk-manifest.json` and `final-tables-brk-restored.img`).
Pre-VM milestone commit `21fecbe`; bounded cleanup tooling `cb96eaa`.
No new kernel CI run; the tested kernel remains run `39640336293` / `dd9752e`.

**Completed: first formal TTBR1 switch checkpoint**
`shim/out/entryprobe-el1-ttbr1-brk.img`, 26,562,560 B, SHA256
`e9fbcb8119f7a0ca1153306dab5bb0d015b15c34f1f803ada5df3bf0ae072cf2`.
Payload `shim/out/matisse-xnu-ttbr1-brk.payload` SHA256
`66fae84c6e5d019989189ae9657a7a25106d5188939a81c03194a5b01fc0a873`.
BRK entry offset `-0x86ac` at VA `0xfffffe0007483ddc` / PA `0x101483ddc`
replaces BL ml_enable_monitor, immediately after set_mmu_ttb_alternate
returns. Same 64 MiB cache cleanup and low/high vector relays as the last
hardware-passed image. Predicted full written-partition SHA256
`2aab2ebd0f3d4fb94242bcc3f6b3179107695d2569d76b9fd0f1dfc133e0d595`.

Image cross-compile succeeded. Host modeled execution passed
(`emulate-ttbr1.py`, `ttbr1-brk-emulation.txt` in the ignored evidence folder).
The explicit 16K walker now handles L3 pages and privileged permissions;
after the actual TTBR1 MSR it flushes modeled translations before continuing.
New TTBR1 `0x1024dc000` maps the checkpoint and high vectors RX at their
expected PAs; unchanged TTBR0 `0x10117c000` retains executable identity
mapping for the relay and readable identity aliases across the cleanup
range. The BRK, cleanup and Linux return all execute; original SCTLR/SP/
SPSel/DTB are restored. Unicorn's fixed stop address itself triggered an
unmapped-address lookup on a TLB flush; using a Linux-entry execution hook
avoids that host-only issue. MMIO, translation, AT/PAR and exception entry
remain modeled, so this is NOT a hardware result. No tracked source changes
or new kernel CI were needed. Final-table hardware result commit `780b9c5`;
TTBR1 preparation `2c5355f`.
**Hardware PASS (explicitly approved):** same-boot `ttbr1-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007483ddc`.
The original set_mmu_ttb_alternate executed and returned with the formal
TTBR1 installed. Automatic Recovery return and complete original 64 MiB
boot_a restoration/hash were verified (`ttbr1-brk-manifest.json`).

**Completed: TTBR0 teardown + relocated early-console checkpoint**
`shim/out/entryprobe-el1-ttbr0-off-brk.img`, 26,562,560 B, SHA256
`c73eb57fafc6efe21aba965f54b28e3442f23aa64cf0cd8e26de8c36714c2bba`.
Payload SHA256 `d4449cd59bcad9cb5988cb46c91ecde3870a51b1d80624c4208f9ee9a1ca1170`.
BRK entry offset `-0x8670` = VA `0xfffffe0007483e18` / PA `0x101483e18`,
after the original TTBR0 replacement, TLB invalidation and early mark 10;
before kva_active becomes true. Predicted full partition SHA256
`98a72e930421759bb74002e166e5e1c887f88cb017c7e9ba9ab794a428f86d11`.

New `--xnu-restore-ttbr0 0x10117c000` is derived from the kernel's
bootstrap_pagetables symbol and modeled actual TTBR0. The builder checks
alignment, segment bounds, initially zero storage and cache-clean coverage.
It does not prove runtime table contents: those are checked by the host
walker at this checkpoint. The shared 2 KiB high vector template retains
all 16 slot entries, holds common code in slot 0 (end +0x7c) and literals
behind slot 1's branch (end +0xb0). Linker assertions enforce boundaries.
On exception it restores that TTBR0, invalidates TLBs, branches to an
identity alias, cleans `[0x100000000,0x104000000)`, disables MMU/caches and
returns to the slot-specific probe recorder. Normal XNU execution retains
its original TTBR0 teardown. This is only validated while bootstrap tables,
high vector mappings and compatible TCR survive, and allocations stay in
the cleanup range; do not assume this supports arbitrary later checkpoints.

36 host tests pass. New image cross-compiles. Modeled execution reaches the
BRK with empty TTBR0 `0x1024d8000`, formal TTBR1 `0x1024dc000`, and alloc end
`0x1024fc000`; all 16 high-vector entries restore SCTLR/SP/SPSel/DTB and
return to Linux. Logs: `ttbr0-off-brk-emulation.txt` and
`ttbr0-all-vectors-emulation.txt`. Translation/AT/MMIO/exception entry remain
modeled and cache coherence is not modeled.
Rebuilding the previous TTBR1 image without the new option reproduces
`e9fbcb81` byte-for-byte. No kernel CI run; TTBR1 hardware milestone `8a257b2`.
Shared-relay implementation commit `f1b6d14`.
**Hardware PASS (explicitly approved):** same-boot `ttbr0-off-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007483e18`.
Original TTBR0 teardown, TLB flush and relocated early-console mark 10
executed. The shared relay restored bootstrap TTBR0 and returned to Recovery.
Complete 64 MiB original boot_a hash verified after restoration; see
`ttbr0-off-brk-manifest.json` and full restored readback.

**Completed: complete arm_vm_init checkpoint**
`shim/out/entryprobe-el1-post-vm-brk.img`, 26,562,560 B, SHA256
`9ac98004aea8b30e9ab6a5a5d1360c66930d814d2ed2622154b438dbbb032b89`.
Payload SHA256 `534508b7776fc84aba87edd866b5a5373684a390d82e64f5c02136c4557399a2`.
BRK at entry offset `0x649ca0`, VA `0xfffffe0007ad6128` / PA `0x101ad6128`,
immediately after arm_vm_init returns to arm_init. Uses the hardware-passed
shared relay with bootstrap TTBR0 `0x10117c000` and cleanup ending
`0x104000000`. Predicted full partition SHA256
`d00c603d4c08ec89ed6be181b6cf10dbb4df8b6cef1a07e7b8ffa99393dec7cf`.
Cross-compile and modeled execution pass (`post-vm-brk-emulation.txt`):
reaches the return, allocation end `0x102838000` is inside cleanup, bootstrap
identity table contents still cover the cleanup aliases, current TTBR0 is
empty and formal TTBR1 still maps the high relay RX. Restores original
SCTLR/SP/SPSel/DTB and returns to Linux. Modeling limitations above still
apply. No new tracked source change, no new CI. TTBR0 result `7090a94`;
post-VM preparation `0e9e385`.
**Hardware PASS (explicitly approved):** same-boot `post-vm-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007ad6128`.
The entire arm_vm_init returned to arm_init. Automatic Recovery return and
complete 64 MiB original boot_a restoration/hash verified; evidence includes
`post-vm-brk-manifest.json` and restored readback.

**Completed: console initialization checkpoint with return value**
`shim/out/entryprobe-el1-post-serial-x0-brk.img`, 26,562,560 B, SHA256
`e9888fe6991bfa4c3084bb60d3b57ffe5c4567c25e144fae26bf2518642b840d`.
Payload `shim/out/matisse-xnu-post-serial-brk.payload` SHA256
`6b8478ff154070014df4c7bab9afd67a92f755b66913f0747a6d419d092454e0`.
BRK at entry offset `0x649cdc` = VA `0xfffffe0007ad6164` / PA `0x101ad6164`,
immediately after serial_init. Same shared relay and cleanup bound as the
post-VM pass. Predicted full written-partition SHA256
`c56ea9c53276c9b72ea9b913c66ae3716be18a6de7e99f0495562feafce57e9f`.
The ordinary probe banner now appends `x0`; the exception recorder captures
it before reusing that register. This distinguishes a successful return
from a skipped/failed console setup without repurposing FAR or PC.

36 host tests and cross-compile pass. Modeled execution reaches the BRK
with x0=1, fbcon_base `0xfffffe000a003000`, disp_base `0xfffffe000d4c0000`,
disp_ready=1 and alloc end `0x102838000`, then returns to Linux with x0=1
in the banner and original boot registers restored. The harness now models
kernel CNTFRQ/CNTPCT/CNTVCT instructions as well as prefix counters. Display
MMIO remains modeled; a successful host display state is NOT proof of
hardware panel refresh. Evidence: `post-serial-x0-brk-emulation.txt`.
The older `post-serial-brk` image `dabd026d` has no x0 field and was never
flashed; use the x0 image above. Full VM hardware milestone `c4c2014`.
Return-value recorder commit `e24a92c`.
**Hardware PASS (explicitly approved):** same-boot
`post-serial-x0-brk-dmesg.txt` records
`stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007ad6164 x0=1`.
serial_init returned success. Automatic Recovery return and complete original
64 MiB boot_a hash verified after restoration. No user panel observation has
been collected for this round; don't equate x0=1 with visible text.

**Completed: later platform initialization checkpoint**
`shim/out/entryprobe-el1-post-platform-brk.img`, 26,562,560 B, SHA256
`a89bd1c087c1a33311c4546db3b6044212c9400b46f401f47996a0a88d011ae1`.
Payload SHA256 `41a9bc023a3f4170c800b1fe85c578316ad348c525c01a6e0356fc6408d0785c`.
BRK at entry offset `0x649e50`, VA `0xfffffe0007ad62d8` / PA `0x101ad62d8`,
after kernel startup through phase 4, console creation/printf setup, idle
initialization and PE_init_platform(TRUE). Same relay/cleanup/x0 recorder.
Predicted partition SHA256
`fca03c37a66e2b863ca95cd666c25c8fc75532cb2f855aa9f87d2324aaa7acb8`.
Cross-compile and host modeled execution pass, including original-register
restoration and Linux return (`post-platform-brk-emulation.txt`). Its first
host attempt exhausted the 20M post-TTBR0 instruction bound in another
framebuffer clear; increasing that bound to 180M reaches the expected BRK.
Allocation end remains `0x102838000`; bootstrap table and high-vector checks
still pass. x0 at this void-function checkpoint has no success/failure meaning.
No new source change or CI run. Console hardware milestone `2ff0748`.
Preparation commit `6d264bc`.
**Hardware PASS (explicitly approved):** same-boot `post-platform-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007ad62d8`,
with x0 `fffffe00080bbc00` (not a status value at this checkpoint).
Original PE_init_platform(TRUE) returned. Automatic Recovery return and
complete original 64 MiB boot_a restoration/hash verified.

**Completed: CPU/timer/GIC setup before machine_startup**
`shim/out/entryprobe-el1-pre-machine-brk.img`, 26,562,560 B, SHA256
`34efc91a3be079fef6b793144e0f9d33289c70e008f628f155bc2e8468ad62fe`.
Payload SHA256 `b2d31f7170972ae4920524b90fbaad4d7bb6ff5daff30b366304c44729d63122`.
BRK offset `0x649f18` at VA `0xfffffe0007ad63a0` / PA `0x101ad63a0`
replaces BL machine_startup, after CPU timebase/timer setup, PE_init_cpu,
FIQ/GIC context initialization, chosen-property checks and early_random.
Same shared relay, x0 recorder and 64 MiB cache-clean range. Predicted
partition SHA256 `975bb3bb8c7d362c1061018e403c7a89d15add86c9f3dfcd855479a893a08e63`.

Cross-compile and modeled execution pass (`pre-machine-brk-emulation.txt`).
The first run stopped at a previously unmodeled GICR_TYPER read, PA
`0xc440008`, in pe_init_fiq. The host harness now models GIC MMIO and the
ICC_SRE/BPR1/PMR/CTLR/IGRPEN1 registers; CPU0 affinity/frame agree with
Recovery's observed redistributor 0 at `0xc440000`. Awake/RWP-clear state and
system-register behavior are model assumptions, not hardware measurements.
No interrupt delivery is modeled. At the checkpoint all DAIF bits remain
masked, modeled GIC enables PPI 27 and SGI 1, allocation end `0x102838000`
remains in cleanup, and the existing mapping/return checks pass. Normal Linux
entry follows with original SCTLR/SP/SPSel/DTB restored. This test exercises
real GIC setup for the first time in this XNU path. No new source changes or
CI run. Platform hardware result `3168dbb`; preparation `27e123d`.
**Hardware PASS (explicitly approved):** same-boot `pre-machine-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007ad63a0`,
x0 `fffffe0007177a68` (boot_args). CPU/timer/GIC context initialization,
chosen-property checks and early_random ran. Automatic Recovery return and
complete original 64 MiB boot_a restoration/hash verified.

**Completed: kernel_bootstrap through vm_page_bootstrap**
`shim/out/entryprobe-el1-post-vm-page-brk.img`, 26,562,560 B, SHA256
`e9b81b88927d98d3b1eef0876679f661ffc610ee498cc9a481b38692c267f461`.
Payload SHA256 `3d6f731bb3ad5e5fe2629ddc0489ba9ebe2f003ff988eda2246b47607ab9992d`.
BRK offset `0x633628` at VA `0xfffffe0007abfab0` / PA `0x101abfab0`,
after vm_page_bootstrap returns inside vm_mem_bootstrap; before zone/object
bootstrap and vm_retire_boot_pages. Uses the existing shared relay/x0
recorder with cleanup extended to `0x108000000` (128 MiB). Predicted
partition SHA256 `02d97949e29ca0de8386f88a869c2cfbaf71311c1b3b519ee1bf3e1d8c9ebc2c`.

Cross-compile and modeled execution pass (`post-vm-page-brk-emulation.txt`).
vm_pages spans VA `0xfffffe000eb38000` to `0xfffffe000f9356d0`; sampled
first/last mappings point to PA `0x102ac4000` and `0x1038c16cf` respectively,
inside the cleanup range. The modeled managed-RAM window is limited to
128 MiB; bootstrap identity aliases across that window and high vectors
are checked before executing the fallback. DAIF stays masked, GIC model
checks pass, and original boot registers are restored at Linux entry.
These are host model results. No new source changes or CI. CPU/GIC hardware
milestone `cfb5e8c`; this checkpoint preparation `0032264`.
**Hardware PASS (explicitly approved):** same-boot `post-vm-page-brk-dmesg.txt`
records `stage=87 fault=1 esr=f2005357 far=0 pc=fffffe0007abfab0`,
x0 `0xa526f9c` (not a success status at this checkpoint). Original
vm_page_bootstrap returned inside vm_mem_bootstrap. The expected BRK is
deliberately inserted, not an unexpected kernel fault. Automatic Recovery
return and full 67,108,864-byte boot_a restoration/readback SHA256 verified.
Evidence: `post-vm-page-brk-manifest.json`, same-boot dmesg and full restored
readback in the evidence directory. No panel observation collected this round.

`oem expdb` exports a gzip 128 MiB dump but has no SWLG/SWX2 evidence; it is
not a usable shim log. GenieZone stage-2/RKP messages are a possible lead,
not an established cause. Full-shim Unicorn emulation reached XNU entry
with modeled MMIO; this does not prove hardware execution.

## >>> SESSION 2026-10-22 - diagnostic kick: log_store trace + XNU sentinels (read first) <<<

**Device state at this writing (observation):** `boot_a` contains the
diagnostic image `shim/out/shim-boot.img`, 7,008,256 bytes, SHA256
`d2960f102eaf3b5e280e28de1511b645c5d448bf6a3af888fa05c86027ac5cba` (boot v4).
It was flashed via `tools/fbraw.py flash boot_a` (both `download:` and
`flash:boot_a` returned OKAY). After `fastboot reboot`, the handset showed the
LK/Redmi logo and **no USB device enumerated** for over 30 seconds. It did not
reset because this diagnostic build deliberately leaves the MTK AP watchdog
disabled after the jump. `boot_a` is **not restored yet**. XNU entry is not
established.

`fastboot boot shim/out/shim-boot.img` was tried first: LK accepted the
6.8 MB transfer, then stayed in fastboot with no "Booting" response, i.e. this
LK still has no working temporary boot. Do not use it as a non-destructive
path.

**What this image does (host-verified, hardware not yet read back):**
- The shim writes a stage trace to MediaTek's `log_store` reserved memory
  (`mediatek,log_store` in the live FDT; PA `0x7ffbf000`, size `0x40000`).
  The trace header `SWX2BOOT\n` and log lines are mirrored at offsets
  `0x1000`, `0x10000` and `0x30000` so a partial LK append cannot hide all
  copies. `fastboot oem lkmsg` dumps exactly these 262,144 bytes
  (`DATA00040000`), and the dump survives a reset. This is **not** the
  ramoops pstore zone (`0x48090000`) and not `/chosen/log_store`
  (`0x11df00`).
- Stages on the panel and in the trace: `S0 wdt` (watchdog mode before/after
  disable), `S1 board`, `S1 disp` (`display_probe` result and error code),
  `S3 payload`, `S4 layout`, `S5 copied`, `S6 bootarg`, `S7 jump`
  (`SCTLR_EL1`), `S7 EL` (`CurrentEL`, `CNTFRQ`), `S7 ID` (`MIDR`, `MPIDR`),
  `SENT`, then `XNU jumping`. Every stage calls `display_refresh()` only
  when `display_probe()` succeeded; otherwise the panel keeps the LK logo.
- Before the jump the shim plants `0x584e55454e545259` ("XNUENTRY") in three
  XNU globals. The next shim boot reads them:
  | Global | PA (this payload) | Written by |
  | --- | --- | --- |
  | `bootstrap_pagetables` | `0x10117c000` | `start_first_cpu` zeroing loop |
  | `const_boot_args` | `0x101177a68` | `arm_init` (`const_boot_args = *args`) |
  | `gPhysBase` | `0x101178100` | `arm_vm_init` |
  | `gVirtBase` | `0x101178110` | `arm_vm_init` |
  A word still equal to the magic means that stage was not reached. The PAs
  are generated by `tools/diag_syms.py KERNEL PAYLOAD > shim/out/diag_syms.h`
  and compile-checked against the payload header; never hardcode them.
  Every boot also writes `SWX2SENT BS=… BA=… GP=… GV=…` at
  `log_store_end - 0x800`, and a reporter boot writes
  `SWX2 REPORT BS=… BA=… GP=… GV=…` at `log_store_end - 0x400`.

**Interpreting the logo:** "no USB" only proves LK stopped its fastboot
gadget. "Panel never changed" is consistent with (a) a shim hang before
`display_probe`, (b) `display_probe` failing and `display_force_refresh()`
returning because `dsi`/`mutex` were never resolved, or (c) a successful push
that the command-mode panel did not latch. The trace distinguishes these;
the logo alone does not.

**`oem p2u on` does not help here:** its implementation in this LK
(`app/mt_boot/sys_commands.c`) only appends `printk.disable_uart=0` to the
next **Linux kernel** command line. It touches neither the shim nor XNU and
does not create a USB console.

**Next concrete actions:**
1. Add `shim/out/diag_syms.h` generation to the build (already wired via
   `XNU_DIAG_KERNEL=…`).
2. Force reset to fastboot (long-press Power, then Power+VolumeDown), run
   `research/.../fbraw.py oem lkmsg OUT.bin`, and copy `OUT.bin` under
   `research/matisse/` (gitignored). Read the `S1 disp` line first.
3. If the trace shows `S7 jump` + `SENT`, the next boot's `SWX2SENT`/reporter
   words establish XNU entry; if it stops at `S1`, fix the display path
   before blaming XNU.
4. Restore `boot_a` from `shim/out/boot_a-live-restore-trim.img` (SHA256
   `a0d33cfa174920eb8da48c78bb956dc40d6345689a24e38f8700addf2c2d2183`),
   `oem reboot-recovery`, then verify the complete 64 MiB partition hashes
   to `2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`.

A second diagnostic build is ready but **not flashed**: `b0e6197727eaecce…`
adds the always-on `SWX2SENT` summary and falls back to the measured matisse
display bases (`0x14017000`/`0x14001000`/`0x14004000`/`0x14000000`) when the
live-FDT lookup fails; every existing state guard is still enforced.

**Update after the first three hardware boots (19:30):**

- `d2960f10…` (gzip, log_store enabled) booted, showed the LK/Redmi logo
  and stayed. After a forced fastboot entry, a 262,144-byte `oem lkmsg`
  dump contained **no** `SWX2` bytes at `0x1000`, `0x10000` or `0x30000`.
  The shim therefore stopped at the first `log_store` access in
  `diag_early`, exactly like the earlier `logstore` entry probe. The
  log-store path is now opt-in (`-DDIAG_LOGSTORE`) and off by default.
- `3e3814a9…` (raw Image, 18,128,896 B, log_store off) rebooted instead of
  staying at the logo. A stable logo means the shim reached
  `wdt_disable_raw()`; a reboot means it did not. Raw Image entry is not
  usable on this LK for this payload; do not use `--raw` without a control.
- `0e6e62b6…` (gzip, 7,008,256 B, log_store off, early OVL read guarded,
  OVL scanout adoption) booted and remained alive for more than 37 s with
  no USB enumeration and no reset. The operator's panel report is pending.
  Since the watchdog stays disabled only after `wdt_disable_raw()`, this
  boot certainly reached C.
- `expdb` after the failed boot contains LK's
  `boot linux-64 via EL2, pc=0x40000000, x0=0x47c80000, mode=0x3c5`
  and no shim/XNU records. That is expected: the shim has no expdb writer.
- `expdb` also still contains the old `SWTX1` banner from the live
  `boot_a` wrapper, so it is not a per-boot record.

**Next control:** re-run the exact validated display probe
`shim/out/entryprobe-display-kick-hw-ok.img` (20,836,352 B, SHA256
`b8cec21a912dc978b7b78fddd343253e9a93b95b3d91e275bca0fe0bc0eeee3f`).
If the panel shows its test screen and moving bar again, the panel/LK state
is healthy and the full shim's display path is the fault. If it does not,
the LK display handoff state changed and the probe's recorded state must be
re-measured before any XNU display work.

`boot_a` currently contains `0e6e62b66f3246fadd5de23a70e54ff74148ab59a150e38eb0e4a058e1f863f9`,
not the live backup. Restore it before any non-test boot.

**UFS/expdb partition-log probe (`939e4875…`, 7,004,160 B, gzip):** built
with `-DDIAG_UFS_LOG`. After the console/display probe it calls `test_ufs()`
(prints CAP/HCS/HCE/UTRLBA/claim/TUR/CAPACITY and the big GPT partitions),
then `ufs_log_write("expdb", blocks - 32, con_text(), len)`, which writes a
`SWLG` header and the console text eight blocks before the end of `expdb`,
then halts. The image was flashed and booted; no USB appeared within 20 s.
The panel state was not reported. Read the result from recovery:

```sh
adb exec-out 'dd if=/dev/block/by-name/expdb bs=1M 2>/dev/null' > expdb.bin
truncate -s 134217728 expdb.bin
python3 tools/shimlog.py --file expdb.bin --scan   # prints "SWLG at 0x..."
```

`tools/shimlog.py --scan` was added for this. If `SWLG` is absent, the MTK
UFS host takeover (`ufs_ready()`/`claim_host()`) failed before the write; the
panel `test_ufs` lines, if visible, carry the failing register state. The
`SWLG` text is the console text up to the probe, so it also carries the
display-stage result.

`fastboot oem dump_pllk_log` returned only `INFO` lines from
`mi_get_lock_state` and no `DATA` payload in this test, unlike
`oem lkmsg`, which returned `DATA00040000`. Do not treat `dump_pllk_log` as a
working text channel until its response framing is measured.

The bounds-checked PL/LK writer (`shim/logstore.c`, `/chosen/log_store` at
`0x11df00`, 64 KiB, header `0xabcd1234`/`0xcdab3412`) has not been retried
after the earlier probe hung; that probe likely wrote past the 64 KiB buffer
at `+0x3ff00`. A retry must stay inside the `off_lk + sz_lk` region and use
`/proc/pl_lk` (same Linux boot) or a measured fastboot dump.

**Process correction (observed):** sending `fastboot oem reboot-recovery`
while a test image is still in `boot_a` does **not** enter recovery; the
phone ends up at the logo with no USB and no fastboot. The required order is:
Power+VolumeDown to fastboot, flash
`shim/out/boot_a-live-restore-trim.img`, then `oem reboot-recovery`. The
expdb `SWLG` block, if the UFS write succeeded, survives that reset; RAM
log_store and sentinels do not.

**Fastboot-only expdb read:** this LK also registers `oem expdb` and contains
`read_expdb`/`deflateInit2` strings. Try
`tools/fbraw.py oem expdb OUT.bin` in fastboot before restoring; it may
return a compressed expdb image (decompress with zlib) and would avoid the
recovery boot entirely.




## >>> SESSION 2026-10-22 - device recheck before next XNU attempt <<<

**Read-only hardware check:** The connected Redmi K50 Pro is already in
Recovery (`ro.bootmode=recovery`, slot `_a`). A fresh 64 MiB read of
`boot_a` has SHA256
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`, matching
`backup/recovery-20261021/boot_a-live-20261021.img`. The original/live `boot_a`
is therefore already restored; no flash was needed for this check. `vendor_boot`
is also 64 MiB, but was not read or changed. No partition was written.

The user clarified that the handset cannot enter Recovery while a temporary
shim/test image remains in `boot_a`: restore the original `boot_a` first, then
request `reboot-recovery`. Keep this as the required cleanup sequence after
every hardware test. Reconfirmed this turn: Recovery ADB reports slot `_a`,
root access, and a 67,108,864-byte `boot_a`; a fresh full readback (discarding
the recovery `dd` tool's 79-byte stdout trailer) hashes to
`2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`, exactly
matching the full restore backup. No partition was written. Host currently
sees Recovery over ADB and no fastboot device; do not infer the handset is in
fastboot from the earlier report.

**Host-only rebuild check (2026-10-22):** The NDK 28.2.13676358 LLVM tools
were put on `PATH`; `bash shim/build.sh out/matisse-xnu.payload --template
../backup/recovery-20261021/boot_a-live-20261021.img` completed. The resulting
boot-v4 image is 7,004,160 bytes with SHA256
`8b4a6986111d6d75363b83ff429faf83c8bcdf33b084cb0edcd57e9e0aefb943` (same
bytes as the already-tested candidate), inflated Image 16,859,648 bytes,
embedded-payload marker offset 253,952, and embedded payload length 16,605,680.
The generic ramdisk matches the original boot image byte-for-byte. All 26
`tools/tests` unit tests pass. This rebuild adds no new hardware evidence and
was not flashed; no XNU startup success is established.

**Next:** instrument the next boot-v4 test to leave distinct visible checkpoints
after payload discovery, segment copy, boot_args/ADT setup, and immediately
before the XNU branch. Add a minimal earliest-entry XNU checkpoint before
`arm_init`; for this command-mode panel it must use the known-good MT6983 DSI
refresh sequence rather than only changing framebuffer memory. First run all
host layout/range/build checks. Hardware validation must remain temporary
`boot_a` only, with full partition readback before the test and full 64 MiB
restore + hash verification afterward.

## >>> SESSION 2026-10-22 - embedded XNU hardware attempt (read first) <<<

**Outcome / evidence:** The user reported that the embedded-payload candidate
stayed at the first boot screen without progress, then manually entered
fastboot. The candidate was `shim/out/shim-boot.img`, SHA256
`8b4a6986111d6d75363b83ff429faf83c8bcdf33b084cb0edcd57e9e0aefb943` (7,004,160
bytes; boot-v4; CI XNU payload 39640336293). Only the first 7,004,160 bytes of
`boot_a` were written. Before reboot, a full 67,108,864-byte readback matched
the predicted SHA256 `4f876b7ba93a95cde1f8bb8cb54a97abec68920ea2591f1a73ed7650dc2726f9`.
ADB did not return during a 131-second watch. This proves the candidate did not
reach recovery/ADB; it does **not** prove whether XNU entry was reached.

`fastboot oem lkmsg` returned 256 KiB, but its contents are the known stale
Android log buffer and are not evidence from this attempt. The candidate
therefore has no trustworthy same-boot XNU trace. User-visible shim output was
previously reported, but the exact final on-screen line for this image was not
captured. Do not claim that the new payload path or XNU entry has passed.

**Recovery completed:** In fastboot, `tools/fbraw.py flash boot_a
shim/out/boot_a-live-restore-trim.img` succeeded. The 20,832,256-byte trimmed
image was verified byte-for-byte as a prefix of the full 64 MiB backup (SHA256
`a0d33cfa174920eb8da48c78bb956dc40d6345689a24e38f8700addf2c2d2183`). Then
`reboot-recovery` returned OKAY. In recovery, the complete `boot_a` readback
SHA256 is `2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`,
matching the full backup. Current slot `_a`, current mode recovery. No
`vendor_boot`, userdata, GPT, bootloader, modem, persist, or calibration
partition was written.

Evidence files are under
`research/matisse/20261022-embedded-payload-test/` (gitignored): stale LK dump,
fastboot command logs, and recovery dmesg after restore. That recovery dmesg
shows the restored old `SWEET-ENTRY v1` wrapper executing; it is only a restore
control, not the candidate boot.

**Next:** The embedded image's shim path still needs an unambiguous on-screen
handoff/early-XNU marker or a same-boot persistent trace before another blind
reflash. Existing XNU early framebuffer marks (patches 0082/0083) begin after
XNU enters `arm_init`; the next diagnostic should expose the earliest possible
XNU entry stage and refresh that marker on the MT6983 command-mode panel. Keep
the test limited to temporary `boot_a`, and fully restore/hash it after each
run. XNU startup remains unproven; do not change `vendor_boot` or userdata.

## >>> SESSION 2026-10-22/23 - XNU/display bisection (read first) <<<

**2026-10-22 XNU payload delivery update (host only):** User reports that
shim output is visible, but XNU does not start; no current screen text/photo
or XNU boot trace was supplied, so the exact failing stage is not measured.
The previous boot-v4 image appended 16,605,680 raw payload bytes directly to
boot.img's 1,250,233-byte compressed **generic** ramdisk. That placed the
`SWEETXNU` magic at offset 1 modulo 8, which the loader's 8-byte-step scan
cannot find if the ramdisk is copied at its measured 8-byte-aligned base;
whether MTK LK also discards the raw compressed-stream trailer is unknown.
Recovery's ramdisk lives in `vendor_boot` and was not modified.

A new boot-v4 path embeds the payload after the shim Image's 4 KiB-aligned
`_end`, with `SWEETAPP`+u64 length metadata; it enlarges the ARM64 Image
header and leaves boot.img's generic ramdisk byte-identical to the verified
backup. `loader.c` reads the embedded payload directly and bounds-checks
segments and ADT before the XNU jump; the old boot-v2 initrd path is retained.
The jump message now pushes an MTK frame and waits ~200 ms before handoff.
Host build `shim/out/shim-boot.img`: 7,004,160 B, SHA256
`8b4a6986111d6d75363b83ff429faf83c8bcdf33b084cb0edcd57e9e0aefb943`;
inflated Image 16,859,648 B; marker offset 253,952; embedded payload exactly
matches `shim/out/matisse-xnu.payload` (SHA256 `df778ce8...ae19e`, from
successful XNU CI run 39640336293). 26 host unit tests pass, the full shim
cross-build passes (pre-existing unused `reset()` warning), and the separate
entry-probe XNU build still cross-compiles from the original stock template.
**No new image was written to the phone, and XNU remains untested on hardware.**
Next: obtain explicit confirmation for a **boot_a-only** temporary test; read
back the full partition after writing and restore the complete 64 MiB backup
SHA256 `2931647a...86b1` after the result. Do not write `vendor_boot`, userdata
or GPT. The prior notes about display regression are earlier observations;
request the exact screen text from the user's visible shim run if the XNU
stage remains unclear.

**Device state:** `boot_a` restored to the verified live image
`backup/recovery-20261021/boot_a-live-20261021.img`; full 64 MiB readback
SHA256 `2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`
matches. No other partition was written. The live image is itself an old
`SWEET-ENTRY v1` probe wrapper around the stock kernel (same ramdisk, 2 MiB
prefix); it boots Android/recovery normally.

**2026-10-22 read-only preflight:** recovery ADB enumerates with root;
`blockdev --getsize64 /dev/block/by-name/boot_a` reports 67,108,864 bytes.
A fresh full-device `adb exec-out dd` read confirms SHA256 `2931647a...86b1`
for the first 67,108,864 bytes, identical to the local verified restore image.
This recovery’s `dd` appends 79 bytes of transfer statistics to **stdout**,
not stderr: never hash the untrimmed ADB output as a partition image.
The original kick image and local restore image also match their recorded
hashes. No partition write or control boot was performed in this preflight.
Await explicit authorization before writing the kick image to `boot_a`;
this LK does not implement temporary `fastboot boot`.

**2026-10-22 same-boot execution control (no partition write):** rebooted the
existing, restored `boot_a` directly back into recovery. ADB returned after
23.4 seconds; its new-boot dmesg contains a `SWEET-ENTRY v1` banner at
`[0.000000]`. All fields in the matching boot-image banner are initially
zero; the live banner has nonzero stage/register fields, confirming the
**currently installed entry-probe shim still executes** in this boot. Raw
log is gitignored at
`research/matisse/20261022-shim-control/recovery-existing-boota-dmesg.txt`.
This is not a test of the newer full shim, screen refresh or XNU. Host-side
comparison shows that the validated `display-kick` image and later normal
`display-kick` build have identical unpacked stock-kernel regions, but
13,866 bytes differ within their 2 MiB prefix; this alone cannot tell
whether the build changed behavior or the recovery panel state changed.
A fresh hardware control with the exact validated image remains the next step.
The 64 MiB restore backup contains nonzero data past the 20,836,352-byte
probe image (32,202 nonzero bytes); restore and hash the **full** 64 MiB,
not just a trimmed prefix. The expected full-partition hash during a probe
write, if the existing tail is preserved, is
`016331b382ae2c88c668931c8feaa448c42f48948b440fdb7b83b455853b97a7`.

**New measured facts:**

- `fastboot oem lkmsg` holds a stale previous-Android kernel ring, not the
  current recovery boot. It is not a same-boot log channel.
- The device's own `/lib/modules/mtk_wdt.ko` (pulled, SHA256
  `f41c25ad...`) gives the MT6983 AP-watchdog protocol: base `0x1c007000`,
  MODE bit0 enable, key **`0x22000000`** (`movk w10,#0x2200,lsl#16` in
  `mtk_wdt_stop`), PING at `+0x08` = `0x1971`. Stock dmesg reports
  `mtk-wdt 1c007000.watchdog: Watchdog enabled (timeout=31 sec)`.
- Shim execution is proven: a clean shim built with `MINIMAL_PROBE` (prints
  then halts) resets on a **~45 s** USB/preloader period, while a shim that
  calls PSCI reset at entry resets on a **~9.7 s** period. The former is the
  31 s watchdog after halt; the latter is immediate. The shim therefore does
  execute on MTK LK.
- The display path regressed in this session. The exact validated kick image
  `shim/out/entryprobe-display-kick-hw-ok.img` (20,836,352 B, SHA256
  `b8cec21a912dc978b7b78fddd343253e9a93b95b3d91e275bca0fe0bc0eeee3f`) is
  retained, but newly built `xnu`-level probes (with and without payload),
  the clean shim, the permissive-clock shim, the forced-push shim and
  RAW_PAINT all showed only logo/black and reset when booted from recovery.
  The validated image has not yet been re-run as a control; do not conclude
  the recovery-mode display state from the new builds.
- Appending the 16.6 MB XNU payload directly after the gzip stock ramdisk
  makes a 37.4 MB boot image; the hybrid `entryprobe-xnu.img` did not display.
  A valid ramdisk or separate-partition payload channel is still needed.
- `persist_mark` (write to `0x00d01100+0x3ff00`) is now compiled out unless
  `DIAG_MARK`; it was present in every failing diagnostic build.
- `DIAG_MARK`/`WDT_HALT`/`WDT_NO_TOUCH`/`RAW_PAINT`/`RAW_RESET` are still
  uncommitted working-tree diagnostics. Record the macro used for any image.

**Next concrete action:** with explicit authorization, test the new
embedded-payload `shim/out/shim-boot.img` on `boot_a` and record the final
visible loader/XNU line before recovery restoration. If the user-reported
visible shim is not reproducible for this new build, first rerun the exact
validated display-kick image `b8cec21a` as a display control. Keep each
partition write separately authorized and verify full restoration.

## >>> MATISSE PORT (Redmi K50 Pro, MT6983) - handoff 2026-10-22 <<<

### DISPLAY REFRESH PROVEN ON HARDWARE 2026-10-22

**The panel now refreshes from the LK-handoff state.** A same-boot shim probe
(`shim/entryprobe_display.c`, level `display-kick`) enabled the DSI engine and
pushed command-mode frames: the operator saw the test screen and a moving
green bar change on the phone, then the device returned to recovery in the
same boot. This is the first verified screen-log path on matisse.

Two hardware runs, temporary `boot_a` writes only, each read back fully:

1. Read-only probe `shim/out/entryprobe-display-v3.img` (20,836,352 B,
   SHA256 `76a2c57fce82feb238c9b9e96e4894e905f159a5036a054f1eccd5c434ca42e1`)
   ran from a temporary `boot_a` and returned to recovery. Raw dmesg:
   `research/matisse/20261021-live/entryprobe-display-v3-dmesg.txt`.
2. Refresh probe (validated build) `shim/out/entryprobe-display-kick-hw-ok.img`
   (20,836,352 B, SHA256 `b8cec21a912dc978b7b78fddd343253e9a93b95b3d91e275bca0fe0bc0eeee3f`)
   showed new frames on the panel. Raw dmesg:
   `research/matisse/20261021-live/entryprobe-display-kick-v3-dmesg.txt`.
   Recovery dmesg has **0** `ovl status error` and **0** CMDQ timeouts, so the
   corrected restore leaves the next kernel's display init healthy (unlike the
   old direct-trigger experiment).
3. `boot_a` was restored from `backup/recovery-20261021/boot_a-live-20261021.img`
   (the exact pre-test live image, SHA256
   `2931647a56039722ea2585990b8ef3b1a2daf4c9b990231879f753049eaf86b1`); the
   full 67,108,864-byte readback matches. Note the live image differs from the
   older `boot_a-stock.img`; the live one is now the restore source.

**Measured LK handoff (read-only, live FDT, `SWTX1` record):**

| Block | Value |
| --- | --- |
| DSI0 CON_CTRL / MODE_CTRL | `0x0` / `0x0` (engine off, command mode) |
| DSI0 START / INTSTA | `0x1` / `0x4` (TE_RDY set; panel sending TE) |
| DSI0 TXRX_CTRL / PSCTRL | `0x0001023c` / `0x2c0505a0` (4 lanes, DSC, 1440-byte lines) |
| DSI0 SIZE_CON / VACT | `0x0c8001e0` / `0xc80` (3200x2x720 DSC) |
| DSI0 MEM_CONTI / BUF_CON0 | `0x3c` / `0x1` (cmd-mode buffer enabled) |
| DSI0 BUF_CON1 / RW_TIMES | `0x0dfd00b1` / `512000` |
| DSI0 LCCON / LD0CON | `0x001f1f01` / `0x8` (HS clock on) |
| MIPI PLL_PWR / CON0 | `0x1` / `0x9cec4ec4` (PLL running at 1.36 GHz) |
| clock gates 0/1/2 status | `0x0` / `0x0` / `0x0` = **all enabled** (MTK status bit 0 = on) |
| OVL0_2L EN / L0_ADDR / pitch | `0x00e00001` / `0xfcb3f000` / `5760` |
| OVL0_2L SRC_CON / SRC_SIZE0 | `0x1` / `0x0c8002d0` (720-wide slices) |
| disp_mutex0 EN / SOF | `0x0` / `0x0` (stopped) |
| disp_mutex0 MOD0 / MOD1 | `0x004ffff5` / `0x202` (OVL1_2L, DSC0, DSI0 present) |
| GCE threads 3/5 | idle (`CURR_STATUS`/`WAIT_TOKEN` 0) |

**Validated minimal refresh:** LK has already done panel rails/reset, the DSI
PHY PLL/HS clock, DSC/PPS and the command-mode buffer. No panel power/reset,
PHY, DSC or GCE programming is needed to show new pixels — the OVL is still
scanning `boot_args.Video`. The shim sequence that worked:

1. Require clock gates DSI0/DSI_CLK/OVL0_2L enabled, DSI command mode, DSC
   PSCTRL, expected SIZE_CON/VACT, BUF_CON0 bit0, OVL enabled at the boot
   framebuffer with the boot stride, and TE_RDY.
2. `DSI_PHY_LCPAT=0x55`, `DSI_PHY_LCCON |= 1`, clear the stale TE flag.
3. Wait for TE_RDY (W0C: write `~TE`), then `MUTEX_CFG=0`, `MUTEX_EN=0`,
   `MUTEX_EN=1`, `DSI_START=1`.
4. Repeat for each frame; disable the mutex when stopping.

`INTSTA` bit4 (FRAME_DONE) **never asserted** on the command-mode path even
though the panel refreshed; completion cannot be polled there. Pace refreshes
by time instead. Do not repeat the old mutex+START-only experiment that
skipped the clock/DSC/buffer preconditions.

**XNU port (patch 0083, compiled locally, not yet booted):**
`osfmk/arm64/sweet_matisse_dsi.c` validates the same handoff state and issues
the same sequence; `pexpert/arm/pe_serial.c` calls it from `fbcon_probe` and
after each drawn character; `mkadt.py --board matisse` now emits a
`sweet-display` node (`reg=<0x14000000 0x18000>`, panel
`l11_38_0a_0a_dsc_cmd`). Local CI-flag syntax/object compile of both files
passes. Patch applies cleanly after 0082. Next: CI build, then temporary
`shim-boot.img` test (needs explicit user confirmation).

**XNU CI + image (2026-10-22):** GitHub Actions run **39640336293** on
`Jinsens/ios-sweet` branch `codex/matisse-xnu-screen-log` completed
**success** (first two runs failed on missing header export, then on `SWEET`
not being defined without `pexpert/arm64/board_config.h`; both fixed).
Kernel `xnu/out/ci-39640336293/kernel.development.sweet`: 17,222,800 B,
Mach-O `arm64`, SHA256
`bc308789486ee96b380ce374bc7fe4c1894f4a115da9662b4cb0cf9a016b543c`.
Linked symbols `_sweet_display_init` and `_sweet_display_refresh` present.
Payload `shim/out/matisse-xnu.payload`: 16,605,680 B, SHA256
`df778ce8960ad2b8f4fed21103fb9e78d7fe630627e21c5155fadf0fdccae19e`
(ADT 4456 B with `sweet-display`; cmdline `debug=0xc serial=3 cpus=1
keepsyms=1 sweetkeys=0 sweettouch=0 sweetbattery=0 sweetusb=0 ufsrw=0
-noprogress`). Boot image `shim/out/shim-boot.img`: v4, 17,883,136 B,
SHA256 `f60e9af968f71cb60bf3da8d5d34d1cb6e757f39f3c6b7a99fea444b3ca3c163`.
**Not flashed or booted.** Booting it is the next hardware step and needs
explicit confirmation; recovery is Power+VolDown to fastboot, then restore
`backup/recovery-20261021/boot_a-live-20261021.img` and read back.

**Tooling:** `tools/mkentryprobe.py --level display-kick` (guarded writes,
21-field compact `SWTX1` banner; field `k` = stage<<16|code) and
`tools/decode-display-probe.py [--kick]`. The record fits the 407-byte Linux
banner exactly. `tools/xnu-cc.py` now honors `XNU_CC`.

## >>> MATISSE PORT (Redmi K50 Pro, MT6983) - handoff 2026-10-21 <<<

### ACTIVE UPDATE 2026-10-21 - XNU built; frame-trigger still blocks visible logs

**CURRENT DEVICE STATE:** the user most recently reported the handset is in
fastboot. A host query on 2026-10-21 using fastboot 37.0.0 saw zero visible
devices, so the present USB/device state is not independently confirmed. The
last verified state was OrangeFox recovery with `boot_a` fully restored from
`backup/boot_a-stock-trim.img`. Entire 67,108,864-byte partition readback
`backup/recovery-20261021/boot_a-final-after-all-probes.img` byte-matches
`backup/boot_a-stock.img`, SHA256
`7a6a462d5e8858c6f7ecd77655363dd84964d44ea6e7c239e961632996a96e94`.
No test image remains installed. No userdata, vendor_boot, LK/preloader,
GPT or calibration writes were performed. This is the preserved known-good
(possibly Magisk-patched) backup, not a newly verified pristine vendor image.

**Completed implementation commits:** `3021f5a` (same-boot entry/FDT/MMU/timer
probe + logging safety), `8a31ac8` (actual IRQ/EOI + guarded matisse
ADT/payload), `6cbbcbe` (XNU Boot_Video early framebuffer mapping), and
`2cf0290` (read-only display snapshot probe, FDT parser fixes and build fix).

**CURRENT MILESTONE 2026-10-21:** working branch
`codex/matisse-xnu-screen-log`. Patch 0082 removes early Qualcomm framebuffer
physical-address/geometry assumptions and derives the surface from
`boot_args.Video`; the final TTBR1 alias walks the new XNU root, maps visible
pixels only, handles up to two 32MiB blocks and refuses a bad/colliding
surface. Normal fbcon validates the surface dimensions.

GitHub Actions run **39595694178** (head `bfd7eb60fe0602f85bfc81bc33a18b4f23c03505`)
completed **success** on 2026-10-21. Kernel artifact:
`xnu/out/ci-39595694178/kernel.development.sweet`, Mach-O plain `arm64`
(not arm64e), 17,206,200 bytes, SHA256
`911db7b3c3616face11ccc730869e12ccf0c72ba5efaee8f3eedd01af7cfc367`.
Payload built with measured matisse ADT and safe one-core/no-device-driver
boot args: `xnu/out/ci-39595694178/matisse-xnu.payload` (16,605,200 B).
Boot-v4 test image also built:
`shim/out/shim-boot.img` (17,883,136 B, original recovery ramdisk plus payload).
It has **not been flashed**; XNU has not yet been booted on hardware.
`/product` is missing from the local Apple D22AP ADT, generator warns/omits it.

Validation: all five early-console Mach-O units syntax/object compiled against
successful run 39158169212's build tree; 11 instruction-level actual-assembly
mapping cases passed. Whole existing 81-patch series applied to Apple
xnu-8796.141.3; new patch applies and CI built/linked it. These do not prove
hardware MMU behavior or screen output.

**LATEST HOST MILESTONE 2026-10-21 (commit `4b05707`):** the read-only `display` probe now
separates FDT/register-window lookup status from the raw register values. The
old `ffffffffffffffff` mutex fields were ambiguous because the same value was
also used as the lookup-failure sentinel. Host-decoding the saved live DTB
resolves `/disp_mutex@14001000` with register windows `0x14001000` and
`0x14401000`; this is a DTB parse result, not proof of live MMIO contents. The
new snapshot records a two-bit status for each register pair, reads both
mutex windows, and snapshots GCE display trigger-loop threads 3 and 5
(CURR_STATUS/WAIT_TOKEN) using their live-FDT base. This remains read-only;
no display/clock/PHY/GCE writes were added.

All seven entry-probe levels cross-compile against the stock v4 template;
all 21 Python tests pass and `git diff --check` is clean. The rebuilt v2
read-only display probe is `shim/out/entryprobe-display-v2.img` (20,832,256 B,
SHA256 `a0d33cfa174920eb8da48c78bb956dc40d6345689a24e38f8700addf2c2d2183`);
it has **not** been transferred, booted or flashed. Fastboot reported zero
visible devices, so no hardware probe result is available for this milestone.
The append-only log-store host ASan/UBSan test previously passed. Rebuilt
`shim/out/shim-boot.img` from the CI XNU payload and stock template: v4,
17,883,136 bytes, SHA256
`9300e166763b4ba69e85fa17867156f24cfa1d0ec9b1fa961e0515c1efe761cf`.
None of these images has been written to the phone.

**DISPLAY BLOCKER, now measured:** read-only entry probe saw DSI0
CON_CTRL=0 (DSI_EN clear), MODE_CTRL=0 (command mode), START=1,
INTSTA=4, CMDQ_SIZE=0x8001, SIZE_CON=0x0c8001e0. OVL0_2L reports
L0_ADDR=`0xfcb3f000`, L0_PITCH=`0x1680` (5760), EN=`0x00e00001`;
the ordinary OVL0 L0 address was zero. The probe reached stage 15 without an
exception, but the final mutex fields were `ffffffff`; because the v1 probe
used that same bit pattern for FDT/range lookup failure, those two fields do
not establish the actual mutex register values. Do not claim mutex state from
that snapshot. DSI framebuffer stores alone are not a validated
panel refresh path.

A one-shot DSI/mutex/start experiment was stopped. The subsequent recovery
dmesg contains 129 CMDQ “SW timeout Pre-dump” records and repeated waits on
token 641; the stock baseline has none. The Xiaomi MT6983 GCE header names
641 `CMDQ_SYNC_TOKEN_STREAM_EOF` (a software sync token, not a DSI-TE event).
The sequence and timeouts correlate, but causation is not isolated; it did not
provide verified visible text. Do NOT repeat direct DSI_START/DSI_EN toggles.
The panel compatible is `l11_38_0a_0a_dsc_cmd,lcm`; the vendor driver has a
WQHD DSC command table plus rail/reset, DSI/PHY and display-pipeline setup.
That initialization/refresh path is not ported to XNU. The ignored local
source snapshot `research/matisse/panel-l11-38-0a-0a-dsc-cmd.c` matches the
live compatible and contains the WQHD 1440x3200 DSC PPS/DCS init table; its
prepare path sequences VDDI (`vaud18`), DVDD GPIO 193, VCI (`vibr`), then
reset GPIO 86. The init callback is separate from prepare. The corresponding
DT/driver/GCE material is in ignored `research/matisse/mt6983.dts`,
`research/matisse/mtk_dsi.c`, `research/matisse/mtk_drm_crtc.c`, and
`research/matisse/mt6983-gce.h`; the GCE header distinguishes DSI0 TE event
358 from software STREAM_EOF token 641. These are source observations, not a
validated XNU port. There is no validated screen-log path yet.

**NEXT CONCRETE ACTION:** when the phone is again visible in fastboot, run the
read-only `shim/out/entryprobe-display-v2.img` via temporary `fastboot boot`
(not `fastboot flash`) and collect the same-boot Linux banner from recovery.
Decode `display_status` before interpreting any all-ones register pair. If
that probe returns normally, use its mutex/GCE values to begin a source-driven
XNU panel/DSI/DSC port. No DSI_START/DSI_EN toggles; use the real DSI0 TE
hardware event rather than software STREAM_EOF. The v2 image has not yet been
run on hardware.

GitHub CLI is installed in ignored research/host-tools. Upstream is read-only;
`Jinsens/ios-sweet` fork was created for CI. XNU branch commit has not been
merged upstream. Before any further temporary `boot_a` write, obtain explicit
user confirmation per device procedure. No userdata, GPT, vendor_boot, LK,
preloader or calibration partitions were written.

**Additional completed host work:** `mkadt.py --board matisse`: one A510
(affinity=0), 13MHz, PPI27, GICD 0xc400000/size0x40000, GICR
0xc440000/size0x200000, fb 0xfcb3f000/1440x3200/stride5760, kernel RAM
0x100000000+0x12e000000, no Qualcomm UFS node. Retains iPhone10,3/D22AP
and logical 1125x2436 canvas. Not a generic daumier profile. `mkpayload.py`
recognizes it and rejects conflicting/truncated boot-args, requiring
cpus=1/sweetkeys=0/sweettouch=0/sweetbattery=0/sweetusb=0/ufsrw=0.
13 host unit tests PASS, existing sweet/QEMU profiles tested unchanged.
Apple /product ADT is missing locally; generator warns and omits that node.

**Hardware results:**

| Probe | Stage/fault | Observation |
| --- | --- | --- |
| entry | 1 / 0 | EL1, MIDR0x410fd462, MPIDR0x81000000, CNTFRQ13MHz, DTB0x47c80000, SCTLR0x30d00980 |
| board | 2 / 0 | Actual shim board_probe discovers GICD0xc400000, PPI27, fb0xfcb3f000 |
| mmu | 5 / 0 | Actual shim/mmu.c enables 16KiB granule, rc0, AT succeeds; SCTLR0x30d01985 |
| timer | 7 / 0 | Redist0xc440000, GICD_CTLR0x12; ISTATUS and PPI27 pending asserted after ~100us |
| irq | 9 / 0 | **Actual IRQ entry/EOI PASS**: rc0, count1, last INTID27; returns to recovery |
| display | level 15 / no fault | Read-only DSI0 snapshot: CON_CTRL=0, MODE_CTRL=0, START=1, INTSTA=4; OVL L0_ADDR=0xfcb3f000, pitch=5760. Mutex fields unresolved in this probe; no writes attempted. |
| logstore | no Linux result | FAIL to return; isolated extra direct PL/LK memory-access path, root cause unknown |

Every successful probe handed back to the preserved original Linux kernel
within the SAME boot and returned to recovery. Each flashed prefix was
read back fully and matched first. IRQ test saves/restores modified GIC
and timer state; timer-only stage leaves CPU DAIF masked. IRQ probe SHA256
`961f9a260d7dffd2e267b08102a8cb6ca53461d2c76b9a50a4318261128e77f5`
(20,832,256 bytes). Exact raw dmesg logs, readbacks and source/layout/hash
manifests are gitignored under `research/matisse/20261021-live/` and
`shim/out/entryprobe-{entry,board,mmu,timer,irq}.{img,json,elf}`.
MMU image SHA256 cabdfa644bfaa3b0514fad5e2fae44469e8e2e56c9a38e21c8808c2f7d14bbb2;
timer image SHA256 c8d5c402126d9a699cca197b00cb5559d0cae0563b7d466c62f949db834d5a2b.

**Safety fix:** old dram_log() wrote text INTO the packed 256-byte
log_store header (including pointer/signature). New writer is append-only,
validates bounds/overflow/tag length and needs a live FDT-provided header;
ASan+UBSan host cases PASS. Nonetheless direct log_store access is NOT
hardware-working: normal dram_log and markers are disabled unless explicitly
SHIM_LOGSTORE_MARK. Raw partition logs and misc BCB writes are opt-in only
via SHIM_STORAGE_LOG. Stop relying on a warm-reset PL/LK log reader.

Fresh backup set (read-only) at `backup/recovery-20261021/manifest.json`:
boot_a, vendor_boot_a, dtbo_a, lk_a/b, vbmeta_a/b, misc, persist,
nvcfg/nvdata/nvram, protect1/2, seccfg. Fresh live DTB (410,235 bytes),
stock dmesg, PL/LK log, partitions and iomem in the live research directory.
Host FDT reader passes on this live tree under ASan/UBSan. Complete shim
cross-build passes (pre-existing unused reset() warning only); C ABS64
relocation check passes. No Apple artifact/device dump/serial is tracked.

Local toolchain: installed Android NDK
`/Users/xiaohuasheng/Library/Android/sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/darwin-x86_64/bin`.
ADB/fastboot:
`/Users/xiaohuasheng/Library/Android/sdk/platform-tools/{adb,fastboot}`;
USB needs escalated access. `tools/fbraw.py` works around LK's macOS
platform-tools pipe error. Prefer `adb devices -l` then direct exec-out:
`adb wait-for-device` sometimes remains waiting although recovery enumerates.
The linked kernel/boot probes and same-boot diagnostic format are documented
in `docs/matisse-bringup.md`.

All device-state summaries and experiments BELOW are historical and
superseded by this section; in particular the old bootloop snapshot is NOT
current.

### UPDATE 2026-10-21 late - log channel found, phone needs stock boot restored

**Device state right now:** `boot_a` contains the instrumented shim
(`shim/out/shim-boot.img`, log_store marker build, `EXTRA_CFLAGS=-DSHIM_LOGSTORE_MARK`).
The phone bootloops on it and does not enter recovery from the `misc`
`boot-recovery` BCB; it is currently not enumerating on USB. Restore with
`fastboot flash boot_a backup/boot_a-stock-trim.img` after Power+VolDown.
The stock image is intact; nothing else was written.

**Why the marker test is still unproven:** the shim writes a valid append into
the **pl_lk log_store buffer** (`dram_buf_header.buf_addr` = 0x7ffbf000 from
the 256-byte header at `/chosen/log_store` = 0x00df1100). `fastboot oem lkmsg`
dumps a **different** 256 KiB buffer (the kernel/UART `klog`, all Android
`dmesg` text), so absence of `SWEET-SHIM` there proves nothing. `/proc/pl_lk`
in recovery is the right reader.

**Verified log_store layout** (recovery `/proc/pl_lk`, live header):
- sram header sig 0xabcd1234 at 0x00df1100; packed DRAM buffer address at +0x14,
  size at +0x1c; `pl_lk_log` at +0x40: sig 0xcdab3412, buff_size 0x40000,
  off_pl **0x400**, sz_pl, off_lk = 0x400 + sz_pl, sz_lk.
- The PL/LK text is clean. `oem lpmsg` returns 0x10000 bytes; `oem lkmsg`
  returns 0x40000 bytes of the kernel log, not the PL/LK sections.
- `shim/logstore.c` appends `SWEET-SHIM <tag> a=... b=...\n` at
  `buf + off_lk + sz_lk` and bumps sz_lk, guarded by the sig and bounds.

**Fastboot on macOS:** platform-tools 34.0.5 and 37.0.0 both fail with
`could not clear input pipe ... usb_read failed e00002e8` after sending a
command to this LK, even for `reboot`; `fastboot devices` still works.
`tools/fbraw.py` (pyusb + libusb) speaks the protocol directly and **works**:
`oem lkmsg` returned exactly 0x40000 bytes and `reboot-recovery` returned
OKAY. Use it for all local fastboot operations.

**BCB caveat:** writing `boot-recovery` into misc block 0 from recovery did
not make LK boot recovery after the shim reset; the phone kept booting
`boot_a`. Do not rely on that loop for log retrieval until the MTK boot-mode
selection is understood.


**Goal:** run the same iOS bring-up on the user's Redmi K50 Pro (`matisse`,
MT6983 / Dimensity 9000, 8 GB, 1440x3200, UFS 3.1). Everything below was
measured on the device or read out of its own firmware, not from datasheets.

### Device state right now (safe)

- `boot_a` is **byte-identical to `backup/boot_a-stock.img`** (verified with
  `adb exec-out dd`, sha256 `ba4255e2...`). Android boots normally; OrangeFox
  recovery boots (`adb reboot recovery`). Nothing else was written.
- `misc` command block is zero (no pending boot-recovery), `oops` is zero.
- The Magisk-patched stock boot is the backup: kernel 19,610,141 B gzip ->
  46,848,116 B `ARMd`, ramdisk 1,250,233 B lz4 (vs 1,380,091 B in the
  firmware package - that difference is Magisk).

### Device facts

| Item | Value | Source |
| --- | --- | --- |
| Product | `matisse`, 22011211C, board `mt6983` | getprop |
| Bootloader | MTK LK (`lk_a` 2.8 MB), `verifiedbootstate=orange` | `lk.img`, getprop |
| boot.img | header **v4**, kernel **gzip**, ramdisk **lz4**, 4096 B signature block, AVB footer (SHA256_RSA2048) | `boot.img` |
| vendor_boot | header v4, dtb 335,112 B, `vendor_ramdisk` lz4, tags/dtb 0x47c80000 | `vendor_boot.img` |
| `fastboot boot` | **NOT SUPPORTED** - LK's command table is download/flash/erase/getvar/oem/set_active only | `lk.img` strings |
| Recovery | lives in **vendor_boot**; there is no recovery partition | TWRP BoardConfig, partitions |
| RAM | `/memory` 0x40000000 + 8 GiB; carve-outs up to 0x22e000000 | live FDT |
| Usable window | 0x100000000 .. 0x22e000000 (4.72 GiB) | live FDT |
| GIC | GICv3, GICD **0x0c400000** (0x40000), GICR **0x0c440000** (0x200000) | live FDT `/interrupt-controller` |
| Timer | CNTFRQ **13 MHz**, virt PPI 11 -> INTID **27**, phys 14 -> 30 | live FDT `/timer` |
| UFS | `ufshci@112b0000`, `mediatek,mt8183-ufshci`, Samsung KLUDG4UHGC-B0E1 | live FDT, sysfs |
| Framebuffer | base **0xfcb3f000**, vramSize **0x34c0000** (3 frames), panel 1440x3200 | `/chosen/atag,videolfb*` |
| Panel | `nt36672e_fhdp_dsi_vdo_120hz_jdi_dphy_drv`, **DSC command mode** | `/chosen/atag,videolfb-lcmname` |
| Touch | FocalTech `fts@0` on SPI6 0x11016000, IRQ GPIO 8, reset GPIO 3 | live FDT |
| Keys | MT6363 (`spmi@1c015000/pmic@4/mt6363keys`), power 116, home 115 | live FDT |
| Battery | MT6375 charger+gauge at I2C 0x11e01000 | live FDT, dmesg |
| USB | MTU3 + xHCI 0x11201000 / 0x11200000 (not DWC3) | live FDT |
| DRAM logs | `ram_console` 0x00d01100 (512 KiB), `log_store` 0x00df1100 (256 B) | `/chosen` |

### What was built (all committed)

The shim is now **board-generic**: `shim/board.c` reads the framebuffer, GIC,
timer PPIs, UFS base, RAM, initrd and the DRAM log buffers out of the **live
FDT**; per-board constants are only fallbacks.

- `shim/fdt.c` - real FDT walker: path lookup with a match stack, multi-entry
  `reg`, string lists, `compatible` search. **Verified on the dumped matisse
  tree** (`shim/host_board.c`: board matisse, fb 0xfcb3f000/0x34c0000,
  gicd 0xc400000, gicr 0xc440000, virt 27, ufs 0x112b0000, dram 8 GiB,
  initrd 0x66f00000..0x6aa514be, lcm name). Two real bugs found that way:
  `~3UL` truncates on Windows/LLP64, and "/" must be zero path components.
- `shim/start.S` - drops EL2 -> EL1 (LK may enter at EL2), sets VBAR_EL2
  early, and turns off a bootloader-left MMU before any C runs.
- `shim/loader.c` - finds the payload by scanning the initrd for `SWEETXNU`
  (MTK concatenates boot.img and vendor_boot ramdisks).
- `shim/console.c` - parameterized geometry, `con_redraw` to repaint into
  another scanout buffer, keeps a text copy for the log channels.
- `shim/ufs.c` - parameterized by the board's UFS base; partition read/write,
  `ufs_mark` progress blocks, `ufs_log_write`.
- `shim/shim.c` - stage markers (`mark()`), DRAM log (`dram_log()`), boot to
  recovery via the `misc` BCB, halt instead of reset on panic.
- `tools/mkbootimg.py` - packs header v2 (ABL) and v4 (LK) from a stock
  template, keeping the signature block; `shim/build.sh` gzips the kernel when
  the template's is gzip and builds with clang+lld (Windows/MSYS2 works:
  `PATH=/d/msys2/ucrt64/bin:$PATH EXTRA_CFLAGS=... bash build.sh --template ...`).
- `tools/avbtool.py` (LineageOS copy, patched for Windows `os.urandom`),
  `tools/shimlog.py`, `tools/matisse-flash.sh`.

### What the hardware did (the important part)

1. **LK accepts our image and jumps to our code.** Proof: a 92-byte stub
   kernel (`shim/out/reset_stub.bin`, just `smc PSCI_SYSTEM_RESET`) makes the
   phone blink and reboot instantly, while the stock image sits on the logo.
2. **The panel is not a usable output.** Every shim build (full, minimal,
   no-clear) shows only the logo, then the watchdog resets. The panel is
   command-mode DSI with DSC: LK pushes one frame into the panel's GRAM and
   stops, so framebuffer writes never appear. `atag,videolfb-islcm_inited`
   is 0 in a normal boot. **Do not spend more time on the framebuffer until
   something drives the DSI command queue.**
3. **UFS writes from the shim never land.** `ufs_mark`/`ufs_log_write` into
   `oops` and `boot_a`+4 MiB left the blocks zero. Either the MTK host is
   gated after LK (clocks/power) or our UTRL takeover fails; the marker
   records HCS/HCE/UTRLBA/UTRLRSR so a successful run would show which.
4. **DRAM logs are the promising channel.** `fastboot oem lkmsg` really does
   dump text (it printed the previous kernel's console), and `oem lpmsg`
   returns OKAY. The 04:11 build wrote plain text into `log_store` and
   `ram_console` and `lpmsg` came back empty - so either the shim never
   reached `shim_main`, or `lpmsg` parses a header we did not write.

### Next experiment (start here tomorrow)

Bisect between `_start` and `shim_main` with the DRAM channel, no C:

1. In `start.S`, as the **first** instructions after saving x0, write a magic
   word + a byte counter to `log_store+0x80` and `ram_console+0x100`, then
   `smc` reset. Boot it, `fastboot oem lkmsg` / `oem lpmsg`.
2. If nothing: the write itself is the problem (address wrong, or the buffers
   are re-initialised by LK after the jump). Then read the `log_store` header
   format out of `lk.img` (`oem lkmsg` prints `DATA%08llx` chunks - the dump
   path probably walks a ring header).
3. If it works: move the marker forward one step at a time (after bss zeroing,
   after `board_probe`, after `con_clear`, after `mmu_enable`, after
   `test_gic`, after `test_ufs`) and the first missing marker names the fault.
4. Only then fix the real blocker (likely the UFS host: compare the MTK
   `ufshcd-mtk` probe sequence in the vendor kernel, or keep LK's list and
   issue commands through it).

### Operational rules learned the hard way

- **Always flash a trimmed image.** `fastboot flash` of a 64 MiB file wedges
  LK's USB stack ("Write to device failed (no link)"); trimming to the AVB
  `original_image_size` (or just the real size) works every time. Re-entering
  fastboot needs Power + Volume Down by hand.
- **`adb shell 'dd ...' > file` corrupts binaries on Windows** (CRLF). Use
  `adb exec-out` for every pull; `adb push` for writes.
- Recovery is in `vendor_boot`, so a broken `boot_a` does not cost recovery:
  `fastboot reboot recovery` still works, and `fastboot flash boot_a
  backup/boot_a-stock.img` (trimmed) restores normal boot.
- The device's own firmware package is at
  `D:/am/matisse_images_OS2.0.2.0.ULKCNXM_20250401.0000.00_14.0_cn_7623fe2c93/`
  (boot.img, vendor_boot.img, dtbo.img, lk.img, preloader, scatter).
- The TWRP/OrangeFox device tree at `D:/twrp/` has the MTK bootctrl layout
  (`bootctrl/boot_control_definition.h`: BCB at misc block 0, magic
  `0x42414342` in `slot_suffix`) and the working vendor_boot repack scripts.


## Agent handoff / active work (2026-10-15, latest)

### >>> UPDATE 2026-10-18 (read first, then the 10-17 handoff below) <<<

**STATE at end of 2026-10-18 (evening):**
- On the phone: userdata = **image v15** (`/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v15.img/.simg`; = v14 + `/private/var/preferences/SystemConfiguration/preferences.plist` with one network service "USB Ethernet": Interface DeviceName ue0 Type Ethernet, IPv4 Manual 192.168.7.2/24 router 192.168.7.1, DNS 1.1.1.1 8.8.8.8, set "Automatic" as CurrentSet). Fallback image v14 (v12/v13 deleted).
- Kernels: **0078** = all 8 cores (`cpus=8`, cpu6/7 = A76, homogeneous SMP; mkadt now emits 8 CPU nodes; ADT `shim/out/sweet-0078hi.adt`); **0079** = display off/on via IOMFB selector 12 kern_RequestPowerChange(state) (fade over 6 vsyncs, then black; verified: power button gives "SWEET FB: display off/on"); **0080** = `SWEET NET: <proc> route add/delete/change ...` and `... add/delete address ...` trace (built, `shim/out/shim-boot-0080s.img`, NOT booted yet).
- **Internet over USB: not working yet.** Host side is ready (temporary iptables: FORWARD accept for 192.168.7.0/24 + MASQUERADE, comment "ios-sweet"; lost on host reboot). With v15 the phone sent no DNS and no outbound traffic (only mDNS/ARP) -> configd probably did not apply the service (or did, without default route/DNS). Next: boot 0080s on v15, watch `SWEET NET` lines (does configd add 192.168.7.2 / default route on ue0?). If configd ignores ue0 (not an IOKit IONetworkInterface; configd 1241.140.6 source in `research/configd-1241`, SCNetworkInterface createInterface needs IONetworkInterface -> controller (IOMACAddress, SelfNamed) -> bus), add IOKit stand-ins and/or rename the ifnet to en*.
- Live log listener (`nc -ukl 5140`) may still be running, writing `logs/live-0078s.txt`.

**LIVE LOG over USB (0077, boot 0077s): no more recovery round-trips.** Boot an image with `sweetusb=2`, run `tools/sweet-usbnet.sh logs/live-NAME.txt` on the PC: it waits for the phone's CDC-ECM interface, sets 192.168.7.1/24 (sudo) and prints/saves the kernel console the phone streams every 100 ms as UDP to 192.168.7.1:5140 (starts with the last 64 KiB). `ping 192.168.7.2` checks the link. Current image: `shim/out/shim-boot-0077s.img` (kernel 0077, ADT sweet-0069hi, cpus=6, sweetfb_noconsole=1, sweetfb_hz=30, sweetusb=2). The recovery pull (`tools/sweet-log-pull.sh`) is still the way after a panic/hang before USB comes up.

**🎉 USB networking works (boot 0076s, 18:24 local): `ping 192.168.7.2` from the PC answers in ~1 ms, 0 % loss (200-packet burst, 1500-byte frames, 512/1024-byte frames).** 0075: CDC-ECM function (bulk 0x81/0x01, interrupt 0x82 NETWORK_CONNECTION; host MAC 02:00:5e:77:ee:01) + kernel ethernet ifnet `ue0` (legacy output, header strip like if_fake) with 192.168.7.2/24 set from the kernel (proto_plumb + SIOCAIFADDR on an in-kernel socket). 0076: received frames into one cluster mbuf (mbuf_allocpacket gave a first mbuf of length 0 -> panic "Failed mbuf validity check: len -14"). Host side (Linux cdc_ether, interface `enp0s20f0u3u1`): `nmcli dev set IF managed no; sudo ip addr add 192.168.7.1/24 dev IF; sudo ip link set IF up`. Boot image `shim/out/shim-boot-0076s.img` (`sweetusb=2`). iOS itself does not see ue0 yet (configd needs an IONetworkInterface in IOKit).

**USB device mode works (boot 0074s, 17:45 local): the host enumerates the phone at high speed as `1209:0001 ios-sweet "Sweet iOS (USB bring-up)"`.** 0072 read-only probe: ABL leaves the USB30_PRIM GDSC and all 7 USB clocks on, DWC3 v3.20a (GSNPSID 0x5533320a) in device mode (GCTL 0x00112000), halted (DCTL RUN_STOP 0, DSTS 0x00d3de04), event buffer still at ABL's 0xfdfc8000, QUSB2 PHY alive. 0073 (`sweetusb=2`): DCTL soft reset, own event buffer (polled, interrupt masked), ep0 DEPSTARTCFG/SETEPCONFIG/SETTRANSFRESOURCE, setup TRB, RUN_STOP; control-transfer state machine as Linux ep0.c; descriptors: vendor class, no data endpoints. It ran but no event came: **0074** forces the VBUS session in the Qualcomm wrapper (QSCRATCH 0xa6f8800: HS_PHY_CTRL |= UTMI_OTG_VBUS_VALID(20)|SW_SESSVLD_SEL(28), SS_PHY_CTRL |= LANE0_PWR_PRESENT(24), as Linux dwc3_qcom_vbus_override_enable) -> connect + enumeration. Image `shim/out/shim-boot-0074s.img`. Black Phone app: main thread waits in -[VMVoicemailManager fetchAccounts] (sync XPC; voicemail needs the modem).

**Navigation works (boot 0071s on v14, log `logs/boot-0071s.txt`):** swipe up from the bottom edge returns to the home screen; app switcher works. Two fixes: **0070** pid_hibernate (syscall 435 was nosys on the macOS config -> SIGSYS killed SpringBoard in -[SBToAppsWorkspaceTransaction _didComplete] when an app left the foreground; the only CONFIG_EMBEDDED-only entry in syscalls.master); **0071** touches starting within 48 px of an edge carry kIOHIDDigitizerEventFromEdgeTip (UIKit `_UIEventHIDTouchEdgeTypeForHIDEvent` reads the digitizer EventMask 0xb0007 bits 0x800/0x1000/0x2000/0x40000). Diagnosed, not fixed: Settings (Preferences) SIGTRAP in PodcastsUI func_1cdc47c58: force-unwrap of `URL.volumeTotalCapacity` (nil on our HFS+ root). Phone/Photos/Messages show black (MobilePhone launches, no crash) - next: stack samples via boot-arg `sweetsample=MobilePhone,MobileSlideShow,MobileSMS,SpringBoard` (image `shim/out/shim-boot-0071s.img`).

**🎉🎉 MILESTONE 2026-10-18 ~15:30 local: iOS 16.7 SPRINGBOARD HOME SCREEN ON THE PHONE** (boot `shim/out/shim-boot-0069s.img` = kernel 0069 + ADT `shim/out/sweet-0069hi.adt`, image **v14**; log `logs/boot-0069s-v14-HOMESCREEN.txt`). Home screen with a few apps, Settings opens, Control Center / lock screen / notification center work, touch + buttons work, 6 A55 cores (`cpus=6`), `sweetfb_noconsole=1 sweetfb_hz=30`. Known issue: cannot leave Settings (home gesture / AssistiveTouch home do nothing) - to investigate.
What it took today (in order): 0058 HID services (Apple IOHIDFamily-1915 user client), 0059 iPhone X identity (hw.product etc.), 0060 SPMI keys + GENI touch, 0061 1125x2436 presented display, 0062 RPMh L11C touch power + IOPMPowerSource battery, 0063 GENI FIFO packing (Goodix GT9896), 0064 PRIV_WORK_INTERVAL grant (SpringBoard "Failed to create work interval"), 0065 SMP via PSCI (6 A55), 0066 crash map regions, 0067 console mute without cons_ops write, 0068 IOSurface lookup of an already-mapped surface returns base -1 (backboardd snapshot crash), 0069 UFS log flush 10 s. Image v13: CommCenter removed from launchd.plist, BKNoWatchdogs. **Image v14** = v13 + Setup skipped (`/var/mobile/Library/Preferences/com.apple.purplebuddy.plist` SetupDone/SetupFinishedAllSteps true, SetupVersion 11, daemonizeNeedsToRun false; purplebuddy.notbackedup mini-buddy flags false; managedconfiguration.notbackedup LockdownActivationIndicatesCloudConfigurationAvailable false; springboard MCForceShowCloudConfigurationUI false) + mobileactivationd data ark seeded (`/private/var/containers/Data/System/D90C94C4-F94E-4FCF-8A28-A93BE3846D8E/Library/internal/data_ark.plist`, -ActivationState Activated etc.) + MediaToolbox D22/D221/Default SubServerConfiguration StartRecorderServer=false (mediaserverd crash loop: portrait-filter prewarm called NULL GL). **Activation:** ADT /chosen certificate-production-status 0, effective-production-status-ap 0, certificate-security-mode 1, effective-security-mode-sep 1 -> mobileactivationd's native Hactivation branch (Codex `codex/activation/report.md`; alternative 4-byte daemon patch documented there). Fallback image v13; v12 deleted.

**🎉 TOUCH WORKS (boot 0063u on image v13, 2026-10-18 ~13:15 local):** tapping (i) on Setup's Hello opens the device-info sheet; swipe-up is recognised (spinner, then back to Hello - under investigation). Chain: RPMh L11C vote (0062, `bsd/dev/sweet_rpmh.c`) -> GT9896 @0x5d on GENI SE7 (0063 fixed FIFO packing: register address went out byte-swapped) -> sweet_hid_touch -> SweetHIDTouch -> backboardd. Battery icon OK (0062 SweetBattery IOPMPowerSource, static 100 %). **Image v13** (= v12 + CommCenter jobs removed from launchd.plist + `BKNoWatchdogs`=true and `FBLaunchWatchdogExceptions{com.apple.purplebuddy:100}` in /var/mobile/Library/Preferences/com.apple.springboard.plist): Setup no longer killed by the scene-create watchdog (it blocked in CoreTelephony getSubscriptionInfo -> CommCenter, Codex `codex/setup/report.md`; that report also lists how to skip Setup: SetupDone/SetupFinishedAllSteps/SetupVersion=11 in com.apple.purplebuddy.plist + activation check). v9 deleted, fallback = v12. Input latency ~1 min with 1 CPU -> Codex task 11 = SMP via PSCI (`codex/smp/`).

**INPUT WORKS (boot 0060u on a freshly flashed v12, log `logs/boot-0060u.txt`):** power, volume up, volume down reach iOS (PMIC -> sweet_keys.c -> SweetHIDButtons -> backboardd). Power locks/unlocks (screen "off" shows the kernel console because the panel never really turns off). **Device identity fixed: iPhone X** (home indicator, Face-ID-style Setup). Earlier test (0059u, `sweethidtest=15` fake Home presses) advanced Setup from Hello to the language list.
- **0058** HID: `iokit/Kernel/arm/hid/`: IOHIDEventServiceUserClient + IOHIDEventServiceQueue + IOHIDEvent compiled UNCHANGED from Apple IOHIDFamily-1915.140.3 (= exact version of the iOS 16.7 IOHIDLib, clone in `research/iohid-1915`) over our minimal IOHIDEventService (SweetHID.cpp: SweetHIDButtons consumer page 0x0C power 0x30 / vol 0xE9/0xEA / menu 0x40; SweetHIDTouch digitizer collections). C hooks `sweet_hid_key(key, down)` (0 power, 1 vol+, 2 vol-, 3 home) and `sweet_hid_touch(finger, down, x, y)`. HID logs go to IOLog. Boot-arg `sweethidtest=N` = fake Home press every N s.
- **0059** identity: hw.machine/hw.model/hw.target/hw.product from the ADT (macOS-config kernel used to answer "iPad8,6"/"Redmi Note 10 Pro"). mkadt: root compatible `D22AP|iPhone10,3|xiaomi,sweet|...`, model iPhone10,3, target-type D22, `/product` copied from the real D22AP device tree (`research/ipsw-x/d22ap.adt`, extracted from the IPSW, NOT in git), `/buttons home-button-type 2`. Codex report `codex/mg/report.md`: MG ProductType = sysctl **hw.product** (unrecognized -> iPod), HWModelStr = hw.target, HomeButtonType = `/buttons/home-button-type`; disk cache `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist` (keyed by build + UUID only -> stale iPod answers survived; reflashed clean v12 to clear it).
- **0060** drivers by Codex (`codex/input/hw-report.md`): `bsd/dev/sweet_keys.c` (SPMI arbiter v5 observer reads, PON 0x810 bits KPDPWR/RESIN SID0, PM6150L GPIO2 SID4 0xc110; works), `bsd/dev/sweet_touch.c` (GENI SE7 I2C @0xa84000, starts 45 s after boot). Touch result: SE7 fw ok (proto 3) but **0x5d and 0x38 both time out -> touch IC unpowered** (L11C LDO needs an RPMh vote). Codex task 8 = RPMh client (`codex/rpmh/`), task 7 = battery (`codex/battery/`).
- **0061** (CI 39117078444): red status bar "rdar:45025538" = UIKit "No status bar visual provider for screen" (360x800 pt is no real iPhone). AppleCLCD now presents **1125x2436** (iPhone X) and shows the centered 1080x2400; touch mapped the same; ADT canvas 1125x2436. `sweetfb_native=1` reverts.
- Images: `shim/out/shim-boot-0060b.img` (0060 + /buttons ADT `sweet-0060hi.adt`). Next: 0061 image with ADT from current mkadt.

### >>> HANDOFF 2026-10-17 late (read this first) <<<

**STATE: iOS 16.7 Setup.app "Hello" screen is ON THE PHONE** (boot `shim/out/shim-boot-0057u.img` on image v12). Photo `/run/media/dipa/android/ios-sweet-work/MILESTONE-setup-hello-2026-10-17.jpg`. SpringBoard + backboardd + Setup run; nothing crashes in the GUI path. Remaining crash/relaunch loops: installcoordinationd (should be fixed by 0057, unverified), mediaserverd (VirtualAudio removed in v12, unverified), mobileactivationd (~100/boot, not analysed), bluetoothd + fairplayd.H2 relaunch constantly (no hardware).

**What is on the phone now:** userdata = image **v12** (`/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v12.img` + `.simg` + `.sha256`; v9 kept as fallback, v8/v10/v11 deleted or superseded). Kernel **0057** (`xnu/out-0057`, CI `39067532732`). ADT **`shim/out/sweet-0057hi.adt`** (QEMU twin `research/q0057-hi.adt`). Boot image **`shim/out/shim-boot-0057u.img`**. Boot args: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1`.

**Build a phone image** (kernel K, ADT A): `python3 tools/mkpayload.py $K $A shim/out/payload-X.bin --cmdline "$B" && (cd shim && ./build.sh ../shim/out/payload-X.bin && cp out/shim-boot.img out/shim-boot-X.img)` then check `python3 tools/unpack_bootv2.py shim/out/shim-boot-X.img shim/out/check-X && cmp shim/out/check-X/ramdisk shim/out/payload-X.bin`. QEMU smoke test: `python3 tools/mkpayload.py $K research/q0057-hi.adt $S/csq/payloadX.bin --ramdisk $S/csq/root-fork.img --cmdline "debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0"` + `PATH=~/swift/usr/bin:$PATH python3 tools/qemu-sweet.py ... --secs 40 --kernel $K --fail-on-panic` (S = scratchpad; root-fork.img lives in the session scratchpad csq/ dir and may be gone in a new session).

**Kernel workflow:** edit `research/bt28/xnu/...` (keep the pre-edit copy in `research/preNNNN/`), syntax check `python3 tools/xnu-cc.py research/run28.log research/bt28 <file>`, make `xnu/patches/NNNN-*.patch` with `diff -u pre bt28 | sed` path rewrite, add new/changed files to `research/check-patch-order.py` list, run it (must say applied + MATCH), commit + push -> GitHub Actions `xnu` builds (~8 min) -> `gh run download <id> -R dipa-studio/ios-sweet -n xnu-kernel -D xnu/out-NNNN`. **Repo is PUBLIC since 2026-10-17** (private macOS runner minutes ran out: "recent account payments have failed"); public = free runners. Commit trailer: only `Co-Authored-By: dipa-studio <183933817+dipa-studio@users.noreply.github.com>`.

**Phone workflow (user does buttons, Claude runs commands):**
- Boot test kernel: phone in fastboot -> `fastboot boot shim/out/shim-boot-X.img`. NEVER `fastboot getvar`.
- **Read logs WITHOUT filming:** after a test the user holds Power+VolDown -> fastboot -> `fastboot boot recovery/recovery.img` (OrangeFox, temp) -> wait for `adb shell true` -> `tools/sweet-log-pull.sh /run/media/dipa/android/ios-sweet-work/logs/<name>.txt` (reads the 4 MiB console ring the kernel keeps in the last 1025 blocks of userdata; patch 0047). Then `adb reboot bootloader` brings the phone back to fastboot without the user.
- **Flash a root image:** `fastboot flash -S 512M userdata <img>.simg` (7 min, 25 chunks). After a big flash the Xiaomi ABL fastboot hangs: next `fastboot boot` stalls at "Sending" -> the USER must re-enter fastboot by hand (Power+VolDown). Do not retry commands against the hung ABL. A flash right after `adb reboot bootloader` once failed on chunk 1 (USB protocol error): just retry. User must NOT unplug during flash (once interrupted at 25/25 -> reflash).
- **Edit the phone root without reflashing (same-length only):** find the file's raw offset in the local image (it is one extent; search the image for its first 4 KiB), compare with `adb exec-out dd if=/dev/block/sda34 ...`, then `adb push` changed 4 KiB blocks and `dd of=/dev/block/sda34 seek=... conv=notrunc,fsync`, read back. Recovery kernel has no hfsplus.
- **Pull the whole phone root** (12 min): `adb exec-out "dd if=/dev/block/sda34 bs=1048576 count=16384" > img`; mount ro with udisksctl or `sudo mount -t hfsplus -o loop,ro`. To write into a pulled image: `sudo fsck.hfsplus -fy` first (volume is never cleanly unmounted -> Linux mounts ro even with -o force), then `sudo mount -t hfsplus -o loop,force`.
- Making images: copy latest img with `cp --sparse=always`, mount rw with force, change, `sync; umount`, `sha256sum`, `img2simg`. Keep only the latest image + one fallback on the SSD (/home is ~98% full; big work files live in `/run/media/dipa/android/ios-sweet-work/`, old kernels/images archived in `.../ios-sweet-work/archive/`).

**Diagnostics built into the kernel (all SWEET-only):** persistent console log (0047, boot-arg sweetlog=0 off; flush every ~1 s); `|name| text` copies of /dev/null writes for processes in `sweetnull` (default backboardd,SpringBoard; 0048); `IOK name:` IOKit lookup/open trace for `sweetiolog` (default backboardd,SpringBoard; 0049/0054, 400 lines max); `SWEET Sandbox: proc op N` (0050); `STR ...` heap text dump on SIGABRT/SIGTRAP for `sweetscan` (default backboardd,SpringBoard; 0051/0053) - this is how NSException/BSAssert reasons were found; `SMP name tN pc .. bt ..` user stack sampler for `sweetsample` (default SpringBoard,backboardd; first at 90 s then every 120 s, 40 samples, 16 frames; 0054/0055); `SWEET FB: N swaps` every 1000 swaps; `CRASH name ...` crash reports (0043/0044). Symbolicate cache addresses with `ipsw dyld a2s research/dsc/dyld_shared_cache_arm64 <addr - slide>`; main-binary addresses: addr - mh + 0x100000000.

**Patches added today (0046-0057, all CI-built):** 0046 IOSurface (IOCoreSurfaceRoot) + IOMobileFramebuffer (AppleCLCD on /arm-io/disp0) + arm-io nubs; 0047 persistent log; 0048 /dev/null tee; 0049 IOKit trace; 0050 permissive "Sandbox" MAC policy; 0051/0053 heap text on crash; 0052 RENAME_SWAP via exchangedata on HFS; 0054/0055 sampler, swap counter, AKS selectors 35 (extended device state = 17) and 101; 0056 panel copy from the vsync timer at 10 Hz (sweetfb_hz) instead of every swap; 0057 no config_ext_resolver (ACL group entries resolve without memberd). ADT (tools/mkadt.py): /arm-io/disp0, /chosen/display-scale 3, /chosen/unique-chip-id (synthetic 0x5357454554000001) + chip-id 0x8015 -> IOPlatformUUID A9683613-3704-53F2-95D0-98A8FB574E78. Image v9-v12: /private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}; launchd.plist StandardOut/ErrorPath renamed to ...PatX (pointing jobs at /dev/console stops them launching: EACCES); v12: LastSystemVersion=20H392 in plain /var/mobile/Library/Preferences/com.apple.migration.plist (stops the 1-hour data migration that ran every boot), VirtualAudio.plugin moved to /Library/Audio/Plug-Ins/HAL.disabled/.

**Codex helper** (`codex exec --skip-git-repo-check -m gpt-6.1-sol -c model_reasoning_effort=high -s workspace-write -C /run/media/dipa/android/ios-sweet-work/codex -o OUT "$(cat task.md)" < /dev/null`, run detached with setsid nohup; config already defaults to gpt-6.1-sol high). Reports in `/run/media/dipa/android/ios-sweet-work/codex/`: mediaserverd-report.md, installcoordinationd-report.md (-> 0057), ufs-performance.md (ranked UFS speed plan), **ufs/0058-sweet-ufs-faster.patch** (READY, not yet applied: ufslogsecs default 10 s log flush batching, optional ufscache=1 directional cache ops, optional ufswait=1 spin-then-sleep; syntax-checked, host-stub verified; see ufs/README.md). Apply it as patch 0058 after review.

**Claude safety-classifier note:** several messages about QuartzCore renderer selection / writing an HFS reader tool were cut off by an automatic classifier (false positives, user agrees). Keep those topics short or let them be; they are not blockers now (rendering already works).

**NEXT STEPS (in order):**
1. **Input** (Setup stuck on Hello): touch + power + volume as IOHIDEventService/IOHIDDevice services backboardd matches (log shows backboardd matching IOHIDDevice with DeviceUsagePairs and IOService with HIDServiceSupport=true). Phone hardware: Goodix/Focaltech touch over I2C/SPI (check live DT `dtb/`), buttons via PMIC (pon) and GPIO keys. Start with a fake HID service (e.g. power button via a timer or boot-arg) to learn the ABI, then real drivers.
2. **Device identity**: MobileGestalt reports iPod + Home button. Add /product node / product-type (iPhone10,3, D22AP), device class iPhone, home-button-less (Face ID) properties in mkadt; find exact MG keys (MGIOMFBSupport.c, `IODeviceTree:/product` lookups seen in backboardd trace).
3. **Battery**: AppleSmartBattery/IOPMPowerSource style service (status bar shows empty red battery).
4. Apply Codex 0058 (UFS speed), consider cpus>1, check whether installcoordinationd/mediaserverd loops are gone, analyse mobileactivationd.
5. Hide kernel console over the GUI when not debugging: boot-arg sweetfb_noconsole=1 (but then panics are not on screen).

- **🎉 MILESTONE: iOS GUI ON THE PHONE - Setup.app "Hello" screen (2026-10-17 ~21:30 UTC, boot 0057u on image v12).** Photo: `/run/media/dipa/android/ios-sweet-work/MILESTONE-setup-hello-2026-10-17.jpg` (animated "dobar d(an)" hello lettering, status bar "iPod" + battery icon, bottom "Pritisnite Home za otvaranje" = "Press Home to open" (locale hr), (i) button). Rendered by iOS 16.7 SpringBoard/Setup through CoreAnimation -> backboardd -> kernel IOSurface (IOCoreSurfaceRoot) -> kernel AppleCLCD swaps -> ABL panel memory. What made it: data migration finally skipped (v12: `LastSystemVersion` 20H392 in the plain `/var/mobile/Library/Preferences/com.apple.migration.plist`; ADT `/chosen/unique-chip-id` + `chip-id` for IOPlatformUUID), 0057 (no ext resolver -> ACL containers work), plus everything before (0046 display/IOSurface, 0050 Sandbox policy, display-scale 3, containers dirs, RENAME_SWAP, AKS 35/101, 10 Hz present).
  **Missing (next work, in rough order):**
  - **Input: no touch, no power button, no volume buttons** (no HID drivers; backboardd looks for IOHIDDevice / HIDServiceSupport services). Setup is stuck on "Hello".
  - **Device identity: it thinks it is an iPod with a Home button** (status bar "iPod", "Press Home to open"): MobileGestalt has no product/model identity (no /product node, product-type, etc.) - give it iPhone10,3 / home-button-less (Face ID class) identity.
  - **No battery detection** (no battery/charger/PMIC service; status bar battery shows empty/red).
  - Locale came up Croatian (hr) - harmless.
  - Performance: cpus=1, polled UFS (Codex UFS patch in progress: `codex/ufs/`), backboardd presents at 60 Hz.
- **README refreshed (2026-10-17):** read the full notes and updated the public overview through patch 0045, v7 hardware observations and prepared v8 diagnostics. Documents real Apple userspace, the missing display/IOSurface GUI work, limited security services, highmem, persistent userdata root, current tooling and build examples. Documentation only; no kernel build, tests, flashing or phone boot performed.

- **v11 still migrated (boot 0056u, 20:32 UTC, `logs/boot-0056u-v11.txt`).** On iOS kCFPreferencesCurrentHost lands in the plain plist: all DMEnvironment setters (setLastBuildVersionPref, setUserDataDispositionPref, ...) use CurrentHost and the phone's `/var/mobile/Library/Preferences/com.apple.migration.plist` holds DMUserDataDisposition; datamigrator's XPC service runs with `_RoleAccount: mobile`. **Image v12** = v11 + `LastSystemVersion` = 20H392 added to that plain `com.apple.migration.plist` (keeps DMUserDataDisposition, 501:501 0600) + `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin` moved to `/Library/Audio/Plug-Ins/HAL.disabled/` (Codex: stops the mediaserverd SIGSEGV loop; untested). **installcoordinationd loop explained (Codex `codex/installcoordinationd-report.md`):** os_crash "Failed to create data directory" EACCES for `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.installcoordinationd/Library/InstallCoordination`: `Library`/`Library/Caches` in the container are ACL-only (uid/gid 0xfffffffe, mode 0000, ACE for group GUID ...012B = gid 299 systemusers); with `config_ext_resolver` the kernel asks an identity resolver (memberd/opendirectoryd) that iOS does not run -> EWOULDBLOCK -> ACE skipped -> EACCES. **Patch 0057**: drop config_ext_resolver from FILESYS_BASE (compatibility UID/GID<->GUID mapping is used instead), likely fixes other ACL-protected containers too. Codex task 3: UFS driver performance design (`codex/ufs-performance.md`).
- **mediaserverd SIGSEGV loop explained (Codex report `/run/media/dipa/android/ios-sweet-work/codex/mediaserverd-report.md`):** the fault (FAR 0x390) is in the HAL plug-in `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin/VirtualAudio` (ApplyBeamFormerPreference <- VirtualAudio_PlugIn::SetPropertyData <- HALS_AHPPlugIn::ObjectSetPropertyData <- AudioObjectSetPropertyData <- MediaExperience vaemVADSerializationListener): the default virtual audio device ('vdef', VirtualAudioDevice_Default) does not exist (no audio hardware/driver, no /product/audio). Proposed workaround for GUI bring-up: move VirtualAudio.plugin out of /Library/Audio/Plug-Ins/HAL/ in the next root image (untested). v11 reflashed OK after an interrupted flash (USB unplugged at chunk 25/25). Codex task 2: installcoordinationd crash loop.
- **ROOT CAUSE of the endless migration: no IOPlatformUUID (2026-10-17).** datamigrator's `-[DMMigrator isMigrationNeededWithBuildVersion:lastBuildVersion:clientPID:]` gets lastBuildVersion from `-[DMEnvironment lastBuildVersionPref]` = `CFPreferencesCopyValue("LastSystemVersion", "com.apple.migration", kCFPreferencesCurrentUser, kCFPreferencesCurrentHost)` -> a ByHost file. The phone had `/var/mobile/Library/Preferences/com.apple.migration.plist` (only DMUserDataDisposition) and NO `Preferences/ByHost` directory at all. On arm XNU publishes IOPlatformUUID (gethostuuid) only from `/chosen/unique-chip-id` (8 bytes) + `/chosen/chip-id` (4 bytes) (IOPlatformExpertDevice::generatePlatformUUID, called from IOPlatformExpert::start); the SWEET ADT had neither, so every CurrentHost preference write failed. Fix: mkadt adds `unique-chip-id` = 0x5357454554000001 (synthetic) and `chip-id` = 0x8015 -> UUID `A9683613-3704-53F2-95D0-98A8FB574E78` (same SHA1 namespace computation as the kernel). ADT `shim/out/sweet-0057hi.adt`, boot image `shim/out/shim-boot-0056u.img` (0056 kernel + 0057hi ADT, QEMU OK). **Image v11** = v10 + `Preferences/ByHost/com.apple.migration.<UUID upper and lower>.plist` {LastSystemVersion: 20H392} (501:501, dir 0755 files 0600). Flashing v11 2026-10-17 ~20:10 UTC. Codex (gpt-6.1-sol high) analysing the mediaserverd SIGSEGV loop in `/run/media/dipa/android/ios-sweet-work/codex/` (report `mediaserverd-report.md`).
- **v10 did NOT stop the migration (boot 0056n on v10, 19:18 UTC, `logs/boot-0056n-v10.txt`):** datamigrator + migrationpluginwrapper ran again, backboardd drew the progress UI (13000+ swaps in ~5 min). So `com.apple.DataMigration.plist`/LastSystemVersion is not the deciding input. What is known about the decision (datamigrator XPC service `DataMigration.framework/XPCServices/com.apple.datamigrator.xpc/com.apple.datamigrator`, strings): `Migration sentinel present. Migration necessary.` (sentinel `/private/var/datamigrator/migrate`, created and removed by datamigrator itself; absent in v10), `System build version changed from %@ to %@. Migration necessary.` / `...unchanged... not necessary`, `Disposition dictionary with build version %@ is out of date relative to current build version %@`, `SyntheticDidUpgrade`, DMUserDataDispositionManager; previous build source not found yet (not in the binary's plain strings; likely in DataMigration.framework's DMUserDataDispositionManager / preferences domain `com.apple.migration`). backboardd calls `DMPerformMigrationIfNeeded` and holds SpringBoard in `checkInAndWaitForDataMigration` until it returns. NEXT: find where the previous build version / disposition dictionary is stored (disassemble DataMigration.framework DMUserDataDispositionManager + datamigrator's isMigrationNeeded path), write it into the image; or get datamigrator's os_log out (OS_ACTIVITY_DT_MODE needs a launchd env change).
- **Data migration never records completion -> skip it (image v10, 2026-10-17).** Boot 0056n (18:16 UTC, `logs/boot-0056n.txt`, 30 min): 10 Hz panel copies, backboardd still presents at ~60 Hz (107k swaps), SpringBoard still in `BKSSystemShellService _checkIn` at sample 14. Every migration run takes 1+ hour on cpus=1 and repeats each boot. DataMigration decides from `/var/mobile/Library/Preferences/com.apple.DataMigration.plist` key `LastSystemVersion` (vs ProductBuildVersion 20H392); the pulled phone root (21:01 local) had NO such file. **v10** = that pulled phone state + fsck.hfsplus -fy (volume was marked not cleanly unmounted -> Linux mounts it read-only even with -o force; fsck found only hard-link counts + minor volume header, repaired) + `com.apple.DataMigration.plist` {LastSystemVersion: 20H392} (501:501 0600). `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v10.img` (+ .sha256, .simg); v8 files deleted, v9 kept as fallback.
- **Repo made PUBLIC (2026-10-17, user request) so GitHub-hosted macOS runners are free:** the 0056 CI run was refused ("recent account payments have failed or your spending limit needs to be increased"). Checked before: no device dumps, serials, IMEI or Apple binaries in the history (.gitignore keeps backup/, dtb/, recovery/, research/ out); commit author emails are public now. 0056 rerun `39056660104` passed -> `xnu/out-0056`, QEMU OK, image `shim/out/shim-boot-0056n.img` booted 18:16 UTC.
- **Boot 0055n (17:16 UTC, ~65 min, `logs/boot-0055n.txt`): migration bar reached 99% in ~30 min (was ~60), then crawled.** Root cause of the slowness: backboardd presents the progress UI at ~58 Hz (**167,000 swaps in ~48 min**) and AppleCLCD copied the full 10 MB frame into the panel on every swap -> ~600 MB/s memcpy on the single CPU (cpus=1), starving everything (polled UFS I/O too). Sampler (16 frames): **SpringBoard waits in `-[BKSSystemShellService _checkIn]`** (synchronous BSXPC call to backboardd, from FBSystemShellInitialize <- SBSystemAppMain) - backboardd holds the system shell check-in until the data migration ends, so the migration is the gate to SpringBoard UI. **Patch 0056**: swap only records the latest swap record; the vsync timer copies it to the panel every 60/sweetfb_hz ticks (default 10 Hz); `SWEET FB: N swaps` every 1000.
- **Boot 0054n (16:53 UTC, ~12 min, `logs/boot-0054n.txt`, patch 0054 sampler):** the data migration runs AGAIN on every boot (it completed once in the 0053s run) - completion is not being recorded. backboardd keeps swapping the progress UI (3700+ swaps in ~10 min, `SWEET FB: N swaps`). Sample during migration: SpringBoard main thread blocked in a synchronous XPC call (`mach_msg2_trap <- dispatch_mach_send_and_wait_for_reply <- xpc_connection_send_message_with_reply_sync <- -[BSXPCServiceConnectionMessage _sendSynchronously:] <- BSXPCServiceConnectionProxy ...`). Unsupported AKS selectors seen: 101 `aks_assert_consume`, 93 `aks_get_internal_info`, 34 `_aks_stash_load` (datamigrator), **35 `aks_get_extended_device_state`** (SpringBoard, chronod, remindd, searchd, symptomsd...). **Patch 0055**: AKS 35 answered like 17 (same DER device state), 101 succeeds; sampler backtraces 16 frames, 40 samples (~80 min).
- **MILESTONE: FIRST iOS PIXELS ON THE PANEL (boot 0053s long run, 15:33 UTC, ~60 min; photos IMG_20261017_183029/183222/183419; log `/run/media/dipa/android/ios-sweet-work/logs/boot-0053s-long.txt`, 407 KB, 1428 flushes).** The iOS data-migration boot screen (Apple logo + progress bar) was shown and the bar advanced to full in ~60 minutes. It is drawn by **backboardd through our kernel display path**: `SWEET SURF: backboardd create id 2/3 1080x2400 fmt 0x42475241 (BGRA) bpe 4 bpr 4352 size 10452992 keys 11`, IOSurface sels 9, 27, 5, 34 and traps 2/3 (lock/unlock), IOMFB SwapBegin (4) / SwapEnd (5, 1132-byte struct) / GetLayerDefaultSurface (3) / 68 / 55, then `SWEET FB: swap N layer 0 surface 2|3 1080x2400 ... a 0,0 1080x2400 b 0,0 1080x2400` alternating (double buffering; only the first 16 swaps are logged). Triggered by `com.apple.datamigrator` (AKS sels 101/93/34 return unsupported) and `com.apple.migrationpluginwrapper`. Probably ProgressUI-style CoreGraphics drawing into IOSurfaces (no GPU used). After the migration: SpringBoard and backboardd stay up (exec once each, no crash), nothing new drawn; crash loops only for mediaserverd, installcoordinationd, mobileactivationd (~108 each in the hour); bluetoothd and fairplayd.H2 relaunch constantly (no hardware). Kernel console text overlays the image (sweetfb_noconsole=1 would hide it).
- **MILESTONE: SpringBoard and backboardd both stay up (boot 0053s on v9, 15:29 UTC, `logs/boot-0053s.txt`).** With /chosen/display-scale = 3: `exec SpringBoard` once, `exec backboardd` once, no CRASH for either; MobileGestaltHelper reads IOMFB size/dot pitch; backboardd opens AppleCLCD (sels 57,15,22,29,33,79,74 as before). No IOSurface creates and no swaps: backboardd keeps polling `IOProviderClass IOAcceleratorES` (-> 0 found) and AGXAccelerator/IOAcceleratorESLegacy - no GPU, so no CoreAnimation renderer yet. Log goes quiet after ~47 s (only 47 flushes): the system idles. NEXT WALL: CoreAnimation rendering without a GPU (CPU path) so the window server produces frames.
- **SpringBoard's invalid mode = scale 0 (boot 0053n, 15:19 UTC, `logs/boot-0053n.txt`, heap scan):** `FBSDisplayMode cannot be configured as requested : <FBSDisplayMode: ...; infxinf@0x (1080x2400/0) 60Hz sRGB SDR>` - pixel size and 60 Hz are fine, scale is 0. libMobileGestalt (MGIOMFBSupport.c, sub_19c560a54) reads `display-scale` (u32, default 0) and `display-rotation` from **IODeviceTree:/chosen**, pixel size via IOMobileFramebufferGetDisplaySize (MobileGestaltHelper sel 8/28 in the log). mkadt now adds `/chosen/display-scale` = 3 (1080x2400 @3x = 360x800 pt). ADT `shim/out/sweet-0054hi.adt` (QEMU `research/q0054-hi.adt`), image `shim/out/shim-boot-0053s.img` = 0053 kernel + 0054hi ADT. Possible catch: MobileGestalt may have cached scale 0 in `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/...`.
- **MILESTONE: backboardd survives and opens the display (boot 0052n on v9, 15:07 UTC, `logs/boot-0052n.txt`).** No `CRASH backboardd`. It walks `IODeviceTree:/arm-io` children, matches IOMobileFramebuffer, `open AppleCLCD type 0 -> 0x0`, calls IOMFB selectors 57, 15, 22, 29, 33, 79 (GetBlock, out 1580/40), 74 (SupportedFrameInfo), 12, 6, 27, 17, 84, 50; opens IOCoreSurfaceRoot (sel 13); looks for AGXAccelerator / IOAcceleratorES / IOAcceleratorESLegacy (none, repeatedly), `IODeviceTree:/product` (none, repeatedly), `/buttons`, aop-smart-cover, IOPMrootDomain. No swaps yet. **SpringBoard now gets past display discovery** and dies (sig 5) in `-[FBSDisplayMode _initWithWidth:height:preferredScale:scaleOverride:refreshRate:gamut:hdr:rotation:virtual:validityCheck:]+580` <- `_initWithCADisplayMode:scale:rotation:` <- `FBSDisplayConfiguration _initWithImmutableDisplay:` <- `FBSDisplaySource initWithDisplay:` <- `FBSDisplayMonitor` (slide 0x14504000): "FBSDisplayMode cannot be configured as requested". Checks: width/height >= 2, preferredScale > 0, scaleOverride >= 0, refreshRate non-zero, valid gamut/HDR/rotation. Suspects: refresh rate 0 (IOMFB timing blocks answered with zeros) and/or scale 0 (MobileGestalt main-screen-scale; no /product node in the ADT). **Patch 0053**: heap text scan also on SIGTRAP and for SpringBoard (default sweetscan list backboardd,SpringBoard) to read the exact mode description.
- **Image v9 (2026-10-17) = v8 + `/private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}` (root:wheel 0755; on iOS these live on the data volume, absent from the rootfs) + launchd.plist with StandardOutPath/StandardErrorPath renamed to ...PatX (the in-place phone fix; daemons' stdout reaches the log via the 0048 /dev/null tee).** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v9.img` SHA256 aa9f9139...6e67, `.simg` 12,145,303,864 bytes; written through `sudo mount -t hfsplus -o loop,force`. **Patch 0052** (`xnu/patches/0052-sweet-hfs-rename-swap.patch`, CI `39042761826` -> `xnu/out-0052`, QEMU OK, image `shim/out/shim-boot-0052n.img`): vn_rename falls back to VNOP_EXCHANGE when VNOP_RENAMEX(RENAME_SWAP) returns ENOTSUP for two regular files on one volume (HFS exchange swaps data and cnode descriptors = swap semantics). Flashing v9 started 2026-10-17 ~15:00 UTC.
- **backboardd EXCEPTION FOUND (boot 0051n, 14:41 UTC, `logs/boot-0051n.txt`, patch 0051 heap scan):** `*** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: '*** -[NSURL initFileURLWithPath:]: nil string parameter'`. First-throw stack (slide 0x337f0000): `+[NSURL fileURLWithPath:]` <- `-[BSSystemContainerForCurrentProcessPathProvider libraryPath]` <- `cachesPath` <- backboardd main+0x1d858 (StartWindowServer, shader cache path). BaseBoard: libraryPath = `BSSystemSharedResourcesDirectoryForIdentifier` = `BSSystemSharedDirectoryForIdentifier(id)` + "/Library"; that calls `container_system_path_for_identifier` (libsystem_containermanager -> `_container_legacy_lookup` -> containermanagerd XPC), which returned NULL (BaseBoard logs "Error getting shared system container ..."). With 0050 the sandbox denials are gone. containermanagerd.log.0 in the pulled (pre-0050) image: "Did not receive any persona attributes", "Expected to find at least one persona", notify_register_dispatch failed 9; `/private/var/containers` does NOT exist in the root image (on iOS it lives on the data volume). usermanagerd does create kernel personas 99/199/1000, but fails to save `/private/var/keybags/usersession.kb` and `persona.kb`: "Failed to atomically swap writing file: Operation not supported (45)" (HFS has no RENAME_SWAP). NEXT: create `/private/var/containers` (and check what containermanagerd needs for system group containers), fix or emulate RENAME_SWAP on HFS, then re-check backboardd.
- **Disk housekeeping (2026-10-17):** /home was at 4 GiB free. Moved (not deleted) `xnu/out-0010..0044` and old `shim/out/*.img`/`payload-*.bin` (everything before 0046) to `/run/media/dipa/android/ios-sweet-work/archive/{xnu,shim-out}` (6.3 GiB); /home now 8.7 GiB free. Large work files (phone-root.img 16 GiB, logs, tracev3 dumps) live in `/run/media/dipa/android/ios-sweet-work/`. **Patch 0051** (`xnu/patches/0051-sweet-crash-heap-strings.patch`, CI `39041440935`): on SIGABRT of a process in boot-arg `sweetscan` (default backboardd) the crash log prints heap text runs as `STR ...` (to find the NSException reason). Boot 0051n 14:41 UTC.
- **Boot 0049n (14:11 UTC, `logs/boot-0049n.txt`, patch 0049 trace): backboardd's IOKit lookups before it dies** (each run): `IODeviceTree:/arm-io/pearl-sep` -> none, matching ApplePearlSEPDriver -> none, `IOService:/IOResources/AppleKeyStore` open OK, matching IOHIDDevice (DeviceUsagePairs) / `HIDServiceSupport` notifications, `usb-drd,t8027` notification; **never any IOMobileFramebuffer / display lookup** - the exception happens before display detection. launchd (tracev3) shows backboardd's mach lookups denied with `error = 159: Sandbox restriction`: com.apple.mobilegestalt.xpc, com.apple.iohideventsystem, com.apple.system.notification_center, com.apple.logd, containermanagerd, ACCHWComponentAuthService, oscard. libsystem_sandbox uses `__mac_syscall("Sandbox", op, arg)` (ops: 1 init, 2 check, 3 note, 4 container path, 5/6/7/8 extensions, 0x15 check_bulk, ...); with no policy named "Sandbox" every call gets ENOPOLICY. **Patch 0050** (`xnu/patches/0050-sweet-sandbox-policy.patch`): built-in permissive MAC policy "Sandbox" in sweet_amfi.c (mpo_policy_syscall returns 0, container/extension ops ENOENT; logs first use of each op as `SWEET Sandbox: proc op N`).
- **Phone root pulled (16 GiB, 12 min, `adb exec-out dd if=/dev/block/sda34`) to `/run/media/dipa/android/ios-sweet-work/phone-root.img` (loop-mounted ro at /run/media/dipa/iOSRoot).** No .ips crash reports. logd tracev3 (Persist, LZ4 `bv41` blocks, decompressed strings in `ios-sweet-work/tracev3/all.txt`): /private/var/db/uuidtext is EMPTY so only launchd's own messages are readable. Findings: v8 launch failure = `Service could not initialize: posix_spawn(/usr/libexec/backboardd), error 0xd - Permission denied` (xpcproxy exit 78) - /dev/console not openable for the mobile user; backboardd[98] spawned `com.apple.MTLCompilerService` before SIGABRT; **launchd denies many mach lookups with `error = 159: Sandbox restriction`** (mediaserverd -> com.apple.backboard.display.services, remoted, mobiletimerd -> sleepd, identityservicesd -> apsd) and **tccd exits OS_REASON_SANDBOX "profile not found"** - no Sandbox kext/profiles on SWEET; likely cause of many daemon crash loops (next wall after the display). CF's uncaught-exception text never reaches stderr for daemons (no CF handler; libc++abi prints only the type). **Patch 0049** (`xnu/patches/0049-sweet-iokit-trace.patch`): `IOK name: ...` console lines for registry path lookups, child iterators, matching (with result count/class), matching notifications and IOServiceOpen of the processes in boot-arg `sweetiolog` (default backboardd), max 400 lines.
- **CONFIRMED: launchd StandardOut/ErrorPath=/dev/console stops backboardd from launching.** After the in-place rename (boot 0047n-b, log `logs/boot-0047n-b.txt`) backboardd launches again and dies as on v7: sig 6, `___pthread_kill <- abort <- demangling_terminate_handler <- _objc_terminate <- std::terminate <- _dispatch_client_callout <- _dispatch_once_callout <- main+0x9334` (window-server dispatch_once). It never opened AppleCLCD or IOCoreSurfaceRoot (no `SWEET FB/SURF: backboardd` lines) -> throws before/at display detection. **Patch 0048** (`xnu/patches/0048-sweet-devnull-tee.patch`, CI `39037749668` -> `xnu/out-0048`, image `shim/out/shim-boot-0048n.img`): writes to /dev/null by processes named in boot-arg `sweetnull` (default `backboardd,SpringBoard`) are printed as `|name| text`. Boot 0048n (13:48 UTC, log `logs/boot-0048n.txt`): only `|backboardd| libc++abi: terminating due to uncaught exception of type NSException` (CF's reason line goes to os_log, not stderr, despite CFLOG_FORCE_STDERR/OS_ACTIVITY_DT_MODE). Next: pull the whole 16 GiB root from recovery (`adb exec-out dd if=/dev/block/sda34`) to `/run/media/dipa/android/ios-sweet-work/phone-root.img` and read ReportCrash .ips files / logd tracev3 for the exception reason.
- **Persistent log WORKS (boot 0047n on v8, 13:31 UTC; pulled from OrangeFox: 70 KB, 111 flushes, `/run/media/dipa/android/ios-sweet-work/logs/boot-0047n.txt`).** In 3 min **backboardd was never exec'd** (v7 ran it), ~70 bare `exec xpcproxy`, SpringBoard loops on FBSDisplayMonitor (no display), mediaserverd sig 11 loop, locationd, lockdownd sig 5. Suspect the v8 plist (StandardOut/ErrorPath=/dev/console on backboardd). **In-place plist edit from recovery (no reflash):** launchd.plist is one extent at image/partition byte offset 0x1e025f000 (195 blocks; verified bytes identical on phone vs local v8 mount); renamed StandardOutPath->StandardOutPatX and StandardErrorPath->StandardErrorPatX (same length, both jobs) in blocks 68 and 105 via `adb push` + `dd of=/dev/block/sda34 seek=... conv=notrunc,fsync`, read back VERIFIED. Phone now differs from local v8 image in those 2 blocks (files in `/run/media/dipa/android/ios-sweet-work/`). Test boot 0047n 13:38:42 UTC. Recovery kernel has no hfsplus (raw block edits only).
- **Patch 0047: persistent console log (CI `39036478663` -> `xnu/out-0047`, QEMU no panic; phone image `shim/out/shim-boot-0047n.img` = 0047 + sweet-0046hi.adt + 0045n cmdline).** `_cnputs` (all console output: kernel printf + daemons' /dev/console) also appends to a 4 MiB ring in sweet_ufs.c; after the UFS partition is ready and writable (ufsrw=1) a thread writes new ring blocks + header to the LAST 1025 blocks (4 KiB) of userdata every ~1 s and SYNCHRONIZE CACHE (prints `SWEET LOG: console log to the last 4100 KiB ...`). Read: phone to fastboot -> `fastboot boot recovery/recovery.img` (OrangeFox) -> `tools/sweet-log-pull.sh OUT.txt` (adb dd of that area + `tools/sweet-log.py`). Not yet verified on the phone. Panics after the last flush are not in it (screen still shows them). 2x font attempt reverted: XNU's v_scale only scales the progress icon.
- **Boot 0046n on v8 (13:05 UTC, VID_20261017_150522_HSR_120):** phone prints `SWEET SURF: IOCoreSurfaceRoot registered` and `SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0x9c000000, default surface 1` (nubs + match work on hardware). Visible userspace: early boot tasks, launchd_cache_loader, `exec SpringBoard`; no `exec backboardd` / `SWEET FB: backboardd` line seen in the readable frames (video blurry). Frame 00:47: lower half of the screen shows OLDER console text (16:01:12) below newer lines -> unconfirmed hint that a swap copied an old snapshot of the panel (CA reads the default/displayed surface = panel memory). SpringBoard still dies in `-[FBSDisplayMonitor _initWithBookendObserver:transformer:]+1160` (sig 5, slide 0x30960000). **Shim: 2x console font** (`shim/loader.c` Video.v_depth scale field = 1) so phone videos are readable; image `shim/out/shim-boot-0046b.img` = 0046n + 2x font.
- **Patch 0046: built-in IOSurface + IOMobileFramebuffer (2026-10-17, offline, NOT phone-tested).** `xnu/patches/0046-sweet-iosurface-iomfb.patch`: new `iokit/Kernel/arm/SweetSurface.{h,cpp}` (IOCoreSurfaceRoot + IOSurfaceRootUserClient: sel 13/0/4/34/35, traps 0-5, wired buffers mapped into the caller, per-client refs) and `SweetFramebuffer.cpp` (AppleCLCD : IOMobileFramebuffer on /arm-io/disp0: sizes, swap = CPU copy of enabled layers into the ABL panel memory, 60 Hz vsync via sendAsyncResult64WithOptions(CanDrop), panel memory published as the default IOSurface, kernel text console paused on the first swap only with `sweetfb_noconsole=1` (default keeps the console: pausing switches it to serial and would hide panics)); SweetPlatformExpert creates nubs for /arm-io children (not gic) attached to the platform expert (IOPlatformDevice::compareName casts its provider to IOPlatformExpert, so nubs under the arm-io nub never matched - first CI build showed that in QEMU). mkadt: /arm-io/disp0 {device_type display-subsystem, compatible sweet,display, canvas-width 1080, canvas-height 2400}. CI `39021572169` -> `xnu/out-0046`. **QEMU** (`research/q0046-hi.adt`, RAM disk init, msgbuf): `SWEET SURF: IOCoreSurfaceRoot registered`, `SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0x9c000000, default surface 1`, init alive, no panic. Phone image **`shim/out/shim-boot-0046n.img`** (SHA256 9c4d2fe9..., CI `39024108588` with the review fixes: swap bounds check, zeroed 64-bit async ref, port release, 1.5 GiB wired budget, ref leak/race fixes; QEMU re-checked) = 0046 kernel + `shim/out/sweet-0046hi.adt` + the 0045n cmdline; boot it on image v8 (backboardd/SpringBoard log to console) and film. Expected new console lines: `SWEET SURF: <proc> create id ...`, `SWEET FB: backboardd sel N ...`, `SWEET FB: swap ...`. Open question for the next wall: QuartzCore's IOMFB server builds its renderer from Metal first (`CAMetalContextCreate`), GLES only when enabled; how the CPU renderer gets selected on a device without a GPU is not yet established.
- **IOSurface values ABI (offline RE, `/run/media/dipa/android/ios-sweet-work/iosurface-values.md`) - TODO in SweetSurface (0046 only accepts and drops values, so IOSurfaceCopyValue always returns NULL):** sel 9 SetValue in = {u32 id, u32 pad, IOCFSerialize([value, key]) or ([dict] = merge)}, sel 0xb RemoveValue in = {u32 id, u32 pad, key\0} (empty key = remove all); both out 4 bytes = new generation, client accepts only (gen+2)|1. Kernel after each change: gen = (gen+2)|1 (odd, never 0) -> state+0x20, serialized size (>=1) -> state+0x28. sel 0xa in {u32 id, u32 0, u8 0}, out {u32 ?, u32 gen, serialized non-bulk dict}; too small -> 0xe00002db + needed size (client retries x16). sel 0x1b in 0x88: bulk struct 0x70 @0, u64 valid size @0x70, u64 changed mask @0x78, u32 id @0x80 -> copy masked fields into state+0x30 (bit0 32B @0, bit1 16B @0x20, bit2 8B @0x30, bits3-10 1B @0x38..0x3f, bits11-14 24B @0x40/4B @0x58/8B @0x5c/1B @0x64).
- **IOMFB display matching confirmed (offline RE, `/run/media/dipa/android/ios-sweet-work/iomfb-match.md`):** the framework never matches on "AppleCLCD"; any registered IOMobileFramebuffer subclass works. `iomfb_match_callback` reads only `dfr`/`AppleTV`/`external` (CFBoolean, service itself, no parents); internal main display = all absent/false (type 0, name "primary"). Display count = /arm-io children whose device_type starts with "display-subsystem" (count 0 -> CreateDisplayList returns NULL; more nodes than services -> 30 s wait). IOMobileFramebufferOpen fails only on IOServiceOpen failure; canvas-width/height read as 4-byte CFData from the IOService-plane parent (optional). CopyProperty = IORegistryEntrySearchCFProperty on the service only. Patch 0046 (disp0 node, `external`=false, AppleCLCD : IOMobileFramebuffer) fits all of this.
- **IOMobileFramebuffer swap + notification ABI (2026-10-17, offline).** Swap record = fb+0x18, **0x46c bytes**, zeroed by SwapBegin and sent whole by `kern_SwapEnd` = IOConnectCallStructMethod(sel 5, rec, 0x46c, no output). **SwapBegin** = sel 4, no input, 1 scalar out = swap id (stored at rec+0x50). `kern_SwapSetLayer(fb, layer<4, IOSurfaceRef, CGRect a, CGRect b, flags)`: rec+0x54+4*layer = **IOSurface id**; rec+0x104 bit layer = layer enabled (rec+0x108 bit cleared); rect a (int x,y,w,h, scaled) at rec+0x64+16*layer, rect b at rec+0xc4+16*layer; rec+0x114+4*layer = transform from flags&0xf; flag bits 5/31/6/7/8/9 set bytes rec+0x2ca/0x2cc/0x2e3/0x2e5/0x2e6/0x467+layer. A NULL surface only sets the enable bit with zero id. SwapWait = sel 6 scalars [swap id, timeout, 0].
  - **Notifications**: `IOMobileFramebufferScheduleWithDispatchQueue`/`GetRunLoopSource` create an IONotificationPort and `IOConnectSetNotificationPort(conn, type, port, 0)` (kernel `registerNotificationPort`). `kern_EnableNotifications(fb, type, cb, ctx)` = **sel 72 scalars [NotifyCallbackFuncs[type], fb, type, 0]** -> the kernel keeps an async reference {port, func, refcon=fb} per type and fires it with `sendAsyncResult64`. Types: 0 HotPlug, 1 Power, 3 FrameInfo (args[0] = mask of 31-bit keys, args[1] u32, then values), 4 CRC, **5 Vsync**: 6 args, each a 32-bit half in a u64 slot: (ts lo, ts hi), (t2 lo, hi) passed through the time-ingest helper, (t3 lo, hi); the framework then calls the client's vsync callback(fb, ts, t2', t3, ctx). Vsync callbacks are dropped while the notification port has queued messages.

- **IOSurface.framework user-client ABI reversed (2026-10-17, offline, step 2 of the GUI plan).** `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image IOSurface` (selector scan script: scratchpad `selscan.py`).
  - Connect (`_iosConnectInitalize`, pthread_once): `IOServiceGetMatchingService(IOServiceNameMatching("IOCoreSurfaceRoot"))`, retried with sleep(1) up to 241 times (a missing service stalls every client ~4 min, no crash); `IOServiceOpen(type 0)`; then **sel 13** (no inputs, struct out 0x18 bytes = `_ioSurfaceLimits`) and requires u16 at out+0x16 == 1 (ABI version); out+0 = offset/plane-base alignment mask, out+4 = bytes-per-row alignment mask (used as (x+m)&~m), +0..+0x14 also copied into IOSurface remote-method init, otherwise logs and closes the connection.
  - **Create = sel 0**: scalar in [parent id or 0], struct in = `IOCFSerialize(props, kIOCFSerializeToBinary)` (OSUnserializeBinary format; keys IOSurfaceWidth/Height/BytesPerRow/BytesPerElement/PixelFormat/AllocSize/PlaneInfo/CacheMode/IsGlobal/...), struct out **0xa68 bytes** ("lock result"). **Lookup = sel 4**: scalar in [id], same struct out. LookupFromMachPort = sel 0x22 (scalar in [port name]); CreateMachPort = sel 0x23 (scalar in [id, options], scalar out [port name]).
  - Lock result (copied to client+0x68, size 0x68 + planes*0x80, min 0xe8): 0x00 u64 base address **mapped in the caller's task**; 0x08 u64 task VA of a per-surface shared state page (+0 u64 registry id, +8/+0xa u16 bound accel ids, +0xc u32 seed, +0x14 u8 tiled, +0x20 u32 value generation, +0x24 YCbCr matrix, +0x28 u32 non-bulk values size, +0x2d u8 displayable, +0x2e u8 sysmem-only, +0x30 0x70 bytes bulk attachments); 0x10 u64 task VA of timestamp page (4 u64 indexed timestamps, +0x20 detach code); 0x18 u32 surface id; 0x1c u32 alloc size; 0x20 width; 0x24 height; 0x28 bytes per row; 0x2c buffer offset; 0x30 pixel format; 0x34 u32 (dcache flush length); 0x38 plane count; 0x40 u16 bytes/element; 0x42 u8 element width; 0x43 u8 element height; 0x44 cache mode; 0x4c u8 subsampling; 0x4d u8 prohibit-use count; 0x50 parent id; 0x54 slice count; 0x58 u64 trace id; 0x60 u64 protection options; planes[n] at 0x68+n*0x80 (+0 width, +4 height, +8 bytes/row, +0xc offset, +0x14 size, +0x18 u16 bpe, +0x1a/+0x1b u8 element w/h, +0x1c u8 component count, +0x1d address format, +0x54 u8 compression type (non-zero -> no CPU base address)).
  - **Traps** (IOConnectTrapN -> `IOUserClient::getTargetAndTrapForIndex`): 0 IncrementUseCount(id, category), 1 DecrementUseCount(id, category), 2 **Lock**(id, options, &seed_out), 3 **Unlock**(id, options, &seed_out), 4 Release(id), 5 Release after a failed create/lookup setup (id).
  - Other selectors: 5 SetYCbCrMatrix, 6 WrapClientImage, 7 WrapClientMemory, 9 SetValue (serialized), 0xa UpdateNonBulkValueCache (struct in 9), 0xb RemoveValue, 0xc BindAccel, 0x11 SetSurfaceNotify (async), 0x12 RemoveSurfaceNotify, 0x13 Log, 0x14 SetPurgeable, 0x15 SetOwnership, 0x16 SetTiled, 0x18 SetTimestamp, 0x19 GetTileFormat, 0x1a CopyDataValueBytes, 0x1b SetBulkAttachments (struct 0x88), 0x1e/0x2e-0x31 (via outlined helpers), 0x1f SetCompressedTileDataRegionMemoryUsed, 0x20 GetGraphicsCommPageAddress, 0x21 SetIndexedTimestamp, 0x24-0x26/0x2d IOSurfaceAccelerator, 0x27-0x29 shared events, 0x2a SignalEvent, 0x2b Set/InitDetachModeCode, 0x2c SetOwnershipIdentity, 0x32-0x34 data properties.
  - => SWEET kernel service: class/name **IOCoreSurfaceRoot** + IOSurfaceRootUserClient; minimum = sel 13, 0, 4, 9/0xa/0xb (values), traps 0-5, 0x22/0x23 later for cross-process surfaces (CA sends surfaces to backboardd by mach port). Memory: IOBufferMemoryDescriptor per surface, mapped into each task with createMappingInTask; ids from a global table.

- **Image v8: daemon logs on the console.** `tools/patch-launchd-plist.py IN OUT LABEL...` rewrites launchd.plist (binary, plistlib): renames every `_PanicOnCrash` to `_PanicOnCrasX` and gives the listed jobs `StandardOutPath`/`StandardErrorPath` `/dev/console` (devfs console is 0622, writable by mobile) plus `OS_ACTIVITY_DT_MODE=YES` (os_log mirrored to stderr) and `CFLOG_FORCE_STDERR=1`. v8 = v7 + that plist for com.apple.backboardd and com.apple.SpringBoard, written through the mount (796310 bytes, root:80 0644), SHA256 in `ios16-root-20H392-v8.img.sha256`, `.simg` ready; boot with `shim/out/shim-boot-0045n.img`. Goal: see backboardd's uncaught exception text and the window server's display probing. ODDITY (unresolved): the v5 raw-image rename at offset 0x1e025f000 is present in the v5-v7 image bytes, but the plist read through a Linux mount of v7 still had the original `_PanicOnCrash` keys; filefrag shows the file as one extent. Never patch file contents through raw offsets again; write through the mount.
- **IOMobileFramebuffer.framework reversed (step 1 of the GUI plan).** `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image IOMobileFramebuffer`.
  - Display discovery (`IOMobileFramebufferCreateDisplayList` -> `getDisplayListNumber`): iterates children of `IODeviceTree:/arm-io`, counts nodes whose `device_type` is "display-subsystem" or "ext-display-subsystem" (or `compatible` "paravirtualizedgraphics,gpu"); then `iomfb_populate_display_infos` adds an IOServiceAddMatchingNotification(IOServiceFirstMatch, **IOServiceMatching("IOMobileFramebuffer")**) and runs the run loop up to 30 s until that many services matched. `iomfb_match_callback` reads service properties `dfr`, `AppleTV`, `external` (CFBoolean) and names displays (external ones "%s-%d").
  - `IOMobileFramebufferOpen(service, task, type, &fb)`: `__ioMobileFramebufferAlloc` (CFPreferences com.apple.iokit.IOMobileGraphicsFamily: canvas_width/height, override_display_width/height, override_panel_width/height, benchmark, enable_ktrace), `IOServiceOpen(service, task, type)` (GetMainDisplay passes type 0), `getEDTProperty` reads `canvas-width`/`canvas-height` from the service's IODeviceTree parent, then fills a table of `kern_*` function pointers.
  - User client selectors (`kern_*` -> IOConnectCall*): 3 _kern_GetLayerDefaultSurface[ScalarMethod_stub]; 4 _kern_SwapBegin[ScalarMethod_stub]; 5 _kern_SwapEnd[StructMethod_stub]; 6 _kern_SwapWaitWithTimeout[ScalarMethod_stub] _kern_SwapWait[ScalarMethod_stub]; 7 _kern_GetID[ScalarMethod_stub]; 8 _kern_GetDisplaySize[ScalarMethod_stub]; 10 _kern_SetTVOutMode[ScalarMethod_stub]; 11 _kern_SetWSSInfo[ScalarMethod_stub]; 12 _kern_RequestPowerChange[ScalarMethod_stub]; 13 _kern_EnableStatistics[ScalarMethod_stub]; 14 _kern_SetFlags[ScalarMethod_stub]; 15 _kern_SetDebugFlags[ScalarMethod_stub]; 16 _kern_SetTVOutSignalType[ScalarMethod_stub]; 17 _kern_SetGammaTable[StructMethod_stub]; 18 _IOMobileFramebufferIsMainDisplay[ScalarMethod_stub]; 19 _kern_SetWhiteOnBlackMode[ScalarMethod_stub]; 20 _kern_SwapSignal[ScalarMethod_stub]; 22 _kern_SetDisplayDevice[ScalarMethod_stub]; 23 _kern_SetDigitalOutMode[ScalarMethod_stub]; 24 _kern_GetHDCPDownstreamState[StructMethod_stub]; 25 _kern_GetDigitalOutState[ScalarMethod_stub]; 26 _kern_printDisplayRegs[ScalarMethod_stub]; 27 _kern_GetGammaTable[StructMethod_stub]; 28 _kern_GetDotPitch[ScalarMethod_stub]; 29 _kern_GetDisplayArea[StructMethod_stub]; 30 _kern_EnableDisableDithering[ScalarMethod_stub]; 31 _kern_SetUnderrunColor[ScalarMethod_stub]; 33 _kern_EnableDisableVideoPowerSavings[ScalarMethod_stub]; 34 _kern_SetVideoDACGain[ScalarMethod_stub]; 35 _kern_SetLine21Data[ScalarMethod_stub]; 41 _IOMobileFramebufferEnableMirroring[ScalarMethod_stub]; 42 _IOMobileFrameBufferGetMirroringCapability[StructMethod_stub]; 43 _IOMobileFramebufferSetRenderingAngle[StructMethod_stub]; 44 _IOMobileFramebufferSetMirrorContentRegion[StructMethod_stub]; 47 _kern_HDCPSendRequest[ScalarMethod_stub]; 48 _kern_HDCPGetReply[ScalarMethod_stub]; 49 _kern_SurfaceIsReplaceable[ScalarMethod_stub]; 50 _kern_SetBrightnessCorrection[ScalarMethod_stub]; 51 _kern_SetColorRemapMode[ScalarMethod_stub]; 52 _kern_SwapCancel[ScalarMethod_stub]; 53 _IOMobileFramebufferSetCanvasSize[ScalarMethod_stub]; 54 _IOMobileFramebufferGetMatrix[Method_stub]; 55 _IOMobileFramebufferSetMatrix[Method_stub]; 56 _IOMobileFramebufferKernelTests[StructMethod_stub]; 57 _kern_GetColorRemapMode[ScalarMethod_stub]; 66 _kern_SPLCSetBrightness[ScalarMethod_stub]; 67 _kern_SPLCGetBrightness[ScalarMethod_stub]; 68 _kern_SetParameter[Method_stub]; 69 _kern_SwapSetColorMatrix[StructMethod_stub]; 72 _kern_DisableNotifications[ScalarMethod_stub] _kern_EnableNotifications[ScalarMethod_stub]; 73 _kern_ChangeFrameInfo[ScalarMethod_stub]; 74 _kern_SupportedFrameInfo[Method_stub]; 75 _kern_FactoryPortal[StructMethod_stub]; 77 _kern_GetCurrentAbsoluteTime[ScalarMethod_stub]; 78 _kern_SetBlock[Method_stub]; 79 _kern_GetBlock[Method_stub]; 80 _kern_GetLinkQuality[ScalarMethod_stub]; 81 _kern_SwapCancelAll[ScalarMethod_stub]; 82 _kern_GetProtectionOptions[ScalarMethod_stub]; 83 _kern_CopyLayerDisplayedSurface[ScalarMethod_stub]; 84 _kern_SetContrast[StructMethod_stub]; 85 _kern_GetBufBlock[Method_stub]; 86 _IOMFBGainEncoderFinishEncoding[Method_stub]; UNKNOWN {}
  - => Kernel needs: an `/arm-io` child node with device_type "display-subsystem" (+ canvas-width/height) and an IOService of class **IOMobileFramebuffer** (subclass name match) attached under it, opened with type 0, implementing at least GetDisplaySize(8), IsMainDisplay(18), GetID(7), GetLayerDefaultSurface(3), SwapBegin(4)/SwapEnd(5 struct)/SwapWait(6), Enable/DisableNotifications(72), frame info(73/74), CopyLayerDisplayedSurface(83), plus IOSurface for the swapped buffers. NEXT: CoreAnimation's AppleDisplay/IOMFBDisplay usage of these (constructor, first swap, vsync), then IOSurface.framework's IOSurfaceRoot selectors.
- **GUI PLAN (decided 2026-10-16): CPU rendering + kernel IOMobileFramebuffer.** User: CPU rendering is fine for now (goal = prove the GUI runs), may later research an Apple-style GPU driver for the Adreno 618 -> keep display output and renderer separable. QuartzCore findings (symbols from the cache's .symbols subcache: `ipsw dyld symaddr research/dsc/dyld_shared_cache_arm64 --image QuartzCore`; strings: `ipsw dyld macho ... QuartzCore --strings`):
  - Renderers: `sw_new_context` (CA::OGL::SWContext, full CPU renderer incl. blur, images, IOSurface-backed surfaces, make_buffer_current(WindowServer::Surface)), `gles_new_context`, `metal_new_context`, `new_null_context`. Env knobs include CA_ENABLE_OGL, CA_DISPLAY_TYPE, CA_FORCE_LOCAL_SERVER, CA_FORCE_TIMER, CA_CANVAS_WIDTH/HEIGHT, CA_MAIN_DISPLAY_SCALE/ORIENTATION, CA_DISABLE_RENDER, CA_ALWAYS_RENDER (selection logic not traced yet).
  - `-[CAWindowServer _detectDisplays]` (0x1884e3150) iterates static tables `open_funcs`/`count_funcs` = AppleCLCDDisplay, AppleRGBOutDisplay, AppleWirelessDisplay only; each `count()` = `display_count_of_type(IOMFBDisplayType)` over **`IOMobileFramebufferCreateDisplayList()`**, `open(i)` = `create_display_of_type` -> `CA::WindowServer::AppleDisplay(CFString name, ..., IOMFBDisplayType)` (an IOMFBDisplay driven by IOMFBServer: vsync_callback, frame_info_callback, swaps). `CA::WindowServer::VirtualDisplay` exists but is never auto-detected (explicit API only). => the main display MUST come from an IOMobileFramebuffer kernel service.
  - NEXT: reverse IOMobileFramebuffer.framework: IOMobileFramebufferCreateDisplayList / IOMobileFramebufferOpen (service matching, display type property), the user-client selectors used by IOMFBServer (display size/modes, swap begin/set-layer/end with IOSurface ids, vsync notifications), then the IOSurface kernel side (IOSurfaceRoot user client: create/lookup/lock, memory mapping) that both IOMFB swaps and CA surfaces need. Kernel: SWEET IOMobileFramebuffer service that copies the swapped IOSurface to the 1080x2400 framebuffer at 0x9c000000.
- **logd fixed; backboardd = DISPLAY WALL (boot 0045n on v7, 21:08:57 UTC, frames VID_20261016_230850 00:55-01:28, slide 0x4c3ec000, `research/crash-0045n-v7.txt`).** No `CRASH logd` any more; new daemons run (runningboardd, WirelessRadioManagerd, networkserviceproxy, dasd, cloudpaird, magicswitchd, atc, askpermissiond, ManagedSettingsAgent, containermanagerd uses AKS 0/17). New: locationd sig 11 at CFRunLoopAddSource+132 (NULL source) from its main+0x2af138 in a dispatch_once. **backboardd** sig 6: abort <- std::terminate <- _objc_terminate <- dispatch_once <- main+0x9334 = the "start %{public}@..." wrapper (0x100009264) for **"window server"** (block 0x10000a770 -> dispatch_once body 0x10001d7f0 = StartWindowServer): MTLSetShaderCachePath, `[CAWindowServer serverWithOptions:@{kCAWindowServerDisableUpdatesOnMainDisplay: @YES}]`, `setRendererFlags:`, **`mainDisplay`** -> `displayId` -> `[CADisplay displayWithDisplayId:]`, setWindowServerDisplay:, bounds, pointScale, currentMode, preferredScale, nativeOrientation, touchScales... With no display, mainDisplay is nil and it throws. (HID system and GS start fine before it.) SpringBoard dies on the same missing display (FBSDisplayMonitor). QuartzCore display backends (from the cache's .symbols subcache, `ipsw dyld str`): `CA::WindowServer::AppleCLCDDisplay` (IOMobileFramebuffer hardware) and **`CA::WindowServer::VirtualDisplay`** (open(), new_server, clone_update). `CA_DISPLAY_TYPE` and `CA_FORCE_LOCAL_SERVER` strings exist in QuartzCore but have no direct xref (likely read via a table of CA_* env names). NEXT: find how CA::WindowServer::Server enumerates displays (IOMobileFramebuffer services vs VirtualDisplay / CA_DISPLAY_TYPE) and choose: (a) kernel IOMobileFramebuffer service + IOSurface, or (b) a VirtualDisplay rendered by CA's CPU path (CA::OGL::SW) and copied to the framebuffer.
- **watchdogd + CommCenter + identityservicesd fixed (boot 0045n on v6, 20:41:30 UTC, frames VID_20261016_224133 00:39-01:13, slide 0x20fec000).** `SWEET WDT: watchdogd sel 0 in 0/0 out 1/0 -> ok`, no watchdogd/CommCenter/identityservicesd crashes (CommCenter/identityservicesd now call AKS 0/17/7). usermanagerd boot task runs (UMD_INIT creates primary session uid 501 "mobile", personas 99 System and 199 Default registered with the kernel); `Failed to atomically swap writing file /private/var//keybags/usersession.kb: Operation not supported (45)` (HFS lacks the atomic swap rename; non-fatal so far). Remaining: SpringBoard (FBSDisplayMonitor, no display), backboardd (uncaught exception), mediaserverd, lockdownd (crash inside obfuscated code, main+0x4c1b4?), logd. **logd**: main+0x18160 = `BUG IN LIBTRACE: Failed to migrate directory` from 0x10001801c(dir 0): 0x100016538 requires the dir to exist with uid/gid/mode from the table at 0x10002df14 (0: /private/var/db/diagnostics 272:272 0751, 1: /private/var/db/uuidtext 272:272 0755, 2: /private/var/db/diagnostics/timesync 272:272 0755) plus a `.migrated` file, else XPC "operation 6" to a helper and crash. **Image v7** = v6 + those dirs/markers (also in mkiosroot.sh), SHA256 in `ios16-root-20H392-v7.img.sha256`, `.simg` ready. Boot with `shim/out/shim-boot-0045n.img`.
- **Exit-reason text works (boot 0044n, frames VID_20261016_221726 00:32-00:53, slide 0x22d5c000).** CommCenter/identityservicesd: `couldn't dlopen libobjc-trampolines.dylib: dlopen(/usr/lib/libobjc-trampolines.dylib, 0x0106): tried: '/usr/lib/libobjc-trampolines.dylib' (no such file, not in dyld cache)`: the file only exists in the OS cryptex and dyld does not search cryptexes at ignition_level=1. 19 cryptex files have no root counterpart (OS: DriverKit dyld cache + .symbols, VisualTestKit, libffi-trampolines, libobjc-trampolines, libstdc++ 6.0.9 + 2 symlinks; App: SafariSupport.bundle, AuthenticationServicesAgent, com.apple.Safari.History, passwordbreachd, safarifetcherd, webbookmarksd, webinspectord, webpushd) -> hard-linked (symlinks copied: Linux hfsplus cannot hard-link symlinks); `tools/mkiosroot.sh` does it generically. watchdogd still crashed: the `-wdt_no_pmu` / `/arm-io/wdt no-pmu` check (0x10000a01c) only runs when os_variant_has_internal_diagnostics; otherwise 0x100009b18 looks up IOServiceMatching("IOService") + IOPropertyMatch {IOPMUPrimary: 1} (NSConstantDictionary) and crashes if absent, then IORegistryEntrySetCFProperty(boot stage 0xff) (failure only logged). Patch **0045**: IOWatchdog sets IOPMUPrimary = 1 and accepts setProperties; CI `38961814194` -> `xnu/out-0045`. **Image v6** = v5 + the 19 cryptex links, SHA256 7c610c6a...297a, `ios16-root-20H392-v6.simg` (full flash needed). Boot image `shim/out/shim-boot-0045n.img` (0045 + sweet-0044hi.adt). Still open: SpringBoard (no display), backboardd (uncaught exception, reason none), mediaserverd (Fig NULL dict), logd (main+0x18160), lockdownd (main+0x4c1b4).
- **System survives daemon crashes (boot 0043p, video frames VID_20261016_215006 00:25/00:42/00:54).** With the v5 plist patch no panic: launchd keeps launching (runningboardd, powerd, tccd, healthd, peopled, cryptexd, routined, aksd, mediaremoted, familynotificationd, nanoprefsyncd, navd, cloudpaird, passd, amsaccountsd, accessoryupdaterd, wcd ...). Shared cache slide that boot 0x406b4000 (`research/crash-0043p.txt`). watchdogd still crashed at main+0xb82c from the PMU step: `-wdt_no_pmu` is only honored when os_variant_has_internal_diagnostics; customer builds read `no-pmu` from IODeviceTree:/arm-io/wdt (IORegistryEntrySearchCFProperty) -> mkadt adds /arm-io/wdt {no-pmu}. **cryptexd** sig 6: abort_with_payload <- os_crash_fmt <- `__darwin_arm_secure_boot_chip.cold.7` ("panic: failed to get crypto algorithm") <- `_img4_chip_select_personalized_ap`: reads /chosen use-ddi-secure-boot, allow-ecid-mismatch, crypto-hash-method ("sha1" | "sha2-384"), /chosen/asmb lp-smb0/1 -> mkadt adds crypto-hash-method "sha2-384". **identityservicesd** sig 6 = same `_objc_fatal` in imp_implementationWithBlock as CommCenter (block trampolines; message needed). lockdownd sig 5 at main+0x4c1b4. Patch **0044**: crash report adds `CRASH name reason ns N code X: text` (exit reason description = abort_with_payload text). CI `38959096811` -> `xnu/out-0044`, QEMU OK. Image `shim/out/shim-boot-0044n.img` = 0044 + `shim/out/sweet-0044hi.adt` (wdt no-pmu, crypto-hash-method) + cmdline (wdt=-1, no -wdt_no_pmu).
- **First symbolicated daemon crashes (boot 0043u on journaled v4, 19:38:53 UTC; video frame chat image 36; transcribed in `research/crash-0043u.txt`; shared cache slide 0x40d60000 for every process).** (Gemini's video transcription invented values, use frames only.) `tools/sweet-crash.py research/dsc/dyld_shared_cache_arm64 x research/crash-0043u.txt`:
  - **watchdogd** pc main+0xb82c (`_os_crash` trap helper) from 0x100009bb0 = set PMU boot stage via IORegistry (no PMU) -> fix: boot-args `wdt=-1 -wdt_no_pmu` (Apple's own test mode writes "%s wdt=-1 -wdt_no_pmu"; watchdogd logs "skipping setting PMU boot stage because -wdt_no_pmu found in boot-args or no-pmu found in EDT"). Kernel effect of wdt=-1: sched hygiene debug off, zone_exhausted_timeout -1 (harmless).
  - **SpringBoard** sig 5 in `-[FBSDisplayMonitor _initWithBookendObserver:transformer:]+1160` <- `+[FBDisplayManager sharedInstance]` <- `FBSystemShellInitialize` <- `SBSystemAppMain`: no main display. GUI WALL: display service (IOMobileFramebuffer / CoreAnimation display).
  - **backboardd** sig 6: abort <- std::terminate <- _objc_terminate (uncaught exception; message not shown).
  - **CommCenter** sig 6: `_objc_fatal` in `imp_implementationWithBlock` (from `+[_PFRoutines initialize]`, CoreData): objc block trampolines cannot be allocated -> likely kernel gap (trampoline page remap); will hit many processes.
  - **mediaserverd** sig 11: CFDictionaryGetValue(NULL) in FigNotificationListenerStartNotifications (routing session manager).
  - **logd** sig 5 at its main+0x18160 (bt main+0x9dfc); lockdownd sig 5 (not yet symbolicated).
  - Image v5 = v4 + launchd.plist (`/System/Library/xpc/launchd.plist`, contiguous at image offset 0x1e025f000, 1456980 bytes) with the 7 `_PanicOnCrash` keys renamed `_PanicOnCrasX` in place (raw image patch, no metadata change) -> `diff-v4-v5.simg` = 7 blocks; v5 SHA256 78f96974...185a. Boot image `shim/out/shim-boot-0043p.img` = 0043 + `wdt=-1 -wdt_no_pmu`.
- **0043 console crash reports (QEMU verified); image `shim/out/shim-boot-0043u.img`; NEXT: flash v4 + boot 0043u.** Boot 0042u (video photo IMG_20261016_211646): short exec lines work; watchdogd crashes with NO `SWEET WDT:` line (fails before its first IOWatchdog call: lookup or IOServiceOpen, or elsewhere); lockdownd, SpringBoard, mediaserverd, CommCenter, identityservicesd, backboardd, watchdogd all crash repeatedly ("Too many corpses being created" = corpse limit, no crash reports). AKS callers: lockdownd (0, 7), imagent/sleepd (0, 17, 7), backboardd (0, 17), UserEventAgent (0, 7, 102, 103 -> unsupported). Patch **0043**: `sweet_crash_log` (osfmk/kern/exception.c, called from proc_prepareexit for crash exits on the current proc) prints `CRASH name[pid] sig S code (exc<<32|code) sub X slide SR mh MH`, `CRASH name pc lr sp fp far`, `CRASH name bt` (10 fp frames); AKS sel 103 notify_state_is_set -> 0. CI `38954520419` -> `xnu/out-0043`. QEMU test: init forks, child `brk #1`: `CRASH launchd[2] sig 5 code 0x600000001 sub 0x1000004f0 slide 0x0`, pc correct, parent keeps running (pid 1 crashes panic before proc_prepareexit, so test with a child). `tools/sweet-crash.py CACHE ROOT [report]` symbolicates typed-in CRASH lines (`ipsw dyld a2s` with addr - slide; main binary offsets = addr - mh + 0x100000000).
- **v3 reflashed 18:50-18:55 UTC (`research/flash-userdata-v3b.log`, 24/24 OKAY); 0042u booted 19:01:17 UTC on the clean disk (`research/boot0042u-2.log`), SLOW MO VID pending (watchdogd failure reason).** ABL hangs on the first `fastboot boot` after every big flash; user re-enters fastboot. User gave standing approval to reflash/boot without asking (memory standing-flash-approval). Commits now end with `Co-Authored-By: dipa-studio <183933817+dipa-studio@users.noreply.github.com>` (user request; never a Claude trailer).
  - watchdogd flow: connect_to_kext (0x100005408): IOServiceGetMatchingService("IOWatchdog") fails -> "failed to discover" + _os_crash; IOServiceOpen(type 1) fails -> "IOServiceOpen failed with error : 0x%x" + _os_crash. Then sel 0 (out 1 scalar): 0 -> "userspace monitoring disabled", 0x100009bb0, sleep 2, exit(0); nonzero -> "userspace monitoring enabled", normal operation.
  - **Image v4 (JOURNALED):** `tools/mkiosroot.sh` now does `mkfs.hfsplus -J`, mounts with `-o loop,force`, adds the /System/Library/Caches/com.apple.dyld hard links (44) and /private/var/keybags/systembag.kb (0700/0600 root). Built from the IPSW dmgs (OS 087-86698-044, SystemOS 087-86593-047, AppOS 087-86715-047) -> `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v4.img` SHA256 fba4aeae...ec0f9 + `.simg`. Header: attr 0x80002100 (unmounted, journaled), journal info block 129, journal 8 MiB at 532480 flagged need-init. Linux `fsck.hfsplus -n` reports "Volume header needs minor repair" on any fresh journaled volume even after -y (quirk). QEMU: 64 MB journaled test root (`rd=md0 rootrw=1`, 0042 kernel): `hfs: Initializing the journal (joffset 0x3000 sz 0x80000)` -> mounted -> init runs, no panic. NEXT: flash v4 (full), boot 0042u.
- **SPRINGBOARD STARTS (boot 0041u 18:39:53 UTC, photo IMG_20261016_204142).** backboardd, bluetoothd, fairplayd.H2, SpringBoard (pid 111, crashed: "Too many corpses"), xpcproxy run; `SWEET AKS: backboardd sel 0 -> 0`, `sel 17 in 1/0 out 0/4096 -> 0`. Panic: watchdogd[113] exited, namespace 2 subcode 5 (SIGTRAP = its _os_crash): watchdogd crashes on purpose when IOServiceGetMatchingService("IOWatchdog") fails ("connect_to_kext() unexpectedly failed") or IOServiceOpen(type 1) fails ("IOServiceOpen failed with error"); IOWatchdog DOES register (QEMU msgbuf: `SWEET WDT: IOWatchdog registered`), cause not on screen yet -> need video. Note `wdt_test=no_user_checkin` makes watchdogd log "detected testing boot-args to not run, exiting" (path not reached here?). **HFS CORRUPTION:** `hfs_swap_BTNode: offset #0 invalid (0xBC1D) (blockSize 0x2000 numRecords 81)`, `node=19677 fileID=4` (catalog), `hfs_mark_inconsistent` repeated. Likely cause: non-journaled HFS + unclean panics while writing (0038e wrote a 2.7 GB core then panicked; every boot since ends in panic). Plan: reflash v3 (clean), longer term HFS journaling. Patch **0042**: one short `exec NAME` console line instead of the 3-line "Allowing spawn of iOS binary" notice; CI `38949451276` -> `xnu/out-0042`; image `shim/out/shim-boot-0042u.img` (same cmdline as 0041u).
- **LAUNCHD DAEMONS RUN (boot 0039u, 18:29:13 UTC, photo IMG_20261016_203052).** With `launchd_unsecure_cache=1` all boot tasks pass and launchd starts LaunchDaemons (pids > 100: identityservicesd 114 and lockdownd 106 crash, "Too many corpses being created"). Fatal: `watchdogd: ERROR: failed to discover watchdog KEXT service` -> exit (namespace 2 = signal, subcode 5) -> `_PanicOnCrash` panic. watchdogd: IOServiceMatching("IOWatchdog"), IOServiceOpen type 1, IOConnectCallScalarMethod sel 0/1 (check in)/3/4 (disable/enable userspace monitoring)/5, IOConnectCallStructMethod sel 2 (1 KiB in) and 9 (small out); VM path ("detected virtual machine environment and no watchdog KEXT found, exiting...") only when it detects a VM. Console also flooded with `hfs: set hardlink bit on vol=iOSRoot cnid=321318` (Linux-made hard links lack kHFSHasLinkChainBit; repaired only in memory on each lookup). Patches **0040** (`iokit/Kernel/arm/SweetWatchdog.cpp`: IOWatchdog + IOWatchdogUserClient, all selectors succeed with zeroed outputs, each logged once as `SWEET WDT: ...`) and **0041** (sweet_hfs hfs_catalog.c: the three link-repair printfs limited to 8 per boot); CI `38948245372` PASSED -> `xnu/out-0041` (QEMU regression OK). Image `shim/out/shim-boot-0041u.img` (0041 + sweet-0038hi.adt + ufsrw rootrw + launchd_unsecure_cache=1) booted 18:39:53 UTC (`research/boot0041u.log`), photo pending.
- **v3 FLASHED + keybag passes; next wall AMFI (launchd_cache_loader).** User said "flash, nothing to lose": `fastboot -S 512M flash userdata ios16-root-20H392-v3.simg` 17:51:52-17:57:01 UTC, 24/24 chunks OKAY (`research/flash-userdata-v3.log`). userdata = v3 (SHA256 fa84274c...22df) until the next rw boot. ABL fastboot then hung ("Sending" forever, `reboot bootloader` timed out) until the user re-entered fastboot by hand (common Xiaomi ABL crash). Boot 0038e 18:06:15 UTC (`research/boot0038e-3.log`), photos IMG_20261016_200812/200838: keybag OK, then boot tasks tzinit (`Bad zoneinfo link`, resets to system partition, OK), finish-restore, finish-demo-restore, sysstatuscheck, prng_seedctl (`failed to load kernel prng seed`, `failed to load virtual random (-147)`, AllowCrash), auearlyboot, then **launchd_cache_loader: `abort: Unable to find AMFI`** -> 2.7 GB core written to /cores in 91 s -> launchd panic `launchd_cache_loader[22] exited -- exit reason namespace 7 subcode 0x1`. launchd_cache_loader: SecStaticCodeCheckValidity of /System/Library/xpc/launchd.plist with detached .sig, then IOServiceMatching("AppleMobileFileIntegrity") + IOServiceOpen type 0 + IOConnectCallMethod sel 6 (1 scalar = 1, struct in = cdhash) = trust the cdhash, then sends the cache to launchd. Boot-arg `launchd_unsecure_cache=1` skips signature + AMFI ("Using unsecure cache"); the value is parsed with strtoul base 10 (helper 0x100002da8), so a path = 0 = off: boot 0039w (18:17:13 UTC, `=/System/Library/xpc/launchd.plist`, photo IMG_20261016_202008) still aborted `Unable to find AMFI` (core dump now skipped: `Failed to generate core file ... error 14`, so 0039 works). Image `shim/out/shim-boot-0039u.img` uses `launchd_unsecure_cache=1`. Patch **0039**: do_coredump = 0 on SWEET (kern.coredump sysctl still works); CI `38945267341` -> `xnu/out-0039` (_do_coredump now in __common). Image `shim/out/shim-boot-0039w.img` = 0039 + sweet-0038hi.adt + ufsrw rootrw + `launchd_unsecure_cache=/System/Library/xpc/launchd.plist`. TODO later: real AppleMobileFileIntegrity IOService (sel 6 trust cdhash etc.).
- **Image v3 ready, awaiting user "flash".** Boot 0038w 17:38:10 UTC, photo (chat image 30): `SWEET AKS: keybagd sel 0 -> 0x0`, `sel 14 out 1/0 -> 0x0` (x2), `MKB_INIT: mount is with NO CPROTECT ... does not support Data Protection`, `System Keybag loaded`; then keybagd still opens /private/var/keybags/systembag.kb (0x100012d3c, for BackupKeyBagKeys) -> ENOENT, `no-effaceable-storage` not set -> `Can't load the keybag. tears in rain... Time to die` -> `FATAL KEYBAG ERROR: kb_load` -> reboot -> Halt/Restart Timed Out panic. Fix: ADT `/defaults/no-effaceable-storage` (mkadt.py; `shim/out/sweet-0038hi.adt`, `research/q0038-hi.adt`, QEMU OK) + file `/private/var/keybags/systembag.kb` (0600 root, dir 0700; XML plist {KeyBagKeys: data}). **v3 image** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v3.img` = v2 + that file, fsck OK, SHA256 fa84274c...22df; `ios16-root-20H392-v3.simg` (img2simg: zero blocks become FILL chunks, so a full flash overwrites every block the rw boots changed). Flash: `fastboot -S 512M flash userdata ios16-root-20H392-v3.simg`, then boot `shim/out/shim-boot-0038e.img` (0038 + sweet-0038hi.adt + ufsrw=1 rootrw=1).
- **0038 AppleKeyStore (CI 38938744130 PASSED -> `xnu/out-0038`; QEMU msgbuf: `SWEET AKS: AppleKeyStore registered (no passcode, unlocked)`; phone image `shim/out/shim-boot-0038w.img` booted 17:38:10 UTC, `research/boot0038w.log`, photo pending).** QEMU full kernel log trick (fb.txt is only the last screen): `llvm-nm KERNEL | grep _msgbufp` -> `--hmp "x /1gx ADDR"` gives struct msgbuf address (static, 0xfffffe0007b56678 on 0038) -> `--hmp "x /3gx STRUCT"` gives bufc (3rd quad) -> `--hmp "memsave BUFC 0x20000 \"FILE\""` (quote the file name), `tr -d "\0" < FILE`. Same inputs give the same addresses. Boot 0037w 17:08:35 UTC, photo IMG_20261016_191052: bindfs fixed mount; mount-phase-1/2 pass; `cc_fips_test` boot task: all corecrypto FIPS POST tests PASSED in userspace; then keybag boot task `keybagd --init`: `MKB_INIT: FATAL KEYBAG ERROR: failed to load system bag`, `Could not get options entry from the device tree` (x2, IODeviceTree:/options = NVRAM, non-fatal), `REBOOTING INTO RECOVERY MODE` -> reboot -> panic `Halt/Restart Timed Out @IOPlatformExpert.cpp:884` (no restart path on SWEET).
  - keybagd (static libaks): load_devicebag (0x100012c30) calls aks_get_system (sel 14, 0-1 scalar in, 1 out) and loads /private/var/keybags/systembag.kb only if the result is 0xe00002f0 (NotFound); any other error -> fatal; SUCCESS -> skip the file (userspace-reboot path). systembag.kb is a plist {KeyBagKeys}; decrypted with effaceable storage unless IODeviceTree:/defaults has `no-effaceable-storage`. Image has no /private/var/keybags. has_data_protection: statfs of /private/var; no cprotect on our HFS -> device-bag volume mapping skipped.
  - libaks connection: IORegistryEntryFromPath / IOServiceMatching("AppleKeyStore"), IOServiceOpen type 0, then selector 0 (no args) must succeed.
  - **AppleKeyStore selector map (iOS 16.7 AppleKeyStore.framework, via `ipsw dyld disass`):** 0 open, 2 create_bag, 3 save_bag, 4 unload_bag, 5 set_system_with_passcode, 6 load_bag, 7 get_lock_state (1 in, 1 out; bit 2 = unlocked since boot), 9 unlock_device, 10 wrap_key, 11 unwrap_key, 12 unlock_bag/test_stashed_kek, 13 lock_bag, 14 get_system, 15 change_secret_se, 16 internal_state, 17 get_device_state (1 in, struct out <=4K: DER SET of SEQUENCE{UTF8 key, INTEGER}: ss state, sls lock_state (MKBGetDeviceLockState returns it), sb backoff, sbo, sfa failed attempts, sfao, sgs generation, sas assertions, sgpe grace, srcd countdown, sr reserved), 18 recover_with_escrow_bag, 19 obliterate_class_d, 20 drain_backup_keys, 21/22 set/clear_backup_bag, 23 get_bag_uuid, 24 rewrap_key_for_backup, 26/27 assert_hold/drop, 30/31 set/get_configuration, 33-37 stash, 38 auth_token_create, 40 generation, 41 fdr_hmac_data, 42 verify_password, 43 _aks_operation, 44 remote_session, 49/50 signing keys, 51/53/54 stash enable/persist/escrow, 55 unload_session_bags, 57/58 remote peer, 59-62 smartcard, 63 migrate_s_key, 65 set_keybag_for_volume_with_cookie_persona, 66/67 sync bags, 68 copy_volume_cookie_persona, 69-73 se_*, 74-79/84/85/99 fv_* (FileVault/VEK), 80/81 absinthe, 82 make_public_backup_bag, 83 drop_auxiliary_auth, 86 kext get/set options, 87 invalidate_bag, 88-91/94/95/106/107 se_*, 92 lower_iteration_count, 93 get_internal_info, 96-98/117 keybag_persona create/list/delete, 100/101 assert promote/consume, 102/103 notify_state set/is_set, 104 fairplay, 105 change_secret_epilogue, 108 delete_xart_leak, 110-113 backup volume, 114 sealed_hashes_set, 115 prewarm_sps, 118-128 AKSIdentity*/AKSGetLastUser.
  - Patch **0038**: `iokit/Kernel/arm/SweetKeyStore.cpp` (class AppleKeyStore on IOResources, IOUserClientClass AppleKeyStoreUserClient; personality in KernelConfigTables.cpp): 0/4/5/9/12/13/26/27/55/102 success, 14 -> handle 0, 7 -> 0x4, 17 -> DER state (ss 4, sls 0), others kIOReturnUnsupported; logs `SWEET AKS: <proc> sel N arg0 in a/b out c/d -> ret` for every call.
  - Tools: shared cache copied to `research/dsc/` (git-ignored; ipsw needs a writable dir for its .a2s symbol cache); `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image AppleKeyStore|MobileKeyBag`; scratchpad `annot2.py BIN DIS FIXUPS` annotates llvm-objdump output with strings and import names (`ipsw macho info --fixups`).
- **0037 bindfs (phone boot 0037w pending photo).** Boot 0036mw 16:57:47 UTC, photo IMG_20261016_190102: WRITABLE ROOT WORKS: `/dev/ufsroot0 on / (hfs, local, noatime)` after mount_hfs update (GetMasterBlock EBUSY harmless), coredump of mount written to /cores (rw). Then `mount: failed to mount /private/preboot/5CBE3601.../usr/standalone/firmware -> ...: Operation not supported by device (19)` -> exit 74 -> launchd panic `mount[2] exited`. Cause: the macOS-derived config had nullfs but no bindfs (ENODEV = unknown fs type). Caller passes env 0 -> bind not required, ENOENT (missing source dir) tolerated. Patch **0037** adds `bindfs` to FILESYS_BASE in MASTER.arm64.sweet; CI `38936550483` -> `xnu/out-0037` (bindfs_vfsops present; QEMU probe regression OK). Image `shim/out/shim-boot-0037w.img` (0037 + sweet-0036hi.adt + ufsrw=1 rootrw=1) booted 17:08:35 UTC (`research/boot0037w.log`). NOTE: userdata has now been modified by rw boots; it no longer equals the v2 image, so sparsediff with v2 as OLD is not safe any more (full reflash or a new baseline needed).
- **0036 phone: launchd boot tasks run; mount -P 1 needs /chosen/boot-manifest-hash.** Boot 0036hr 16:15:32 UTC, photo IMG_20261016_185236: platform check passes; fsck boot task runs (`fsck: not booting main or diagnostic OS. Skipping fsck on OS container`); mount-phase-1: `failed to get filesystem info` x2 (no IODeviceTree:/filesystems/fstab, non-fatal), `mount: data volume missing, but not required in env: 0`, mount_hfs update of /: `GetMasterBlock: Error 16 opening /dev/ufsroot0` (EBUSY, mounted block dev, non-fatal) then mount() EROFS (read-only boot) -> `mount: / failed with 1`; then `failed to get boot-manifest-hash` -> errx exit 66 -> launchd panic `mount[3] exited`. /sbin/mount: fn 0x100005f90 reads IODeviceTree:/chosen boot-manifest-hash (hex), bindfs-mounts /private/preboot/<HEX>/usr/standalone/firmware (ENOENT tolerated unless required). `tools/mkadt.py` now adds `boot-manifest-hash` = SHA-384("sweet boot manifest") (5CBE3601...5205). ADTs `shim/out/sweet-0036hi.adt`, `research/q0036-hi.adt` (QEMU probe OK, csops 0x26000001). Image `shim/out/shim-boot-0036mw.img` (0036 + new ADT + ufsrw=1 rootrw=1), booted 16:57:47 UTC (`research/boot0036mw.log`): FIRST WRITABLE BOOT of userdata (restore source: v2 image on SSD).
- **0036 CS_SIGNED (phone boot 0036hr pending photo).** Phone boot 0035hr 2026-10-16 15:57:02 UTC (`research/boot0035hr.log`, photo IMG_20261016_180249): highmem works on hardware; shared cache maps (trust-cache warnings only), Apple launchd runs (`Darwin Bootstrapper Version 7.0.0`, `entering ondemand mode`), starts boot task fsck, then userspace panic `boot task is not a platform binary (fsck, /sbin/fsck)`. launchd+0x3fea0: `csops(pid, CS_OPS_STATUS)` must have bit 0x04000000. Cause: nothing set CS_SIGNED (AMFI does it in vnode_check_signature), so csproc_get_blob() returns NULL for every process and csops never reports CS_PLATFORM_BINARY (also breaks per-process cdhash/entitlement queries). QEMU probe init (`csops` status printer, scratchpad csq/) on 0035: `0x02000001`; patch **0036** (`*cs_flags |= CS_SIGNED` in the SWEET AMFI hook), CI `38929635791` -> `xnu/out-0036`: `0x26000001`. Images `shim/out/shim-boot-0036hr.img` / `-0036hw.img` (same cmdlines as 0035). 0036hr booted 16:15:32 UTC (`research/boot0036hr.log`). Next launchd boot tasks (embedded __config plist): fsck -qL and mount -P 1/2 (RequireSuccess); no /etc/fstab on the image.
- **0035 ENTITLEMENTS + PLATFORM BINARIES (QEMU verified).** `xnu/patches/0035-sweet-amfi-entitlements-platform-binaries.patch`: new `iokit/Kernel/arm/SweetEntitlements.cpp` (OSObject subclass: OSDictionary from OSUnserializeXML of the signature's XML entitlements + original XML for CS_OPS_ENTITLEMENTS_BLOB); `bsd/kern/sweet_amfi.c` answers queryEntitlementBoolean/String(+WithProc, string also matches array members), copyEntitlementAsOSObject(+WithProc, retained), asdict (retained), get_xml (kalloc_data copy); built-in MAC policy "SWEET AMFI" (registered from bsd_init before vfs/exec) with mpo_vnode_check_signature: every signed binary on the MNT_ROOTFS mount gets CS_PLATFORM_BINARY (stand-in for AMFI's static trust cache: iOS system binaries are ad-hoc, cd_flags 0x2, platform byte 0), and attaches entitlements ONLY for the writable copy (cs_blob != csb_ro_addr; revalidation passes the RO blob). LESSON: first version attached in adjustContextWithoutMonitor -> QEMU `unable to adjust OSEntitlements context without monitor: 53` because ubc_cs_convert_to_multilevel_hash (4K-page signatures on 16K kernel) sets csb_entitlements_blob = NULL; ALL iOS 16 binaries (launchd, backboardd, SpringBoard) use 4K code pages, so this would have broken launchd on the phone. Boot-arg `sweet_amfi_debug=1` logs `SWEET AMFI: <ident>: N entitlements (test: ...)`. `tools/mkinit.py OBJ OUT [ENTS.plist]` adds an XML entitlements blob (special slot -5; default output byte-identical). QEMU `research/q0035-ent` (RAM disk `research/root-hfs-ent.img`, init with 3 test entitlements, highmem): `org.sweet.init: 3 entitlements (test: bool 1 string 1 array 1)` then init PID 1. MAC policies loaded: 0034 = 1, 0035 = 2 (QMP `mac_policy_list`). CI `38909807230` -> `xnu/out-0035`. Phone images: `shim/out/shim-boot-0035hr.img` (highmem, ro), `-0035hw.img` (highmem, +ufsrw=1 rootrw=1), `-0035r.img` (low mem fallback).
- **PHONE TEST ORDER (updated):** 0035hr -> if instant reset, 0035r -> then 0035hw.

- **HIGHMEM: XNU in the upper DDR bank, ~3.95 GiB (was 1.2 GiB).** Live FDT memory = <0x80000000 2G> + <0x1_00000000 0xfcc00000>; all fixed reserved-memory carve-outs are in the low bank (highest: ramdump_fb 0xaf000000, rmtfs ~0xfde01000), dynamic pools have alloc-ranges <= 0xffffffff, UFS node has no `iommus` (Linux DMAs anywhere). `tools/mkadt.py --highmem` sets /chosen dram-base 0x100000000 dram-size 0xfcc00000; `tools/mkpayload.py` now takes phys_base/mem_size from the ADT (default payloads byte-identical, verified); `tools/qemu-sweet.py` sizes -m from the payload (8G for highmem). Patch **0034** (`0034-sweet-ufs-dma-above-4g.patch`): UFS refuses DMA memory above 4G without UFSHCI CAP.64AS (bit 24), logs `DMA at 0x...`. CI `38905889883` success -> `xnu/out-0034`. **QEMU verified** (`research/q0034-hi`, ADT `research/q0034-hi.adt` --qemu --highmem, HFS RAM disk): phys_base 0x100000000, init PID 1 heartbeat, no panic; `max_mem` = `mem_size` = 0xfcc00000 read via QMP (`research/q0034-hi-mem`). Phone images: `shim/out/shim-boot-0034hr.img` (read-only) / `-0034hw.img` (+ufsrw=1 rootrw=1), ADT `shim/out/sweet-0034hi.adt` (asmb SIP + sweet-ufs + highmem). PHONE RISK: unknown runtime TZ/XPU protection in the upper bank would show as an instant reset; fall back to 0033r/w. Watchdog ruled out for the overnight reset: msm-watchdog bark is 20 s (qcom,bark-time 0x4e20) yet panic screens stay up for many minutes.

- **Offline research 2026-10-16 (user away, phone unavailable). Images rebuilt** `shim/out/shim-boot-0033r.img` / `-0033w.img` now also carry `wdt_test=no_user_checkin`. NEXT ON PHONE: boot 0033r (expect past in6_nigroup), then 0033w (first writes: rw root fixes HFS link-chain bits itself and gives /var for launchd sockets /var/run/syslog, /var/run/lockdown.sock).
- **launchd.plist** (`/System/Library/xpc/launchd.plist`, compiled; 532 daemons, 66 RunAtLoad/KeepAlive, list in scratchpad `early-daemons.txt`). `_PanicOnCrash` daemons: containermanagerd/usermanagerd (InternalOnly), lockdownd/remoted/trustd/remotepairingdeviced (AutomationOnly), **watchdogd unconditional** (KeepAlive SuccessfulExit=false). watchdogd needs IOKit `IOWatchdog` kext service; if absent it exits only when `kern.hv_vmm_present` (ADT `/defaults/vmm-present`, hvg_sysctl.c) or with boot-arg `wdt_test=no_user_checkin` ("detected testing boot-args to not run, exiting"; reads kern.bootargs). Using the boot-arg (vmm-present would change many daemons; keep as an option).
- **GUI path research (QuartzCore in the shared cache, `ipsw dyld str CACHE STR`, ~10 s/search):** CA::WindowServer has IOMFBServer/IOMFBDisplay (Apple DCP/IOMobileFramebuffer), AppleCLCDDisplay, AppleRGBOutDisplay, **VirtualServer/VirtualDisplay** and **MemorySurface**, EAGLServer. Renderers: CA::OGL::MetalContext, GLContext and a **CPU software rasterizer CA::OGL::SW** (samplers/blend modes; "image is too large for software renderer"). Env knobs: `CA_ENABLE_OGL`, `CA_DISPLAY_TYPE`, `CA_FORCE_LOCAL_SERVER` (249 CA_* vars). Hypothesis (unverified): backboardd's render server could run a VirtualServer/MemorySurface with the SW renderer and we copy the surface to the ABL framebuffer -> needs disassembly of CA::WindowServer server creation. Expected other GUI walls: IOSurface (closed-source kext; needs a reimplementation), IOMobileFramebuffer user client, IOHID touch events, AppleKeyStore/keybag (SEP) for lock state, MobileGestalt answers (AppleCLCD etc.).

- **0032r RESULT (boot 05:07:50 UTC, `research/boot0032r.log`, photo IMG_20261016_071200): SHARED CACHE MAPPED, LAUNCHD RUNS.** With SIP relaxed (ADT asmb) the `not in trust cache` lines are only warnings for every subcache (.31 ... .42.dyldlinkedit); no SIP rejection; libSystem loads; launchd configures networking (`skip attaching fsw to lo0 using legacy TX model`) -> kernel data abort far 0x28 in `SHA1Init` (symbolized as sha1_init) from `in6_nigroup` (IPv6 on lo0): g_crypto_funcs NULL. Kernel addresses are unslid: `~/swift/usr/bin/llvm-symbolizer --obj=KERNEL ADDR` works directly.
- **0032** BearSSL SHA-1 (`0032-sweet-bearssl-sha1.patch`), **0033** BearSSL SHA-384/512 (`0033-sweet-bearssl-sha384-sha512.patch`, BearSSL ctx stored in SHA512_CTX, static-asserted to fit; ubc_subr.c uses SHA-384 for code directories). `0020-stage-bearssl.sh` now also stages hash/sha1.c, hash/sha2big.c, codec/{dec,enc}{32,64}be.c. CI `38854909053` success (0032-only run `38854802073` superseded). Kernel `xnu/out-0033` (SHA1Init -> _br_sha1_init verified by disassembly; nm symbols have a leading underscore). Images (ADT sweet-0032): `shim/out/shim-boot-0033r.img` (read-only), `shim-boot-0033w.img` (+ufsrw=1 rootrw=1). Still NULL g_crypto_funcs users: des, chacha20poly1305, rsa, aesxts, rand, flow_divert.
- `research/check-patch-order.py` now skips patches touching CI-staged sweet_hfs sources (0031) instead of stopping.

- **0031r RESULT (boot 05:03:52 UTC 2026-10-16, `research/boot0031r.log`, photo IMG_20261016_070508): SHARED CACHE FOUND.** dyld now opens `dyld_shared_cache_arm64` (v2 image hard links), kernel refuses to map it: `shared_region: ... map(...'dyld_shared_cache_arm64'): not in trust cache` -> `vnode is not SIP-protected` (vm_unix.c: macOS arm64 path needs trust cache, else root-owned + SF_RESTRICTED unless CSR_ALLOW_UNRESTRICTED_FS) -> `dyld cache '(null)' not loaded: syscall to map cache into shared region failed` -> libSystem not loaded -> coredump to /cores fails error 30 (EROFS) -> panic initproc failed -> "MACH Reboot" (spins; screen kept). Lots of `hfs: set hardlink bit on vol=iOSRoot cnid=... linkid=...` spam (Linux-made hard links lack the link-chain bit; read-only mount cannot persist the fix). FIX: ADT `/chosen/asmb` with `lp-sip0`=0x6f (u64; incl. CSR_ALLOW_UNRESTRICTED_FS) and `lp-sip1`=1 (unauthenticated root), `tools/mkadt.py` -> `shim/out/sweet-0032.adt`. Images with kernel 0031: `shim/out/shim-boot-0032r.img` (read-only) and `shim-boot-0032w.img` (+ufsrw=1 rootrw=1).

- **2026-10-16 morning:** userdata now = **v2** image (`diff-v1-v2.simg` flashed 21:06:50 UTC 2026-10-15, OKAY, `research/flash-userdata-diff-v2.log`); future diffs use `ios16-root-20H392-v2.img` as OLD. Booted `shim-boot-0029k.img` (ignition_level=1) right after (`research/boot0029k-v2.log`); **no photo** (user asleep). Next morning the phone was in PixelOS recovery = some hardware reset (ABL then booted Android, which cannot use userdata). OBSERVATION: our kernel cannot reset the SoC itself (`PE_halt_restart` is never set; reboot/halt print "MACH Reboot"/"CPU halted" and spin), so the reset came from outside XNU: Qualcomm watchdog or a TZ/XPU violation. Unknown which; re-boot 0029k and watch/video until the reset. Do NOT tap factory reset/format data in recovery (would wipe the iOS image).
- **0030** (`xnu/patches/0030-sweet-ufs-writes.patch`, CI `38852655542`): UFS WRITE(10) + read-modify-write for partial 4 KiB blocks + SYNCHRONIZE CACHE (DKIOCSYNCHRONIZE), only with boot-arg `ufsrw=1`; `ufs_write_blocks` re-checks partition bounds before every write (only place data goes out).
- **0031** (`xnu/patches/0031-sweet-read-write-root.patch`, CI `38852837176`): boot-arg `rootrw=1` clears MNT_RDONLY before the root mount (no data volume on sweet: /private/var is on the root; image has no /etc/fstab); Apple HFS accepts a dirty non-journaled ROOT read-write on SWEET (logs "mounting dirty non-journaled root read-write"). Later: journaled HFS for crash safety. Kernel `xnu/out-0031` (CI `38852837176` success, 5m31s; 0030 run cancelled by the newer push). Boot images (phone ADT `sweet-0028.adt`, ramdisk byte-matched): `shim/out/shim-boot-0031r.img` = 0029k args (read-only, ignition_level=1) and `shim/out/shim-boot-0031w.img` = same + `ufsrw=1 rootrw=1`. PLAN: boot 0031r first and film until the reset (diagnose overnight reset), then 0031w (first ever writes to userdata; v2 image on the SSD is the restore source). `research/check-patch-order.py` cannot check 0031 (staged sweet_hfs files are not in its index); CI stage scripts run first, so CI is the check.

- **ignition_level=1 result** (boot 21:01:39 UTC, `research/boot0029k.log`, photo IMG_20261015_230308): preboot/cryptex stages skipped (need >= 2/3), `dylib_cache: finding shared cache on system volume: ignition level = 1, search root fd = 4, subpath = System/Library/Caches/com.apple.dyld/` -> not found -> `ignition boot failed: 19` -> no shared cache -> libSystem not loaded. FIX IN PROGRESS: image v2 = copy of the flashed image + `/System/Library/Caches/com.apple.dyld/` with HARD LINKS to the 44 cryptex cache files (no extra space; avoids symlink-following doubts), then `tools/sparsediff.py ios16-root-20H392.img ios16-root-20H392-v2.img diff.simg` and flash only the diff. Boot with `ignition_level=1` (shim-boot-0029k.img).
- **ignition_level=0 result** (boot 20:58:07 UTC, `research/boot0029j.log`, photo IMG_20261015_230037): all stages skipped as "ignition level insufficient" (hello/preboot/cryptex1 sniff >= 2, select os cryptex path/graft >= 3, **dyld_cache >= 1**), `ignition boot failed: 89`, "ignition partially disabled", then same `Library not loaded: /usr/lib/libSystem.B.dylib (no such file, no dyld cache)`. => dyld gets the shared cache directory from ignition's dyld_cache stage. Next: `ignition_level=1` (`shim/out/shim-boot-0029k.img`) to skip preboot/cryptex stages but run dyld_cache; its log "finding shared cache on %s ... subpath = %s" will show where it searches. If it searches rootfs /System/Library/Caches/com.apple.dyld, add a symlink there (image rebuild; consider a diff-only sparse flash tool to avoid rewriting 12 GB).
- **APPLE dyld/launchd RUNNING (libignition) with `unentitled_ios_sim_launch=1`** (boot 20:53:24 UTC, `research/boot0029i.log`, photo IMG_20261015_225617): kernel prints "Allowing spawn of iOS binary ... boot-arg set to true"; `shared_region: ... check_np ... vm_shared_region_start_address() failed` (no region yet, normal); dyld's built-in **libignition** (Darwin Ignition Sequence 1.0.0, libignition-51~1974; prints rd=ufsroot0, hardware target sweet, ignition level 0x5, platform 0x2, boot spec local, 7 stages): `hello from launchd.1` -> `preboot: mounting preboot` -> `failed to lookup preboot: 25` (ENOTTY: looks for a separate Preboot volume by role) -> ignition fails -> dyld `Library not loaded: /usr/lib/libSystem.B.dylib ... (no such file, no dyld cache)` -> launchd exit -> panic initproc failed. dyld boot-args found via strings: `ignition_level`, `ignition_force_dylib_root` (forced rootfs/livefs/cryptex fallback), `ignition_halt_after`, `ignition_live_app_graft`; dyld cache search paths include /System/Cryptexes/OS, /System/Volumes/Preboot/Cryptexes/OS/, /private/preboot/Cryptexes/OS/ (present in our image). Next try `ignition_level=0` (`shim/out/shim-boot-0029j.img`).
- **APPLE launchd EXECUTED FROM UFS (2026-10-15).** Flashed iOS image: `img2simg` -> `ios16-root-20H392.simg` (12.1 GB) then `fastboot -S 512M flash userdata ...simg` (24 chunks, 307 s, `research/flash-userdata-ios16.log`; a raw-image flash was killed by earlyoom at 2.6 GB RSS - always flash the sparse image). ABL fastboot hung on getvar after the flash; power+vol-down fixed it. Boot 0029u 20:49:04 UTC (`research/boot0029u-ios16.log`), photo IMG_20261015_225134: `hfs: mounted iOSRoot` -> load_init_program /sbin/launchd -> panic `unexpected SIGKILL of launchd (CS_KILLED) with reason -- namespace 9 code 0xe` = OS_REASON_EXEC / EXEC_EXIT_REASON_WRONG_PLATFORM: our kernel is XNU_TARGET_OS_OSX, iOS-platform binaries are "alien" and refused unless spawn attr platform set or DEVELOPMENT TUNABLE `unentitled_ios_sim_launch`. Next try: same kernel, cmdline + `unentitled_ios_sim_launch=1` (`shim/out/shim-boot-0029i.img`). LONGER TERM: macOS-config kernel marks iOS maps alien (vm_map_mark_alien) and differs from iOS in many policies; an iPhoneOS-config kernel build may eventually be needed.
- **iOS 16 ROOT IMAGE BUILT:** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392.img` (16 GiB case-sensitive HFS+, label iOSRoot, 12 GB used, fsck OK after `fsck.hfsplus -y` rewrote 32 non-ASCII localized WorkflowKit Shortcut names Linux decomposes differently). Built by `tools/mkiosroot.sh OS.dmg SYSTEMOS.dmg APPOS.dmg OUT.img [SIZE]` (needs sudo; apfs-fuse as root; OS dmg is UDIF -> link must end .dmg). Contents: iPhone X 20H392 OS root + SystemOS cryptex at /private/preboot/Cryptexes/OS (so /System/Cryptexes/OS -> cache, 44 files, dyld_v1 arm64) + AppOS at .../App. /sbin/launchd and /usr/lib/dyld root:wheel 0755. NEXT: (confirm) `fastboot flash userdata ios16-root-20H392.img` (auto-sparse), boot 0029u (rd=ufsroot0) -> Apple launchd attempt.
- **MILESTONE ON HARDWARE: BOOT FROM INTERNAL FLASH.** 0029 CI `38797924912` PASSED, `xnu/out-0029` SHA256 `a68bc847...2a37`. Image `shim/out/shim-boot-0029u.img` (payload-0029u = 0029 kernel + `shim/out/sweet-0028.adt` + `rd=ufsroot0`, NO RAM disk). Boots 20:01:34 and 20:12:37 UTC (`research/boot0029u*.log`), photo IMG_20261015_221403: `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB, read-only` -> `BSD root: ufsroot0, major 1, minor 0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `SWEET: root volume is not authenticated (error 25); continuing unsealed` -> `load_init_program ... /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> heartbeat (ran for minutes unattended). userdata currently holds the 8 MB `research/root-hfs.img`. NEXT: build a multi-GB HFS+ image with the iOS 16 (iPhone X 20H392) userland (OS rootfs + SystemOS cryptex contents incl. dyld shared cache at /System/Library/Caches/com.apple.dyld; preserve modes/owners/symlinks; decompress LZBITMAP via apfs-fuse), flash it to userdata (sparse, needs user OK), then try Apple's dyld/launchd. Driver is read-only (root mounts RDONLY), fine for first launchd attempts.
- **userdata FLASHED with the 8 MB HFS test volume** (user said "flash it", 2026-10-15 19:53:17 UTC, `research/flash-userdata-hfs-test.log`: `fastboot flash userdata research/root-hfs.img` OKAY; pre-checks: one sweet device, partition-size:userdata 0x1AC4BFB000, partition-type f2fs, image fsck OK, SHA256 3626ea69...). Android userdata is now destroyed (expected/approved). Boot 0028u at 19:53:20 (`research/boot0028u-hfs.log`), photo IMG_20261015_215541: `BSD root: ufsroot0` -> **`hfs: mounted SweetRoot on device b(1, 0)` from internal UFS** -> panic `rootvp not authenticated after mounting @bsd_init.c:931` (arm64 macOS-config requires FSIOC_KERNEL_ROOTAUTH sealed root for non-ramdisk roots). Patch **0029** (`dd1c284`): SWEET logs `SWEET: root volume is not authenticated (error N); continuing unsealed` instead of panicking (Apple configs keep the panic; pivot_root still requires auth). CI **`38797924912`** running. Next: download out-0029, phone image with sweet-0028.adt + rd=ufsroot0 (no RAM disk), boot -> expect init from UFS.
- **UFS ROOT DRIVER VERIFIED ON HARDWARE (read-only).** 0028 CI `38796139570` PASSED, `xnu/out-0028` SHA256 `8b981640...9da6`; QEMU HFS RAM-disk regression `research/q0028-hfs` still runs init. Phone boot 2026-10-15 19:50:24 UTC (`research/boot0028u.log`, image `shim/out/shim-boot-0028u.img`, phone ADT `shim/out/sweet-0028.adt` with /sweet-ufs, NO RAM disk, `rd=ufsroot0`), photos IMG_20261015_215151/215220: `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB, read-only` -> `BSD root: ufsroot0, major 1, minor 0` -> `hfs_ValidateHFSPlusVolumeHeader: unknown Volume Signature : f188` (Android-encrypted data, as expected) -> `cannot mount root, errno = 19`, kernel retries mountroot (no panic). Nothing written to flash. NEXT (destructive, needs user confirmation right before): `fastboot flash userdata research/root-hfs.img` (8 MB HFS with test init) then boot rd=ufsroot0 -> expect init from internal flash.
- **PATCH 0028 (`33aceac`): XNU read-only UFS root device**, CI **`38796139570`** running (next artifacts xnu/out-0028, research/bt29, run29.log). `bsd/dev/sweet_ufs.c`: reuses ABL's UFSHCI state (like the shim probe), takes over UTRL at a kmem_alloc_contig region (DMA layout: UTRL 0x0, UCD 0x1000, 1 MiB bounce 0x4000), verifies UTRLBA readback before any doorbell, IE=0 polled, one SCSI cmd at a time on LUN0 (TUR x3, READ(10) via bounce, PRDT 256 KiB chunks), flush_dcache64 by PA around each command (host not dma-coherent per DT), finds GPT partition by name (`ufsroot=` boot-arg, default `userdata`), exposes `/dev/ufsroot0`/`rufsroot0` (512-byte logical sectors, physical 4 KiB, DKIOC* ioctls), refuses writes (EROFS). IOKitBSDInit: `rd=ufsroot0` -> sweet_ufs_rootdev() (lazy init) else panic. ADT: `tools/mkadt.py` adds `/sweet-ufs` reg <0x1d84000 0x3000> for the PHONE only (not --qemu). Local compile OK, imports resolve. Sandbox research/bt28 (pre0028 originals). FIRST PHONE TEST PLAN (non-destructive): boot 0028 with phone ADT + `rd=ufsroot0` and NO RAM disk: expect `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB` + `BSD root: ufsroot0`, then root mount failure (userdata is still Android-encrypted) -> panic. That validates the driver before flashing an HFS image into userdata (needs explicit user confirmation at that time).
- **SUPERSEDES the 64 GiB iosroot GPT plan: user said "u can delete android" (2026-10-15).** Use the existing `userdata` partition (LUN0 sda34, 4K LBA 0x2B8C00, 0x1AC4B MiB ~107 GiB) directly as the iOS root: NO GPT changes. Keep bootloaders, recovery (OrangeFox), modem/persist/fsg/modemst untouched. Write the HFS image later with `fastboot flash userdata` (sparse, auto-split under the 768 MiB limit). Android will no longer boot normally (expected; reinstallable). Order: XNU UFS driver first, read-only against the current GPT; flash only once it can locate userdata. Nothing erased yet.
- **DECIDED (user, 2026-10-15): 64 GiB `iosroot` on LUN0 (sda), taken from userdata (wipes Android user data; Android/super kept).** Planned GPT (4K LBAs): userdata 0x2B8C00..0xD7D6FF (~43 GiB, keep type/unique GUIDs and attributes); iosroot 0xD7D700..0x1D7D6FF (0x1000000 blocks = 64 GiB, 1 MiB aligned); backup GPT area unchanged. Procedure: user backs up phone data -> boot OrangeFox recovery (adb) -> back up GPT (sgdisk --backup + raw dd of LBA 0-5 and last 6 blocks) to research/ -> sgdisk edit -> Format Data -> verify Android boots to setup. NOT EXECUTED YET.
- **INTERNAL UFS WORKS FROM THE SHIM (read-only), user authorized internal-UFS approach incl. flashing (2026-10-15, "i allow everything").** Shim probe `shim/ufs.c` (commit `12ed3ed` + filter): ufshc@1d84000 is UFSHCI 2.1 (ver 0x210, cap 0x1587031F, 32 slots), ABL leaves HCE=1, HCS=0x10F (device present, lists ready, UIC ready); probe takes over UTRL at its own buffer (verifies UTRLBA readback before any doorbell), TEST UNIT READY/READ CAPACITY/READ(10) all succeed. LUN0 = 0x1D7D800 x 4 KiB (~126.6 GB), valid GPT (64 entries at LBA 2). Photos IMG_20261015_212916/213325/213429. Big LUN0 partitions (start in 4K LBAs / MiB): super 0xCA00/0x2200 (8.5 GiB), logdump 0x22CA00/64, minidump 0x230A00/128, rawdump 0x238A00/128, recovery 0x240C00/128, cache 0x248C00/384 ext4, exaid 0x260C00/384, cust 0x278C00/1024 ext4, **userdata 0x2B8C00/0x1AC4B MiB (~107 GiB), fs unreadable = encrypted (metadata encryption)**. cache..cust contiguous = only 1.75 GiB (too small for iOS ~10+ GiB). Plan needing user consent: shrink userdata in GPT (wipes Android data) and add an `iosroot` partition at the end of LUN0. LUNs 1-5 not listed yet (sdb..sdf backed up in backup/lun).
- **fastboot limit measured (2026-10-15, read-only getvar):** `max-download-size: 805306368` (768 MiB); `max-fetch-size` unsupported; `is-userspace: no` (ABL fastboot). A `fastboot boot` image (shim+kernel+RAM disk) must be < 768 MiB, so the 2.5 GiB iOS shared cache cannot ride in the boot RAM disk even if XNU gets all RAM. Real userland needs storage access (UFS driver, or USB host) or a much smaller cache. Unlocking full RAM is still needed later (GUI).
- **iOS 16 USERLAND SOURCE (Claude, 2026-10-15).** Chose **iPhone X (iPhone10,3/10,6, D22/D221AP)** iOS **16.7.16 (20H392)** over iPhone 8: same A11 (no PAC -> plain arm64, runs on Kryo 470/ARMv8.2), last iOS for A11; X userland is home-button-less and tall-screen (1125x2436 vs sweet 1080x2400), better fit for later GUI. Note the X has its OWN SystemOS cryptex (different digest from iPhone 8's). IPSW URL in `research/ipsw-x/URL.txt` (+ BuildManifest). Images on the user's external SSD (NOT in git, do not format that disk: it holds the user's photos): `/run/media/dipa/android/ios-sweet-ipsw/iPhone10,3_16.7.16_20H392/` — `087-86593-047.dmg` = Cryptex1,SystemOS (2,743,074,816 B), `087-86698-044.dmg` = OS rootfs (4,730,814,044 B), both zip CRC-32 verified, plus Firmware/*.root_hash/.trustcache/.mtree. AppOS cryptex is 087-86715-047.dmg (10 MB, same as iPhone 8).
  - Images are raw APFS; files use LZBITMAP (decmpfs type 14). 7z cannot decode. Tools: `ipsw` 3.1.738 (AUR ipsw-bin, user-approved) needs apfs-fuse on Linux; **apfs-fuse** built in user space (system cmake broken: cmake 4.4.4 wants libjsoncpp.so.27, jsoncpp 1.9.6 installed = partial upgrade, NOT fixed) with pip cmake + `-DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS="-include cstdint"`, installed to `~/.local/bin/apfs-fuse` (+apfs-dump-quick). Mount via a comma-free symlink (apfs-fuse splits options on ',' in `iPhone10,3`; exFAT has no symlinks -> put link elsewhere): `apfs-fuse LINK/087-86698-044.dmg MNT` -> `MNT/root/...`; unmount `fusermount3 -u MNT`.
  - Verified from headers: `dyld_shared_cache_arm64` magic `dyld_v1   arm64`; `/sbin/launchd`, `/usr/lib/dyld` = cputype 0x100000c subtype 0 (plain arm64). libSystem exists only inside the cache.
  - **BLOCKER for real userland via RAM disk:** shared cache = 3.10 GiB in 44 files (~2.5 GiB without the 592 MB `.symbols`), but the kernel manages only 1.20 GiB (0xb0000000..0xfd000000). Options: (A) give XNU the phone's full ~6 GB (banks 0x80000000+2G and 0x100000000+0xfcc00000, carve-out holes need pmap support) AND check fastboot max-download-size for a multi-GB boot image; (B) storage driver (Qualcomm UFS) to read a root from flash (no flashing allowed: would need a file/partition strategy); (C) run against individual dylibs extracted from the cache (uncertain).
- **MILESTONE ON HARDWARE: HFS+ ROOT FILESYSTEM -> PID 1.** Boot 2026-10-15 18:22:12 UTC (`research/boot0027b.log`, image `shim/out/shim-boot-0027b.img`), photo `IMG_20261015_202334.jpg`: `BSD root: md0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> repeated `SWEET init: still alive`, no panic. Kernel 0027 (built-in Apple OSS HFS + BearSSL MD5), init with SP alignment fix. NEXT: real iOS 16 userland on the HFS image (dyld, libSystem/dyld shared cache, launchd). Known gaps: entitlements (none granted), SHA-1/SHA-384/512 providers, no storage driver (RAM disk only), root mounted read-only.
- **PHONE HFS ROOT (0027) + SIGBUS ROOT-CAUSED.** Phone boot 2026-10-15 17:49:06 UTC (`research/boot0027.log`, photo `IMG_20261015_200031.jpg`): `hfs: mounted SweetRoot on device b(1, 0)` (HFS works on hardware), init printed its first line, then `pid 1 exited -- exit reason namespace 2 subcode 0xa` = SIGNAL/SIGBUS -> `initproc failed to start` panic. Diagnostic boot 18:08:13 UTC (`research/boot0027d.log`, image `shim/out/shim-boot-0027d.img`, diag init `research/diag-init` from scratch diag-init.S with [a][b][c] markers, `cs_debug=1`; photo `IMG_20261015_201547.jpg`): output `PID [a][b]` then SIGBUS -> fault at first `strb w21,[sp]`; no code-signing messages. Cause: kernel enters a static binary with sp at argc, not necessarily 16-aligned (depends on arg/apple string lengths, differs mockfs vs HFS); EL0 SP alignment check -> EXC_ARM_SP_ALIGN -> SIGBUS (sleh handle_sp_align). QEMU does not enforce SP alignment. NOT a kernel bug: real binaries start in dyld which aligns. Fix `1a8fc15`: sweet-init aligns sp at entry. Rebuilt `research/root-hfs.img`, QEMU `research/q0027-hfs2` OK. Phone image `shim/out/shim-boot-0027b.img` SHA256 `098ba46c...c5c` (payload-0027b, ramdisk match check-0027b) ready, awaiting FASTBOOT. Lesson: Assembly `;` is a comment in Apple arm64 asm (use .macro, not ;-joined #define).
- **HFS ROOT WORKS (QEMU).** 0027 CI `38779441895` PASSED, `xnu/out-0027` SHA256 `a8643886...5f6a`. QEMU `research/q0027-hfs` (RAM disk = `research/root-hfs.img`, rd=md0): `BSD root: md0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` + heartbeat, no panic. Phone image `shim/out/shim-boot-0027.img` (payload-0027 = 0027 kernel + sweet-0021.adt + root-hfs.img + rd=md0, ramdisk byte-match check-0027) ready; awaiting FASTBOOT for phone test.
- **HFS ROOT WORK (Claude), patches 0026-0027.**
  - 0026 (`f0e990c` + link fix `64c96f4`): Apple OSS **hfs-627.100.6** (commit `31d5ec238dfab485e78a9a30b8b92d13e8914f03`, paired with xnu-8796.141.3 in macos-135 release.json) staged by `xnu/patches/0026-stage-hfs.sh` into `bsd/sweet_hfs` (core + hfs_encodings; AppleKeyStore include/key prototypes guarded by CONFIG_PROTECT; SDK TargetConditionals copied). New config option `sweet_hfs` (MASTER + MASTER.arm64.sweet); 40 `optional sweet_hfs` files; Makefile.template foreach adds `_CFLAGS_ADD = -include sweet_hfs_compat.h -I...` (MUST come after kernel -D flags; compat undefs BSD/XNU_KERNEL_PRIVATE and the CONFIG_* HFS tests so kext-config.h decides: macOS profile, no content protection). `sweet_hfs_iokit.c` replaces hfs_iokit.cpp/hfs_encodings_kext.cpp: `sweet_hfs_register()` called in bsd_init after vfsinit (encodings init, vfs_fsadd VFS_TBLCANMOUNTROOT, zones, sysctls); is_ejectable 0, iterate_media none, serial KERN_NOT_SUPPORTED, OSKextGetCurrentLoadTag -> kOSKextInvalidLoadTag (renamed via compat macro). hfs_encodings' own `hfs_allocated` renamed per object (separate kexts on Apple; first CI `38777325964` failed on that duplicate at link). mockfs now validates Mach-O magic (its TODO) so non-Mach-O roots fall through to HFS (new fs entries come AFTER mockfs in vfsconf). CI `38778133720` PASSED, `xnu/out-0026` SHA256 `0b49170c...701a`, prints `SWEET HFS: hfs-627.100.6 registered`.
  - HFS+ image: `research/root-hfs.img` (8 MB, mkfs.hfsplus from AUR hfsprogs installed with user approval; loop-mounted with sudo; /sbin/launchd = research/sweet-init, /dev; fsck.hfsplus OK). QEMU `research/q0026-hfs`: `BSD root: md0` then data abort in MD5Init from hfs_getvoluuid (mount computes volume UUID).
  - 0027 (`14bf2b7`): 0020 stager also copies BearSSL hash/md5.c + codec/dec32le.c/enc32le.c; `corecrypto_md5.c` SWEET branch converts MD5_CTX <-> br_md5_context around br_md5_* calls; wrappers sweet_md5.c/sweet_dec32le.c/sweet_enc32le.c. Host test 40/40 match md5sum. CI **`38779441895`** running.
  - Local tooling: `tools/xnu-cc.py` has `XNU_CC_POST` (flags after logged ones). Sandbox `research/bt28/xnu` = 0025 CI tree + 0026/0027 edits; originals `research/pre0026`, `research/pre0027`. HFS local objects `research/hfs-objs`. Checker entries updated to bt28.
  - SHA-1 / SHA-384/512 still have no provider.
- **MILESTONE ON HARDWARE: USERSPACE PID 1 RUNS ON THE PHONE.** Boot 2026-10-15 17:16:00 UTC (`research/boot0025.log`: send OKAY0.410s, boot OKAY0.135s), image `shim/out/shim-boot-0025.img` SHA256 `9dbfa0b1...89db0`. Photo `IMG_20261015_191733.jpg`: `BSD root: md0` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> repeated `SWEET init: still alive` (5 s heartbeat), no panic. Kernel 0025 + mockfs RAM disk + built-in no-entitlements AMFI. No flashing performed.
- **NEXT (toward real iOS userland):** real root filesystem with real binaries. Options: (a) build Apple OSS `hfs` (macOS 13.5 tag `hfs-627.100.6`) into SWEET like libpthread, then an HFS RAM disk image; (b) iOS 16 dyld + libSystem/dyld shared cache from the IPSW (needs APFS or repacking onto HFS). Expected hurdles: entitlements (sweet_amfi grants none), SHA-1/MD5/SHA-384/512 providers (BearSSL), missing kexts/daemons. Current init is a static test program, not launchd.
- **MILESTONE: USERSPACE RUNS (QEMU).** 0025 CI `38774788048` PASSED, `xnu/out-0025` SHA256 `5496069b...4877`. QEMU `research/q0025-rd` (150 s, no panic): `BSD root: md0` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> `SWEET init: still alive` every 5 s. Exec, ad-hoc SHA-256 code signature, syscalls, devfs /dev/console, select timeouts all work. Phone image prepared: `shim/out/shim-boot-0025.img` 16,515,072 bytes SHA256 `9dbfa0b15140c0bd733a16c4f727f6a7df44a8741f98d8e266d5f2fa4f689db0` (payload-0025 = 0025 kernel + sweet-0021.adt + research/sweet-init + rd=md0; ramdisk byte-match check-0025). Waiting for user to put phone in FASTBOOT.
- **0024 CI `38773277811` PASSED**, `xnu/out-0024` SHA256 `a0ba964b...4348`. QEMU `research/q0024-rd` and **PHONE (boot 2026-10-15 17:05:55 UTC, `research/boot0024.log`, image `shim/out/shim-boot-0024.img` SHA256 `cf3dac86...6964`, 16,510,976 bytes, payload `shim/out/payload-0024.bin` = 0024 kernel + `shim/out/sweet-0021.adt` + RAM disk `research/sweet-init` + rd=md0; ramdisk byte-match `check-0024`)** agree: `Added memory device md0`, `BSD root: md0`, `load_init_program: attempting to load /sbin/launchd`, then data abort in accelerate_entitlement_queries (PC 0x...77d2458, FAR 0x1a0) = NULL AMFI interface. Photo `IMG_20261015_190715.jpg`. RAM disk root is physically verified.
- **0025 (`2518c79`)**: new `bsd/kern/sweet_amfi.c` registers a built-in AMFI interface at EARLY_BOOT/MIDDLE: no entitlements (adjustContextWithoutMonitor succeeds without creating a context, queries KERN_DENIED, copies NULL/KERN_NOT_FOUND, get_xml/transmuted false); WithMonitor KERN_NOT_SUPPORTED; OSEntitlements_query and query_context_to_object panic explicitly (CoreEntitlements closed; no kernel caller); TrustCache version 0 (0015 keys on trust_cache_rt==NULL, img4 absent -> still unavailable); CoreEntitlements table zero (PMAP_CS only). Compiles locally; checker updated (bt27 entries). CI **`38774788048`** running. Next artifacts `xnu/out-0025`, `research/bt29`, `research/run29.log`. Real iOS binaries will need real entitlements later (no OSS CoreEntitlements exists).
- **0021-0024 RAM-disk-root iteration (Claude), QEMU only so far, no new phone boot since 0020.** Each kernel tested with `tools/mkpayload.py KERNEL research/q0021.adt OUT --ramdisk research/sweet-init --cmdline "debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0"` then `tools/qemu-sweet.py OUT DIR --secs 150 --kernel KERNEL --fail-on-panic` (PATH needs ~/swift/usr/bin for llvm-nm). Fresh ADTs: `research/q0021.adt` (QEMU), `shim/out/sweet-0021.adt` (phone).
  - 0021 CI `38769532000` PASSED, `xnu/out-0021` SHA256 `2b7500a1...962e`, `research/bt25`/`run25.log`. QEMU `research/q0021-rd`: md0 root, then assert `iomediaClass` in IOService::publishHiddenMedia.
  - 0022 (`20ece9c`, publishHiddenMedia without IOMedia class) CI `38770666158` PASSED, `xnu/out-0022` SHA256 `a4d0e787...30c5`, `research/bt26`/`run26.log`. QEMU `research/q0022-rd`: data abort in SHA256_Init (NULL g_crypto_funcs) from vm_kernel_addrhash_internal.
  - 0023 (`c8089d1`, libkern SHA256_Init/Update/Final use built-in ccsha256_ltc_di + ccdigest_init/update on SWEET; also serves code-signing SHA-256; SHA-1/MD5/SHA-384/512 still have NO provider - BearSSL from 0020 could supply them later) CI `38771876423` PASSED, `xnu/out-0023` SHA256 `d763fed7...1003`, `research/bt27`/`run27.log`. QEMU `research/q0023-rd`: **mockfs mounted md0, `load_init_program: attempting to load /sbin/launchd`**, then panic `mockfs_fsnode_vnode failed to create fictitious pages ... rvalue = 5` (mockfs.c:351).
  - 0024 (`3293c80`): mockfs used memdev mi_base (4K page number of a VIRTUAL kernel address for IOKit RAMDisk, truncated to 32 bits) << PAGE_SHIFT as a physical address. Now zero-copy pager mapping only when mi_phys (with 4K units); virtual md uses pagein/blockmap/strategy -> memdev bcopy. memdev's own mdPhys path assumes 4K ppnums (unsafe on 16K arm64), so do NOT switch the RAMDisk to physical. CI **`38773277811`** running. Next artifacts `xnu/out-0024`, `research/bt28`, `research/run28.log`.
  - Sandboxes: bt25/bt26/bt27 contain the next patch's edited files; pristine copies in `research/pre0022`..`pre0024`.
- **PATCH 0021 (Claude): RAM disk root via mockfs -> first userspace program.** Commits `9c37771` (initial; CI `38768500280` FAILED: mockfs_fsnode.c kfree_type(mockfs_fsnode_t) typed-alloc static assert; local xnu-cc stubs `__builtin_xnu_types_compatible` so it can't catch this) and `193772f` (fix), CI **`38769532000`** running: https://github.com/dipa-studio/ios-sweet/actions/runs/38769532000. Next artifacts: `xnu/out-0021`, `research/bt25`, `research/run25.log`.
  - Kernel: `config/MASTER.arm64.sweet` FILESYS_BASE += `mockfs` (XNU's built-in FS: root device is itself a Mach-O, served as /sbin/launchd, plus /dev for devfs). Upstream mockfs bit-rot fixed: kalloc_type/kfree_type used the POINTER typedef mockfs_fsnode_t (nodes were 8 bytes) -> `struct mockfs_fsnode`. `IOService::setRootMedia` asserted IOBlockStorageDriver class exists (IOStorageFamily absent): SWEET-only, a missing class means root=NULL (same as IOSetImageBoot); Apple path keeps assert. All 21 patches apply (`research/check-patch-order.py`); 5 TUs compile locally with -DMOCKFS (`research/mockfs-objs`), imports resolve vs 0020 kernel. Sandbox `research/bt24/xnu` now holds 0021 versions of those 3 files; pristine 0020 copies in `research/pre0021`.
  - Tools: `tools/mkpayload.py --ramdisk IMAGE` appends IMAGE as an extra payload segment right after the kernel, moves kernel_end past it (shim unchanged: ADT+boot_args follow it, image stays below topOfKernelData), and adds ADT `/chosen/memory-map` `RAMDisk`=<phys,size>. Boot with `rd=md0`. Without --ramdisk output is byte-identical to existing 0020 QEMU and phone payloads (verified). `tools/sweet-init.S` + `tools/mkinit.py`: PIC static init (open /dev/console, print PID, heartbeat every 5 s via select, never exits) wrapped into a static arm64 Mach-O (__PAGEZERO, __TEXT@0x100000000, LC_UNIXTHREAD, ad-hoc SHA-256 CodeDirectory v0x20400, 4K pages). Build: `~/swift/usr/bin/clang -target arm64-apple-ios16.0 -c tools/sweet-init.S -o X.o && tools/mkinit.py X.o research/sweet-init` (ld64.lld cannot link static iOS). Signature not verified by a real codesign; the kernel will tell.
  - QEMU with 0020 kernel + RAM disk (`research/q0020-rd.bin`, rd=md0, 150 s `research/q0020-rd150`): `Added memory device md0`, **`BSD root: md0, major 1, minor 0`** (plumbing works), then the setRootMedia assert (now fixed in 0021). NOTE: QEMU needs >60 s to reach IOFindBSDRoot (60 s run showed nothing after stf0; likely RTC/inittodr wait), so use --secs 150.
  - NEXT: download 0021, QEMU 150 s with ramdisk; expect mockfs mount then init exec -> `*** SWEET userspace: init running as PID 1 ***`. Then phone boot with same payload (phone ADT shim/out/sweet-0020.adt pattern + --ramdisk + rd=md0).
- **0020 PHONE RESULT (user transcription of screen, 2026-10-15): KERNEL INIT COMPLETE, WAITING FOR ROOT.** After Skywalk self-tests PASSED, kminit, ifnet_attach lo0/gif0/stf0, screen prints `BSD`, then `Waiting on <dict><key>IOProviderClass</key><string>IOMedia</string><key>Content</key><string>Apple_HFS</string></dict>` and `Failed to write boot-picker-bringup-reason to NVRAM.` No panic. This is IOFindBSDRoot waiting for a root device (none exists); NVRAM warning is expected (no NVRAM driver). QEMU `cpu_idle` snapshot is presumably the same wait (not GDB-confirmed; milestone GDB run was cancelled by user since phone answered it). Draft `research/check-milestones20.gdb` (hbreaks inittodr/vfs_mountroot/IOFindBSDRoot/bsd_utaskbootstrap) left uncommitted/ignored.
- **NEXT MILESTONE: root filesystem -> launchd.** Plan: (1) RAM disk: shim places an image in RAM, ADT /chosen/memory-map `RAMDisk` entry, boot-arg `rd=md0` (XNU md driver). (2) HFS is a separate kext on Apple; build Apple OSS `hfs` (tag paired with macOS 13.5 in distribution-macOS macos-135 release.json) into SWEET like libpthread 0019. APFS is closed. (3) Minimal HFS image with iOS 16 launchd (later: dyld/shared cache; AMFI/code-signing absent will be a later hurdle). User's end goal is full iOS GUI.
- **0020 (AES) VERIFIED + PHONE BOOTED (Claude took over after Codex usage limit).** Actions `38765422261` (`ab9e23f`) PASSED; kernel `xnu/out-0020/kernel.development.sweet` checksum OK; buildtree `research/bt24`, log `research/run24.log`. Actual-kernel AES checks PASS (`research/check-aes20.gdb` / `.log`, 12 extra CBC cases match OpenSSL). 45s QEMU `research/q0020`: no panic, zero unknown glyphs, past old TCP/AES panic -> Skywalk self-tests PASSED -> bsd_autoconf kminit -> ifnet_attach lo0/gif0/stf0; final snapshot PC in `cpu_idle` (idle or waiting; NOT yet diagnosed, could be root-device wait). Image `shim/out/shim-boot-0020.img` 16,461,824 bytes SHA256 `775200cb02d95fc750981516b79d91dcdc96184bbe67a38f9d57c59b4a391828`, ramdisk byte-matches `payload-0020.bin` (`check-0020`). **Phone temporarily booted 2026-10-15 16:10:00 UTC**: one FASTBOOT product sweet, send OKAY0.407s, boot OKAY0.134s, total0.549s, exit0 (`research/boot0020.log`). Awaiting user photo; do not reboot the phone before it. NEXT: read photo; diagnose what QEMU waits on after stf0 (GDB backtrace of threads / longer run). Codex's STOP request below is complete.
- User requests STOP after completing the current0020 AES iteration (Actions/QEMU/phone), then leave a precise committed handoff for another AI. User says FASTBOOT ready again after0019 photo. Do not start a new blocker fix after this iteration.
- Goal: iOS16-generation XNU on Redmi Note10Pro sweet/SM7150. Always commit, use gh/Actions for kernel builds, keep this handoff current. Temporary fastboot boot only; preserve Android/backups. No userland/root filesystem boot yet.
- **Patch0020 update:** initial `e71098d` / Actions `38765012466` superseded by scalar-only compile flags. Added -mgeneral-regs-only to all six provider/BearSSL TUs and corecrypto_aes.c; object disassembly now confirms no SIMD. Updated implementation `ab9e23f`, Actions `38765422261` queued/running: https://github.com/dipa-studio/ios-sweet/actions/runs/38765422261. Do not launch another run. portable BearSSL AES CBC and legacy one-block ECB wired directly into SWEET corecrypto_aes.c. Genuine bitslice implementation, no CPU AES/SIMD requirement, heap or fake global crypto table. Source fetched by `0020-stage-bearssl.sh` from official BearSSL git at immutable `7bea48e5e850ab4cafbe68d3765cdaba13a86d6f`; MIT license retained, downloaded source stays out of git. Kernel limits include adapter only. All128/192/256-bit keys, CBC encrypt/decrypt, copied contexts, in-place/out-of-place; zero block count and size overflow handled, invalid keys clear context. Seven TUs compile to arm64 objects (`research/aes-objs`), imports resolve, all20 patches apply and sources match (`research/patch0020-check.log`). Sandbox `research/bt23/xnu`, originals `research/pre0020`. NEXT: Actions then actual-kernel NIST/random CBC/ECB checks and >=45s QEMU before another phone boot. Next artifacts out-0020 / bt24 / run24.log. 0020 updated CI pending. Local sources/checks match the scalar-only patch.
- **Latest built kernel0019**, implementation `256d3f0`, patches0001-0019. Actions `38763072907` PASSED (4m5s): https://github.com/dipa-studio/ios-sweet/actions/runs/38763072907. Kernel `xnu/out-0019/kernel.development.sweet`, SHA256 `a27df7568f03fb541cf0e8024c0a594fa178a85054fcf9eba29158cbd4c5ecc0`; checksum verified. Log/source `research/run23.log` / `research/bt23` (no nested .git). Seven pthread source/build/header files and staged adaptations byte-match local compile inputs.
- **0019 hardware photo received** `IMG_20261015_175306.jpg`: pthread registration visible, passes mbinit and DLIL checksum/scheduling, same AES fault as QEMU: PC`0xfffffe00079174e0` (`aes_encrypt_key128`), LR`0xfffffe000767c3a4` (`tcp_init`), FAR0x78, x8=0. Disassembly proves NULL g_crypto_funcs->ccaes_cbc_encrypt. Photo processed; no more0019 observation pending. General crypto table remains absent, other hash/mode operations may still fault after0020. DO NOT bypass TCP or install fake crypto. XNU already contains SHA256/HMAC implementations; AES source there is a dispatch wrapper. Full future provider interface libkern/libkern/crypto/register_crypto.h. Apple/corecrypto default README describes evaluation-only licensing, so no external corecrypto was fetched or staged; BearSSL selected for this portable AES integration.
- **0019 pthread FIXED in QEMU and observed beyond its init on phone**: 45s QEMU `research/q0019`, zero unknown glyphs, then AES panic. Actual-kernel API checks PASS `research/check-pthread19.gdb` / `.log`, guest `research/q0019-api`: function/callback table identity, actual provider pthread_init returns, locks/global hash/threadcall/typed zones initialized, thread_selfid via syscall shim + buffer bounds, process hash init/delete via shims restores NULL. Monitor quit prints expected remote-disconnect after PASS. All fixture guests stopped.
- **0019 provider**: genuine Apple libpthread-514.100.2 commit `a6c70ca0e544347a0377babf748f831577ec24f9`, exact paired release with xnu8796.141.3 in distribution-macOS/macos-135/release.json (`research/macos-135-release.json`). 0019 shell stage runs before patches (workflow copies *.sh); three SWEET wrapper TUs use kext BSD header view, vm_fault_external, renamed provider-side pthread_kern/current_uthread; register real function table at EARLY_BOOT SECOND, real pthread_init later. Stager applies bounded address casts, zero user addresses, proper panic format. SWEET opaque ksyn storage enlarged48->56 for trailing kwe_uth, checked with provider static assertion. Local pre-CI TargetConditionals fixture was syntax-only; artifact bt23 carries actual SDK header and CI is authoritative.
- **Phone0019 temporarily booted** 2026-10-15 15:51:36 UTC: exactly one FASTBOOT/product sweet verified; send OKAY0.406s, boot OKAY0.134s, total0.546s, exit0 (`research/boot0019.log`). Image `shim/out/shim-boot-0019.img`, 16,457,728 bytes, SHA256 `19429a57e486b856880df9e27327ed488e0139345311fdb0350974e72103a9a1`. Phone ADT/payload `shim/out/sweet-0019.adt` / `payload-0019.bin` timer19, unpacked ramdisk byte-match (`check-0019`). EntryPA0xb146b488, kernelendPA0xb2000000, boot_argsPA0xb2004000/top0xb2008000. Last user FASTBOOT+USB reply preceded this boot; do not assume phone is still in FASTBOOT.
- **CPU0018 physically verified** by `IMG_20261015_173951 (2).jpg`: boot CPU0 registered, real SGI1 verified, initialization complete -> BSD/MAC/mbinit -> old pthread-table panic. SweetCPU uses legacy IOCPU, ml_processor_register/processor_start/initCPU, real SGI1 clears SIGPdisabled before max CPUs completion; no forced flags/skipped waits. CPU APIs and two-CPU rejection passed (`research/check-cpu18.gdb`, `q0018-api`, `q0018-smp`). Single core; SMP/suspend unsupported. CURRENT SWEET has NO USE_APPLEARMSMP.
- QEMU next run: generate fresh `tools/mkadt.py research/q0020.adt --qemu` (timer27), mkpayload with `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`, then `tools/qemu-sweet.py PAYLOAD OUT --secs 45 --kernel KERNEL --fail-on-panic`. 12s can miss later panics because IORTC waits30s. Runner snapshot only: panic exit2, blank/incomplete exit3; absence of panic does not mean complete boot. Qualcomm XPU/ABL/panel and phone SMC PSCI require hardware checks.
- Local checker `tools/xnu-cc.py research/run23.log research/bt23 FILTER --per-file`, XNU_CC_OBJDIR for objects. For new BearSSL TUs add XNU_CC_EXTRA with -I bt23/xnu/libkern/crypto/sweet_bearssl/{inc,src}. Patch checker `research/check-patch-order.py` uses temporary upstream index; source fixtures/artifacts ignored under research. GDB mask registers to unsigned64, use function-entry manual-call return traps, discard manipulated guests. No QEMU guest intentionally left running.

## Session log and earlier handoffs (superseded by the active summary above)

### Prior 0019 transition notes (superseded)

- Goal: iOS 16 generation XNU on sweet. Always commit completed work, use gh/Actions for kernel builds, keep this handoff current. Temporary fastboot boot only; preserve Android/backups.
- **0019 build PASSED** Actions `38763072907` (4m5s), implementation `256d3f0`. Kernel `xnu/out-0019/kernel.development.sweet`, SHA256 `a27df7568f03fb541cf0e8024c0a594fa178a85054fcf9eba29158cbd4c5ecc0`; checksum verified. Log/source `research/run23.log` / `research/bt23`; all seven patched files and staged provider adaptations match local compile inputs. Prefer bt23 as the next baseline.
- **0019 pthread FIXED in QEMU**: 45s run `research/q0019` registers provider, passes mbinit, DLIL SUM16 checks and DLIL thread scheduling. Actual-kernel GDB checks `research/check-pthread19.gdb` / `.log` PASS: function/callback table identity, real _pthread_init returns, locks/global hash/cleanup threadcall/typed zones non-NULL, thread_selfid via actual syscall shim with output bounds, process hash init/delete through actual shims restores NULL. Guest stopped after tests; no fixture left running. GDB monitor quit prints expected remote disconnect after PASS.
- **Current next blocker:** QEMU0019 panic data abort FAR0x78 at `aes_encrypt_key128` PC`0xfffffe00079174e0`, called by `tcp_init` LR`0xfffffe000767c3a4`. Disassembly confirms `g_crypto_funcs == NULL`, loading ccaes_cbc_encrypt at+0x78. General crypto registration is still absent; DO NOT bypass TCP or supply fake crypto. Next integrate a genuine compatible crypto implementation/provider (AES first needed here, other digest/mode APIs also used). XNU contains SHA256/HMAC but no AES implementation; its corecrypto_aes.c is a dispatch wrapper. Full crypto table is in libkern/libkern/crypto/register_crypto.h.
- **0019 phone image prepared and verified**, user reports FASTBOOT+USB again; exactly one device/product sweet verified. `shim/out/shim-boot-0019.img`, 16,457,728 bytes, SHA256 `19429a57e486b856880df9e27327ed488e0139345311fdb0350974e72103a9a1`; unpacked ramdisk matches phone payload (`shim/out/check-0019`, `payload-0019.bin`, `sweet-0019.adt`, timer19). EntryPA0xb146b488; kernelendPA0xb2000000; boot_argsPA0xb2004000/top0xb2008000. Usual debug cmdline cpus=1/-noprogress. **Temporarily booted 2026-10-15 15:51:36 UTC**: send OKAY0.406s, boot OKAY0.134s, total0.546s, exit0 (`research/boot0019.log`). User asked for photo after45s. Await the photo; DO NOT reboot again before observation. Bootloader acceptance only; no0019 physical progress observed yet.
- **Hardware photo confirms 0018 progress** (`IMG_20261015_173951 (2).jpg`): real phone prints `SWEET CPU: boot CPU 0 registered, SGI1 verified, initialization complete`, enters BSD/MAC, reaches `mbinit: done`, then the same missing-pthread-function-table panic as QEMU at pthread_shims.c:318. CPU completion/interrupt delivery is now physically observed. The photo was received; no further phone boot performed yet.
- **Patch0019 committed/pushed `256d3f0`; Actions `38763072907` queued/running:** https://github.com/dipa-studio/ios-sweet/actions/runs/38763072907. Do not launch a duplicate. genuine Apple OSS libpthread-514.100.2 provider (immutable commit `a6c70ca0e544347a0377babf748f831577ec24f9`), paired with xnu-8796.141.3 by Apple's `distribution-macOS/macos-135/release.json` (`research/macos-135-release.json`). `0019-stage-libpthread.sh` fetches/stages it before patches; workflow now copies shell scripts too. SWEET-only wrappers compile all three real kernel sources, rename provider-side symbols, use kext header view and vm_fault_external, register the real function table at EARLY_BOOT SECOND. Existing pthread_init then runs the actual provider initialization. No pthread wait/syscall is bypassed. Source staging includes bounded explicit address casts/zero user addresses and panic-format fixes. SWEET kernel opaque ksyn storage enlarged from48 to56 bytes to contain this provider's trailing kwe_uth, with a provider size assertion. Local compile of three provider TUs plus pthread_shims/kern_fork passed; imports resolve and provider exports do not collide with existing kernel symbols. All19 patches apply and changed sources match (`research/patch0019-check.log`). Source sandbox `research/bt22/xnu`, originals `research/pre0019`, objects `research/pthread-objs`. Local TargetConditionals fixture only has target macros for syntax; CI stages the real macOS SDK header and is authoritative. Next Actions artifacts: out-0019 / bt23 / run23.log; run QEMU >=45 seconds. 0019 CI is pending; no 0019 QEMU run yet.
- **Latest kernel implementation `e7d9550`, patches0001-0018; CI `38760004398` PASSED (3m56s)**: https://github.com/dipa-studio/ios-sweet/actions/runs/38760004398. No build is pending. Kernel `xnu/out-0018/kernel.development.sweet`, SHA256 `b3c8d59acc88f93a2c836eb2527a05ed4fbaac7c580a2ea8c3b795137f4aadf1`; checksum verified. Source/log `research/bt22` / `research/run22.log` (baseline for patch0019; no nested .git). New driver/config sources match CI exactly.
- **CPU wait FIXED in QEMU**: SweetCPU uses legacy IOCPU, validates single boot CPU, ml_processor_register -> processor_start -> initCPU real SGI1 -> scheduler/IOKit completion -> ml_set_max_cpus. It does NOT force a flag or skip a wait. CPU APIs passed: Running CPU0, processor_array[0] association, SGI signalling enabled, name, unsupported secondary-start error (`research/check-cpu18.gdb` / `.log`, `research/q0018-api`). Single-core only; SMP and suspend unsupported. Two-CPU topology fails explicitly (`research/q0018-smp`).
- **Next blocker reproduced in 45s QEMU** (`research/q0018-long`, zero unknown glyphs): BSD initialization reaches MAC framework, network mcache and `mbinit: done`, then **`pthread kernel extension not loaded (function table is NULL). @pthread_shims.c:318`**. Do not bypass pthread_init: find/integrate the matching open-source libpthread kernel implementation and register its real function table. A 12s run `research/q0018` looked panic-free before the 30s IORTC wait elapsed; use >=45s or GDB checkpoints for the next iteration. No userland/root filesystem boot yet.
- **Phone 0018 TEMPORARILY BOOTED** 2026-10-15 **15:31:15 UTC**: exactly one FASTBOOT device verified product sweet; send OKAY0.400s, boot OKAY0.134s, total0.539s, exit0. Local log `research/boot0018.log`. This confirms bootloader acceptance only. User was asked for a photo; await it, DO NOT reboot the phone again before their observation. No flashing performed.
- Current hardware image `shim/out/shim-boot-0018.img`, **16,437,248 bytes**, SHA256 **`b7a23595d143a76c72c9fe2cfcc5d77a8f16987b5af1a2703bbffdfa6922908c`**. Phone ADT/payload `shim/out/sweet-0018.adt` / `shim/out/payload-0018.bin` (timer19), unpacked ramdisk exactly matches payload (`shim/out/check-0018`). EntryPA0xb146b488; kernelendPA0xb1ffc000; boot_argsPA0xb2000000/top0xb2004000. Command line `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`.
- QEMU payload/ADT `research/q0018.bin` / `research/q0018.adt` (timer27). Run `python3 tools/qemu-sweet.py research/q0018.bin research/NEW-OUT --secs 45 --kernel xnu/out-0018/kernel.development.sweet --fail-on-panic`; fresh ADT seed for a new payload. Output fb.png/fb.txt/regs.txt/qemu.log/result.json. Visible panic exit2; blank/incomplete decode exit3; no_panic_observed is snapshot-only, not successful boot. -noprogress prevents the graphical indicator corrupting console text. Qualcomm XPU/bootloader/panel and SMC PSCI still need physical validation.
- Patches0014-0017 fixed PRNG registration/direct SHA256, explicit unavailable trust-cache service with rejected loads/no matches, built-in SweetPlatformExpert, and timer PPI from ADT (phone19/default19, QEMU27). All their checks/artifacts are preserved in the history below. CURRENT SWEET has NO USE_APPLEARMSMP; do not assume gCPUIC/gAIC/gPMGR AppleARMSMP globals exist.
- Local single-file checker: tools/xnu-cc.py research/run22.log research/bt22 FILTER --per-file, output via XNU_CC_OBJDIR. Patch checker `research/check-patch-order.py` applies all18 via temporary index, matches sandbox sources. Sandbox for0018 `research/bt21/xnu`, originals `research/pre0018`, objects `research/cpu-objs`. First compiler issues were missing machine/atomic.h and nonexistent getNameSymbol; fixed and both TUs compiled successfully before CI. Fixtures/artifacts under research are local-only/ignored.
- GDB: mask int(register)&((1<<64)-1) for unsigned kernel pointers; use function-entry manual-call return traps, discard manipulated guests. No QEMU fixture is intentionally left running.

### Earlier active handoff snapshot (superseded)


- **0018 milestone:** Actions `38760004398` PASSED (3m56s), checksums passed, new CPU/build files byte-match CI tree. Kernel `xnu/out-0018/kernel.development.sweet`, SHA256 `b3c8d59acc88f93a2c836eb2527a05ed4fbaac7c580a2ea8c3b795137f4aadf1`; log/tree `research/run22.log` / `research/bt22`. QEMU `research/q0018` 12s: boot CPU registered, SGI1 verified, CPU initialization complete, **BSD initialization entered** (copyright, MAC framework, buffer headers), no panic/zero unknown glyphs. Direct CPU API checks PASS (`research/check-cpu18.gdb`, `.log`, `research/q0018-api`): CPU0 Running, processor matches processor_array[0], real SGI enabled, CPU name correct, secondary-start returns KERN_NOT_SUPPORTED. Two-CPU ADT correctly panics before registration (`research/q0018-smp`). Longer 45s run `research/q0018-long` still running; next BSD wait/fault not yet identified.
- **Prepared 0018 phone image:** `shim/out/shim-boot-0018.img`, 16,437,248 bytes, SHA256 `b7a23595d143a76c72c9fe2cfcc5d77a8f16987b5af1a2703bbffdfa6922908c`. Phone ADT/payload `shim/out/sweet-0018.adt` / `shim/out/payload-0018.bin`, usual debug args with cpus=1 and -noprogress. Unpacked ramdisk byte-compared (`shim/out/check-0018`). Entry PA remains 0xb146b488; kernel end PA 0xb1ffc000, boot_args 0xb2000000/top0xb2004000. User reports FASTBOOT+USB ready; read-only check found exactly one product sweet. NEXT: temporary fastboot boot of this verified image, record send/boot results, then await hardware photo without rebooting again.
- Patch 0018 committed/pushed **`e7d9550`**; Actions **`38760004398`** queued/running: https://github.com/dipa-studio/ios-sweet/actions/runs/38760004398. Do not launch a duplicate run.
- Active continuation: patch **0018** implemented in `iokit/Kernel/arm/SweetCPU.cpp` + arm64 source list + exact qcom,kryo470 personality. Keeps legacy IOCPU, accepts ONLY the single boot CPU, fills ml_processor_info from parsed topology with CPU object as cpu_id, calls ml_processor_register and processor_start. initCPU sends SGI1 via ICC_SGI1R_EL1 and waits (1-second deadline) for real cpu_signal_handler to clear SIGPdisabled. Only after successful startup calls ml_cpu_init_completed/cpusRunning and publishes completion/max CPUs. Secondary startup/suspend are explicitly unsupported. Local C++ compile passed for both TUs (`research/cpu-objs`), all imports resolve, object contains dsb ishst / ICC_SGI1R / isb; all18 patches apply in order and match sources. Sandbox `research/bt21/xnu`, originals `research/pre0018`; next CI artifacts should use `xnu/out-0018`, `research/bt22`, `research/run22.log`. User has the phone available; first run QEMU, then prepare/temporarily boot useful hardware progress.
- Goal: iOS 16 generation XNU on Redmi Note 10 Pro / sweet. User wants persistent progress, commits, kernel builds via `gh` Actions, and this handoff kept current across agent switches. Temporary `fastboot boot` only; preserve Android and backups.
- Latest kernel implementation: **`aa3f991`**, patches **0001-0017**. All four new Actions builds PASSED: 0014 `38748543058` (4m0s), 0015 `38749549779` (4m3s), 0016 `38750733996` (4m3s), 0017 **`38751507437` (4m7s)**. Latest: https://github.com/dipa-studio/ios-sweet/actions/runs/38751507437. No build is pending.
- **Current blocker: CPU initialization has no SWEET CPU driver.** QEMU registers SweetPlatformExpert, then kernel_bootstrap_thread waits in **kperf_init_early -> ml_wait_max_cpus**. Confirmed via actual-kernel GDB call stack (`research/locate-cpu-wait2.log` / `.symbols`) and normal-run `max_cpus_initialized == 2` (MAX_CPUS_WAIT). Do not force the flag or remove the wait. Next implement real CPU registration and completion using XNU's CPU/processor interfaces. CURRENT SWEET uses legacy IOCPU (`USE_APPLEARMSMP` is absent); enabling AppleARMSMP is a separate architectural change requiring GIC/PMGR services. No userland/root filesystem boot yet.
- Fixed this session: PC=0 unregistered PRNG callback (0014, built-in HMAC-SHA256 provider + direct entropy/write_random hashing); absent Image4/AMFI trust-cache service (0015, unavailable runtime, loads rejected and queries no-match); missing platform driver (0016, SweetPlatformExpert exact personality); incorrect QEMU timer IRQ (0017, validated ADT PPI property, phone default19/QEMU27).
- **Latest kernel** `xnu/out-0017/kernel.development.sweet`, SHA256 `055a80190ab998b4df9203f1102072dcc78faad1779a537a514e522b682b582f`. Buildtree/log **`research/bt21` / `research/run21.log`**; all nine changed C/C++/header/config files byte-match local checked sources. Checksums passed. Entry PA `0xb146b488`, kernel end PA `0xb1ff4000`. Preferred baseline for patch 0018: `research/bt21/xnu` (no nested .git).
- **QEMU verified**: `research/q0017-noprogress` ran **30 seconds**, no visible panic, **zero unknown glyphs**, IRQ exceptions returned normally (4 IRQs, tickless idle). It still waits for CPU completion, so this is NOT a completed boot. Earlier 15s run `research/q0017` captured 122 IRQs, timer value27, GICR_ISENABLER0 `0x08000002`, and CPU WAIT; graphical progress overlay corrupted 13 glyphs. Add **`-noprogress`** to keep the console readable. New runner classifies incomplete/blank decoding explicitly (exit3 with --fail-on-panic), visible panic exits2. Snapshot-only; use regs/GDB to diagnose stalls.
- Current QEMU payload/ADT: **`research/q0017-noprogress.bin` / `research/q0017.adt`**. Run: `python3 tools/qemu-sweet.py research/q0017-noprogress.bin research/NEW-OUT --secs 30 --kernel xnu/out-0017/kernel.development.sweet --fail-on-panic`. Generate fresh seed with mkadt for a new payload. QEMU virt models generic ARM execution, not Qualcomm XPU/bootloader/panel or the phone's SMC PSCI conduit.
- **Prepared phone image**: **`shim/out/shim-boot-0017.img`**, 16,416,768 bytes, SHA256 `51ff7fe30b4a6668c3452ffe1da922c5ac30cb8de60625c2a652587ecbd0ec92`. Phone ADT/payload `shim/out/sweet-0017.adt` / `shim/out/payload-0017.bin` (timer19). Unpacked ramdisk exactly matches payload (`shim/out/check-0017`). Command line **`debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`**. NOT sent to phone this session: known CPU-driver wait should be addressed in QEMU first. Last actual phone boot remains 0013; user's latest photo `IMG_20261015_160033.jpg` confirmed the old PRNG PC=0 panic.
- Direct QEMU/GDB API checks: PRNG 600-byte chunking, buffer bounds, changing output, zero-length reads/writes, user reseed, contended refresh, generator1 init/output PASS again on latest0017 (`research/check-prng17.gdb` / `.log`, guest `research/q0017-api`). Trust-cache unavailable-service/argument/output cases PASS on0015 (`research/check-trustcache.gdb` / `.log`). Platform registration + target/product normal/truncated/zero/null/bounds PASS on0016 (`research/check-platform.gdb`, `research/check-platform3.log`). Different platform ID correctly retains IOPanicPlatform fallback (`research/q0016-negative`). Timer PPI32 and size2 correctly panic (`research/q0017-badppi`, `research/q0017-badsize`); omitted property selects19 (`research/q0017-default`). These are API/bring-up checks, not crypto certification or SMP scheduling validation.
- Local checker **`research/check-patch-order.py`** verifies all17 patches and changed source equality using a temporary upstream git index. Fixtures/logs/artifacts under research are local-only and ignored. For GDB unsigned kernel pointers, mask `int(register_value) & ((1<<64)-1)`; x30/x29 may be rendered signed. Stop at a function entry for manual-call return traps, and discard each manipulated guest after API tests. All fixture QEMU guests have been stopped.


- User wants persistent progress toward iOS 16, commits for completed changes, builds through `gh` / GitHub Actions.
- Keep this file current at every milestone so another agent can resume immediately, including when usage limits interrupt a session.
- Patch 0017 committed/pushed `aa3f991`, Actions `38751507437` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38751507437. Next artifacts: xnu/out-0017, research/bt21, research/run21.log. Do not duplicate.
- Patch 0016 kernel downloaded/checksum verified: SHA256 `d06336c54baa06e0df6561a8bd7df98a9b9f7502c9381c62d1a44c045cd1ce46`, `xnu/out-0016/kernel.development.sweet`; buildtree/log `research/bt20` / `research/run20.log`. QEMU `research/q0016` (10s) prints platform expert registered, zero unknown glyphs, no panic observed BUT stalls (timer mapping still wrong until 0017). Direct actual-kernel GDB tests passed for registered instance and target/product names, including maxLength=1/0 and NULL, sentinels intact (`research/check-platform.gdb`, `research/check-platform3.log`, `research/q0016-api3`). First two fixture attempts used premature/wrong return breakpoints; discarded, corrected fixture uses start-return observation then a function-entry return trap. No phone boot yet.
- Patch 0016 CI `38750733996` PASSED (4m3s); artifacts not yet downloaded/booted. Patch 0017 prepared to fix QEMU timer fidelity BEFORE interpreting future no-panic/stall runs: actual `qemu-system-aarch64 -M virt,gic-version=3,dumpdtb=research/qemu-virt.dtb ...` timer property has virtual PPI 11 => INTID 27, while phone uses INTID 19. Kernel formerly hardcoded 19. New optional /arm-io/gic virtual-timer-intid property selects the PPI (validate exact u32 and 16..31); missing property retains phone default 19. One RO-late global drives GIC enable and sleh_irq dispatch. tools/mkadt.py emits phone19/QEMU27. Both C TUs compile (`research/timer-objs`), ADT serialize/parse checks confirm values/addresses, all 17 patches apply and match. Sandbox `research/bt19/xnu`, originals `research/pre0017`; initial local sleh compile failed due read-only exported header not updated, fixed export and BOTH recompiled successfully. CI exports normally.
- Architecture correction for next agent: CURRENT SWEET board_config does NOT define USE_APPLEARMSMP; it compiles the legacy IOCPU path, not cpu_boot_thread/IOPMGR AppleARMSMP. Existing roadmap mentions AppleARMSMP as a future approach. Do not look for gCPUIC/gAIC/gPMGR globals in the current kernel: they are absent. Determine the actual CPU-init wait after platform registration before choosing the next driver architecture.
- Patch 0016 committed/pushed as `0dcdf5c`; Actions `38750733996` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38750733996. Next artifacts: `xnu/out-0016`, `research/bt20`, `research/run20.log`.
- Patch 0016 implemented: new `iokit/Kernel/arm/SweetPlatformExpert.cpp`, arm64 source list, built-in personality in KernelConfigTables.cpp guarded by ARM64_BOARD_CONFIG_SWEET. Matches only xiaomi,sweet at score 10000, keeps IOPanicPlatform fallback for other hardware. Uses IODTPlatformExpert base initialization/power domain and createNubs CPU logical-ID annotation; overrides top-level publishing because base requires nonexistent Apple NVRAM /options. Publishes real CPU/top-level nodes, provides bounded target/product names, and registers the platform service. GIC/PMGR and arm-io child-bus drivers remain separate work. Both C++ TUs compile (`research/platform-objs`), all imports resolve against 0015/own objects, all 16 patches apply and sources match. Sandbox `research/bt19/xnu`; originals `research/pre0016`. NEXT: full Actions/QEMU; inspect CPU boot waits after the platform registers, do not equate absence of panic with successful boot.
- Patch 0015 CI `38749549779` PASSED (4m3s), checksum verified. Kernel `xnu/out-0015/kernel.development.sweet`, SHA256 `ca1b424a596c7058550ad1659080a333e7643270f019656401d80bf2b876645f`; buildtree/log `research/bt19` / `research/run19.log`. QEMU `research/q0015` (10s, zero unknown glyphs) passes trust-cache handling and machine lockdown, then panics `Unable to find driver for this platform: "xiaomi,sweet" @IOPlatformExpert.cpp:2319` (IOPanicPlatform fallback). Next concrete work: add a built-in SWEET IODTPlatformExpert personality/class; keep the fallback for mismatched platforms. No phone boot yet.
- Direct trust-cache API checks PASSED in the actual 0015 kernel under QEMU/GDB (`research/check-trustcache.gdb`, `research/check-trustcache.log`, `research/q0015-api`): runtime pointers NULL, hash/UUID queries return NOT_FOUND, typed/untyped/legacy loads return NOT_SUPPORTED, capabilities outputs zero and bounds preserved, invalid-argument cases still reject. Second PRNG check also verifies contended refresh returns immediately (`research/check-prng2.log`, `research/q0014-api2`).
- Patch 0015 committed/pushed `8f851d4`, Actions run `38749549779` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38749549779. Do not duplicate.
- Direct PRNG API checks passed in the REAL 0014 kernel under QEMU/GDB, stopped before trust-cache init (`research/check-prng.gdb`, log `research/check-prng.log`, guest `research/q0014-api`). Invoked read_random for 600 bytes (chunking and buffer sentinel intact), repeated output differed, zero-length read/write succeeded, write_random(64-byte input) succeeded, secondary generator 1 initialized and generated distinct output. QEMU log has no exceptions during these checks; guest was discarded after the tests. This tests API behavior, not a cryptographic certification or SMP scheduling.
- Patch 0015 implemented in `bsd/kern/kern_trustcache.c`: SWEET with missing Image4/AMFI marks both runtime pointers NULL and logs service unavailability. Existing static loader handles NULL. Public load APIs return KERN_NOT_SUPPORTED; hash/UUID queries return KERN_NOT_FOUND; capabilities outputs are zero with KERN_NOT_SUPPORTED. Invalid-input checks remain, and Apple paths/available-provider initialization retain existing behavior. No trust is granted. Local arm64 compile passed (`research/trustcache-objs`), all 15 patches apply and sources match. Sandbox `research/bt18/xnu`, baseline `research/pre0015`. Next: Actions/QEMU and direct unavailable-service API checks using GDB.
- Patch 0014 CI `38748543058` PASSED (4m0s), artifact checksum passed. Kernel `xnu/out-0014/kernel.development.sweet`, SHA256 `741de20c851755d333759d7e668346f3edb4f325efac4cc7c142d80d1f5ff75c`; buildtree/log `research/bt18` / `research/run18.log`. QEMU run `research/q0014` (10s, zero unknown glyphs) prints `SWEET PRNG: HMAC-SHA256 registered`, passes commpage initialization, loads vnguard policy, THEN panics `image4 interface not available @kern_trustcache.c:253`. PC=0 fault FIXED IN QEMU. No new phone boot performed.
- Next work: SWEET currently has neither AppleImage4 nor AMFI. `trust_cache_runtime_init` unconditionally requires both; unlike BCM2837, SWEET enters this startup path. Investigate an explicitly unavailable trust-cache runtime with rejected loads/no query matches while providers are absent; preserve all Apple-platform checks. Merely skipping the startup call would leave later AMFI/IMG4 dereferences unsafe. Existing load_static_trust_cache already handles trust_cache_rt == NULL.
- QEMU runner now supports `--kernel MATCHING_KERNEL --fail-on-panic`: runtime font extraction from the payload produces exact `fb.txt`, plus `result.json`; a visible panic returns 2. Baseline 0013 re-run `research/q0013-text` detects the same NULL callback panic with zero unknown glyphs. Synthetic glyph roundtrip (including literal `?`) and invalid-VA rejection passed. Screen rows remain circular, not a full log; `no_panic_observed` does not prove successful boot. README has the invocation.
- Patch 0014 committed/pushed as `be61f99`; Actions run `38748543058` is running: https://github.com/dipa-studio/ios-sweet/actions/runs/38748543058. Do not start a duplicate build.
- 2026-10-15 active: hardware photo `IMG_20261015_160033.jpg` confirms the same PC=0 instruction-fetch abort as QEMU (LR `0xfffffe0007443fe4` = read_random). Independently reproduced with kernel 0013 in `research/q0013` (8-second run); prior allocator fix works. Latest tooling commit reviewed: `8d66d2d`.
- Patch 0014 implemented: registers a built-in HMAC-SHA256 DRBG provider at EARLY_BOOT/FIRST, before commpage creation. A shared mutex serializes output/reseed across CPUs (correctness first; no per-CPU scaling yet), initgen validates CPU identities, refresh uses try-lock and the existing entropy filter/health tests. SWEET entropy accumulation and write_random now use the built-in SHA256 directly; no incomplete g_crypto_funcs table is registered. The existing bootloader-seed requirement remains enforced.
- Local validation: both PRNG translation units compile to arm64 objects in `research/prng-objs`; no SHA256 wrapper imports remain. All 14 patches apply in CI order and resulting PRNG sources match local inputs. Sandbox `research/bt17/xnu`, originals `research/pre0014`, checker `research/check-patch-order.py`. NEXT: Actions kernel build, download to `xnu/out-0014`, run the QEMU payload before requesting another phone boot. Baseline QEMU ADT/payload are `research/q0013.adt` / `research/q0013.bin`.
- 2026-10-15 08:15 QEMU EMULATION WORKS (`tools/qemu-sweet.py`, see "QEMU emulation" below). Patch 0013 VERIFIED in QEMU: boot passes kmem_crypto_init and ulock_initialize. NEXT BLOCKER (reproduced in QEMU, expected on hardware too): `pmap_create_commpages -> random() -> RandomULong -> read_random -> prng_funcs.refresh == NULL` (instruction fetch abort at pc 0, lr read_random+0x34). Nobody calls `register_and_init_prng` (closed corecrypto kext does it on Apple). Fix = patch 0014: SWEET-registered cckprng_funcs provider (osfmk/prng/prng_random.c); note random_cpu_init also panics while !prng_ready. The 07:26 hardware boot of 0013 will most likely show this same panic.
- Previous hardware boot: patch 0013 (`a3a46e2`) CI `38686345689` PASSED (3m44s), image `shim/out/shim-boot-0013.img` was temporarily booted on 2026-10-15 at 05:26 UTC: send OKAY (0.407s), boot OKAY (0.134s), total 0.546s. Superseded by the hardware/QEMU PC=0 observation and patch 0014 work above.
- Latest kernel implementation commit: `8f851d4`. Latest successful kernel CI: `38749549779` (patch 0015), https://github.com/dipa-studio/ios-sweet/actions/runs/38749549779.
- CURRENT image: `shim/out/shim-boot-0013.img`, 16,412,672 bytes, SHA256 `110d1c46aee4b60253db24b6535e9781c4cf0fd25ca2a70e9016cf81378cea25`. Kernel `xnu/out-0013/kernel.development.sweet`, SHA256 `f0b1d375995316c550ce7014ff234d8271e9b9ac2857795ceff41b76ba2cd740`, plain arm64. CI log confirms 0013 applied; checksums passed. Entry PA `0xb146b488`, kernel end PA `0xb1ff4000`.
- Current log/buildtree: `research/run17.log`, `research/bt17` (extracted, no nested .git; preferred baseline for patch 0014). Payload/ADT: `shim/out/payload-0013.bin`, `shim/out/sweet-0013.adt` (includes /defaults). Unpacked ramdisk byte-compared with payload in `shim/out/check-0013`.
- Patch-0012 original image: `shim/out/shim-boot-0012.img`, 16,412,672 bytes, SHA256 `cf4b8ec4a65a51888d71c17d09cd0831d6b761b90e2531fdb8e575c32f4fd24a`. Kernel `xnu/out-0012/kernel.development.sweet`, SHA256 `69b78f49f6459cc63a0af44e7cf73c1779ef72130ef72b12ae86c88183ffc742`; checksums passed and sweet_early_relocate symbol present. Entry PA remains `0xb146b488`, kernel end PA `0xb1ff4000`.
- Patch-0012 log/tree: `research/run16.log`, `research/bt16` (extracted, no nested .git; preferred baseline for next patch). CI log confirms 0012 applied. Payload `shim/out/payload-0012.bin`, ADT `shim/out/sweet-0012.adt`; packed ramdisk byte-compared with payload in `shim/out/check-0012`.
- Active payload command line: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam`.
- Latest observed photo: `/home/dipa/Downloads/IMG_20261015_071346.jpg` (local only). Stages 9 (kernel framebuffer VA), 10 (TTBR0 off/TLB flush), 11 (physmap table pointers) and 12 (calling pmap_bootstrap) all visible. Persistent diagnostics are VERIFIED ON HARDWARE.
- pmap.c:1987 is the assert after SecureDTLookupEntry(NULL, "/defaults", &entry). The pmap-max-asids property itself is optional; an empty /defaults uses XNU's MAX_ASIDS. Do not bypass the assertion or guess an ASID limit.
- Previous payload-only fix image: `shim/out/shim-boot-0012-defaults.img`, SHA256 `3ff61a268853c6ea7cbf284833f1346a1d791980ca366b6e1a215a86e040512b` (16,412,672 bytes). Same kernel/CI as 0012; ADT 2516 bytes (`shim/out/sweet-0012-defaults.adt`), root /defaults present with no ASID override. Payload `shim/out/payload-0012-defaults.bin`; unpacked ramdisk exactly matches payload (`shim/out/check-0012-defaults`). This boot reached the allocator crypto NULL-pointer fault below.
- Latest hardware photo: `/home/dipa/Downloads/IMG_20261015_071608.jpg` (local only). Normal serial framebuffer console works. VM bootstrap/zone initialization, logging, scheduler, IPC, KDP and ulock_initialize print before the kmem_crypto_init data abort. gPanicBase warnings are expected because PLATFORM_PANIC_LOG_DISABLED is configured, and are not the faulting call.
- Faulting instruction: `ldr x0, [x8, #0x150]` in crypto_random_kmem_ctx_size; x8 = NULL g_crypto_funcs. Source: libkern/crypto/corecrypto_rand.c and osfmk/vm/vm_kern.c:2448. rand.h explicitly permits NON-cryptographic kmem RNGs, but requires no locking in generation and context size <=256. Do not substitute read_random here: the global PRNG provider is also unregistered and kmem generation must remain lock-free. early_random may seed contexts during single-CPU bootstrap; it uses the built-in HMAC-SHA256 DRBG with the host-generated ADT seed.
- Patch 0013 keeps the replacement entirely private to vm_kern.c: SplitMix64 state is 8 bytes per CPU/sizeclass, initialized using early_random. Uniform selection uses rejection sampling to avoid modulo bias. Other platforms retain the original corecrypto calls; SWEET's generic crypto APIs/registration remain untouched. Reference: https://prng.di.unimi.it/splitmix64.c (Sebastiano Vigna, public domain).
- Validation: vm_kern.c compiles to arm64 (`research/kmem-rng-objs`, using `research/run16.log` + `research/bt16`). Undefined symbols include early_random and NO crypto_random_* references. All 13 patches apply in CI order and match the checked source. Original saved as `research/vm_kern.pre0013.c`.
- RNG host checks: extracted actual new helper in `research/kmem-rng-check.c`; 16,384 outputs match the author's downloaded reference (`research/splitmix64-reference.c`), 147,456 bounded outputs satisfy their limits (including 1 and UINT64_MAX), and a forced rejection advances correctly. Full kernel crypto services and the registered kernel PRNG provider remain future work; this patch only supplies the allocator RNG contract.
- Patch-0011 artifacts: kernel `xnu/out-0011/kernel.development.sweet`, log `research/run15.log`, extracted buildtree `research/bt15` (no nested .git). CI log confirms patch 0011 applied; artifact checksums passed. Kernel is plain arm64, entry VA `0xfffffe000746b488` -> PA `0xb146b488`, kernel end PA still `0xb1ff4000`.
- Patch-0011 image: 16,412,672 bytes, SHA256 `29ad5cbfc752473de4af2c471a24a8b34f92da4bff5afec65c614b90cd3ad53a`. Kernel SHA256 `8ac3a956894b09f7bd0d890de8c5de38c65513287feb1e1bcd12db8ca772ee5c`. Payload/ADT in `shim/out/payload-0011.bin`, `shim/out/sweet-0011.adt`. Header unpacked/checked in `shim/out/check-0011`.
- First patch-0011 attempt timed out waiting for FASTBOOT; superseded by the successful send/boot recorded above. Do not repeat the boot before the user reports the current screen.
- Patch-0011 photo: `/home/dipa/Downloads/IMG_20261015_070218.jpg` (local only). Visible stages 1-8 and old red shim text; entry PA B146B488, boot args PA B1FF8000 / top B1FFC000. No evidence yet locating execution after stage 8.
- Patch-0012 sandbox: `research/bt15/xnu` (NO nested .git). Proposed permanent debug VA: VM_MIN_KERNEL_ADDRESS + ARM_TT_L2_SIZE, in the reserved gap below gVirtBase and above low globals; map the same PA framebuffer as Normal-NC in the final TTBR1 before arm_vm_physmap_init changes phystokv(). Keep the early panic sink until normal kprintf startup.
- Patch 0012 validation: all 4 changed C translation units compiled to arm64 objects in `research/post-ttbr0-objs`; internal header export added to osfmk/arm64/Makefile and recreated locally in EXPORT_HDRS for pe_serial.c. All 12 patches apply in CI order and match all 6 edited files. Baselines are saved under `research/pre0012/`.
- Actual mapping-helper host model (`research/early-map-check.c`, barrier stubbed only for host compilation) confirms: VA `0xfffffe0002000000`, L1 index 2016, L2 index 1; descriptor `0x006000009c000605` = framebuffer PA, Normal-NC, outer-shareable, EL1 RW, PXN+UXN. All other L2 entries preserved. The gap is excluded by ARM_LARGE_MEMORY's pmap_virtual_region(0).
- Next boot command line MUST add `-enable_kprintf_spam`: DEVELOPMENT disables kprintf_output by default even when DB_KPRT is set (`pexpert/arm/pe_kprintf.c`). Planned command line: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam`.

### Patch 0012 screen legend (after original stages 1-8)
- 9: kernel framebuffer VA live; 10: TTBR0 off / TLB flushed; 11: physmap pointers rebound.
- 12: calling pmap_bootstrap; 13: pmap_bootstrap returned; 14: early I/O page tables ready; 15: arm_vm_init returned.
- 16: calling serial_init; 17: fbcon config read / mapping; 18: fbcon mapping returned; 19: serial_init returned; 20: kprintf startup complete.
- Normal serial initialization clears the screen, so earlier stages may disappear before 19. If serial exists, early console disables after 20; otherwise early panic output stays enabled.
- Local source sandbox: `research/bt13/xnu` (git baseline `623b060`, patches 0001-0008 already in baseline; uncommitted export edits correspond to patch 0009). Preserve those edits when producing patch 0010.
- Previous compile log/tree: `research/run13.log`, `research/bt13`; previous kernel: `xnu/out/kernel.development.sweet`. New successful log/tree: `research/run14.log`, `research/bt14`; new kernel: `xnu/out-0010/kernel.development.sweet`.
- Current device access: patch-0012 image was temporarily started from FASTBOOT; no adb connection is expected. Never flash partitions for bring-up; use `fastboot boot` with the shim image.
- Patch `0010` lives in `xnu/patches/0010-sweet-early-framebuffer-debug.patch`. Its exact compiled sources are under `research/bt13/xnu`; generated objects are in `research/early-objs`.
- Local checks passed: all 5 changed/new translation units compiled to arm64 Mach-O objects; new console only imports `_doprnt` and `iso_font` (both exist in the previous kernel). All 10 patches applied to a temporary upstream git index in CI order, and all 7 resulting changed source files matched the local compile inputs.
- Reproduce changed-file compilation: `XNU_CC_OBJDIR="$PWD/research/early-objs" python3 tools/xnu-cc.py research/run13.log research/bt13 arm_init.c arm_vm_init.c kern/debug.c arm64/start.s osfmk/arm64/sweet_early.c --per-file` (create the output directory first). The helper now accepts explicit new source paths absent from the old artifact's Makefiles and rejects empty selections.
- Device command result: `adb reboot bootloader`, then `timeout 30s fastboot boot shim/out/shim-boot-0010.img`: send OKAY (16028 KB), boot OKAY, total 0.541s. This confirms bootloader acceptance only, not XNU progress.
- The successful boot above was on 2026-10-14. On 2026-10-15, `timeout 45s fastboot boot ...` timed out waiting for a device; no image was sent. Boot image SHA256 rechecked and unchanged. No new build is needed to repeat the trial.
- Earlier retry: `timeout 90s fastboot boot shim/out/shim-boot-0010.img` timed out (exit 124). This is superseded by the successful 2026-10-15 repeat boot recorded above; do not repeat it until the user reports the screen.
- User observation confirms MMU-on C entry, platform expert, CPU topology/bootstrap, startup bootstrap and cpu_init all complete. Failure is within arm_vm_physmap_init (non-XNU_MONITOR branch), before stage 8 / TTBR0 teardown. Old red shim text remains behind the early console; this is expected because only written rows are cleared.
- New edit/compile sandbox is `research/bt14/xnu` (extracted artifact, NO nested .git; `git diff` there would act on the parent project). Use a saved baseline file / generated unified diff, or explicitly initialize a local baseline before editing. `research/bt13/xnu` does have its own baseline git repo.
- Exact user-reported panic: `arm_vm_init.c:1606 Assertion failed: (temp_ptov_table[ptov_index - 1].va + temp_ptov_table[ptov_index - 1].len) <= physmap_end`.
- Root cause: MacOSX build sets `ARM_LARGE_MEMORY=1`; `XNU_MONITOR` is off. Block alignment in arm_vm_physmap_slide can add gaps, but the non-PPL large-memory physmap_end omitted the padding already present in the small-memory branch. Patch 0011 reserves PTOV_TABLE_SIZE*ARM_TT_TWIG_SIZE (8*32MB) in both the aperture bound and its L1 allocation; monitor/PPL builds retain zero padding. The assertion is preserved.
- Validation: `arm_vm_init.c` compiled to an arm64 object using `research/run14.log` + `research/bt14` (object in `research/physmap-objs`). All 11 patches apply in CI order and produce exactly that source. Host harness `research/physmap-check.c` extracts the real XNU slide function with page-table-write stubs: all 65,536 page-aligned slides within 1GB checked against the current kernel memory layout; old bound fails 65,504 cases, padded bound fails zero; worst overrun 0x1ffc000 bytes. Baseline source saved as `research/arm_vm_init.pre0011.c`.
- CI: https://github.com/dipa-studio/ios-sweet/actions/runs/38638702083 (success; artifacts and full log downloaded).
- New image: `shim/out/shim-boot-0010.img` (16,412,672 bytes, SHA256 `ed2c270e0081bad5ff517be415f9bfe8805463aea7866732203fa97ac737e64e`). Previous image preserved as `shim/out/shim-boot-pre0010.img`.
- New kernel SHA256: `3ee05270cc291c49589a101678f904b1b69cb2f2575e05398bce8a4e052f409a`. Artifact checksums passed; arm64 (not arm64e), early-console symbols present. Payload entry VA `0xfffffe000746a488` -> PA `0xb146a488`; kernel end PA `0xb1ff4000`. Command line remains `debug=0xc serial=3 cpus=1 keepsyms=1`.

### Patch 0010 screen legend
- Bottom-left white 64x32 tile: assembly entered `start_first_cpu`, before page tables/MMU.
- Text `SWEET EARLY 1`: entered `arm_init` with MMU on; 2: platform expert done; 3: CPU topology done; 4: CPU bootstrap done; 5: startup bootstrap done; 6: CPU init done; 7: entering `arm_vm_init`; 8: leaving bootstrap TTBR0.
- `SWEET EARLY PANIC:` prints a copy of panic arguments before normal panic handling needs per-CPU/thread state.
- In patches 0010/0011 the console disabled before TTBR0 invalidation; patch 0012 replaces that with a persistent kernel VA alias and diagnostics through normal serial/kprintf startup (see its legend above).
- Screen contents require a physical observation from the user: adb cannot capture a framebuffer after Android has been replaced by a bare-metal kernel.

## QEMU emulation (2026-10-15)
- `tools/mkadt.py OUT.adt --qemu` = same ADT with QEMU virt GICD 0x08000000 / GICR 0x080a0000 / CNTFRQ 62.5 MHz.
- `tools/qemu-sweet.py PAYLOAD OUTDIR [--secs N] [--hmp "x /8gx ADDR"] [--gdb]`: host-side copy of shim/loader.c
  (segments + ADT + boot_args at their PAs, 6-instruction stub sets x0 and jumps), `-M virt,gic-version=3 -cpu cortex-a76`
  (same ID_AA64MMFR0 as Kryo 470), 4G RAM so 0x9c000000 fb and 0xb0000000 kernel are plain RAM.
  Outputs `fb.png` (the screen), `regs.txt`, `qemu.log` (exceptions with ESR/FAR/ELR). `--gdb` halts with a stub on :1234
  (system `gdb` + `set architecture aarch64`, `hbreak *VA` works with the MMU on).
- Not emulated: Qualcomm XPUs, ABL, real panel, PSCI conduit (QEMU virt uses hvc). Hardware still decides those.
- Example: `python3 tools/mkadt.py /tmp/q.adt --qemu && python3 tools/mkpayload.py xnu/out-0013/kernel.development.sweet /tmp/q.adt /tmp/pq.bin --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam' && python3 tools/qemu-sweet.py /tmp/pq.bin /tmp/q`

## Layout
- `backup/`  partition dumps + full sdb..sdf LUNs, `SHA256SUMS` (all verified on-device == local)
- `dtb/`     live FDT (from OrangeFox), PixelOS boot.img dtb, dtbo, dts versions
- `shim/`    bare-metal boot shim (`./build.sh` -> `out/shim-boot.img`)
- `research/` mainline sm7150 dts, XNU pe_fiq.c etc, PocketDarwin patches
- `tools/unpack_bootv2.py`

## Device access
- No root in PixelOS 17 (user build, adb root disabled). Root = `fastboot boot recovery/recovery.img` (OrangeFox R12.0_2, temp boot, not flashed).
- `fastboot boot` works for stock boot.img, OrangeFox, and our shim.
- Shim resets itself via PSCI SYSTEM_RESET; IMEM restart_reason 0x77665500 is seen
  (bootreason=bootloader) but ABL still boots Android, so it does not stop in fastboot.

## Hardware facts (live DT + shim measurements)
- Framebuffer (ABL cont splash): 0x9c000000, 1080x2400, stride 4320, a8r8g8b8, panel is DSC *video* mode (no kick needed).
- RAM: 0x80000000+2G, 0x100000000+0xfcc00000. Live reserved-memory = PixelOS base dtb + sweet dtbo fixups.
- GICv3: GICD 0x17a00000, GICR 0x17a60000 (stride 0x20000, 8 PEs). GICD_TYPER 0x007B041D: SecurityExtn=1 (DS=0).
  ABL leaves GICD_CTLR = 0x10 (ARE_NS on, groups off). Writing 0x13 reads back 0x12: EnableGrp1A (bit1) is the one that matters.
- GIC Group 1 VERIFIED (shim 2c): vtimer INTID 19 = 30 IRQs/300ms, ptimer INTID 18 = 30, SGI 1 to self = 10/10.
  ICC_SRE=1 already, ICC_CTLR 0x8440, priority/enable writes stick. GICR_IGROUPR0 and GICD_IGROUPRn read 0 from NS
  even though Group 1 delivery works (RAZ in NS view) - do not use them as a health check.
  INTID 28 shows pending constantly (unknown PPI 12).
- Timer 19.2 MHz. Qualcomm PPI numbering: secure-phys/phys/virt/hyp = PPI 1/2/3/0 -> vtimer INTID 19, ptimer 18 (NOT 27/30).
- PSCI 1.1 via SMC, SMCCC 1.1. CPU_ON ok; SYSTEM_SUSPEND and SYSTEM_RESET2 unsupported.
- SMP: CPU_ON works for all 7 secondaries (MPIDR 0x81000n00).
- Entry EL1, MMU off, x0 = DTB (magic ok). ID_AA64MMFR0 = ...101122: 16K granule YES, 4K YES.
- No serial console: debug UART SE is in SPI mode on sweet. Screen is the only debug channel.

## XNU implications
- XNU GICv3 path (pexpert/arm/pe_fiq.c, VMAPPLE) puts the timer (PPI 27) in Group 0 = FIQ. With SecurityExtn=1,
  Group 0 is secure -> impossible from NS EL1. Need: Group 1 IRQ, ICC_IGRPEN1, INTID 19, and the timer handled in the IRQ path.
- XNU VMAPPLE PSCI uses `hvc`; sweet needs `smc`.
- PocketDarwin NonAppleGenericCpu.patch only swaps -mcpu=apple-h7 for -march=armv8-a.

## Milestone 3 (2026-10-14): 16K MMU verified
- ABL loads the Image at 0x80080000 (base 0x80000000 + 0x80000) regardless of text_offset=0.
- 16K granule, T0SZ=28 (L2 start, 32MB blocks), MAIR 0x44ff00, TCR 0x1409CB51C, PARange=2 (40-bit PA).
- Only own image pages WB (Qualcomm XPUs: never map carve-outs cacheable); fb Normal-NC; rest Device.
- AT S1E1R checks all as expected; unmapped -> translation fault L2.
- Fill fb: 110 ms (MMU off) -> 3.5 ms (NC). Memory loop 70 ms -> 0.7 ms. vtimer + SGI IRQs still fine with MMU on.

## XNU loader design notes (xnu-8796)
- Entry: `_start` -> `start_first_cpu`, x0 = boot_args (PA), MMU off, EL1. boot_args: Revision/Version 2,
  virtBase/physBase/memSize/topOfKernelData, Boot_Video (fb 0x9c000000, rowBytes 4320, 1080x2400, depth 32),
  deviceTreeP/Length (ADT), CommandLine[608], bootFlags, memSizeActual.
- start.s maps [kernel mach header .. physBase+memSize) with cacheable BLOCKS. On Qualcomm every XPU carve-out
  must therefore lie below the kernel: use physBase = 0xb0000000 (above ramdump_fb 0xaf000000+16M,
  cdsp_sec, qseecom), end before rmtfs 0xfde01000 -> memSize ~0x4d000000 for bring-up.
- Base ADT: iPhone 8 d20ap DeviceTree from iOS 16.7.16 IPSW (tools/remotezip.py + tools/adt.py; LZFSE payload).
  Template values are empty: the loader must fill chosen/*, memory-map, cpu*/reg etc. like iBoot.
- XNU build: GitHub Actions (dipa-studio/ios-sweet, xnu.yml) via blacktop/darwin-xnu-build, MACOS_VERSION=13.5.
  New-clang fixes in xnu/patches; Xcode 16 iig is incompatible (IORPC.kernelContent) -> macos-14 runner, oldest Xcode.

## Kernel architecture plan (SWEET machine config)
- First stock build (VMAPPLE) is arm64e (PAC). Kryo 470 = ARMv8.2, no PAC -> SWEET must build plain arm64
  (HAS_APPLE_PAC is only defined for __arm64e__, so arm64 drops PAC automatically).
- Template: VMAPPLE (16K pages, GICv3 code in pe_fiq.c) minus APPLEVIRTUALPLATFORM hypercalls/paravirt PAC,
  plus BCM2837 (generic ARM: no APPLE_ARM64_ARCH_FAMILY Apple sysregs). Do not link the KDK lib<cfg>.os.*.a.
- Timer: XNU expects it on FIQ (sleh_fiq -> rtclock_intr). SWEET: GIC Group 1, so sleh_irq must ack ICC_IAR1,
  route INTID 19 to the same timer path, SGIs to cpu_signal_handler, EOI via ICC_EOIR1.
- SMP/interrupts: open-source AppleARMSMP (USE_APPLEARMSMP) needs an IOInterruptController (AIC role) and an
  IOPMGR (CPU power -> PSCI CPU_ON via SMC). Plan: built-in IOKit classes (SweetGIC, SweetPMGR, platform
  expert) compiled into the kernel and matched via iokit/KernelConfigTables.cpp -> no closed kexts needed.
- Debug: xnu patch 0003 sweet-fbcon = kprintf drawn on the framebuffer (ADT node "sweet-fbcon").
- ADT /arm-io ranges must not map to parent 0 (pe_arm_get_soc_base_phys()==0 disables serial): use parent 0x1000000.

## Status 2026-10-14 late: first XNU boot attempt
- SWEET kernel builds (CI run 38635343732, patches 0001-0009). Verified offline: arm64, no PAC, no >v8.2 code,
  70300 classic relocs all in range (slide 0 = no loader work), entry = __start.
- Boot: shim loader worked (initrd 0x846F0000-0x855FE3E0, BOOTARG 0xB1FF8000, JUMP 0xB146A488) but screen stayed
  red -> xnu died/hung before serial_init (fbcon). kprintf is a no-op before STARTUP_SUB_KPRINTF.
- Historical next-step plan (patch 0010, now implemented and built; see active handoff above): early fb debug channel
  1. start.s start_first_cpu (after mov x20,x0 / mov x21,#0): PA marker band (MMU off, direct store to 0x9c000000).
  2. start.s after both create_bootstrap_mapping calls: add ONE block entry to the existing V=P L2 table
     (x25 + 2*PGBYTES; do NOT call create_bootstrap_mapping again: fb and RAM share L1 entry 0, it would replace
     the L2 table). Entry = TYPE_BLOCK|VALID|SH(OUTER)|ATTRINDX(CACHE_ATTRINDX_WRITECOMB=1)|AF|PNX|NX|0x9c000000,
     index = (va & ARM_TT_L2_INDEX_MASK) >> ARM_TT_L2_SHIFT.
  3. new osfmk/arm64/sweet_early.c (+files.arm64 entry): sweet_early_mark(n) colored bands at screen bottom and
     sweet_early_putc using iso_font via VA=PA while the bootstrap TTBR0 is live; disable before
     arm_vm_init's set_mmu_ttb(invalid_ttep) (arm_vm_init.c:2148).
  4. markers in arm_init after PE_init_platform, ml_parse_cpu_topology, cpu_bootstrap, kernel_startup_bootstrap,
     cpu_init, before arm_vm_init.
  5. panic hook at top of panic_trap_to_debugger: va_copy + _doprnt(fmt, &copy, sweet_early_putc, 16).
  Verify with tools/xnu-cc.py (compile+symbols) and patch-order simulation before the single CI run.

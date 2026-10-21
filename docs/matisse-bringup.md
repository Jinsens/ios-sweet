# MT6983/matisse bring-up checkpoints

This is an **early kernel port**, not a bootable iOS release. Do not write an
`ios16-root` image to userdata until XNU itself has a stable diagnostic path.

## Measurements from the attached matisse (2026-10-21)

- LK supports `download`/`flash`, but not temporary `fastboot boot`.
- Known-good backup uses boot header v4, gzip Linux Image, and stock ramdisk.
  Recovery is supplied by vendor_boot; leave it unchanged.
- Same-boot hardware entry probe reports EL1, MIDR 0x410fd462, MPIDR
  0x81000000, CNTFRQ 13,000,000, x0=DTB at 0x47c80000,
  SCTLR_EL1=0x30d00980 (MMU/data/instruction caches off).
- The **actual shim board_probe** also completes on hardware: GICD
  0x0c400000, virtual timer INTID 27, framebuffer 0xfcb3f000. It returns to
  OrangeFox using the original Linux kernel in the same boot.
- A probe adding direct PL/LK log_store access **does not return to recovery**.
  The cause inside that extra path is not established. Do not enable it as a
  normal log channel. `fastboot oem lkmsg` is a different kernel/UART log.
- The DTB's panel-name string disagrees with the recovery's Android command
  line: do not infer a working DSI command sequence from the name alone.

Device dumps, images, private hardware identifiers and downloaded third-party
artifacts belong in gitignored `backup/` or `research/`, never in commits.
See NOTES.md for the exact current device state and artifact hashes.

## Same-boot entry probe

`tools/mkentryprobe.py` wraps a supplied known-good boot v4. The wrapper reads
entry registers, optionally exercises the real shim FDT reader, records the
results into the bundled Linux banner, then jumps to that original Linux
kernel at a 2 MiB-aligned address. It does **not** flash anything itself. Its
Linux ramdisk and boot signature block are preserved byte-for-byte, although
the modified image is no longer signature-valid and needs an unlocked device.

This avoids assuming that DRAM logs survive another preloader/LK boot. The
wrapped kernel must allow placement anywhere and have text_offset=0. The
builder refuses an ambiguous/too-small banner or truncated boot data.

Example, from the repository root (substitute your installed LLVM directory):

```sh
python3 tools/mkentryprobe.py \
  --template backup/boot_a-stock-trim.img \
  --out shim/out/entryprobe-board.img \
  --level board \
  --toolchain "$LLVM_BIN" \
  --max-image-size 0x7c80000
```

The load-window bound above is for this measured matisse only (kernel at
0x40000000, DTB at 0x47c80000), not a generic MT6983 constant. The output
manifest records hashes, original banner, layout and stage.

Available stages:

| Level | Behavior | Hardware status |
| --- | --- | --- |
| `entry` | Register capture, no C/stack needed | PASS, returns to recovery |
| `board` | Actual shim FDT/board parsing | PASS, stage 2 / fault 0 |
| `logstore` | Adds direct log_store memory access | FAIL to return; opt-in experiment only |
| `mmu` | Exercises shim 16 KiB mappings and restores MMU-off entry | PASS, stage 5 / fault 0 |
| `timer` | Reads GIC; arms/restores masked virtual timer, observes PPI27 pending | PASS, stage 7 / pending 0->1 |
| `irq` | Saves/configures GIC, enters actual IRQ vector, EOI, restores state | PASS, stage 9 / rc0 / count1 / INTID27 |

The exception fallback is best-effort. In particular, secure-access failures,
asynchronous external aborts and watchdogs are not guaranteed recoverable.
Do not label a stage hardware-tested merely because an emulator passed it.

## Each device test

1. Confirm product/slot/unlocked state and preserve full boot_a, vendor_boot,
   DTBO, LK/AVB and calibration backups. Keep a SHA256 manifest.
2. Ask for confirmation of the specific partition write. Only boot_a is used
   for these probes; do not touch preloader, LK, userdata or GPT.
3. Flash the **trimmed** image, not a zero-padded partition-sized file. On
   this LK a 64 MiB fastboot transfer previously wedged USB.
4. Read back the entire written prefix and compare it, then reboot recovery.
5. Read `dmesg` with `adb exec-out`; search for `SWEET-ENTRY`. Do not use a
   reset-dependent old log as evidence of this boot.
6. Restore the original backup when finished and verify the full partition.
   A failed test needs Power+VolumeDown to fastboot, then restoration.

macOS platform-tools failed on LK response reads in this setup. The tested
fallback is `python3 tools/fbraw.py`, using pyusb+libusb. USB access requires
an unsandboxed command. Do not publish `fastboot devices` output: it includes
a serial number.

## Safety fixes in the shim

- `dram_log()` must never copy text into the packed 256-byte log_store
  **header**. `logstore_write()` is append-only and checks both buffer sizes,
  offsets, overflow and tag bounds. It has host ASan/UBSan tests, but direct
  access is still **not a working hardware log channel**.
- Normal builds do not enable that channel. `SHIM_LOGSTORE_MARK` explicitly
  opts into the experimental direct memory accesses.
- `SHIM_STORAGE_LOG` is required to enable raw partition log writes and the
  `misc` BCB rewrite. Never use them implicitly while testing reads.

Host checks:

```sh
python3 -m unittest discover -s tools/tests -v
clang -O1 -g -fsanitize=address,undefined -DLOGSTORE_HOST_TEST -Ishim \
  -Wall -Wextra -Werror shim/host_logstore.c shim/logstore.c \
  -o shim/out/host_logstore
shim/out/host_logstore
```

## XNU payload profile (CI-built; not yet hardware-booted)

`tools/mkadt.py OUT --board matisse` now supplies the measured single-core
A510 / 13 MHz / INTID27 profile, correct GIC region sizes, framebuffer
geometry and the 0x100000000..0x22e000000 kernel RAM window. It retains
`iPhone10,3` / `D22AP` identity, 1125x2436 logical display canvas, and the
`xiaomi,sweet` compatibility string required by the existing platform expert.
It does **not** expose the Qualcomm UFS host. This is not a daumier profile.

`mkpayload.py` recognizes that ADT and automatically requires/adds `cpus=1
sweetkeys=0 sweettouch=0 sweetbattery=0 sweetusb=0 ufsrw=0 -noprogress`.
Conflicting arguments or truncation are rejected; sweet/QEMU behavior is
unchanged. These flags are a guard, **not** proof that the existing XNU is
safe to boot on MediaTek.

Patch 0082 now replaces that early path with validated boot_args.Video
geometry and a final-TTBR1 mapping. CI run 39595694178 built the DEVELOPMENT
SWEET Mach-O as plain arm64. A `matisse-xnu.payload` and v4 shim boot image
were packaged locally, but neither has been booted on the phone.

**Remaining blocker: actual MT6983 command-mode DSC refresh.** The LK-entry
read-only probe found DSI0 command mode but DSI_EN clear; OVL has the real
scanout address. A one-shot direct DSI/mutex trigger was unsafe: recovery
later logged 129 CMDQ software-timeout pre-dump records. The waits name token
641, which Xiaomi's MT6983 header identifies as `CMDQ_SYNC_TOKEN_STREAM_EOF`,
not the DSI TE hardware event. The stock baseline had none; causation is not
isolated. Do not use direct DSI_START/DSI_EN toggles.

The live probe recorded DSI0 `CON_CTRL=0`, `MODE_CTRL=0`, `START=1`,
`INTSTA=4`, `CMDQ_SIZE=0x8001`, `SIZE_CON=0x0c8001e0`, and OVL0_2L
`L0_ADDR=0xfcb3f000`, `L0_PITCH=0x1680`, `EN=0x00e00001`. It reached its
final stage without an exception, but the two mutex output fields were
`ffffffff`. The v1 probe used that same bit pattern for FDT/range lookup
failure, so the old fields are ambiguous and do not establish the mutex state. The panel compatible is
`l11_38_0a_0a_dsc_cmd,lcm`; its vendor path includes a WQHD DSC command table,
power rails/reset GPIO, DSI/PHY configuration and the display/GCE pipeline.
None of that initialization is implemented in XNU yet. The framebuffer
console patch alone therefore does not establish visible screen logs.

### XNU Boot_Video console (patch 0082, 2026-10-21)

The early XNU console now gets PA/width/height/stride/depth from `boot_args`
Video, not Qualcomm constants. Assembly uses generated Video offsets and
walks the actual bootstrap root to preserve high-bank RAM mappings. It maps
only visible pixels (not the entire triple-buffer reservation), handles two
L2 blocks, and preserves the physical block offset across TTBR1 relocation.
Malformed surfaces or translation failures stay headless. This **does not
initialize/trigger the MT6983 command-mode DSC display**.

Compile-checked as real arm64 Mach-O objects against run 39158169212's build
tree: `start.s`, `sweet_early.c`, `arm_init.c`, `arm_vm_init.c`, `pe_serial.c`.
For descriptor-level regression checks using the *actual preprocessed assembly*:

```
python3 tools/check-xnu-video.py --tree BUILDTREE --log CI.log \
  --llvm NDK_LLVM_BIN --python-deps UNICORN_DIR
```

This test is not a substitute for hardware MMU or screen validation.

### 2026-10-21 CI and LK display snapshot

- GitHub Actions run `39595694178` succeeded for head
  `bfd7eb60fe0602f85bfc81bc33a18b4f23c03505`.
- `kernel.development.sweet`: 17,206,200 B, SHA256
  `911db7b3c3616face11ccc730869e12ccf0c72ba5efaee8f3eedd01af7cfc367`,
  Mach-O `arm64` (not arm64e).
- `matisse-xnu.payload`: 16,605,200 B; v4 shim test image:
  `shim/out/shim-boot.img`, 17,883,136 B. The payload has the one-core and
  all MediaTek-incompatible-driver-disable boot args. Neither image has been
  flashed or booted.
- LK-entry snapshot: DSI0 CON_CTRL `0`, MODE_CTRL `0`, START `1`, INTSTA
  `4`, CMDQ_SIZE `0x8001`, SIZE_CON `0x0c8001e0`; OVL0_2L L0 address
  `0xfcb3f000`, pitch `5760`, EN `0x00e00001`. Mutex fields were unresolved.
- An experimental one-shot direct DSI/mutex trigger was followed by 129 CMDQ
  software-timeout pre-dump records. Token 641 is
  `CMDQ_SYNC_TOKEN_STREAM_EOF`, not the DSI TE event; the saved stock baseline
  had no CMDQ software-timeout pre-dumps. Causation is not isolated. The test
  did not prove visible refresh, was stopped, and `boot_a` restored/read-back
  verified. Do not repeat the direct START/EN sequence.

The `display` entry-probe level is read-only: it resolves MMIO windows
through the live FDT and records register pairs in the stock Linux banner. It
does not enable clocks or write DSI/OVL/mutex/GCE registers, and it does not
prove that CPU framebuffer writes can reach the panel. Its v1 mutex fields
were ambiguous because raw `0xffffffff` and lookup failure used the same
encoding.

### Read-only display preflight v2 (host-built; not device-tested)

The revised snapshot adds a two-bit status per register pair (0 = compatible
missing, 1 = reg/range invalid, 2 = both MMIO reads completed; thus a raw
all-ones read is distinguishable from lookup failure). It captures both
register windows described by `disp_mutex0` at `0x14001000` and `0x14401000`,
and snapshots GCE display trigger-loop threads 3 and 5 at the live-FDT GCE
base: each pair is `CURR_STATUS` and `WAIT_TOKEN`. These are **read-only**
register reads; there is no trigger, doorbell, token write, DSI start, or clock
write.

Host decoding of the saved `research/matisse/20261021-live/live.dtb` resolves
`/disp_mutex@14001000` with those two mutex register windows, and resolves the
GCE node at `0x1e980000`. This proves the FDT parser can identify the nodes,
not that either powered hardware block returns meaningful values. The rebuilt
`shim/out/entryprobe-display-v2.img` is 20,832,256 bytes, SHA256
`a0d33cfa174920eb8da48c78bb956dc40d6345689a24e38f8700addf2c2d2183`. All
seven levels cross-compile and the host suite has 21 passing tests. It has not
been transferred, booted or flashed; fastboot 37.0.0 saw zero visible devices
in the 2026-10-21 host query, so there is no new hardware measurement.

For source-driven porting, the ignored Xiaomi MT6983 source snapshot records
a 1440x3200 WQHD panel DSC command table and VDDI/DVDD/VCI/reset sequencing.
It also distinguishes GCE DSI0 TE hardware event 358 from software
`STREAM_EOF` token 641. None of those power/PHY/DSC/refresh writes has been
ported or hardware-validated in XNU. Do not repeat direct `DSI_START`/`DSI_EN`
toggles; obtain explicit confirmation before any boot-partition write.

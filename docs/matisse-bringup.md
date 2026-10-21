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

## XNU payload profile (not yet boot-tested)

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

**Next kernel blocker:** XNU patches 0010/0012 still directly write/map
0x9c000000 and assume 1080x2400 before normal ADT framebuffer discovery.
Those must be replaced with boot_args Video-derived mappings/geometry or
an explicitly headless path **before** flashing an XNU payload. Actual
command-mode DSI refresh remains unimplemented. No XNU build/boot or iOS
launch is claimed by the successful shim probes.

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

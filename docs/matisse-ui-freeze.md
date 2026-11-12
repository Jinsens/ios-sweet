# matisse UI freeze investigation (2026-11-13)

The user reports that, roughly one minute after entering iOS, touch, status-bar
interaction and the power button stop responding. Interaction sometimes resumes
briefly, then stops again; apps do not visibly open. Root cause is not yet proven.

## What the 2521 console establishes

Source: ignored `research/matisse/20261109-resident-gpu/shim2521-ui/gpu-normal/after-console.txt`.
The boot arguments identify trial2521 and retain the verified2518 display fix.
The fresh root snapshot also has a launchd.log explicitly identifying2521: it
spawns Preferences[246] at105.669s, with no exit recorded through169.902s.

- Frame188 finishes at115.242s; frame189 finishes at139.174s: a23.932s gap.
  During this gap, multiple complete touch DOWN/UP pairs and a complete power-key
  DOWN/UP reach SweetHID. This demonstrates continued kernel input activity,
  not that backboardd consumed every event.
- A600-vsync interval in that gap reports zero new swaps/presents. Its timer
  remains at about60Hz. DSIM still reports all382 previous transfers completed,
  zero underruns and zero truncated holds. There is no synchronous swap timeout.
- GPU resident status remains ready=1, submits=3347, failures=0 during the gap.
  There is no reported CSF fault/fatal/AS fault. This does not prove that every
  userspace fence or service has progressed.
- Preferences is actually exec'd at console line3130, immediately after a
  valid touch. No Preferences crash or code-signing denial is recorded. An app
  process starting does not establish that its scene became ready or visible.
- The later frame628 at169.526s to frame629 at181.031s gap follows power and
  volume-down DOWN events with no UP before reset. It is confounded by the
  user's return-to-fastboot chord and must not independently prove deadlock.
- SPI FIFO ETIMEDOUT errors recover repeatedly and predate the first freeze.
  Many valid touches follow them. They remain an input reliability issue but
  are not established as the cause of the shared touch/key/UI symptom.

The evidence supports a pause in the userspace UI/application scene path while
kernel timers and input reporting remain alive. It does not yet identify which
thread or dependency caused that pause, nor exclude a userspace GPU wait.

## Existing sample misses the pause

The only2521 sample is at139.595s, after rendering resumes. Using the exact20H392
cache and slide0x58c8000:

- SpringBoard's main thread and UIEventFetcher are in ordinary CFRunLoop waits.
- A SpringBoard background worker waits on CoreLocation XPC
  (`CLGetStatusBarIconState` / `CLCopyTechnologiesInUse` / UNSLocationMonitor).
- Another worker converts and ASTC-encodes an application snapshot.
- A backboardd worker is in kern_SwapEnd, expected with sweetfb_syncswap=1.
  Another awaits a dispatch group from its executable offset0x18a78.

These are a post-recovery snapshot, not a causal freeze stack. Preferences,
runningboardd and lsd were not sampled. Historical lsd device-identity blocking
was fixed in2312; it must not be assumed to have returned without fresh evidence.

## Confirmed wait-interface defect

The Darwin transport returned -1/errno from drmSyncobjTimelineWait, whereas
libdrm's public syncobj-wait contract returns negative errno values. Mesa checks
for -ETIME when probing busy buffers. Its misleading
`DMA_BUF_IOCTL_EXPORT_SYNC_FILE failed (err=-1)` diagnostic can originate from
this *nonshared* timeline-wait branch; it is not evidence of a failing dma-buf
ioctl. Generic `export failed` errors are separate, longstanding sync-file
compatibility limitations.

The source correction normalizes the public wait result to -errno (deadline
expiry is -ETIME) and retries bounded internal GPU polls until the caller's
absolute deadline. A5s internal poll timeout previously terminated even a longer
or infinite wait. Actual-C transport tests cover the return ABI, finite/infinite
chunk retries, final expiry with the fence retained, and immediate real errors.
This correction is not installed on the device and is not a proven freeze fix.

Seven Preferences watchdog reports in the fresh snapshot belong to older
kernels: none of their stackshot kernel UUIDs matches frozen2521
`846316F9-13E0-3D23-8F94-0E8BCA41AB33`. Two old reports show Settings/About
waiting in SCNetworkInterface enumeration / EthernetWatcher / IOKit quiescence.
That is a testable historical dependency, not evidence that2521 shares that
block or that it explains the whole UI pause.

Large offscreen surfaces also attempt physically contiguous allocation and then
fall back to scattered memory. The failed1150-page scan/GC in the freeze window
is included in a logged43.108ms allocation, so that measured allocation cannot
alone account for the entire23.932s pause. No memory-exhaustion claim is justified.

## Prepared trial25f1

Ignored directory: `research/matisse/20261113-ui-freeze/`.
Build/preparation is complete; hardware execution requires separate approval.

- Image `shim25f1/matisse-shim-gpu25f1.img`,26750976B, SHA256
  `ffa884726796c55a18aceaf92146c9c07435dfa369f9534a64c370682ad2fa2a`.
- Frozen2521 kernel SHA256
  `551ff209b023f0dedb8f4183c0d59837617cbb0643fcfb9998dc397ceec61dd2`.
  Exact payload rebuild verifies unchanged kernel bytes. Mesa and apps are not
  installed or replaced by this trial.
- Sample SpringBoard,backboardd,runningboardd,lsd,Preferences every10s;
  first65s after sampler start (expected uptime~114.6s, not an absolute65s).
  This covers the previously missed115–139s interval and later interaction.
- Keep gpuasync1 and sweetfb_syncswap1/vendor1/hold14500/lead300/native60.
  Remove only optional frame/slow/screenshot/mark/symbol/spam diagnostics to fit
  the embedded602-byte command line. Exact diff is runtime-flags-diff.json.
- Local image build exit0, no new CI run. Stock Linux fallback, ramdisk and
  signature byte identity checked. Automatic-return configuration remains180s;
  actual automatic recovery is not promised by a static configuration check.
- Expected full64MiB boot_a during trial:
  `ba0445f551f5437630ab71fa94ee89112afd07a9df8219ed0e744bd8228df178`.
  Restore backup remains `backup/boot_a-before-2499-20261110.img`, SHA256
  `c4b70dafb0b8a69ce59f6a246c7bab5412827c80306dde6113cc483b9ec707d8`.
- Copied runner retains the shared device lock, exact-image CLI approval,
  serial/identity/geometry checks, whole-partition written readback and full
  backup restoration with independent final Recovery hash. Independent review
  passed; read-only device preflight exit0. No trial write has occurred.
- Normal iOS runtime retains rootrw1/ufsrw1 and writes filesystem metadata;
  this is not a read-only-root trial. No offline root delta is involved.

A fresh16GiB current-root snapshot was verified against all128 device chunks in
210s, copying20 drifted chunks. It is local/ignored and required no device writes.
Use this current snapshot for future analysis; do not reverse an old metadata
delta after UI boots.

Next: execute only after the human approves the exact25f1 trial; interact until
stall, allow samples, recover logs and verify full boot_a restoration; symbolize
repeated frozen-window stacks and follow the blocking service dependency.

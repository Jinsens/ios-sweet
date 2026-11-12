# Display sources

These are the files the kernel is actually built from for the matisse panel,
kept here because `xnu/out/` (the build tree, and the only other copy) is
ignored by git. The build compiles the copies in

    xnu/out/local/xnu/osfmk/arm64/sweet_matisse_dsi.{c,h}
    xnu/out/local/xnu/iokit/Kernel/arm/SweetFramebuffer.cpp

so an edit here has to be copied there before `tools/build-xnu-clt.sh`.

They are not carried by a patch yet: `patches/0083`, `0095`, `0098` and
`0112` created the file at earlier points, and everything since (the OVL
scanout path, the frame-source stop, the counters) lives only in this
directory. A CI kernel therefore does not have the current driver.

Last synced from the build tree at trial 2512 (2026-11-10). Since 2496 this
also carries the corrected DSIM command queue offset (0xd00, per the vendor's
mt6983 table), the DCS 0x45 scan-line read, the 300 us lead between MUTEX_EN
and DSI_START, the absolute-deadline 60 Hz vsync schedule (`sweet_vsync_arm`),
the OVL shadow-bank switch (`sweetfb_shadow`, LK leaves BYPASS_SHADOW on), the
TE status-latch probe, and `sweetfb_hold_us` with its `trunc` counter.


Trial 2513 (2026-11-13, pending hardware validation) corrects the MT6983
`DSI_INTSTA` **write-zero-to-clear** protocol. All acknowledgements now use
`~flags`, preserving unrelated events. The old direct-mask writes never
cleared the requested latch, so earlier microsecond TE “periods” are invalid.
TE measurement is enabled by default (`sweetfb_teint=0` disables it); seven
fresh edges must give six consistent 30–120 Hz intervals. When measured,
both OVL addresses are staged with both mutexes stopped, a fresh TE is
awaited, then a complete frame is triggered and held. A TE timeout keeps the
last frame and retries, without a CPU-copy fallback. Console refresh also
uses TE. `sweetfb_tealign=0` disables measured-TE alignment for comparison.
The existing asynchronous shadow path is retained when no valid TE is found.
No new DCS panel command is enabled by this change; the optional scan-line
read remains experimental (its receive-packet parsing/RACK need separate work).

Run `python3 -m unittest discover -s tools/tests -p test_matisse_display.py`
for the W0C/TE/frame-order MMIO regression model. It executes the actual
driver source with simulated register access and time; it cannot prove the
physical panel is free of flicker.


Trial 2514 adds a 100 us bound on observed TE age. A high latch after a long
polling gap is acknowledged and discarded, with the original deadline kept.
The short launch section preserves IRQ state and retries a fresh TE if the
thread was delayed before it could enter that section. The configured lead
is capped at 300 us so the launch cannot hold interrupts off for milliseconds;
TE wait and the frame hold remain interruptible. `te late poll/launch` and
`accepted age/start` logs expose these timing limits. Regression coverage is
now 17 cases, including injected scheduling delays and IRQ restoration.


Trial 2515 adds opt-in `sweetfb_vendor=1` for the l11 vendor DSIM launch
sequence: clear per-line wait, enable NULL/HS while retaining LK packet
length/CPHY bits, update only CMDQ size fields, pulse reset, then START.
The default remains the 2514 path. CMCTL and fresh CMD_DONE observations
measure completion. An active CMCTL/BUSY extends the hold with a bound and
is never deliberately cut off at that bound; a later present waits for idle
before stopping the pipeline. HS is cleared only after completion evidence.
Trial 2515 uses a 14.5 ms minimum hold because NULL packets can lengthen the
frame. The MMIO regression suite now has 27 cases.

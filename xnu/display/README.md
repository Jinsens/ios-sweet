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

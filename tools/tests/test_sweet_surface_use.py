"""Execute the real IOSurface in-use and framebuffer ownership code.

The mock object model checks retain/release and lock balance; ASan/UBSan check
record bounds and shared-page accesses. No kernel implementation is duplicated.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    """Extract one complete top-level function, keeping its actual body."""
    match = re.search(r'(?m)^[^\n]+\n' + re.escape(name) + r'\(', source)
    if not match:
        raise AssertionError('Missing production function: ' + name)
    opening = source.index('{', match.end())
    depth = 0
    # These functions have no braces in literals; count their real block scopes.
    for at in range(opening, len(source)):
        if source[at] == '{':
            depth += 1
        elif source[at] == '}':
            depth -= 1
            if not depth:
                return source[match.start():at + 1]
    raise AssertionError('Unclosed production function: ' + name)


PRELUDE = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <functional>
#include <string>
#include <vector>
using IOReturn = int;
enum { kIOReturnSuccess, kIOReturnBadArgument, kIOReturnNotFound };
#define SWEET_SURFACE_MAX_ID 4096
#define SWEET_SURFACE_STATE_USE 0x18
#define SWEET_FB_SWAP_REC_SIZE 0x46c
#define SWEET_FB_LAYERS 4
static void IOLog(const char *, ...) {}
#define OSSafeReleaseNULL(p) do { if (p) { (p)->release(); (p) = nullptr; } } while (0)
struct IOLock { bool held = false; unsigned locks = 0, unlocks = 0; };
static void IOLockLock(IOLock *l) { assert(l && !l->held); l->held = true; l->locks++; }
static void IOLockUnlock(IOLock *l) { assert(l && l->held); l->held = false; l->unlocks++; }
struct OSObject {
    int refs = 1; bool freed = false;
    virtual ~OSObject() = default;
    virtual void free() { assert(refs == 0 && !freed); freed = true; }
    void retain() { assert(refs > 0 && !freed); refs++; }
    void release() { assert(refs > 0 && !freed); if (--refs == 0) free(); }
};
struct Mapping : OSObject {};
struct SharedState {
    alignas(8) uint8_t bytes[64] = {};
    void *getBytesNoCopy() { return bytes; }
};
struct PixelMemory { uint64_t getLength() { return 16; } };
struct SweetSurface : OSObject {
    uint32_t id = 0, useCount[2] = {};
    uint32_t width = 2, height = 2, bytesPerRow = 8, offset = 0,
        bytesPerElement = 4, elementWidth = 1, elementHeight = 1,
        pixelFormat = 0x42475241;
    uint32_t pixels[4] = {};
    uint64_t phys = 0x100001000ULL;
    PixelMemory ownMemory;
    PixelMemory *memory = &ownMemory;
    uint64_t physicalAddress() { return phys; }
    uintptr_t kernelAddress() { return (uintptr_t)pixels; }
    SharedState ownState;
    SharedState *state = &ownState;
};
static IOLock tableLock;
static IOLock *sweet_surface_lock = &tableLock;
static SweetSurface *sweet_surface_table[SWEET_SURFACE_MAX_ID] = {};
static uint32_t sweet_surface_users[SWEET_SURFACE_MAX_ID] = {};
struct SweetSurfaceRef : OSObject {
    SweetSurface *surface = nullptr;
    Mapping *data = nullptr, *state = nullptr;
    uint32_t count = 1, useCount[2] = {};
    void free() override;
};
struct OSArray {
    std::vector<SweetSurfaceRef *> values;
    unsigned getCount() { return (unsigned)values.size(); }
    SweetSurfaceRef *getObject(unsigned i) { return values.at(i); }
    void removeObject(unsigned i) { auto *p = values.at(i); values.erase(values.begin()+i); p->release(); }
    void flushCollection() { while (!values.empty()) removeObject(0); }
};
struct IOSurfaceRootUserClient {
    IOLock ownLock;
    OSArray ownRefs;
    IOLock *fLock = &ownLock;
    OSArray *fRefs = &ownRefs;
    bool terminated = false;
    void terminate() { terminated = true; }
    SweetSurfaceRef *findRef(uint32_t, unsigned *);
    IOReturn changeUseCount(uint32_t, uint32_t, int);
    IOReturn trapIncrementUse(void *, void *, void *, void *, void *, void *);
    IOReturn trapDecrementUse(void *, void *, void *, void *, void *, void *);
    IOReturn trapRelease(void *, void *, void *, void *, void *, void *);
    IOReturn clientClose();
};
'''

DISPLAY_MOCKS = r'''
#define SWEET_FB_FORMAT_BGRA 0x42475241
static uint32_t sweet_fb_swaps_logged;
static uint64_t sweet_fb_swap_count;
static int sweet_console_muted;
static uint64_t fakeClock;
static uint64_t mach_absolute_time() { return ++fakeClock; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns = t * 1000; }
static bool PE_parse_boot_argn(const char *, void *, size_t) { return false; }
static bool sweet_disp_shadow_on, sweet_disp_vendor, sweet_disp_te_sync;
static bool sweet_disp_te_align = true;
static uint64_t sweet_disp_te_period = 16500;
static IOLock *displayLock;
static unsigned wakes, sleeps;
static std::function<void()> sleepHook;
enum { kMillisecondScale = 1000000, THREAD_UNINT = 0, THREAD_TIMED_OUT = 1 };
static void clock_interval_to_deadline(unsigned n, unsigned scale, uint64_t *deadline) {
    assert(n == 1000 && scale == kMillisecondScale); *deadline = 1000000;
}
static void IOLockWakeup(IOLock *lock, void *, bool one) {
    assert(lock->held && !one); wakes++;
}
static int IOLockSleepDeadline(IOLock *lock, void *, uint64_t deadline, int flags) {
    assert(lock->held && deadline == 1000000 && flags == THREAD_UNINT); sleeps++;
    IOLockUnlock(lock);
    if (sleepHook) sleepHook();
    IOLockLock(lock);
    return sleepHook ? 0 : THREAD_TIMED_OUT;
}
struct Timer {
    bool disabled = false, cancelled = false;
    void disable() { assert(!displayLock->held); disabled = true; }
    void cancelTimeout() { assert(!displayLock->held && disabled); cancelled = true; }
};
struct WorkLoop {
    unsigned removals = 0;
    void removeEventSource(Timer *t) {
        assert(!displayLock->held && t->disabled && t->cancelled); removals++;
    }
};
struct AppleCLCD {
    IOLock ownLock;
    IOLock *fLock = &ownLock;
    Timer ownTimer;
    WorkLoop ownWorkLoop;
    Timer *fVsync = &ownTimer;
    WorkLoop *fWorkLoop = &ownWorkLoop;
    bool fVsyncAttached = true;
    uint64_t fPhys = 0x100000;
    sweet_fb_frame fPending = {};
    bool fDirty = false, fConsolePaused = true, fScanout = true, fStopping = false;
    bool fSyncSwap = false, fPowerOff = false;
    uint64_t fSwapSerial = 0, fInFlightSerial = 0;
    uint32_t fWidth = 2, fHeight = 2, fLogicalW = 2, fLogicalH = 2, fRowBytes = 8;
    int32_t fOffX = 0, fOffY = 0;
    uint32_t pixels[4] = {};
    uintptr_t fPixels = (uintptr_t)pixels;
    SweetSurface *fSurface = nullptr, *fShown = nullptr;
    uint64_t fStatSwaps = 0, fStatTried = 0, fStatScans = 0, fStatRepeats = 0,
        fStatSamePhys = 0, fLastScanPhys = 0, fStatRefused = 0, fStatCopyUs = 0,
        fStatCopies = 0, fStatPresents = 0, fStatPresentUs = 0;
    uint32_t fLastScanId = 0;
    int fStatReason = 0;
    void quiesce();
    void swap(const uint8_t *);
    void present(sweet_fb_frame *);
};
static std::function<void(uint64_t)> scanHook;
static std::function<void()> refreshHook;
static int scanResult, scanCalls, refreshCalls;
static int sweet_display_scanout(uint64_t phys, uint32_t stride) {
    assert(stride == 8 && displayLock && !displayLock->held);
    scanCalls++;
    if (scanHook) scanHook(phys);
    return scanResult;
}
static void sweet_display_refresh(int force) {
    assert(force == 1 && displayLock && !displayLock->held);
    refreshCalls++;
    if (refreshHook) refreshHook();
}
'''

CHECKS = r'''
static uint64_t shared(SweetSurface &s) {
    return __atomic_load_n((uint64_t *)(s.ownState.bytes + 0x18), __ATOMIC_ACQUIRE);
}
static void publish(SweetSurface &s, uint32_t id) {
    assert(!sweet_surface_table[id]);
    s.id = id;
    s.phys += id * 4096;
    s.refs = 2; // table plus the first client's mapping
    sweet_surface_table[id] = &s;
    sweet_surface_users[id] = 1;
}
static void attach(IOSurfaceRootUserClient &c, SweetSurfaceRef &r, SweetSurface &s, bool first = true) {
    r.surface = &s;
    c.fRefs->values.push_back(&r); // collection's sole reference to r
    if (!first) { s.retain(); sweet_surface_users[s.id]++; }
}
static IOReturn inc(IOSurfaceRootUserClient &c, uint32_t id, uint32_t category) {
    return c.trapIncrementUse((void *)(uintptr_t)id, (void *)(uintptr_t)category,
        nullptr, nullptr, nullptr, nullptr);
}
static IOReturn dec(IOSurfaceRootUserClient &c, uint32_t id, uint32_t category) {
    return c.trapDecrementUse((void *)(uintptr_t)id, (void *)(uintptr_t)category,
        nullptr, nullptr, nullptr, nullptr);
}
static void record(uint8_t *rec, uint32_t mask, uint32_t a, uint32_t b = 0,
                   uint32_t c = 0, uint32_t d = 0) {
    memset(rec, 0, SWEET_FB_SWAP_REC_SIZE);
    const uint32_t ids[4] = { a, b, c, d };
    memcpy(rec + 0x104, &mask, 4);
    memcpy(rec + 0x54, ids, sizeof(ids));
}
static void dequeue(AppleCLCD &fb, sweet_fb_frame &frame) {
    assert(fb.fDirty && !frame.surfaces[0]);
    IOLockLock(fb.fLock);
    sweet_fb_frame_move(&frame, &fb.fPending);
    fb.fDirty = false;
    IOLockUnlock(fb.fLock);
}
static void finish_display(AppleCLCD &fb) {
    sweet_fb_frame_release(&fb.fPending);
    sweet_surface_end_use(fb.fShown);
    fb.fShown = nullptr;
    assert(!fb.fLock->held && fb.fLock->locks == fb.fLock->unlocks);
}
static void clean_client(IOSurfaceRootUserClient &c) {
    assert(c.clientClose() == kIOReturnSuccess);
    assert(c.terminated && c.fRefs->getCount() == 0);
    assert(!c.fLock->held && c.fLock->locks == c.fLock->unlocks);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    std::string test(argv[1]);
    SweetSurface a, b;
    SweetSurfaceRef ar, br;
    IOSurfaceRootUserClient ca, cb;
    publish(a, 7);
    attach(ca, ar, a);
    if (test == "shared_categories") {
        uint64_t sentinel = 0x1122334455667788ULL;
        memcpy(a.ownState.bytes + 0x10, &sentinel, 8);
        memcpy(a.ownState.bytes + 0x20, &sentinel, 8);
        auto *held = sweet_surface_begin_use(7);
        assert(held == &a && a.refs == 3 && shared(a) == 1);
        assert(inc(ca, 7, 1) == 0);
        assert(shared(a) == 0x100000001ULL && a.useCount[0] == 1 && a.useCount[1] == 1);
        sweet_surface_end_use(held);
        assert(a.refs == 2 && shared(a) == 0x100000000ULL);
        assert(dec(ca, 7, 1) == 0 && shared(a) == 0);
        uint64_t value;
        memcpy(&value, a.ownState.bytes + 0x10, 8); assert(value == sentinel);
        memcpy(&value, a.ownState.bytes + 0x20, 8); assert(value == sentinel);
    } else if (test == "invalid_ids") {
        assert(!sweet_surface_begin_use(0));
        assert(!sweet_surface_begin_use(SWEET_SURFACE_MAX_ID));
        assert(!sweet_surface_begin_use(8));
        sweet_surface_lock = nullptr;
        assert(!sweet_surface_begin_use(7));
        sweet_surface_lock = &tableLock;
        sweet_surface_end_use(nullptr);
        assert(a.refs == 2 && shared(a) == 0);
    } else if (test == "count_bounds") {
        IOLockLock(sweet_surface_lock);
        assert(!sweet_surface_use_locked(&a, 0, -1));
        assert(!sweet_surface_use_locked(&a, 2, 1));
        assert(sweet_surface_use_locked(&a, 0, UINT32_MAX));
        assert(shared(a) == UINT32_MAX);
        assert(!sweet_surface_use_locked(&a, 0, 1));
        assert(!sweet_surface_use_locked(&a, 0, INT64_MIN));
        assert(!sweet_surface_use_locked(&a, 1, INT64_MAX));
        IOLockUnlock(sweet_surface_lock);
        assert(!sweet_surface_begin_use(7) && a.refs == 2 && shared(a) == UINT32_MAX);
        assert(inc(ca, 7, 0) == kIOReturnBadArgument && ar.useCount[0] == 0);
        IOLockLock(sweet_surface_lock);
        assert(sweet_surface_use_locked(&a, 0, -(int64_t)UINT32_MAX));
        IOLockUnlock(sweet_surface_lock);
        assert(shared(a) == 0);
    } else if (test == "client_isolation") {
        attach(cb, br, a, false);
        assert(inc(ca, 7, 0) == 0);
        auto *held = sweet_surface_begin_use(7);
        assert(shared(a) == 2 && a.refs == 4);
        assert(dec(cb, 7, 0) == kIOReturnBadArgument && shared(a) == 2);
        assert(dec(ca, 7, 0) == 0 && shared(a) == 1);
        assert(dec(ca, 7, 0) == kIOReturnBadArgument && shared(a) == 1);
        assert(inc(cb, 7, 1) == 0 && shared(a) == 0x100000001ULL);
        assert(dec(ca, 7, 1) == kIOReturnBadArgument);
        assert(inc(ca, 7, 2) == kIOReturnBadArgument);
        assert(inc(ca, 8, 0) == kIOReturnNotFound);
        sweet_surface_end_use(held);
        assert(shared(a) == 0x100000000ULL);
        clean_client(cb);
        assert(shared(a) == 0 && a.refs == 2 && !a.freed);
    } else if (test == "client_teardown") {
        attach(cb, br, a, false);
        assert(inc(ca, 7, 0) == 0 && inc(ca, 7, 0) == 0 && inc(ca, 7, 1) == 0);
        assert(inc(cb, 7, 1) == 0);
        auto *held = sweet_surface_begin_use(7);
        assert(shared(a) == 0x200000003ULL && a.refs == 4);
        clean_client(ca);
        assert(shared(a) == 0x100000001ULL && a.refs == 3);
        assert(cb.trapRelease((void *)7, nullptr, nullptr, nullptr, nullptr, nullptr) == 0);
        assert(br.freed && !sweet_surface_table[7] && a.refs == 1);
        assert(shared(a) == 1 && !a.freed);
        sweet_surface_end_use(held);
        assert(shared(a) == 0 && a.freed && a.refs == 0);
        assert(cb.trapRelease((void *)7, nullptr, nullptr, nullptr, nullptr, nullptr) == kIOReturnNotFound);
    } else if (test == "frame_enabled_layers") {
        publish(b, 8); attach(cb, br, b);
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 5, 7, 999, 8);
        sweet_fb_frame frame = {};
        assert(sweet_fb_frame_acquire(&frame, rec));
        assert(!memcmp(frame.rec, rec, sizeof(rec)));
        assert(frame.surfaces[0] == &a && !frame.surfaces[1] && frame.surfaces[2] == &b && !frame.surfaces[3]);
        assert(a.refs == 3 && b.refs == 3 && shared(a) == 1 && shared(b) == 1);
        sweet_fb_frame_release(&frame);
        sweet_fb_frame_release(&frame); // releasing an empty owner is safe
        assert(a.refs == 2 && b.refs == 2 && !shared(a) && !shared(b));
        clean_client(cb);
    } else if (test == "frame_invalid_rollback") {
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 0x10, 7);
        sweet_fb_frame frame = {};
        assert(!sweet_fb_frame_acquire(&frame, rec));
        assert(a.refs == 2 && !shared(a));
        record(rec, 3, 7, 999);
        assert(!sweet_fb_frame_acquire(&frame, rec));
        assert(a.refs == 2 && !shared(a));
        for (auto *s: frame.surfaces) assert(!s);
        record(rec, 3, 7, 0); // enabled empty layer does not name a buffer
        assert(sweet_fb_frame_acquire(&frame, rec));
        assert(a.refs == 3 && shared(a) == 1);
        sweet_fb_frame_release(&frame);
    } else if (test == "frame_duplicate_layers") {
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 9, 7, 0, 0, 7);
        sweet_fb_frame frame = {};
        assert(sweet_fb_frame_acquire(&frame, rec));
        assert(a.refs == 4 && shared(a) == 2);
        sweet_fb_frame_release(&frame);
        assert(a.refs == 2 && shared(a) == 0);
    } else if (test == "frame_moves_and_table_removal") {
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        sweet_fb_frame queued = {}, active = {}, retry = {};
        assert(sweet_fb_frame_acquire(&queued, rec));
        sweet_fb_frame_move(&active, &queued);
        assert(!queued.surfaces[0] && active.surfaces[0] == &a);
        assert(a.refs == 3 && shared(a) == 1);
        clean_client(ca); // drops ID-table entry while the queued frame remains valid
        assert(!sweet_surface_table[7] && !a.freed && a.refs == 1 && shared(a) == 1);
        sweet_fb_frame_move(&retry, &active);
        assert(retry.surfaces[0] == &a && !active.surfaces[0]);
        assert(!sweet_fb_frame_acquire(&queued, rec)); // cannot rediscover it by old ID
        sweet_fb_frame_release(&active); // transferred owner must not decrement twice
        assert(a.refs == 1 && shared(a) == 1);
        sweet_fb_frame_release(&retry);
        assert(a.freed && !shared(a));
    } else if (test == "actual_swap_supersede") {
        publish(b, 8); attach(cb, br, b);
        AppleCLCD fb; displayLock = fb.fLock;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        fb.swap(rec);
        assert(fb.fDirty && fb.fPending.surfaces[0] == &a && shared(a) == 1);
        record(rec, 1, 8); fb.swap(rec);
        assert(fb.fPending.surfaces[0] == &b && shared(a) == 0 && shared(b) == 1);
        assert(a.refs == 2 && b.refs == 3 && fb.fStatSwaps == 2);
        record(rec, 16, 7); fb.swap(rec); // bad new frame must preserve the valid pending one
        assert(fb.fPending.surfaces[0] == &b && fb.fStatSwaps == 2);
        finish_display(fb); clean_client(cb);
    } else if (test == "actual_syncswap" || test == "actual_syncswap_retry") {
        AppleCLCD fb; displayLock = fb.fLock; fb.fSyncSwap = true;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        sleepHook = [&] {
            assert(shared(a) == 1 && fb.fDirty && sleeps <= 2);
            sweet_fb_frame active = {};
            dequeue(fb, active); fb.fInFlightSerial = active.serial;
            scanResult = test == "actual_syncswap_retry" && sleeps == 1 ? -4 : 0;
            fb.present(&active); sweet_fb_frame_release(&active);
            assert(shared(a) == 1); // still protected until successful display replacement
            fb.fInFlightSerial = 0;
        };
        fb.swap(rec);
        assert(sleeps == (test == "actual_syncswap_retry" ? 2u : 1u));
        assert(!fb.fDirty && fb.fShown == &a && shared(a) == 1);
        finish_display(fb);
    } else if (test == "actual_syncswap_replaced") {
        publish(b, 8); attach(cb, br, b);
        AppleCLCD fb; displayLock = fb.fLock; fb.fSyncSwap = true;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE], newer[SWEET_FB_SWAP_REC_SIZE];
        record(rec, 1, 7); record(newer, 1, 8);
        sleepHook = [&] {
            assert(sleeps == 1 && shared(a) == 1);
            fb.fSyncSwap = false; fb.swap(newer); fb.fSyncSwap = true;
        };
        fb.swap(rec);
        assert(sleeps == 1 && fb.fPending.surfaces[0] == &b && !shared(a) && shared(b) == 1);
        finish_display(fb); clean_client(cb);
    } else if (test == "actual_syncswap_inflight") {
        publish(b, 8); attach(cb, br, b);
        AppleCLCD fb; displayLock = fb.fLock; fb.fSyncSwap = true;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE], newer[SWEET_FB_SWAP_REC_SIZE];
        record(rec, 1, 7); record(newer, 1, 8);
        sweet_fb_frame active = {};
        sleepHook = [&] {
            assert(sleeps <= 2);
            if (sleeps == 1) {
                dequeue(fb, active); fb.fInFlightSerial = active.serial;
                fb.fSyncSwap = false; fb.swap(newer); fb.fSyncSwap = true;
                assert(fb.fPending.serial != active.serial);
            } else {
                assert(fb.fInFlightSerial == active.serial && shared(a) == 1 && shared(b) == 1);
                fb.present(&active); sweet_fb_frame_release(&active);
                fb.fInFlightSerial = 0;
            }
        };
        fb.swap(rec);
        assert(sleeps == 2 && fb.fShown == &a && fb.fPending.surfaces[0] == &b);
        finish_display(fb); clean_client(cb);
    } else if (test == "actual_syncswap_timeout" || test == "actual_syncswap_off") {
        AppleCLCD fb; displayLock = fb.fLock; fb.fSyncSwap = true;
        fb.fPowerOff = test == "actual_syncswap_off";
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        fb.swap(rec);
        assert(sleeps == (fb.fPowerOff ? 0u : 1u));
        assert(fb.fDirty && fb.fPending.surfaces[0] == &a && shared(a) == 1);
        finish_display(fb);
    } else if (test == "actual_stopping_swap") {
        AppleCLCD fb; displayLock = fb.fLock; fb.fStopping = true;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        fb.swap(rec);
        assert(!fb.fDirty && !fb.fPending.surfaces[0] && !fb.fStatSwaps);
        assert(a.refs == 2 && !shared(a));
        finish_display(fb);
    } else if (test == "actual_quiesce" || test == "actual_quiesce_failed" ||
               test == "actual_quiesce_async") {
        publish(b, 8); attach(cb, br, b);
        AppleCLCD fb; displayLock = fb.fLock;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        sweet_fb_frame active = {};
        fb.swap(rec); dequeue(fb, active); fb.present(&active);
        sweet_fb_frame_release(&active);
        record(rec, 1, 8); fb.swap(rec);
        bool preserve = test != "actual_quiesce";
        scanResult = test == "actual_quiesce_failed" ? -4 : 0;
        if (test == "actual_quiesce_async") {
            sweet_disp_shadow_on = true; sweet_disp_te_period = 0;
        }
        scanHook = [&](uint64_t phys) {
            assert(phys == fb.fPhys && shared(a) == 1);
            assert(fb.fStopping && !fb.fVsyncAttached && fb.ownWorkLoop.removals == 1);
            assert(!shared(b) && !fb.fDirty); // queued source can go before DMA switch
        };
        fb.quiesce();
        assert(fb.fStopping && !fb.fDirty && !fb.fShown && !fb.fPending.surfaces[0]);
        assert(fb.ownTimer.disabled && fb.ownTimer.cancelled && fb.ownWorkLoop.removals == 1);
        assert(shared(a) == (preserve ? 1u : 0u) && !shared(b));
        assert(scanCalls == (test == "actual_quiesce_async" ? 1 : 2));
        fb.quiesce(); // stop followed by free must not release twice
        assert(fb.ownWorkLoop.removals == 1);
        if (preserve) sweet_surface_end_use(&a); // test reclaims intentional hardware-safety pin
        finish_display(fb); clean_client(cb);
    } else if (test == "actual_present_ownership") {
        publish(b, 8); attach(cb, br, b);
        AppleCLCD fb; displayLock = fb.fLock;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        sweet_fb_frame active = {};
        fb.swap(rec); dequeue(fb, active);
        scanHook = [&](uint64_t phys) {
            assert(phys == a.phys && shared(a) == 1 && a.refs == 3 && !fb.fShown);
        };
        fb.present(&active);
        sweet_fb_frame_release(&active);
        assert(fb.fShown == &a && shared(a) == 1 && a.refs == 3);
        record(rec, 1, 8); fb.swap(rec); dequeue(fb, active);
        scanHook = [&](uint64_t phys) {
            assert(phys == b.phys && fb.fShown == &a);
            assert(shared(a) == 1 && shared(b) == 1); // both held until hardware returns
        };
        fb.present(&active); sweet_fb_frame_release(&active);
        assert(fb.fShown == &b && !shared(a) && shared(b) == 1 && a.refs == 2 && b.refs == 3);
        fb.swap(rec); dequeue(fb, active); // present the same physical surface twice
        scanHook = [&](uint64_t) { assert(shared(b) == 2 && b.refs == 4); };
        fb.present(&active); sweet_fb_frame_release(&active);
        assert(fb.fShown == &b && shared(b) == 1 && b.refs == 3);
        assert(fb.fStatScans == 3 && fb.fStatPresents == 3 && scanCalls == 3 && !refreshCalls);
        finish_display(fb); clean_client(cb);
    } else if (test == "actual_retry_empty") {
        AppleCLCD fb; displayLock = fb.fLock;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        sweet_fb_frame active = {};
        fb.swap(rec); dequeue(fb, active);
        scanResult = -4;
        scanHook = [&](uint64_t) { assert(shared(a) == 1 && a.refs == 3); };
        fb.present(&active);
        assert(fb.fDirty && fb.fPending.surfaces[0] == &a && !active.surfaces[0]);
        sweet_fb_frame_release(&active);
        assert(shared(a) == 1 && a.refs == 3 && !refreshCalls && !fb.fShown);
        clean_client(ca); // retry must carry the pointer rather than look up the old ID again
        assert(!sweet_surface_table[7] && a.refs == 1 && shared(a) == 1);
        dequeue(fb, active); scanResult = 0;
        scanHook = [&](uint64_t) { assert(shared(a) == 1 && a.refs == 1); };
        fb.present(&active); sweet_fb_frame_release(&active);
        assert(fb.fShown == &a && shared(a) == 1 && a.refs == 1 && !fb.fDirty);
        finish_display(fb);
    } else if (test == "actual_retry_newer_swap") {
        publish(b, 8); attach(cb, br, b);
        AppleCLCD fb; displayLock = fb.fLock;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE], newer[SWEET_FB_SWAP_REC_SIZE];
        record(rec, 1, 7); record(newer, 1, 8);
        sweet_fb_frame active = {};
        fb.swap(rec); dequeue(fb, active); scanResult = -4;
        scanHook = [&](uint64_t) {
            assert(shared(a) == 1);
            fb.swap(newer); // compositor enqueues while the old frame waits for TE
            assert(shared(a) == 1 && shared(b) == 1);
        };
        fb.present(&active);
        assert(fb.fDirty && fb.fPending.surfaces[0] == &b && active.surfaces[0] == &a);
        sweet_fb_frame_release(&active);
        assert(!shared(a) && shared(b) == 1 && !refreshCalls);
        finish_display(fb); clean_client(cb);
    } else if (test == "actual_copy_source_ownership") {
        AppleCLCD fb; displayLock = fb.fLock; fb.fScanout = false;
        a.pixels[0] = 0x11223344; a.pixels[1] = 0x55667788;
        a.pixels[2] = 0x99aabbcc; a.pixels[3] = 0xddeeff00;
        uint8_t rec[SWEET_FB_SWAP_REC_SIZE]; record(rec, 1, 7);
        sweet_fb_frame active = {};
        fb.swap(rec); dequeue(fb, active);
        clean_client(ca);
        assert(!sweet_surface_table[7] && a.refs == 1);
        refreshHook = [&] {
            assert(shared(a) == 1 && a.refs == 1 && !a.freed);
            assert(!memcmp(fb.pixels, a.pixels, sizeof(a.pixels)));
        };
        fb.present(&active);
        assert(shared(a) == 1 && fb.fStatCopies == 1 && refreshCalls == 1 && !scanCalls);
        sweet_fb_frame_release(&active);
        assert(a.freed && !shared(a));
        finish_display(fb);
    } else {
        assert(false && "Unknown test");
    }
    if (!ca.terminated) clean_client(ca);
    assert(!tableLock.held && tableLock.locks == tableLock.unlocks);
    assert(a.freed && a.refs == 0 && !shared(a));
    for (auto *s: sweet_surface_table) assert(!s);
}
'''


class SurfaceUseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        surface = (ROOT / 'xnu/display/SweetSurface.cpp').read_text()
        framebuffer = (ROOT / 'xnu/display/SweetFramebuffer.cpp').read_text()
        surface_names = ('sweet_surface_use_locked', 'sweet_surface_begin_use',
                         'sweet_surface_end_use', 'sweet_surface_put',
                         'SweetSurfaceRef::free', 'IOSurfaceRootUserClient::findRef',
                         'IOSurfaceRootUserClient::changeUseCount',
                         'IOSurfaceRootUserClient::trapIncrementUse',
                         'IOSurfaceRootUserClient::trapDecrementUse',
                         'IOSurfaceRootUserClient::trapRelease',
                         'IOSurfaceRootUserClient::clientClose')
        frame_struct = re.search(r'struct sweet_fb_frame\s*\{.*?\n\};', framebuffer, re.S)
        assert frame_struct, 'Missing production frame ownership structure'
        code = PRELUDE + '\n'.join(function(surface, name) for name in surface_names)
        code += '\n' + frame_struct[0] + '\n'
        code += '\n'.join(function(framebuffer, name) for name in (
            'get32', 'sweet_fb_frame_release', 'sweet_fb_frame_acquire', 'sweet_fb_frame_move'))
        code += DISPLAY_MOCKS
        code += '\n'.join(function(framebuffer, name) for name in (
            'AppleCLCD::swap', 'AppleCLCD::present', 'AppleCLCD::quiesce'))
        code += CHECKS
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / 'surface-use'
        source_path = cls.binary.with_suffix('.cpp')
        source_path.write_text(code)
        result = subprocess.run(['clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
                                 '-Werror', '-fsanitize=address,undefined',
                                 str(source_path), '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            cls.tmp.cleanup()
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_swap_balances_superseded_and_rejected_frames(self):
        self.run_case('actual_swap_supersede')

    def test_syncswap_waits_through_presentation(self):
        self.run_case('actual_syncswap')

    def test_syncswap_waits_through_te_retry(self):
        self.run_case('actual_syncswap_retry')

    def test_syncswap_superseded_frame_returns_without_waiting_for_new_frame(self):
        self.run_case('actual_syncswap_replaced')

    def test_syncswap_keeps_waiting_for_own_inflight_frame_after_new_swap(self):
        self.run_case('actual_syncswap_inflight')

    def test_syncswap_timeout_preserves_queued_ownership(self):
        self.run_case('actual_syncswap_timeout')

    def test_powered_off_display_does_not_block_syncswap(self):
        self.run_case('actual_syncswap_off')

    def test_stopping_rejects_and_releases_new_swap(self):
        self.run_case('actual_stopping_swap')

    def test_shutdown_drains_timer_and_queue_before_switching_hardware(self):
        self.run_case('actual_quiesce')

    def test_shutdown_failed_switch_preserves_hardware_source(self):
        self.run_case('actual_quiesce_failed')

    def test_shutdown_async_path_preserves_hardware_source(self):
        self.run_case('actual_quiesce_async')

    def test_real_present_holds_both_frames_until_scanout_finishes(self):
        self.run_case('actual_present_ownership')

    def test_real_te_retry_preserves_pointer_after_user_release(self):
        self.run_case('actual_retry_empty')

    def test_real_te_retry_keeps_newer_swap_and_releases_failed_one(self):
        self.run_case('actual_retry_newer_swap')

    def test_real_copy_holds_source_until_pixels_are_consumed(self):
        self.run_case('actual_copy_source_ownership')

    def test_shared_low_high_categories_and_adjacent_fields(self):
        self.run_case('shared_categories')

    def test_invalid_ids_and_uninitialized_table(self):
        self.run_case('invalid_ids')

    def test_count_bounds_preserve_existing_owners(self):
        self.run_case('count_bounds')

    def test_client_cannot_decrement_another_client_or_scanout(self):
        self.run_case('client_isolation')

    def test_client_close_and_release_remove_only_own_contribution(self):
        self.run_case('client_teardown')

    def test_frame_holds_all_and_only_named_enabled_layers(self):
        self.run_case('frame_enabled_layers')

    def test_invalid_frame_rolls_back_acquired_layers(self):
        self.run_case('frame_invalid_rollback')

    def test_same_surface_in_two_layers_balances_both_holds(self):
        self.run_case('frame_duplicate_layers')

    def test_dequeued_and_retried_frame_survives_user_and_id_table_removal(self):
        self.run_case('frame_moves_and_table_removal')


if __name__ == '__main__':
    unittest.main()

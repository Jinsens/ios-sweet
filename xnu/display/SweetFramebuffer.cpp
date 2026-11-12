/*
 * Built-in IOMobileFramebuffer for SWEET: the ABL's continuous-splash
 * framebuffer (1080x2400 a8r8g8b8 in RAM, the panel scans it out by
 * itself) as the main display, driven by CoreAnimation's CPU renderer.
 *
 * IOMobileFramebuffer.framework (iOS 16.7) counts /arm-io children with
 * device_type "display-subsystem", waits for that many services matching
 * IOServiceMatching("IOMobileFramebuffer"), opens each with type 0 and
 * reads canvas-width/canvas-height from the service's provider. The class
 * here (AppleCLCD, an IOMobileFramebuffer) matches the /arm-io/disp0 nub.
 *
 * User client selectors used by CoreAnimation:
 *   3  GetLayerDefaultSurface [layer, ?] -> [IOSurface port name]
 *   4  SwapBegin -> [swap id]
 *   5  SwapEnd, struct 0x46c: +0x50 swap id, +0x54 IOSurface id per layer
 *      (4 layers), +0x64 rect a / +0xc4 rect b per layer (int x,y,w,h),
 *      +0x104 enabled layer mask
 *   6  SwapWait, 7 GetID, 8 GetDisplaySize -> [w, h],
 *   12 RequestPowerChange [state]: 0 = display off (backboardd, power
 *      button / auto-lock), else on. Off fades the panel out over a few
 *      vsyncs (halving each pixel) and keeps it black until on again;
 *      the panel itself stays powered (AMOLED black pixels are off).
 *   18 IsMainDisplay,
 *   28 GetDotPitch, 72 Enable/DisableNotifications [func, refcon, type, 0]
 * Everything else is accepted and logged once.
 *
 * SwapEnd copies the enabled layers' surfaces into the panel memory (no
 * scaling, CPU copy). Vsync (notification type 5) is a 60 Hz timer that
 * sends (count, mach_absolute_time, 0) as 32-bit halves to clients that
 * enabled it, through the port they registered (IOConnectSetNotificationPort).
 *
 * The panel memory itself is published as an IOSurface and returned as the
 * default framebuffer surface. The kernel text console keeps drawing over
 * the GUI by default: boot-arg sweetfb_noconsole=1 stops drawing it from
 * the first swap on (it still reaches the persistent log; panics draw).
 */
#include "SweetSurface.h"

extern "C" int sweet_console_muted;     /* osfmk/console/serial_console.c */

#if defined(SWEET)
#include <arm64/sweet_matisse_dsi.h>
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOKitKeys.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <IOKit/IOWorkLoop.h>
#include <IOKit/IOTimerEventSource.h>
#include <IOKit/system.h>
#include <pexpert/pexpert.h>
#include <kern/clock.h>
#include <sys/proc.h>

enum {
	kSweetFBGetLayerDefaultSurface  = 3,
	kSweetFBSwapBegin               = 4,
	kSweetFBSwapEnd                 = 5,
	kSweetFBSwapWait                = 6,
	kSweetFBGetID                   = 7,
	kSweetFBGetDisplaySize          = 8,
	kSweetFBRequestPowerChange      = 12,
	kSweetFBIsMainDisplay           = 18,
	kSweetFBGetDotPitch             = 28,
	kSweetFBNotifications           = 72,
};

#define SWEET_FB_SWAP_REC_SIZE          0x46c
#define SWEET_FB_LAYERS                 4
#define SWEET_FB_NOTIFY_TYPES           8
#define SWEET_FB_NOTIFY_VSYNC           5
#define SWEET_FB_VSYNC_US               16667
#define SWEET_FB_DOT_PITCH              458     /* ppi, as the iPhone X panel */
/*
 * The iOS userland only has status bar / layout data for real iPhone
 * screens (UIKit: "No status bar visual provider for screen", the red
 * rdar://45025538 bar for 360x800 points). The display is therefore
 * presented as the iPhone X panel, 1125x2436 (375x812 points @3x), and the
 * centered 1080x2400 part of it is shown on the 1080x2400 panel.
 * Boot-arg sweetfb_native=1 presents the real panel size instead.
 */
#define SWEET_FB_LOGICAL_W              1125
#define SWEET_FB_LOGICAL_H              2436
#define SWEET_FB_FORMAT_BGRA            0x42475241

class IOMobileFramebuffer : public IOService {
	OSDeclareDefaultStructors(IOMobileFramebuffer);
};

OSDefineMetaClassAndStructors(IOMobileFramebuffer, IOService);

class IOMobileFramebufferUserClient;

class AppleCLCD : public IOMobileFramebuffer {
	OSDeclareDefaultStructors(AppleCLCD);

	IOWorkLoop         *fWorkLoop;
	IOTimerEventSource *fVsync;
	uint64_t            fVsyncCount;

	static void vsyncFired(OSObject *owner, IOTimerEventSource *sender);

public:
	uint64_t      fPhys;
	uint32_t      fWidth, fHeight, fRowBytes;
	uint32_t      fLogicalW, fLogicalH;     /* size reported to userland */
	int32_t       fOffX, fOffY;             /* logical origin of the panel */
	uintptr_t     fPixels;          /* kernel mapping of the panel memory */
	IOMemoryMap  *fMap;
	SweetSurface *fSurface;         /* the panel memory as an IOSurface */
	IOLock       *fLock;            /* swaps and client list */
	OSArray      *fClients;
	uint32_t      fSwapID;
	bool          fConsolePaused;
	uint8_t       fPending[SWEET_FB_SWAP_REC_SIZE];     /* latest swap, not yet shown */
	bool          fDirty;
	uint32_t      fFlushTicks;    /* vsync ticks between panel copies */
	bool          fPowerOff;      /* RequestPowerChange(0) */
	uint32_t      fFadeStep;      /* fade-out steps done while off */
	bool          fScanout;       /* point the overlay at the frame (sweetfb_copy=1: copy) */
	SweetSurface *fShown;         /* surface the panel is fetching from, retained */
	/* What the panel path costs, for the vsync log. */
	uint64_t      fStatTicks, fStatLast, fStatSum, fStatMax;
	uint64_t      fStatSwaps, fStatPresents, fStatScans, fStatCopies;
	uint32_t      fLastScanId;    /* surface the panel was pointed at last */
	uint64_t      fLastScanPhys;  /* ... and the memory it read from */
	uint64_t      fStatRepeats;   /* ... the same one twice in a row */
	uint64_t      fStatSamePhys;  /* ... the same memory under a new id */
	uint32_t      fProbe;         /* sweetfbprobe: steps of the panel fetch test */
	uint64_t      fStatCopyUs, fStatTried, fStatRefused, fStatPresentUs;
	/* The display link is a schedule, not a chain of "sleep a frame":
	 * each tick is due at a fixed absolute time, so the timer's own
	 * lateness does not accumulate into the period. That lateness is
	 * what made the vsync 17.6 ms instead of the panel's 16.667, and a
	 * rate that is 5% off sweeps the moment the compositor presents
	 * across the whole panel every second - the bar that flashes once
	 * and goes. fVsyncUs is the panel's own frame; fPhaseUs shifts the
	 * whole schedule once, to slide the presentation phase. */
	uint64_t      fNextDue;
	uint32_t      fVsyncUs;
	int32_t       fPhaseUs;
	int32_t       fStatReason;    /* why the last direct scanout was refused */

	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
	void free() APPLE_KEXT_OVERRIDE;
	void addClient(IOMobileFramebufferUserClient *client);
	void removeClient(IOMobileFramebufferUserClient *client);
	void swap(const uint8_t *rec);
	void present(const uint8_t *rec);
};

OSDefineMetaClassAndStructors(AppleCLCD, IOMobileFramebuffer);

class IOMobileFramebufferUserClient : public IOUserClient {
	OSDeclareDefaultStructors(IOMobileFramebufferUserClient);

public:
	AppleCLCD          *fFB;
	task_t              fTask;
	mach_port_t         fPort;
	io_user_reference_t fFunc[SWEET_FB_NOTIFY_TYPES];
	io_user_reference_t fRefcon[SWEET_FB_NOTIFY_TYPES];

	bool initWithTask(task_t owningTask, void *securityID, UInt32 type) APPLE_KEXT_OVERRIDE;
	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
	IOReturn clientClose(void) APPLE_KEXT_OVERRIDE;
	IOReturn registerNotificationPort(mach_port_t port, UInt32 type,
	    io_user_reference_t refCon) APPLE_KEXT_OVERRIDE;
	IOReturn externalMethod(uint32_t selector, IOExternalMethodArguments *args,
	    IOExternalMethodDispatch *dispatch, OSObject *target,
	    void *reference) APPLE_KEXT_OVERRIDE;
	void notify(uint32_t type, const uint64_t *values, uint32_t count);
};

OSDefineMetaClassAndStructors(IOMobileFramebufferUserClient, IOUserClient);

bool
AppleCLCD::start(IOService *provider)
{
	PE_Video video;

	if (!IOService::start(provider)) {
		return false;
	}
	PE_current_console(&video);
	fPhys = video.v_baseAddr;
	fWidth = (uint32_t)video.v_width;
	fHeight = (uint32_t)video.v_height;
	fRowBytes = (uint32_t)video.v_rowBytes;
	if (fPhys == 0 || fWidth == 0 || fHeight == 0 || fRowBytes < fWidth * 4) {
		IOLog("SWEET FB: no boot framebuffer\n");
		return false;
	}
	int native = 0;
	PE_parse_boot_argn("sweetfb_native", &native, sizeof(native));
	fLogicalW = fWidth;
	fLogicalH = fHeight;
	if (!native) {
		fLogicalW = SWEET_FB_LOGICAL_W;
		fLogicalH = SWEET_FB_LOGICAL_H;
	}
	fOffX = ((int32_t)fLogicalW - (int32_t)fWidth) / 2;
	fOffY = ((int32_t)fLogicalH - (int32_t)fHeight) / 2;
	fLock = IOLockAlloc();
	fClients = OSArray::withCapacity(4);
	fSurface = sweet_surface_create_physical(fPhys, fWidth, fHeight, fRowBytes, SWEET_FB_FORMAT_BGRA);
	fPixels = fSurface != NULL ? fSurface->kernelAddress() : 0;
	fWorkLoop = IOWorkLoop::workLoop();
	if (fLock == NULL || fClients == NULL || fPixels == 0 || fWorkLoop == NULL) {
		IOLog("SWEET FB: setup failed\n");
		OSSafeReleaseNULL(fSurface);    /* stays published: also held by the table */
		OSSafeReleaseNULL(fWorkLoop);
		return false;
	}
	fVsync = IOTimerEventSource::timerEventSource(this, &AppleCLCD::vsyncFired);
	if (fVsync == NULL || fWorkLoop->addEventSource(fVsync) != kIOReturnSuccess) {
		IOLog("SWEET FB: no vsync timer\n");
		return false;
	}
	uint32_t hz = 10;
	PE_parse_boot_argn("sweetfb_hz", &hz, sizeof(hz));
	fFlushTicks = (hz == 0 || hz >= 60) ? 1 : 60 / hz;
	fVsync->setTimeoutUS(SWEET_FB_VSYNC_US);
	int copy = 0;
	fScanout = !(PE_parse_boot_argn("sweetfb_copy", &copy, sizeof(copy)) && copy != 0);
	(void)PE_parse_boot_argn("sweetfbtesync", &sweet_disp_te_sync, sizeof(sweet_disp_te_sync));
	(void)PE_parse_boot_argn("sweetfbwait", &sweet_disp_wait_done, sizeof(sweet_disp_wait_done));
	(void)PE_parse_boot_argn("sweetfb_hold", &sweet_disp_hold, sizeof(sweet_disp_hold));
	(void)PE_parse_boot_argn("sweetfb_teoff", &sweet_disp_te_off, sizeof(sweet_disp_te_off));
	(void)PE_parse_boot_argn("sweetfb_teext", &sweet_disp_te_ext, sizeof(sweet_disp_te_ext));
	(void)PE_parse_boot_argn("sweetfb_teon", &sweet_disp_te_on, sizeof(sweet_disp_te_on));
	(void)PE_parse_boot_argn("sweetfb_sline", &sweet_disp_sline, sizeof(sweet_disp_sline));
	(void)PE_parse_boot_argn("sweetfb_teint", &sweet_disp_te_int, sizeof(sweet_disp_te_int));
	(void)PE_parse_boot_argn("sweetfb_lead", &sweet_disp_lead_us, sizeof(sweet_disp_lead_us));
	if (sweet_disp_lead_us > 300) {
		sweet_disp_lead_us = 300;
	}
	(void)PE_parse_boot_argn("sweetfb_hold_us", &sweet_disp_hold_us, sizeof(sweet_disp_hold_us));
	(void)PE_parse_boot_argn("sweetfbprobe", &fProbe, sizeof(fProbe));
	fVsyncUs = SWEET_FB_VSYNC_US;
	(void)PE_parse_boot_argn("sweetfb_vsync_us", &fVsyncUs, sizeof(fVsyncUs));
	(void)PE_parse_boot_argn("sweetfb_phase_us", &fPhaseUs, sizeof(fPhaseUs));

	setProperty(kIOUserClientClassKey, "IOMobileFramebufferUserClient");
	setProperty("external", false);
	setProperty("DisplayPipePlaneBaseAlignment", 64ULL, 32);
	registerService();
	IOLog("SWEET FB: AppleCLCD %ux%u stride %u at 0x%llx, default surface %u, presented as %ux%u\n",
	    fWidth, fHeight, fRowBytes, fPhys, fSurface->id, fLogicalW, fLogicalH);
	return true;
}

void
AppleCLCD::free()
{
	OSSafeReleaseNULL(fShown);
	IOService::free();
}

/* Arm the next tick at its absolute due time: due += one panel frame, then
 * sleep until then. A timer that fires late does not push the schedule, it
 * only shortens the next sleep - so the rate stays on the panel's frame and
 * the presentation phase stays put. */
static void
sweet_vsync_arm(AppleCLCD *fb, IOTimerEventSource *sender)
{
	uint64_t now = mach_absolute_time();
	uint64_t perNs = (uint64_t)fb->fVsyncUs * 1000ull;
	uint64_t perAbs = 0, nowNs = 0, dueNs = 0;
	int64_t deltaUs;

	nanoseconds_to_absolutetime(perNs, &perAbs);
	if (fb->fNextDue == 0) {
		fb->fNextDue = now;
		if (fb->fPhaseUs > 0) {
			uint64_t off = 0;

			nanoseconds_to_absolutetime((uint64_t)fb->fPhaseUs * 1000ull,
			    &off);
			fb->fNextDue += off;
		}
	}
	fb->fNextDue += perAbs;
	absolutetime_to_nanoseconds(fb->fNextDue, &dueNs);
	absolutetime_to_nanoseconds(now, &nowNs);
	deltaUs = ((int64_t)dueNs - (int64_t)nowNs) / 1000;
	if (deltaUs < 300) {
		/* Too late to keep this slot: re-base instead of bursting. */
		fb->fNextDue = now + perAbs;
		deltaUs = (int64_t)fb->fVsyncUs;
	}
	sender->setTimeoutUS((uint32_t)deltaUs);
}

void
AppleCLCD::vsyncFired(OSObject *owner, IOTimerEventSource *sender)
{
	AppleCLCD *fb = (AppleCLCD *)owner;
	uint64_t now = mach_absolute_time();
	uint64_t count = ++fb->fVsyncCount;
	uint64_t values[6] = {
		(uint32_t)count, (uint32_t)(count >> 32),
		(uint32_t)now, (uint32_t)(now >> 32),
		0, 0,
	};
	/* How far the display link is from 60 Hz, and what the panel path costs.
	 * A late tick is the whole UI's frame budget: CoreAnimation paces itself
	 * by these notifications, so the sum and the worst gap say whether the
	 * compositor is slow or the tick is. */
	if (fb->fStatLast != 0) {
		uint64_t delta = now - fb->fStatLast;
		fb->fStatSum += delta;
		if (delta > fb->fStatMax) {
			fb->fStatMax = delta;
		}
	}
	fb->fStatLast = now;
	if (++fb->fStatTicks == 600) {
		uint64_t ns = 0, maxns = 0, us, max;
		absolutetime_to_nanoseconds(fb->fStatSum, &ns);
		absolutetime_to_nanoseconds(fb->fStatMax, &maxns);
		us = ns / 1000;
		max = maxns / 1000;
		uint64_t waitUs = 0, sendUs = 0, sendMaxUs = 0, teUs = 0;
		uint64_t teWaitUs = 0, teWaitMaxUs = 0, frameWaitUs = 0, frameWaitMaxUs = 0;
		if (sweet_disp_frame_seen != 0) {
			absolutetime_to_nanoseconds(sweet_disp_frame_wait_ticks, &frameWaitUs);
			frameWaitUs = frameWaitUs / 1000 / sweet_disp_frame_seen;
			absolutetime_to_nanoseconds(sweet_disp_frame_wait_max, &frameWaitMaxUs);
			frameWaitMaxUs = frameWaitMaxUs / 1000;
		}
		if (sweet_disp_te_gaps != 0) {
			absolutetime_to_nanoseconds(sweet_disp_te_ticks, &teUs);
			teUs = teUs / 1000 / sweet_disp_te_gaps;
		}
		if (sweet_disp_te_waits != 0) {
			absolutetime_to_nanoseconds(sweet_disp_te_wait_ticks, &teWaitUs);
			teWaitUs = teWaitUs / 1000 / sweet_disp_te_waits;
			absolutetime_to_nanoseconds(sweet_disp_te_wait_max, &teWaitMaxUs);
			teWaitMaxUs = teWaitMaxUs / 1000;
		}
		if (sweet_disp_waits != 0) {
			absolutetime_to_nanoseconds(sweet_disp_wait_ticks, &waitUs);
			waitUs = waitUs / 1000 / sweet_disp_waits;
		}
		uint64_t holdUs = 0, scanPeriodUs = 0, preUs = 0;
		if (sweet_disp_scan_period != 0) {
			absolutetime_to_nanoseconds(sweet_disp_scan_period, &scanPeriodUs);
			scanPeriodUs = scanPeriodUs / 1000;
		}
		if (sweet_disp_pre_busy != 0) {
			absolutetime_to_nanoseconds(sweet_disp_pre_ticks, &preUs);
			preUs = preUs / 1000 / sweet_disp_pre_busy;
		}
		if (sweet_disp_hold_frames != 0) {
			absolutetime_to_nanoseconds(sweet_disp_hold_ticks, &holdUs);
			holdUs = holdUs / 1000 / sweet_disp_hold_frames;
		}
		if (sweet_disp_sends != 0) {
			absolutetime_to_nanoseconds(sweet_disp_send_ticks, &sendUs);
			sendUs = sendUs / 1000 / sweet_disp_sends;
			absolutetime_to_nanoseconds(sweet_disp_send_max, &sendMaxUs);
			sendMaxUs = sendMaxUs / 1000;
		}
		IOLog("SWEET FB: 600 vsyncs %llu us avg %llu max %llu; swaps %llu presents %llu scans %llu same %llu samephys %llu copies %llu copy %llu us present %llu us tried %llu refused %llu why %d; dsi calls %u pushes %u busy %u gui %u console %u wait %llu us send %llu/%llu us te %u/%u %llu us stale %u wait %llu/%llu us\n",
		    us, us / 599, max, fb->fStatSwaps, fb->fStatPresents, fb->fStatScans,
		    fb->fStatRepeats, fb->fStatSamePhys, fb->fStatCopies, fb->fStatCopyUs, fb->fStatPresentUs,
		    fb->fStatTried, fb->fStatRefused, (int)fb->fStatReason, sweet_disp_calls,
		    sweet_disp_pushes, sweet_disp_busy_skips, sweet_disp_gui_flips,
		    sweet_disp_console_flips, waitUs, sendUs, sendMaxUs,
		    sweet_disp_te_seen, sweet_disp_te_missed, teUs,
		    sweet_disp_te_stale, teWaitUs, teWaitMaxUs);
		IOLog("SWEET FB: te seen %u missed %u stale %u wait %llu/%llu us samephys %llu\n",
		    sweet_disp_te_seen, sweet_disp_te_missed, sweet_disp_te_stale,
		    teWaitUs, teWaitMaxUs, fb->fStatSamePhys);
		IOLog("SWEET FB: frame sync seen %u missed %u wait %llu/%llu us\n",
		    sweet_disp_frame_seen, sweet_disp_frame_missed,
		    frameWaitUs, frameWaitMaxUs);
		IOLog("SWEET FB: underrun %u; te edges %u lows %u; dcs %u teoff %d teext %d\n",
		    sweet_disp_underruns, sweet_disp_te_edges, sweet_disp_te_lows,
		    sweet_disp_dcs, sweet_disp_te_off, sweet_disp_te_ext);
		{
			uint64_t tePeriodUs = 0, teSyncUs = 0, pollMaxUs = 0, launchMaxUs = 0;

			if (sweet_disp_te_period != 0) {
				absolutetime_to_nanoseconds(sweet_disp_te_period,
				    &tePeriodUs);
				tePeriodUs = tePeriodUs / 1000;
			}
			if (sweet_disp_te_sync_hits != 0) {
				absolutetime_to_nanoseconds(sweet_disp_te_sync_ticks,
				    &teSyncUs);
				teSyncUs = teSyncUs / 1000 / sweet_disp_te_sync_hits;
			}
			IOLog("SWEET FB: te period %llu us align %d hits %u miss %u wait %llu us\n",
			    tePeriodUs, sweet_disp_te_align,
			    sweet_disp_te_sync_hits, sweet_disp_te_sync_missed,
			    teSyncUs);
			absolutetime_to_nanoseconds(sweet_disp_te_poll_max, &pollMaxUs);
			absolutetime_to_nanoseconds(sweet_disp_te_launch_max, &launchMaxUs);
			IOLog("SWEET FB: te late poll %u launch %u accepted age %llu us start %llu us\n",
			    sweet_disp_te_late, sweet_disp_te_launch_late,
			    pollMaxUs / 1000, launchMaxUs / 1000);
		}
		IOLog("SWEET FB: scanline 0x%x 0x%x 0x%x 0x%x 0x%x ok %d max %u wraps %u period %llu us\n",
		    sweet_disp_scan[0], sweet_disp_scan[1], sweet_disp_scan[2],
		    sweet_disp_scan[3], sweet_disp_scan[4], sweet_disp_scan_ok,
		    sweet_disp_scan_max, sweet_disp_scan_wraps, scanPeriodUs);
		IOLog("SWEET FB: phase n %u avg %u min %u max %u last %u; pre busy %u idle %u %llu us\n",
		    sweet_disp_phase_n,
		    sweet_disp_phase_n ? (uint32_t)(sweet_disp_phase_sum / sweet_disp_phase_n) : 0,
		    sweet_disp_phase_min, sweet_disp_phase_max, sweet_disp_scan_line,
		    sweet_disp_pre_busy, sweet_disp_pre_idle,
		    sweet_disp_pre_busy ? preUs : 0);
		IOLog("SWEET FB: con_ctrl 0x%x txrx 0x%x cmdqsize 0x%x cmdq0 0x%x\n",
		    sweet_disp_cmdq[0], sweet_disp_cmdq[1], sweet_disp_cmdq[2],
		    sweet_disp_cmdq[3]);
		IOLog("SWEET FB: mutex stops %u running %u last %u; hold %d frames %u %llu us hold_us %u trunc %u shadow %d/%d\n",
		    sweet_disp_mutex_stops, sweet_disp_mutex_running,
		    sweet_disp_mutex_last, sweet_disp_hold, sweet_disp_hold_frames,
		    holdUs, sweet_disp_hold_us, sweet_disp_hold_trunc,
		    sweet_disp_shadow, sweet_disp_shadow_on);
		{
			uint64_t doneMaxUs = 0;

			absolutetime_to_nanoseconds(sweet_disp_done_max, &doneMaxUs);
			IOLog("SWEET FB: vendor %d active %u idle %u cmd_done %u last 0x%x completion max %llu us ctrl 0x%x hs 0x%x\n",
			    sweet_disp_vendor, sweet_disp_state_active, sweet_disp_state_done,
			    sweet_disp_cmd_done, sweet_disp_state_last, doneMaxUs / 1000,
			    sweet_disp_ctrl_last, sweet_disp_hs_last);
		}
		fb->fStatTicks = 0; fb->fStatSum = 0; fb->fStatMax = 0;
		fb->fStatSwaps = fb->fStatPresents = fb->fStatScans = fb->fStatCopies = 0;
		fb->fStatCopyUs = 0; fb->fStatTried = fb->fStatRefused = 0;
		fb->fStatPresentUs = 0; fb->fStatRepeats = 0; fb->fStatSamePhys = 0;
		sweet_disp_waits = 0; sweet_disp_wait_ticks = 0;
		sweet_disp_sends = 0; sweet_disp_send_ticks = 0; sweet_disp_send_max = 0;
		sweet_disp_te_seen = 0; sweet_disp_te_missed = 0;
		sweet_disp_te_ticks = 0; sweet_disp_te_gaps = 0;
		sweet_disp_te_stale = 0; sweet_disp_te_waits = 0;
		sweet_disp_frame_seen = 0; sweet_disp_frame_missed = 0;
		sweet_disp_frame_wait_ticks = 0; sweet_disp_frame_wait_max = 0;
		sweet_disp_te_wait_ticks = 0; sweet_disp_te_wait_max = 0;
	}

	IOLockLock(fb->fLock);
	for (unsigned int i = 0; i < fb->fClients->getCount(); i++) {
		IOMobileFramebufferUserClient *client =
		    (IOMobileFramebufferUserClient *)fb->fClients->getObject(i);
		client->notify(SWEET_FB_NOTIFY_VSYNC, values, 6);
	}
	if (fb->fPowerOff) {
		/* Fade out: halve every channel each vsync, then black, then idle. */
		uint32_t step = fb->fFadeStep;
		if (step <= 6) {
			fb->fFadeStep = step + 1;
		}
		IOLockUnlock(fb->fLock);
		if (step <= 6) {
			for (uint32_t y = 0; y < fb->fHeight; y++) {
				volatile uint32_t *row = (volatile uint32_t *)(fb->fPixels + (uint64_t)y * fb->fRowBytes);
				for (uint32_t x = 0; x < fb->fWidth; x++) {
					row[x] = step < 6 ? (row[x] >> 1) & 0x7f7f7f7f : 0;
				}
			}
			sweet_display_refresh(1);
		}
		sweet_vsync_arm(fb, sender);
		return;
	}
	/* sweetfbprobe=1: is the panel reading the surface all the time, or only
	 * when a transfer is triggered? Paint the memory it is fetching from and
	 * send nothing. A picture that turns solid anyway means the overlay
	 * fetches continuously and an address written between two triggers is
	 * already on the glass, which is what tears the frame. */
	if (fb->fProbe != 0 && fb->fShown != NULL) {
		if (fb->fProbe == 1 && count > 900) {
			uintptr_t base = fb->fShown->kernelAddress();

			if (base != 0) {
				volatile uint32_t *px = (volatile uint32_t *)(base + fb->fShown->offset);
				for (uint32_t y = 0; y < fb->fHeight; y++) {
					for (uint32_t x = 0; x < fb->fWidth; x++) {
						px[(uint64_t)y * (fb->fRowBytes / 4) + x] = 0xff0000ffu;
					}
				}
				IOLog("SWEET FB: probe painted the scanned surface, nothing sent\n");
			}
			fb->fProbe = 2;
		} else if (fb->fProbe == 2 && count > 1020) {
			IOLog("SWEET FB: probe sending it now\n");
			(void)sweet_display_scanout(fb->fShown->physicalAddress(), fb->fRowBytes);
			fb->fProbe = 3;
		}
	}
	bool flush = fb->fDirty && (count % fb->fFlushTicks) == 0;
	if (fb->fProbe == 2) {
		flush = false;   /* hold the painted surface on the panel */
	}
	uint8_t *rec = NULL;
	if (flush) {
		rec = (uint8_t *)IOMallocData(SWEET_FB_SWAP_REC_SIZE);
		if (rec != NULL) {
			memcpy(rec, fb->fPending, SWEET_FB_SWAP_REC_SIZE);
			fb->fDirty = false;
		}
	}
	IOLockUnlock(fb->fLock);
	if (rec != NULL) {
		fb->present(rec);
		IOFreeData(rec, SWEET_FB_SWAP_REC_SIZE);
	}
	/* Waiting for the panel's frame boundary must not stretch the display
	 * link: the compositor paces itself by these notifications, so the next
	 * tick is armed from where this one was due, not from when the work
	 * finished. */
	sweet_vsync_arm(fb, sender);
}

void
AppleCLCD::addClient(IOMobileFramebufferUserClient *client)
{
	IOLockLock(fLock);
	fClients->setObject(client);
	IOLockUnlock(fLock);
}

void
AppleCLCD::removeClient(IOMobileFramebufferUserClient *client)
{
	IOLockLock(fLock);
	unsigned int i = fClients->getNextIndexOfObject(client, 0);
	if (i != (unsigned int)-1) {
		fClients->removeObject(i);
	}
	IOLockUnlock(fLock);
}

static uint32_t
get32(const uint8_t *p, uint32_t off)
{
	uint32_t v;
	memcpy(&v, p + off, 4);
	return v;
}

static uint32_t sweet_fb_swaps_logged;
static uint64_t sweet_fb_swap_count;

/* Copy each enabled layer's surface into the panel memory (no scaling). */
/*
 * A swap only records the frame: copying 10 MB into the panel on every
 * swap (backboardd presents at 60 Hz) took the whole CPU. The vsync timer
 * shows the latest frame every fFlushTicks ticks (boot-arg sweetfb_hz,
 * default 10 Hz).
 */
void
AppleCLCD::swap(const uint8_t *rec)
{
	IOLockLock(fLock);
	memcpy(fPending, rec, SWEET_FB_SWAP_REC_SIZE);
	fDirty = true;
	fStatSwaps++;
	IOLockUnlock(fLock);
	if ((++sweet_fb_swap_count % 1000) == 0) {
		IOLog("SWEET FB: %llu swaps\n", sweet_fb_swap_count);
	}
}

void
AppleCLCD::present(const uint8_t *rec)
{
	uint32_t mask = get32(rec, 0x104);
	uint64_t copyBegan = 0;
	uint64_t presentBegan = mach_absolute_time();


	if (!fConsolePaused) {
		int quiet = 0;
		fConsolePaused = true;
		/* A larger panel shows a centered iPhone canvas. Clear the margins
		 * once so the kernel console does not remain around the UI. */
		if (fWidth > fLogicalW || fHeight > fLogicalH) {
			bzero((void *)fPixels, (size_t)fRowBytes * fHeight);
		}
		if (PE_parse_boot_argn("sweetfb_noconsole", &quiet, sizeof(quiet)) && quiet != 0) {
			/* Not PE_initialize_console(kPEDisableScreen): its console switch
			 * writes the read-only-late cons_ops_index and panics. */
			sweet_console_muted = 1;
		}
	}

	/* One full-screen layer can be shown by pointing the overlay at its
	 * surface: no 18.4 MB copy through the CPU, and the frame the
	 * compositor just finished is the frame the panel fetches. Layers that
	 * do not fill the panel (or that need scaling) still take the copy. */
	if (fScanout && (mask != 0) && (mask & (mask - 1)) == 0) {
		uint32_t layer = 0;
		fStatTried++;
		while (((mask >> layer) & 1u) == 0) {
			layer++;
		}
		const uint8_t *a = rec + 0x64 + 16 * layer;
		const uint8_t *b = rec + 0xc4 + 16 * layer;
		uint32_t id = get32(rec, 0x54 + 4 * layer);
		int32_t sx = (int32_t)get32(a, 0), sy = (int32_t)get32(a, 4);
		int32_t dx = (int32_t)get32(b, 0) - fOffX, dy = (int32_t)get32(b, 4) - fOffY;
		int32_t w = (int32_t)get32(b, 8), h = (int32_t)get32(b, 12);
		SweetSurface *s = id != 0 ? sweet_surface_copy(id) : NULL;
		uint64_t phys = 0;

		if (s != NULL) {
			if (w <= 0 || h <= 0) {
				w = (int32_t)s->width;
				h = (int32_t)s->height;
			}
			if (sx == 0 && sy == 0 && dx == 0 && dy == 0 &&
			    w == (int32_t)fWidth && h == (int32_t)fHeight &&
			    s->width == fWidth && s->height == fHeight &&
			    s->bytesPerRow == fRowBytes && s->bytesPerElement == 4 &&
			    s->elementWidth == 1 && s->elementHeight == 1 &&
			    s->pixelFormat == SWEET_FB_FORMAT_BGRA) {
				phys = s->physicalAddress();
			}
		}
		if (phys != 0) {
			int rc = sweet_display_scanout(phys, fRowBytes);
			if (rc == 0) {
				fStatScans++;
				/* Two frames in a row onto one surface means the panel
				 * is reading memory the compositor is drawing into: a
				 * frame with bands of the next one in it. */
				if (id == fLastScanId) {
					fStatRepeats++;
				}
				if (phys == fLastScanPhys) {
					fStatSamePhys++;
				}
				fLastScanId = id;
				fLastScanPhys = phys;
				OSSafeReleaseNULL(fShown);
				fShown = s;		/* the panel is reading it now */
				goto shown;
			}
			fStatRefused++;
			fStatReason = rc;
			if (rc == -4) {
				/* A measured TE disappeared. Keep the last complete picture
				 * instead of falling through to an unsynchronized CPU copy.
				 * Retry this record only if no newer swap is already queued. */
				IOLockLock(fLock);
				if (!fDirty) {
					memcpy(fPending, rec, SWEET_FB_SWAP_REC_SIZE);
					fDirty = true;
				}
				IOLockUnlock(fLock);
				s->release();
				return;
			}
			if (fStatScans + fStatRefused <= 3) {
				IOLog("SWEET FB: scanout refused rc=%d id=%u %ux%u bpr %u fmt 0x%x phys 0x%llx -> 0x%llx mask 0x%x\n",
				    rc, id, s ? s->width : 0, s ? s->height : 0,
				    s ? s->bytesPerRow : 0, s ? s->pixelFormat : 0,
				    phys, (unsigned long long)fPixels, mask);
			}
		} else {
			fStatRefused++;
			fStatReason = -10;	/* no physical address for it */
			if (fStatScans + fStatRefused <= 3) {
				IOLog("SWEET FB: scanout has no address id=%u %ux%u bpr %u fmt 0x%x bpe %u sx %d,%d d %d,%d %dx%d panel %ux%u\n",
				    id, s ? s->width : 0, s ? s->height : 0,
				    s ? s->bytesPerRow : 0, s ? s->pixelFormat : 0,
				    s ? s->bytesPerElement : 0, sx, sy, dx, dy, w, h, fWidth, fHeight);
			}
		}
		OSSafeReleaseNULL(s);
	}

	copyBegan = mach_absolute_time();
	for (uint32_t layer = 0; layer < SWEET_FB_LAYERS; layer++) {
		uint32_t id = get32(rec, 0x54 + 4 * layer);
		const uint8_t *a = rec + 0x64 + 16 * layer;
		const uint8_t *b = rec + 0xc4 + 16 * layer;
		int32_t sx = (int32_t)get32(a, 0), sy = (int32_t)get32(a, 4);
		int32_t dx = (int32_t)get32(b, 0), dy = (int32_t)get32(b, 4);
		int32_t w = (int32_t)get32(b, 8), h = (int32_t)get32(b, 12);

		if (!(mask & (1u << layer)) || id == 0) {
			continue;
		}
		SweetSurface *s = sweet_surface_copy(id);
		if (sweet_fb_swaps_logged < 16) {
			sweet_fb_swaps_logged++;
			IOLog("SWEET FB: swap %u layer %u surface %u %ux%u fmt 0x%x bpr %u a %d,%d %ux%u b %d,%d %dx%d\n",
			    get32(rec, 0x50), layer, id, s ? s->width : 0, s ? s->height : 0,
			    s ? s->pixelFormat : 0, s ? s->bytesPerRow : 0, sx, sy, get32(a, 8),
			    get32(a, 12), dx, dy, w, h);
		}
		if (s == NULL || s == fSurface) {
			OSSafeReleaseNULL(s);
			continue;
		}
		uintptr_t src = s->kernelAddress();
		uint32_t bpe = s->bytesPerElement ? s->bytesPerElement : 4;
		if (w <= 0 || h <= 0) {
			w = (int32_t)s->width;
			h = (int32_t)s->height;
		}
		if (sx < 0 || sy < 0) {
			sx = sy = 0;
		}
		if (dx < 0 || dy < 0) {
			dx = dy = 0;
		}
		/* Logical (presented) display coordinates -> panel coordinates. */
		dx -= fOffX;
		dy -= fOffY;
		if (dx < 0) {
			sx -= dx;
			w += dx;
			dx = 0;
		}
		if (dy < 0) {
			sy -= dy;
			h += dy;
			dy = 0;
		}
		if (w > (int32_t)s->width - sx) {
			w = (int32_t)s->width - sx;
		}
		if (h > (int32_t)s->height - sy) {
			h = (int32_t)s->height - sy;
		}
		if (w > (int32_t)fWidth - dx) {
			w = (int32_t)fWidth - dx;
		}
		if (h > (int32_t)fHeight - dy) {
			h = (int32_t)fHeight - dy;
		}
		uint64_t last = (uint64_t)s->offset + (uint64_t)(sy + h - 1) * s->bytesPerRow +
		    (uint64_t)(sx + w) * 4;
		if (w <= 0 || h <= 0 || s->elementWidth != 1 || s->elementHeight != 1 ||
		    s->memory == NULL || last > s->memory->getLength()) {
			src = 0;
		}
		if (src != 0 && bpe == 4) {
			for (int32_t y = 0; y < h; y++) {
				memcpy((void *)(fPixels + (uint64_t)(dy + y) * fRowBytes + (uint64_t)dx * 4),
				    (const void *)(src + s->offset + (uint64_t)(sy + y) * s->bytesPerRow + (uint64_t)sx * 4),
				    (size_t)w * 4);
			}
		}
		s->release();
	}
	/* Command-mode panels need a transfer after the CPU copy. The vsync
	 * timer already limits presentation to sweetfb_hz; console refreshes
	 * stop at the first swap and cannot carry GUI frames to the panel. */
	sweet_display_refresh(1);
	{
		uint64_t copyNs = 0;
		absolutetime_to_nanoseconds(mach_absolute_time() - copyBegan, &copyNs);
		uint64_t copyUs = copyNs / 1000;
		fStatCopyUs += copyUs;
		fStatCopies++;
	}
shown:
	fStatPresents++;
	{
		uint64_t presentNs = 0;
		absolutetime_to_nanoseconds(mach_absolute_time() - presentBegan, &presentNs);
		fStatPresentUs += presentNs / 1000;
	}
	/* Opt-in, bounded thumbnails of the framebuffer RAM after presentation.
	 * These describe copied pixels, not proof that the physical panel showed
	 * them. Hex rows fit the console line limit and use existing logging. */
	static unsigned int shots;
	int capture = 0;
	uintptr_t shotBase = fPixels;
	uint32_t shotRowBytes = fRowBytes;
	if (fShown != NULL) {
		/* The frame was shown straight from its surface; the panel RAM
		 * still holds an older frame, so read the one that went out. */
		uintptr_t shown = fShown->kernelAddress();
		if (shown != 0) {
			shotBase = shown + fShown->offset;
			shotRowBytes = fShown->bytesPerRow;
		}
	}
	/* Late frames show the desktop, which is what the panel really ends up
	 * displaying: the first swaps are the boot logo. */
	uint32_t shotFrame = get32(rec, 0x50);
	if (shots < 3 && shotFrame >= 120 && (shotFrame % 120) == 0 &&
	    PE_parse_boot_argn("sweetfbshot", &capture, sizeof(capture)) && capture == 1) {
		shots++;
		const uint32_t tw = 80, th = 178, frame = shotFrame;
		static const char hex[] = "0123456789abcdef";
		IOLog("FBIMG begin %u %u %u panel %u %u\n", frame, tw, th, fWidth, fHeight);
		for (uint32_t y = 0; y < th; y++) {
			char line[161];
			uint32_t py = (uint32_t)((uint64_t)(2 * y + 1) * fHeight / (2 * th));
			for (uint32_t x = 0; x < tw; x++) {
				uint32_t px = (uint32_t)((uint64_t)(2 * x + 1) * fWidth / (2 * tw));
				uint32_t pixel = *(volatile uint32_t *)(shotBase + (uint64_t)py * shotRowBytes + px * 4);
				uint32_t gray = (((pixel >> 16) & 255) * 77 +
				    ((pixel >> 8) & 255) * 150 + (pixel & 255) * 29) >> 8;
				line[x * 2] = hex[gray >> 4];
				line[x * 2 + 1] = hex[gray & 15];
			}
			line[160] = 0;
			IOLog("FBIMG row %u %u %s\n", frame, y, line);
		}
		IOLog("FBIMG end %u\n", frame);
	}
}

bool
IOMobileFramebufferUserClient::initWithTask(task_t owningTask, void *securityID, UInt32 type)
{
	if (!IOUserClient::initWithTask(owningTask, securityID, type)) {
		return false;
	}
	fTask = owningTask;
	return true;
}

bool
IOMobileFramebufferUserClient::start(IOService *provider)
{
	fFB = OSDynamicCast(AppleCLCD, provider);
	if (fFB == NULL || !IOUserClient::start(provider)) {
		return false;
	}
	fFB->addClient(this);
	return true;
}

IOReturn
IOMobileFramebufferUserClient::clientClose(void)
{
	if (fFB != NULL) {
		fFB->removeClient(this);
		IOLockLock(fFB->fLock);
		mach_port_t old = fPort;
		fPort = MACH_PORT_NULL;
		IOLockUnlock(fFB->fLock);
		if (old != MACH_PORT_NULL) {
			releaseNotificationPort(old);
		}
	}
	terminate();
	return kIOReturnSuccess;
}

IOReturn
IOMobileFramebufferUserClient::registerNotificationPort(mach_port_t port, __unused UInt32 type,
    __unused io_user_reference_t refCon)
{
	mach_port_t old;
	IOLockLock(fFB->fLock);
	old = fPort;
	fPort = port;
	IOLockUnlock(fFB->fLock);
	if (old != MACH_PORT_NULL) {
		releaseNotificationPort(old);
	}
	return kIOReturnSuccess;
}

/* Called with the framebuffer lock held. */
void
IOMobileFramebufferUserClient::notify(uint32_t type, const uint64_t *values, uint32_t count)
{
	OSAsyncReference64 ref;

	if (type >= SWEET_FB_NOTIFY_TYPES || fFunc[type] == 0 || fPort == MACH_PORT_NULL) {
		return;
	}
	bzero(ref, sizeof(ref));
	setAsyncReference64(ref, fPort, (mach_vm_address_t)fFunc[type], fRefcon[type], fTask);
	sendAsyncResult64WithOptions(ref, kIOReturnSuccess, (io_user_reference_t *)values, count,
	    kIOUserNotifyOptionCanDrop);
}

static uint64_t sweet_fb_logged;        /* selectors already logged (< 64) */

IOReturn
IOMobileFramebufferUserClient::externalMethod(uint32_t selector, IOExternalMethodArguments *args,
    __unused IOExternalMethodDispatch *dispatch, __unused OSObject *target,
    __unused void *reference)
{
	IOReturn ret = kIOReturnSuccess;
	uint64_t out[2] = { 0, 0 };
	uint32_t outCount = 0;

	switch (selector) {
	case kSweetFBGetLayerDefaultSurface:
		out[0] = sweet_surface_port_in_task(fFB->fSurface, fTask);
		outCount = 1;
		break;
	case kSweetFBSwapBegin:
		IOLockLock(fFB->fLock);
		out[0] = ++fFB->fSwapID;
		IOLockUnlock(fFB->fLock);
		outCount = 1;
		break;
	case kSweetFBSwapEnd: {
		uint8_t rec[SWEET_FB_SWAP_REC_SIZE];
		if (args->structureInputDescriptor != NULL ||
		    args->structureInputSize < SWEET_FB_SWAP_REC_SIZE) {
			ret = kIOReturnBadArgument;
			break;
		}
		memcpy(rec, args->structureInput, sizeof(rec));
		fFB->swap(rec);
		break;
	}
	case kSweetFBGetID:
		out[0] = 0;
		outCount = 1;
		break;
	case kSweetFBGetDisplaySize:
		out[0] = fFB->fLogicalW;
		out[1] = fFB->fLogicalH;
		outCount = 2;
		break;
	case kSweetFBIsMainDisplay:
		out[0] = 1;
		outCount = 1;
		break;
	case kSweetFBGetDotPitch:
		out[0] = SWEET_FB_DOT_PITCH;
		outCount = 1;
		break;
	case kSweetFBNotifications:
		if (args->scalarInputCount >= 3 && args->scalarInput[2] < SWEET_FB_NOTIFY_TYPES) {
			uint32_t type = (uint32_t)args->scalarInput[2];
			IOLockLock(fFB->fLock);
			fFunc[type] = args->scalarInput[0];
			fRefcon[type] = args->scalarInput[1];
			IOLockUnlock(fFB->fLock);
			char name[32];
			proc_selfname(name, sizeof(name));
			IOLog("SWEET FB: %s notifications type %u %s\n", name, type,
			    args->scalarInput[0] ? "on" : "off");
		}
		break;
	case kSweetFBRequestPowerChange:
		if (args->scalarInputCount >= 1) {
			bool off = args->scalarInput[0] == 0;
			IOLockLock(fFB->fLock);
			if (off != fFB->fPowerOff) {
				fFB->fPowerOff = off;
				fFB->fFadeStep = 0;
				fFB->fDirty = true;     /* on: redraw the latest frame */
			}
			IOLockUnlock(fFB->fLock);
			IOLog("SWEET FB: display %s\n", off ? "off" : "on");
		}
		break;
	case kSweetFBSwapWait:
	default:
		break;
	}

	for (uint32_t i = 0; i < args->scalarOutputCount; i++) {
		args->scalarOutput[i] = i < outCount ? out[i] : 0;
	}
	if (args->structureOutputDescriptor == NULL && args->structureOutput != NULL) {
		bzero(args->structureOutput, args->structureOutputSize);
	}
	if (selector >= 64 || !(sweet_fb_logged & (1ULL << selector))) {
		if (selector < 64) {
			sweet_fb_logged |= 1ULL << selector;
		}
		char name[32];
		proc_selfname(name, sizeof(name));
		IOLog("SWEET FB: %s sel %u in %u/%u out %u/%u -> 0x%x\n", name, selector,
		    args->scalarInputCount, args->structureInputSize,
		    args->scalarOutputCount, args->structureOutputSize, ret);
	}
	return ret;
}
#endif /* SWEET */

/*
 * Built-in IOSurface for SWEET.
 *
 * Apple's IOSurface kext is closed. IOSurface.framework (iOS 16.7) finds
 * IOServiceNameMatching("IOCoreSurfaceRoot"), opens it with type 0 and
 * talks to the user client below:
 *
 *   sel 13  limits: struct out 0x18, u16 at +0x16 must be 1 (ABI version),
 *           +0 offset alignment mask, +4 bytes-per-row alignment mask
 *   sel 0   create: scalar [parent id], struct in = serialized property
 *           dictionary (binary or XML), struct out = lock result (0xa68)
 *   sel 4   lookup by id: scalar [id], struct out = lock result
 *   sel 34  lookup by mach port: scalar [port name], struct out = lock result
 *   sel 35  create mach port: scalars [id, options], scalar out [port name]
 *   traps   0/1 use count +/- (id, category), 2 lock / 3 unlock
 *           (id, options, &seed), 4/5 release (id)
 *
 * The lock result tells the library where the surface lives in the
 * caller's address space: +0 pixels, +8 shared state page (registry id,
 * seed at +0xc), +0x10 timestamps, then id, sizes and geometry (layout in
 * sweet_surface_fill_result). Every surface is wired kernel memory mapped
 * into each task that creates or looks it up; mappings are counted per
 * client and dropped on release or close.
 *
 * Values, notifications, accelerators and shared events are accepted and
 * ignored (logged once per selector).
 */
#include "SweetSurface.h"

#if defined(SWEET)
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOKitKeys.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <IOKit/system.h>
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSUnserialize.h>
#include <sys/proc.h>
#include <mach/mach_time.h>
#include <pexpert/pexpert.h>

enum {
	kSweetSurfaceCreate             = 0,
	kSweetSurfaceLookup             = 4,
	kSweetSurfaceWrapClientImage    = 6,
	kSweetSurfaceWrapClientMemory   = 7,
	kSweetSurfaceLimits             = 13,
	kSweetSurfaceLookupFromPort     = 34,
	kSweetSurfaceCreatePort         = 35,
};

enum {
	kSweetSurfaceTrapIncrementUse   = 0,
	kSweetSurfaceTrapDecrementUse   = 1,
	kSweetSurfaceTrapLock           = 2,
	kSweetSurfaceTrapUnlock         = 3,
	kSweetSurfaceTrapRelease        = 4,
	kSweetSurfaceTrapReleaseFailed  = 5,
};

#define SWEET_SURFACE_RESULT_SIZE       0xa68
#define SWEET_SURFACE_MAX_ID            4096
#define SWEET_SURFACE_MAX_DIM           16384
#define SWEET_SURFACE_MAX_ALLOC         (256u << 20)
#define SWEET_SURFACE_MAX_PROPS         (64u << 10)
#define SWEET_SURFACE_ROW_ALIGN         64
#define SWEET_SURFACE_TIMESTAMPS        0x800   /* offset in the state page */
#define SWEET_SURFACE_STATE_SEED        0xc     /* u32 in the state page */
#define SWEET_SURFACE_STATE_USE         0x18    /* low/high u32 use categories */
#define kIOSurfaceLockReadOnly          0x1

static IOLock       *sweet_surface_lock;
static SweetSurface *sweet_surface_table[SWEET_SURFACE_MAX_ID];
static uint32_t      sweet_surface_users[SWEET_SURFACE_MAX_ID];
static uint32_t      sweet_surface_next_id = 1;
static uint32_t      sweet_surface_creates_logged;
static uint64_t      sweet_surface_wired;       /* bytes of buffer surfaces */
static TUNABLE(uint32_t, sweet_surface_gles, "sweetgles", 0);
#define SWEET_SURFACE_WIRED_MAX         (1536ull << 20)

/*
 * The compositor makes a panel-sized surface for every frame it commits and
 * drops it again as soon as the next one is made. Allocating 18.4 MB of
 * physically contiguous memory and zeroing it through a write-combined
 * mapping took longer than the frame the surface was for, so a buffer
 * released by a task is kept here for the next surface of the same size that
 * the same task makes. Only buffers this kernel allocated are kept, and only
 * for their own task, so no other process's pixels are handed on; a buffer
 * that has already belonged to the task does not need zeroing again.
 */
#define SWEET_SURFACE_CACHE             6
#define SWEET_SURFACE_CACHE_MIN         (4u << 20)

struct sweet_surface_cached {
	IOBufferMemoryDescriptor *memory;
	uint64_t                 size;
	task_t                   task;          /* may be handed it back */
	uint64_t                 kept;          /* sequence, for the oldest */
};

static struct sweet_surface_cached sweet_surface_cache[SWEET_SURFACE_CACHE];
static uint64_t sweet_surface_cache_seq;
static uint64_t sweet_surface_cache_hits, sweet_surface_cache_misses;
static uint64_t sweet_surface_slow_alloc_us, sweet_surface_slow_zero_us;
static uint32_t sweet_surface_slow_logged;

static uint64_t
sweet_surface_now(void)
{
	uint64_t t = 0;
	absolutetime_to_nanoseconds(mach_absolute_time(), &t);
	return t / 1000;
}

/* A buffer of this size that task had before, taken out of the cache, or NULL.
 * The wired budget does not move: the buffer was counted when it was first
 * allocated and stays counted while it is held here and by its next surface. */
static IOBufferMemoryDescriptor *
sweet_surface_cache_take(uint64_t size, task_t task)
{
	IOBufferMemoryDescriptor *memory = NULL;

	if (sweet_surface_lock == NULL || task == TASK_NULL) {
		return NULL;
	}
	IOLockLock(sweet_surface_lock);
	for (unsigned int i = 0; i < SWEET_SURFACE_CACHE; i++) {
		if (sweet_surface_cache[i].memory == NULL || sweet_surface_cache[i].size != size ||
		    sweet_surface_cache[i].task != task) {
			continue;
		}
		memory = sweet_surface_cache[i].memory;
		sweet_surface_cache[i].memory = NULL;   /* the reference is the caller's now */
		if (sweet_surface_cache[i].task != TASK_NULL) {
			task_deallocate(sweet_surface_cache[i].task);
		}
		bzero(&sweet_surface_cache[i], sizeof(sweet_surface_cache[i]));
		break;
	}
	IOLockUnlock(sweet_surface_lock);
	if (memory != NULL) {
		sweet_surface_cache_hits++;
	} else {
		sweet_surface_cache_misses++;
	}
	return memory;
}

/* Keep a buffer for task to make its next surface of this size from. Takes
 * the caller's reference when it says yes; the wired budget does not move,
 * because the buffer was already counted while the surface held it. */
static bool
sweet_surface_cache_keep(IOBufferMemoryDescriptor *memory, uint64_t size, task_t task)
{
	unsigned int slot = SWEET_SURFACE_CACHE;
	IOBufferMemoryDescriptor *evicted = NULL;
	bool kept = false;

	if (memory == NULL || task == TASK_NULL || size < SWEET_SURFACE_CACHE_MIN ||
	    sweet_surface_lock == NULL) {
		return false;
	}
	IOLockLock(sweet_surface_lock);
	for (unsigned int i = 0; i < SWEET_SURFACE_CACHE; i++) {
		if (sweet_surface_cache[i].memory == NULL) {
			slot = i;
			break;
		}
		if (slot == SWEET_SURFACE_CACHE || sweet_surface_cache[i].kept < sweet_surface_cache[slot].kept) {
			slot = i;       /* the oldest goes */
		}
	}
	if (slot != SWEET_SURFACE_CACHE) {
		if (sweet_surface_cache[slot].memory != NULL) {
			/* Released outside the lock: it is 18 MB of pages. */
			evicted = sweet_surface_cache[slot].memory;
			if (sweet_surface_cache[slot].task != TASK_NULL) {
				task_deallocate(sweet_surface_cache[slot].task);
			}
			OSAddAtomic64(-(SInt64)sweet_surface_cache[slot].size, (volatile SInt64 *)&sweet_surface_wired);
		}
		task_reference(task);
		sweet_surface_cache[slot].memory = memory;      /* takes the caller's reference */
		sweet_surface_cache[slot].size = size;
		sweet_surface_cache[slot].task = task;
		sweet_surface_cache[slot].kept = ++sweet_surface_cache_seq;
		kept = true;
	}
	IOLockUnlock(sweet_surface_lock);
	if (evicted != NULL) {
		evicted->release();
	}
	return kept;
}


OSDefineMetaClassAndStructors(SweetSurface, OSObject);

/* Boot-arg sweetsurf_low4g=1 also asks for panel-sized surfaces below 4 GiB,
 * the range the overlays were known to fetch from before the 34-bit address
 * register was used. */
static TUNABLE(bool, sweet_surface_low4g, "sweetsurf_low4g", false);

void
SweetSurface::free()
{
	bool owned = OSDynamicCast(IOBufferMemoryDescriptor, memory) != NULL;

	OSSafeReleaseNULL(kernelMap);
	if (memoryPrepared && memory != NULL) {
		memory->complete();
	}
	if (owned && !memoryPrepared &&
	    sweet_surface_cache_keep((IOBufferMemoryDescriptor *)memory, allocSize, owner)) {
		memory = NULL;          /* wired budget and reference moved with it */
	} else if (owned) {
		OSAddAtomic64(-(SInt64)allocSize, (volatile SInt64 *)&sweet_surface_wired);
	}
	OSSafeReleaseNULL(memory);
	OSSafeReleaseNULL(state);
	if (owner != TASK_NULL) {
		task_deallocate(owner);
		owner = TASK_NULL;
	}
	OSObject::free();
}

uintptr_t
SweetSurface::kernelAddress(){
	IOBufferMemoryDescriptor *buffer = OSDynamicCast(IOBufferMemoryDescriptor, memory);
	if (buffer != NULL) {
		return (uintptr_t)buffer->getBytesNoCopy();
	}
	IOLockLock(sweet_surface_lock);
	if (kernelMap == NULL && memory != NULL) {
		kernelMap = memory->map(mapCache | (memoryReadOnly ? kIOMapReadOnly : 0));
	}
	IOLockUnlock(sweet_surface_lock);
	return kernelMap != NULL ? (uintptr_t)kernelMap->getVirtualAddress() : 0;
}

/* Only a buffer this kernel allocated is known to be one contiguous run of
 * physical memory with a stable address. Caller-owned and pinned descriptors
 * are mapped through the kernel map (or belong to someone else), so they get
 * the CPU copy instead of being handed to the display overlays. */
uint64_t
SweetSurface::physicalAddress()
{
	IOBufferMemoryDescriptor *buffer = OSDynamicCast(IOBufferMemoryDescriptor, memory);
	if (buffer == NULL || allocSize == 0 || (offset & 0x3f) != 0 || !contiguous) {
		return 0;
	}
	return (uint64_t)buffer->getPhysicalAddress() + offset;
}

/* Runs of physical memory the pixels are in: 1 means the display overlay can
 * fetch the whole surface from one address. 0 when the pages cannot be named. */
static uint32_t
sweet_surface_phys_runs(SweetSurface *s, uint64_t *first)
{
	IOByteCount at = 0, length = 0;
	uint64_t expected = 0;
	uint32_t breaks = 0;

	if (first != NULL) {
		*first = 0;
	}
	if (s->memory == NULL || s->allocSize == 0) {
		return 0;
	}
	while (at < s->allocSize) {
		addr64_t pa = s->memory->getPhysicalSegment(at, &length, 0);
		if (pa == 0 || length == 0) {
			return 0;
		}
		if (at == 0) {
			if (first != NULL) {
				*first = pa;
			}
		} else if (pa != expected) {
			breaks++;
		}
		expected = pa + length;
		at += length;
	}
	return breaks + 1;
}

static uint32_t *
sweet_surface_seed(SweetSurface *s)
{
	return (uint32_t *)((uint8_t *)s->state->getBytesNoCopy() + SWEET_SURFACE_STATE_SEED);
}

/* IOSurfaceClientIsInUse (20H392) reads this entire aligned 64-bit field;
 * IsInUseForCategory reads its low/high half. Publish both together so a
 * concurrent reader cannot observe a transient zero between categories.
 * Caller holds sweet_surface_lock. */
static bool
sweet_surface_use_locked(SweetSurface *s, uint32_t category, int64_t delta)
{
	if (category >= 2 || delta < -(int64_t)s->useCount[category] ||
	    delta > (int64_t)UINT32_MAX - s->useCount[category]) {
		return false;
	}
	s->useCount[category] = (uint32_t)((int64_t)s->useCount[category] + delta);
	uint64_t counts = (uint64_t)s->useCount[1] << 32 | s->useCount[0];
	__atomic_store_n((uint64_t *)((uint8_t *)s->state->getBytesNoCopy() +
	    SWEET_SURFACE_STATE_USE), counts, __ATOMIC_RELEASE);
	return true;
}

SweetSurface *
sweet_surface_begin_use(uint32_t id)
{
	SweetSurface *s = NULL;
	if (id == 0 || id >= SWEET_SURFACE_MAX_ID || sweet_surface_lock == NULL) {
		return NULL;
	}
	IOLockLock(sweet_surface_lock);
	s = sweet_surface_table[id];
	if (s != NULL) {
		if (sweet_surface_use_locked(s, 0, 1)) {
			s->retain();
		} else {
			s = NULL;
		}
	}
	IOLockUnlock(sweet_surface_lock);
	return s;
}

void
sweet_surface_end_use(SweetSurface *s)
{
	if (s == NULL) {
		return;
	}
	IOLockLock(sweet_surface_lock);
	(void)sweet_surface_use_locked(s, 0, -1);
	IOLockUnlock(sweet_surface_lock);
	s->release();
}

/*
 * Give the surface an id and a table slot; the table holds one retain and a
 * user count (creators, client mappings, kernel holders). Lock held.
 */
static bool
sweet_surface_insert_locked(SweetSurface *s)
{
	for (uint32_t n = 0; n < SWEET_SURFACE_MAX_ID - 1; n++) {
		uint32_t id = sweet_surface_next_id;
		sweet_surface_next_id = id + 1 < SWEET_SURFACE_MAX_ID ? id + 1 : 1;
		if (sweet_surface_table[id] == NULL) {
			s->id = id;
			s->retain();
			sweet_surface_table[id] = s;
			sweet_surface_users[id] = 1;
			*(uint64_t *)s->state->getBytesNoCopy() = id;   /* registry id */
			return true;
		}
	}
	return false;
}

/* Add a user to a surface that may have left the table (port lookups). */
static bool
sweet_surface_get(SweetSurface *s)
{
	bool ok = true;
	IOLockLock(sweet_surface_lock);
	if (sweet_surface_table[s->id] == s) {
		sweet_surface_users[s->id]++;
	} else if (sweet_surface_table[s->id] == NULL) {
		s->retain();
		sweet_surface_table[s->id] = s;
		sweet_surface_users[s->id] = 1;
	} else {
		ok = false;
	}
	IOLockUnlock(sweet_surface_lock);
	return ok;
}

/* Drop a user; the last one removes the surface from the table. */
static void
sweet_surface_put(SweetSurface *s)
{
	bool drop = false;
	IOLockLock(sweet_surface_lock);
	if (sweet_surface_table[s->id] == s && --sweet_surface_users[s->id] == 0) {
		sweet_surface_table[s->id] = NULL;
		drop = true;
	}
	IOLockUnlock(sweet_surface_lock);
	if (drop) {
		s->release();
	}
}

SweetSurface *
sweet_surface_copy(uint32_t id)
{
	SweetSurface *s = NULL;
	if (id == 0 || id >= SWEET_SURFACE_MAX_ID || sweet_surface_lock == NULL) {
		return NULL;
	}
	IOLockLock(sweet_surface_lock);
	s = sweet_surface_table[id];
	if (s != NULL) {
		s->retain();
	}
	IOLockUnlock(sweet_surface_lock);
	return s;
}

/* The table lock; the framebuffer may publish a surface before the root starts. */
static void
sweet_surface_init_lock(void)
{
	if (sweet_surface_lock == NULL) {
		IOLock *lock = IOLockAlloc();
		if (!OSCompareAndSwapPtr(NULL, lock, &sweet_surface_lock)) {
			IOLockFree(lock);
		}
	}
}

static SweetSurface *
sweet_surface_alloc(void)
{
	sweet_surface_init_lock();
	SweetSurface *s = OSTypeAlloc(SweetSurface);
	if (s == NULL || !s->init()) {
		OSSafeReleaseNULL(s);
		return NULL;
	}
	s->state = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
	    kIODirectionInOut | kIOMemoryKernelUserShared, page_size, page_size);
	if (s->state == NULL) {
		s->release();
		return NULL;
	}
	bzero(s->state->getBytesNoCopy(), page_size);
	return s;
}

static bool
sweet_surface_publish(SweetSurface *s)
{
	bool ok;
	IOLockLock(sweet_surface_lock);
	ok = sweet_surface_insert_locked(s);
	IOLockUnlock(sweet_surface_lock);
	return ok;
}

SweetSurface *
sweet_surface_create_physical(uint64_t phys, uint32_t width, uint32_t height,
    uint32_t bytesPerRow, uint32_t pixelFormat)
{
	SweetSurface *s = sweet_surface_alloc();
	if (s == NULL) {
		return NULL;
	}
	s->width = width;
	s->height = height;
	s->bytesPerRow = bytesPerRow;
	s->bytesPerElement = 4;
	s->elementWidth = s->elementHeight = 1;
	s->pixelFormat = pixelFormat;
	s->allocSize = (uint32_t)round_page(bytesPerRow * height);
	s->memory = IOMemoryDescriptor::withPhysicalAddress(phys, s->allocSize, kIODirectionInOut);
	s->mapCache = kIOMapWriteCombineCache;
	s->contiguous = s->memory != NULL;      /* one pinned physical range */
	if (s->memory == NULL || !sweet_surface_publish(s)) {
		s->release();
		return NULL;
	}
	return s;
}

mach_port_name_t
sweet_surface_port_in_task(SweetSurface *s, task_t task)
{
	mach_port_name_t name = 0;
	IOUserClient::copyPortNameForObjectInTask(task, s, &name);
	return name;
}

static uint32_t
sweet_dict_u32(OSDictionary *dict, const char *key, uint32_t def)
{
	OSNumber *n = OSDynamicCast(OSNumber, dict->getObject(key));
	return n != NULL ? n->unsigned32BitValue() : def;
}

static uint32_t
sweet_div_up(uint32_t a, uint32_t b)
{
	return b > 1 ? (a + b - 1) / b : a;
}

static uint32_t
sweet_row_bytes(uint32_t width, uint32_t ew, uint32_t bpe)
{
	uint64_t row = (uint64_t)sweet_div_up(width, ew) * bpe;
	return (uint32_t)((row + SWEET_SURFACE_ROW_ALIGN - 1) & ~(uint64_t)(SWEET_SURFACE_ROW_ALIGN - 1));
}

/* Geometry from the creation properties (IOSurface.h keys). */
static IOReturn
sweet_surface_layout(SweetSurface *s, OSDictionary *props)
{
	uint64_t end;
	bool wrapped = props->getObject("IOSurfaceAddress") != NULL;
	bool bgra = false;
	if (props->getObject("IOSurfaceAddressRanges") != NULL) {
		return kIOReturnUnsupported;
	}

	s->width = sweet_dict_u32(props, "IOSurfaceWidth", 0);
	s->height = sweet_dict_u32(props, "IOSurfaceHeight", 0);
	s->bytesPerElement = (uint16_t)sweet_dict_u32(props, "IOSurfaceBytesPerElement", 4);
	s->elementWidth = (uint8_t)sweet_dict_u32(props, "IOSurfaceElementWidth", 1);
	s->elementHeight = (uint8_t)sweet_dict_u32(props, "IOSurfaceElementHeight", 1);
	s->pixelFormat = sweet_dict_u32(props, "IOSurfacePixelFormat", 0);
	/* HakiOS negotiates new XR targets as BGRA before anyone draws into
	 * them. Report the new layout; never reinterpret caller-owned pixels. */
	if ((sweet_surface_gles == 1 || sweet_surface_gles == 2) && !wrapped &&
	    (s->pixelFormat == 'b3a8' || s->pixelFormat == 'w30r' ||
	    s->pixelFormat == 'l10r' || s->pixelFormat == 'w30R')) {
		bgra = true;
		s->pixelFormat = 'BGRA';
		s->bytesPerElement = 4;
		s->elementWidth = s->elementHeight = 1;
	}
	s->cacheMode = sweet_dict_u32(props, "IOSurfaceCacheMode", 0);
	s->offset = sweet_dict_u32(props, "IOSurfaceOffset", 0);
	if (s->width > SWEET_SURFACE_MAX_DIM || s->height > SWEET_SURFACE_MAX_DIM ||
	    s->bytesPerElement > 64 || s->elementWidth == 0 || s->elementHeight == 0) {
		return kIOReturnBadArgument;
	}
	s->bytesPerRow = sweet_dict_u32(props, "IOSurfaceBytesPerRow",
	    sweet_row_bytes(s->width, s->elementWidth, s->bytesPerElement));
	if (s->bytesPerRow < (uint64_t)sweet_div_up(s->width, s->elementWidth) * s->bytesPerElement) {
		return kIOReturnBadArgument;
	}
	end = (uint64_t)s->offset + (uint64_t)s->bytesPerRow * sweet_div_up(s->height, s->elementHeight);

	OSArray *planes = bgra ? NULL : OSDynamicCast(OSArray, props->getObject("IOSurfacePlaneInfo"));
	s->planeCount = 0;
	if (planes != NULL) {
		uint64_t next = s->offset;
		if (planes->getCount() > SWEET_SURFACE_MAX_PLANES) {
			return kIOReturnBadArgument;
		}
		for (unsigned int i = 0; i < planes->getCount(); i++) {
			OSDictionary *pd = OSDynamicCast(OSDictionary, planes->getObject(i));
			struct sweet_surface_plane *p = &s->planes[i];
			if (pd == NULL) {
				return kIOReturnBadArgument;
			}
			p->width = sweet_dict_u32(pd, "IOSurfacePlaneWidth", s->width);
			p->height = sweet_dict_u32(pd, "IOSurfacePlaneHeight", s->height);
			p->bytesPerElement = (uint16_t)sweet_dict_u32(pd, "IOSurfacePlaneBytesPerElement", 1);
			p->elementWidth = (uint8_t)sweet_dict_u32(pd, "IOSurfacePlaneElementWidth", 1);
			p->elementHeight = (uint8_t)sweet_dict_u32(pd, "IOSurfacePlaneElementHeight", 1);
			if (p->width > SWEET_SURFACE_MAX_DIM || p->height > SWEET_SURFACE_MAX_DIM ||
			    p->bytesPerElement > 64 || p->elementWidth == 0 || p->elementHeight == 0) {
				return kIOReturnBadArgument;
			}
			p->bytesPerRow = sweet_dict_u32(pd, "IOSurfacePlaneBytesPerRow",
			    sweet_row_bytes(p->width, p->elementWidth, p->bytesPerElement));
			p->offset = sweet_dict_u32(pd, "IOSurfacePlaneOffset", (uint32_t)next);
			p->size = sweet_dict_u32(pd, "IOSurfacePlaneSize",
			    p->bytesPerRow * sweet_div_up(p->height, p->elementHeight));
			next = round_page((uint64_t)p->offset + p->size);
			if ((uint64_t)p->offset + p->size > end) {
				end = (uint64_t)p->offset + p->size;
			}
		}
		s->planeCount = planes->getCount();
	}

	uint64_t alloc = sweet_dict_u32(props, "IOSurfaceAllocSize", 0);
	if (wrapped) {
		/* Borrowed memory ends exactly where its owner says. */
		if (alloc < end) {
			return kIOReturnBadArgument;
		}
	} else {
		if (bgra || alloc < end) {
			alloc = end;
		}
		alloc = round_page(alloc);
	}
	if (alloc == 0 || alloc > SWEET_SURFACE_MAX_ALLOC) {
		return kIOReturnNoMemory;
	}
	s->allocSize = (uint32_t)alloc;
	return kIOReturnSuccess;
}

/* Per-client mapping of one surface. */
class SweetSurfaceRef : public OSObject {
	OSDeclareDefaultStructors(SweetSurfaceRef);

public:
	SweetSurface *surface;
	IOMemoryMap  *data;
	IOMemoryMap  *state;
	uint32_t      count;
	uint32_t      useCount[2];     /* this client's contributions only */

	bool init() APPLE_KEXT_OVERRIDE { return OSObject::init(); }
	void free() APPLE_KEXT_OVERRIDE;
};

OSDefineMetaClassAndStructors(SweetSurfaceRef, OSObject);

void
SweetSurfaceRef::free()
{
	OSSafeReleaseNULL(data);
	OSSafeReleaseNULL(state);
	if (surface != NULL) {
		IOLockLock(sweet_surface_lock);
		for (uint32_t category = 0; category < 2; category++) {
			(void)sweet_surface_use_locked(surface, category, -(int64_t)useCount[category]);
			useCount[category] = 0;
		}
		IOLockUnlock(sweet_surface_lock);
		sweet_surface_put(surface);
		surface->release();
		surface = NULL;
	}
	OSObject::free();
}

class IOCoreSurfaceRoot : public IOService {
	OSDeclareDefaultStructors(IOCoreSurfaceRoot);

public:
	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
};

OSDefineMetaClassAndStructors(IOCoreSurfaceRoot, IOService);

bool
IOCoreSurfaceRoot::start(IOService *provider)
{
	if (!IOService::start(provider)) {
		return false;
	}
	sweet_surface_init_lock();
	setProperty(kIOUserClientClassKey, "IOSurfaceRootUserClient");
	registerService();
	IOLog("SWEET SURF: IOCoreSurfaceRoot registered\n");
	return true;
}

class IOSurfaceRootUserClient : public IOUserClient {
	OSDeclareDefaultStructors(IOSurfaceRootUserClient);

	task_t   fTask;
	IOLock  *fLock;
	OSArray *fRefs;

	SweetSurfaceRef *findRef(uint32_t id, unsigned int *index);
	IOReturn addRef(SweetSurface *s, SweetSurfaceRef **refOut, bool *existing);
	IOReturn returnSurface(SweetSurface *s, IOExternalMethodArguments *args);
	IOReturn create(IOExternalMethodArguments *args);
	IOReturn changeUseCount(uint32_t id, uint32_t category, int delta);

public:
	bool initWithTask(task_t owningTask, void *securityID, UInt32 type) APPLE_KEXT_OVERRIDE;
	void free() APPLE_KEXT_OVERRIDE;
	IOReturn clientClose(void) APPLE_KEXT_OVERRIDE;
	IOReturn externalMethod(uint32_t selector, IOExternalMethodArguments *args,
	    IOExternalMethodDispatch *dispatch, OSObject *target,
	    void *reference) APPLE_KEXT_OVERRIDE;
	IOExternalTrap *getTargetAndTrapForIndex(IOService **targetP, UInt32 index) APPLE_KEXT_OVERRIDE;

	IOReturn trapIncrementUse(void *id, void *category, void *, void *, void *, void *);
	IOReturn trapDecrementUse(void *id, void *category, void *, void *, void *, void *);
	IOReturn trapLock(void *id, void *options, void *seedOut, void *, void *, void *);
	IOReturn trapUnlock(void *id, void *options, void *seedOut, void *, void *, void *);
	IOReturn trapRelease(void *id, void *, void *, void *, void *, void *);
};

OSDefineMetaClassAndStructors(IOSurfaceRootUserClient, IOUserClient);

static uint64_t sweet_surf_logged;      /* selectors / traps already logged (< 64) */

static void
sweet_surf_log_once(const char *what, uint32_t n)
{
	uint32_t bit = (what[0] == 't' ? 48 : 0) + n;
	if (bit >= 64 || (sweet_surf_logged & (1ULL << bit))) {
		return;
	}
	sweet_surf_logged |= 1ULL << bit;
	char name[32];
	proc_selfname(name, sizeof(name));
	IOLog("SWEET SURF: %s %s %u\n", name, what, n);
}

bool
IOSurfaceRootUserClient::initWithTask(task_t owningTask, void *securityID, UInt32 type)
{
	if (!IOUserClient::initWithTask(owningTask, securityID, type)) {
		return false;
	}
	fTask = owningTask;
	fLock = IOLockAlloc();
	fRefs = OSArray::withCapacity(16);
	return fLock != NULL && fRefs != NULL;
}

void
IOSurfaceRootUserClient::free()
{
	OSSafeReleaseNULL(fRefs);
	if (fLock != NULL) {
		IOLockFree(fLock);
		fLock = NULL;
	}
	IOUserClient::free();
}

IOReturn
IOSurfaceRootUserClient::clientClose(void)
{
	IOLockLock(fLock);
	fRefs->flushCollection();
	IOLockUnlock(fLock);
	terminate();
	return kIOReturnSuccess;
}

/* Lock held. */
SweetSurfaceRef *
IOSurfaceRootUserClient::findRef(uint32_t id, unsigned int *index)
{
	for (unsigned int i = 0; i < fRefs->getCount(); i++) {
		SweetSurfaceRef *ref = (SweetSurfaceRef *)fRefs->getObject(i);
		if (ref->surface->id == id) {
			if (index != NULL) {
				*index = i;
			}
			return ref;
		}
	}
	return NULL;
}

/*
 * Count one more create/lookup of s by this client, mapping it into the
 * task the first time. Takes over the caller's user count on s.
 *
 * A lookup of a surface this client already has maps nothing and counts
 * nothing (*existing): IOSurface.framework then gets base address -1 in the
 * lock result and reuses its cached IOSurfaceClient. Counting it instead
 * made the framework build a second client for the same id; releasing that
 * one (traps 4 + 5) unmapped the memory under the first (backboardd
 * snapshot crash in CGDataProviderCreateWithData).
 */
IOReturn
IOSurfaceRootUserClient::addRef(SweetSurface *s, SweetSurfaceRef **refOut, bool *existing)
{
	*existing = false;
	IOLockLock(fLock);
	SweetSurfaceRef *ref = findRef(s->id, NULL);
	if (ref != NULL && ref->surface == s) {
		IOLockUnlock(fLock);
		sweet_surface_put(s);
		*refOut = ref;
		*existing = true;
		return kIOReturnSuccess;
	}
	IOLockUnlock(fLock);

	ref = OSTypeAlloc(SweetSurfaceRef);
	if (ref == NULL || !ref->init()) {
		OSSafeReleaseNULL(ref);
		sweet_surface_put(s);
		return kIOReturnNoMemory;
	}
	s->retain();
	ref->surface = s;
	ref->count = 1;
	ref->data = s->memory->createMappingInTask(fTask, 0, kIOMapAnywhere | s->mapCache |
	    (s->memoryReadOnly ? kIOMapReadOnly : 0));
	ref->state = s->state->createMappingInTask(fTask, 0, kIOMapAnywhere | kIOMapReadOnly);
	if (ref->data == NULL || ref->state == NULL) {
		ref->release();
		return kIOReturnVMError;
	}
	IOLockLock(fLock);
	SweetSurfaceRef *raced = findRef(s->id, NULL);
	if (raced != NULL && raced->surface == s) {
		/* Another thread mapped it meanwhile: count there, drop ours. */
		raced->count++;
		IOLockUnlock(fLock);
		ref->release();
		*refOut = raced;
		return kIOReturnSuccess;
	}
	fRefs->setObject(ref);
	IOLockUnlock(fLock);
	ref->release();         /* fRefs holds it */
	*refOut = ref;
	return kIOReturnSuccess;
}

static void
put32(uint8_t *p, uint32_t off, uint32_t v)
{
	memcpy(p + off, &v, 4);
}

static void
put64(uint8_t *p, uint32_t off, uint64_t v)
{
	memcpy(p + off, &v, 8);
}

/* IOSurface.framework's lock result (copied to IOSurfaceClient + 0x68). */
static void
sweet_surface_fill_result(SweetSurface *s, SweetSurfaceRef *ref, uint8_t *out)
{
	bzero(out, SWEET_SURFACE_RESULT_SIZE);
	put64(out, 0x00, ref->data->getAddress());
	put64(out, 0x08, ref->state->getAddress());
	put64(out, 0x10, ref->state->getAddress() + SWEET_SURFACE_TIMESTAMPS);
	put32(out, 0x18, s->id);
	put32(out, 0x1c, s->allocSize);
	put32(out, 0x20, s->width);
	put32(out, 0x24, s->height);
	put32(out, 0x28, s->bytesPerRow);
	put32(out, 0x2c, s->offset);
	put32(out, 0x30, s->pixelFormat);
	put32(out, 0x34, s->allocSize);
	put32(out, 0x38, s->planeCount);
	memcpy(out + 0x40, &s->bytesPerElement, 2);
	out[0x42] = s->elementWidth;
	out[0x43] = s->elementHeight;
	put32(out, 0x44, s->cacheMode);
	put32(out, 0x50, s->parentID);
	put32(out, 0x54, 1);
	for (uint32_t i = 0; i < (s->planeCount ? s->planeCount : 1); i++) {
		uint8_t *p = out + 0x68 + i * 0x80;
		const struct sweet_surface_plane linear = { s->width, s->height, s->bytesPerRow,
		    s->offset, s->allocSize - s->offset, s->bytesPerElement, s->elementWidth, s->elementHeight };
		const struct sweet_surface_plane *pl = s->planeCount ? &s->planes[i] : &linear;
		put32(p, 0x00, pl->width);
		put32(p, 0x04, pl->height);
		put32(p, 0x10, pl->bytesPerRow);
		put32(p, 0x0c, pl->offset);
		put32(p, 0x14, pl->size);
		memcpy(p + 0x18, &pl->bytesPerElement, 2);
		p[0x1a] = pl->elementWidth;
		p[0x1b] = pl->elementHeight;
	}
}

static IOReturn
sweet_struct_out(IOExternalMethodArguments *args, const void *buf, uint32_t len)
{
	if (args->structureOutputDescriptor != NULL) {
		if (len > args->structureOutputDescriptor->getLength()) {
			return kIOReturnNoSpace;
		}
		args->structureOutputDescriptor->writeBytes(0, buf, len);
		args->structureOutputDescriptorSize = len;
		return kIOReturnSuccess;
	}
	if (args->structureOutput == NULL || len > args->structureOutputSize) {
		return kIOReturnNoSpace;
	}
	memcpy(args->structureOutput, buf, len);
	args->structureOutputSize = len;
	return kIOReturnSuccess;
}

/* Map s for this client and answer with its lock result. Takes s's user count. */
IOReturn
IOSurfaceRootUserClient::returnSurface(SweetSurface *s, IOExternalMethodArguments *args)
{
	SweetSurfaceRef *ref = NULL;
	bool existing = false;
	IOReturn ret = addRef(s, &ref, &existing);
	if (ret != kIOReturnSuccess) {
		return ret;
	}
	uint8_t *out = (uint8_t *)IOMallocZeroData(SWEET_SURFACE_RESULT_SIZE);
	if (out == NULL) {
		ret = kIOReturnNoMemory;
	} else {
		sweet_surface_fill_result(s, ref, out);
		if (existing) {
			put64(out, 0x00, ~0ULL);        /* "already yours": use the cached client */
		}
		ret = sweet_struct_out(args, out, SWEET_SURFACE_RESULT_SIZE);
		IOFreeData(out, SWEET_SURFACE_RESULT_SIZE);
	}
	if (ret != kIOReturnSuccess && !existing) {
		/* The caller never learns the id: undo this create/lookup. */
		trapRelease((void *)(uintptr_t)s->id, NULL, NULL, NULL, NULL, NULL);
	}
	return ret;
}

/* Retain the caller's backing pages so later writes remain visible. */
static IOReturn
sweet_surface_pin(SweetSurface *s, uint64_t address, task_t task)
{
	if (address == 0 || address + s->allocSize < address) {
		return kIOReturnBadArgument;
	}
	for (unsigned int attempt = 0; attempt < 2; attempt++) {
		IOMemoryDescriptor *mem = IOMemoryDescriptor::withAddressRange(address, s->allocSize,
		    attempt == 0 ? kIODirectionInOut : kIODirectionOut, task);
		if (mem != NULL) {
			if (mem->prepare() == kIOReturnSuccess) {
				s->memory = mem;
				s->memoryPrepared = true;
				s->memoryReadOnly = attempt != 0;
				s->mapCache = kIOMapDefaultCache;
				return kIOReturnSuccess;
			}
			mem->release();
		}
	}
	return kIOReturnVMError;
}

IOReturn
IOSurfaceRootUserClient::create(IOExternalMethodArguments *args)
{
	const char *buf = (const char *)args->structureInput;
	uint32_t len = args->structureInputSize;
	char *copy = NULL;
	IOReturn ret;

	if (args->structureInputDescriptor != NULL) {
		len = (uint32_t)args->structureInputDescriptor->getLength();
		if (len == 0 || len > SWEET_SURFACE_MAX_PROPS) {
			return kIOReturnBadArgument;
		}
		copy = (char *)IOMallocData(len);
		if (copy == NULL) {
			return kIOReturnNoMemory;
		}
		args->structureInputDescriptor->readBytes(0, copy, len);
		buf = copy;
	}
	OSObject *obj = (buf != NULL && len > 0) ? OSUnserializeXML(buf, len) : NULL;
	if (copy != NULL) {
		IOFreeData(copy, len);
	}
	OSDictionary *props = OSDynamicCast(OSDictionary, obj);
	if (props == NULL) {
		OSSafeReleaseNULL(obj);
		IOLog("SWEET SURF: create: bad property dictionary (%u bytes)\n", len);
		return kIOReturnBadArgument;
	}

	SweetSurface *s = sweet_surface_alloc();
	if (s == NULL) {
		props->release();
		return kIOReturnNoMemory;
	}
	s->parentID = args->scalarInputCount > 0 ? (uint32_t)args->scalarInput[0] : 0;
	ret = sweet_surface_layout(s, props);
	OSObject *address = props->getObject("IOSurfaceAddress");
	if (ret == kIOReturnSuccess && address != NULL) {
		OSNumber *value = OSDynamicCast(OSNumber, address);
		ret = value != NULL ? sweet_surface_pin(s, value->unsigned64BitValue(), fTask) : kIOReturnBadArgument;
		if (ret == kIOReturnSuccess && !sweet_surface_publish(s)) {
			ret = kIOReturnNoResources;
		}
	} else if (ret == kIOReturnSuccess) {
		IOBufferMemoryDescriptor *mem = NULL;
		bool reused = false;
		uint64_t began = sweet_surface_now();
		/* A surface this task made before and gave up: same size, already
		 * zeroed by its first use, already one run of physical memory. */
		mem = sweet_surface_cache_take(s->allocSize, fTask);
		reused = mem != NULL;
		if (mem == NULL &&
		    (uint64_t)OSAddAtomic64(s->allocSize, (volatile SInt64 *)&sweet_surface_wired) +
		    s->allocSize <= SWEET_SURFACE_WIRED_MAX) {
			/* G710 is not CPU-cache coherent. New owned BGRA surfaces use
			 * Normal-NC in every CPU mapping, matching the GPU's NC PTEs.
			 * Borrowed buffers retain their owner's cache policy. */
			IOOptionBits cache = sweet_surface_gles == 2 && s->pixelFormat == 'BGRA'
			    ? kIOMapWriteCombineCache : 0;
			IOOptionBits options = kIODirectionInOut | kIOMemoryKernelUserShared |
			    kIOMemoryMapperNone | cache;
			/* A frame the display shows is fetched by the overlays from one
			 * physical address, so a surface of a panel-sized frame has to be
			 * one run of physical memory. Ask for that first; a scattered
			 * buffer still works for every other use and is presented by the
			 * CPU copy. */
			if (s->pixelFormat == 'BGRA' && s->allocSize >= (4u << 20)) {
				mem = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task,
				    options | kIOMemoryPhysicallyContiguous, s->allocSize,
				    sweet_surface_low4g ? 0xffffffffull : ~(uint64_t)0);
			}
			if (mem == NULL) {
				mem = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
				    options, s->allocSize, page_size);
			}
		}
		if (mem == NULL) {
			if (!reused) {
				OSAddAtomic64(-(SInt64)s->allocSize, (volatile SInt64 *)&sweet_surface_wired);
			}
			ret = kIOReturnNoMemory;
		} else {
			uint64_t first = 0;
			uint64_t allocated = sweet_surface_now();
			/* Only a buffer that has never belonged to this task has to be
			 * zeroed; one that came back from the cache holds its own
			 * pixels, which IOSurface never promises to be zeroed anyway. */
			if (!reused) {
				bzero(mem->getBytesNoCopy(), s->allocSize);
			}
			uint64_t zeroed = sweet_surface_now();
			if (!reused && (allocated - began >= 2000 || zeroed - allocated >= 2000) &&
			    sweet_surface_slow_logged < 12) {
				sweet_surface_slow_logged++;
				sweet_surface_slow_alloc_us = allocated - began;
				sweet_surface_slow_zero_us = zeroed - allocated;
				IOLog("SWEET SURF: slow buffer %u bytes alloc %llu us zero %llu us\n",
				    s->allocSize, sweet_surface_slow_alloc_us, sweet_surface_slow_zero_us);
			}
			s->memory = mem;
			/* Remember who may have it back when it is given up. */
			task_reference(fTask);
			s->owner = fTask;
			s->mapCache = sweet_surface_gles == 2 && s->pixelFormat == 'BGRA'
			    ? kIOMapWriteCombineCache : kIOMapDefaultCache;
			s->contiguous = sweet_surface_phys_runs(s, &first) == 1;
			s->physFirst = first;
			if (!sweet_surface_publish(s)) {
				ret = kIOReturnNoResources;
			}
		}
	}
	if (ret != kIOReturnSuccess || sweet_surface_creates_logged < 64) {
		char name[32];
		proc_selfname(name, sizeof(name));
		sweet_surface_creates_logged++;
			IOLog("SWEET SURF: %s create id %u %ux%u fmt 0x%x bpe %u bpr %u planes %u size %u keys %u phys 0x%llx contig %u -> 0x%x\n",
		    name, s->id, s->width, s->height, s->pixelFormat, s->bytesPerElement,
		    s->bytesPerRow, s->planeCount, s->allocSize, props->getCount(),
		    s->physFirst, s->contiguous ? 1 : 0, ret);
	}
	props->release();
	if (ret != kIOReturnSuccess) {
		s->release();
		return ret;
	}
	ret = returnSurface(s, args);   /* takes the creator's user count */
	s->release();
	return ret;
}

IOReturn
IOSurfaceRootUserClient::externalMethod(uint32_t selector, IOExternalMethodArguments *args,
    __unused IOExternalMethodDispatch *dispatch, __unused OSObject *target,
    __unused void *reference)
{
	uint64_t arg0 = args->scalarInputCount > 0 ? args->scalarInput[0] : 0;

	switch (selector) {
	case kSweetSurfaceLimits: {
		uint8_t limits[0x18] = { 0 };
		put32(limits, 0x0, SWEET_SURFACE_ROW_ALIGN - 1);
		put32(limits, 0x4, SWEET_SURFACE_ROW_ALIGN - 1);
		limits[0x16] = 1;
		sweet_surf_log_once("sel", selector);
		return sweet_struct_out(args, limits, sizeof(limits));
	}
	case kSweetSurfaceCreate:
		return create(args);
	case kSweetSurfaceWrapClientImage:
	case kSweetSurfaceWrapClientMemory:
		/* 20H392 constructs a client on success. A zero result gives it a
		 * NULL shared page and crashes GetBulkAttachments at address0x30.
		 * Refuse these shortcuts, as HakiOS does, so callers can fall back. */
		sweet_surf_log_once("unsupported", selector);
		return kIOReturnUnsupported;
	case kSweetSurfaceLookup: {
		SweetSurface *s = sweet_surface_copy((uint32_t)arg0);
		IOReturn ret = kIOReturnNotFound;
		if (s != NULL) {
			ret = sweet_surface_get(s) ? returnSurface(s, args) : kIOReturnNotFound;
			s->release();
		}
		sweet_surf_log_once("sel", selector);
		return ret;
	}
	case kSweetSurfaceLookupFromPort: {
		OSObject *obj = NULL;
		IOReturn ret = IOUserClient::copyObjectForPortNameInTask(fTask, (mach_port_name_t)arg0, &obj);
		SweetSurface *s = OSDynamicCast(SweetSurface, obj);
		if (ret == kIOReturnSuccess) {
			ret = (s != NULL && sweet_surface_get(s)) ? returnSurface(s, args) : kIOReturnBadArgument;
		}
		OSSafeReleaseNULL(obj);
		sweet_surf_log_once("sel", selector);
		return ret;
	}
	case kSweetSurfaceCreatePort: {
		SweetSurface *s = sweet_surface_copy((uint32_t)arg0);
		if (s == NULL || args->scalarOutputCount < 1) {
			OSSafeReleaseNULL(s);
			return kIOReturnNotFound;
		}
		args->scalarOutput[0] = sweet_surface_port_in_task(s, fTask);
		args->scalarOutputCount = 1;
		s->release();
		sweet_surf_log_once("sel", selector);
		return kIOReturnSuccess;
	}
	default:
		/* Values, notifications, ownership, timestamps...: accept and ignore. */
		for (uint32_t i = 0; i < args->scalarOutputCount; i++) {
			args->scalarOutput[i] = 0;
		}
		if (args->structureOutput != NULL) {
			bzero(args->structureOutput, args->structureOutputSize);
		}
		sweet_surf_log_once("sel", selector);
		return kIOReturnSuccess;
	}
}

IOReturn
IOSurfaceRootUserClient::changeUseCount(uint32_t id, uint32_t category, int delta)
{
	if (category >= 2) {
		return kIOReturnBadArgument;
	}
	IOReturn ret = kIOReturnNotFound;
	IOLockLock(fLock);
	SweetSurfaceRef *ref = findRef(id, NULL);
	if (ref != NULL) {
		ret = kIOReturnBadArgument;
		/* A client must not decrement the framebuffer's or another client's
		 * contribution. These are library zero/nonzero transitions, not the
		 * full process-local count returned by IOSurfaceGetUseCount. */
		if ((delta > 0 && ref->useCount[category] < UINT32_MAX) ||
		    (delta < 0 && ref->useCount[category] != 0)) {
			IOLockLock(sweet_surface_lock);
			if (sweet_surface_use_locked(ref->surface, category, delta)) {
				ref->useCount[category] += delta;
				ret = kIOReturnSuccess;
			}
			IOLockUnlock(sweet_surface_lock);
		}
	}
	IOLockUnlock(fLock);
	return ret;
}

IOReturn
IOSurfaceRootUserClient::trapIncrementUse(void *id, void *category,
    void *, void *, void *, void *)
{
	return changeUseCount((uint32_t)(uintptr_t)id, (uint32_t)(uintptr_t)category, 1);
}

IOReturn
IOSurfaceRootUserClient::trapDecrementUse(void *id, void *category,
    void *, void *, void *, void *)
{
	return changeUseCount((uint32_t)(uintptr_t)id, (uint32_t)(uintptr_t)category, -1);
}

static IOReturn
sweet_surface_seed_out(SweetSurface *s, void *seedOut, bool bump)
{
	uint32_t seed = bump ? OSIncrementAtomic((volatile SInt32 *)sweet_surface_seed(s)) + 1 :
	    *sweet_surface_seed(s);
	if (seedOut != NULL && copyout(&seed, (user_addr_t)seedOut, sizeof(seed)) != 0) {
		return kIOReturnBadArgument;
	}
	return kIOReturnSuccess;
}

/* Retained surface this client created or looked up, or NULL. */
static SweetSurface *
sweet_surface_held(IOLock *lock, OSArray *refs, uint32_t id)
{
	SweetSurface *s = NULL;
	IOLockLock(lock);
	for (unsigned int i = 0; i < refs->getCount(); i++) {
		SweetSurfaceRef *ref = (SweetSurfaceRef *)refs->getObject(i);
		if (ref->surface->id == id) {
			s = ref->surface;
			s->retain();
			break;
		}
	}
	IOLockUnlock(lock);
	return s;
}

IOReturn
IOSurfaceRootUserClient::trapLock(void *id, __unused void *options, void *seedOut,
    void *, void *, void *)
{
	SweetSurface *s = sweet_surface_held(fLock, fRefs, (uint32_t)(uintptr_t)id);
	if (s == NULL) {
		return kIOReturnNotFound;
	}
	IOReturn ret = sweet_surface_seed_out(s, seedOut, false);
	s->release();
	return ret;
}

IOReturn
IOSurfaceRootUserClient::trapUnlock(void *id, void *options, void *seedOut,
    void *, void *, void *)
{
	SweetSurface *s = sweet_surface_held(fLock, fRefs, (uint32_t)(uintptr_t)id);
	if (s == NULL) {
		return kIOReturnNotFound;
	}
	IOReturn ret = sweet_surface_seed_out(s, seedOut,
	    !((uintptr_t)options & kIOSurfaceLockReadOnly));
	s->release();
	return ret;
}

IOReturn
IOSurfaceRootUserClient::trapRelease(void *id, void *, void *, void *, void *, void *)
{
	unsigned int index;
	IOLockLock(fLock);
	SweetSurfaceRef *ref = findRef((uint32_t)(uintptr_t)id, &index);
	if (ref != NULL && --ref->count == 0) {
		fRefs->removeObject(index);     /* unmaps and drops the user count */
	}
	IOLockUnlock(fLock);
	return ref != NULL ? kIOReturnSuccess : kIOReturnNotFound;
}

static IOExternalTrap sweet_surface_traps[] = {
	[kSweetSurfaceTrapIncrementUse]  = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapIncrementUse },
	[kSweetSurfaceTrapDecrementUse]  = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapDecrementUse },
	[kSweetSurfaceTrapLock]          = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapLock },
	[kSweetSurfaceTrapUnlock]        = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapUnlock },
	[kSweetSurfaceTrapRelease]       = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapRelease },
	[kSweetSurfaceTrapReleaseFailed] = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapRelease },
};

IOExternalTrap *
IOSurfaceRootUserClient::getTargetAndTrapForIndex(IOService **targetP, UInt32 index)
{
	if (index >= sizeof(sweet_surface_traps) / sizeof(sweet_surface_traps[0])) {
		sweet_surf_log_once("trap", index);
		return NULL;
	}
	sweet_surf_log_once("trap", index);
	*targetP = this;
	return &sweet_surface_traps[index];
}
#endif /* SWEET */

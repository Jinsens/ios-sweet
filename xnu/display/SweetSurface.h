/*
 * Built-in IOSurface for SWEET (see SweetSurface.cpp).
 *
 * Shared with SweetFramebuffer.cpp: the framebuffer looks surfaces up by
 * id when a swap names them and publishes the panel memory itself as the
 * default framebuffer surface.
 */
#ifndef _SWEET_SURFACE_H
#define _SWEET_SURFACE_H

#include <pexpert/arm64/board_config.h>

#if defined(SWEET)
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <libkern/c++/OSObject.h>
#include <mach/mach_types.h>

#define SWEET_SURFACE_MAX_PLANES        20

struct sweet_surface_plane {
	uint32_t width, height, bytesPerRow, offset, size;
	uint16_t bytesPerElement;
	uint8_t  elementWidth, elementHeight;
};

class SweetSurface : public OSObject {
	OSDeclareDefaultStructors(SweetSurface);

public:
	uint32_t id;
	uint32_t width, height, bytesPerRow, offset, pixelFormat, allocSize;
	uint16_t bytesPerElement;
	uint8_t  elementWidth, elementHeight;
	uint32_t cacheMode, planeCount, parentID;
	uint32_t useCount[2];      /* shared state +0x18, protected by the table lock */
	struct sweet_surface_plane planes[SWEET_SURFACE_MAX_PLANES];

	bool                      memoryPrepared, memoryReadOnly;
	/* The pixels are one run of physical memory. The display overlays fetch
	 * a frame from a single physical address, so a scattered surface can
	 * only be presented by the CPU copy. */
	bool                      contiguous;
	uint64_t                  physFirst;    /* first physical address, 0 if unknown */
	/* The task this surface was made for. A buffer released here is only
	 * handed back to the same task (see the free list in SweetSurface.cpp),
	 * so one process's pixels never become another's. */
	task_t                    owner;
	IOMemoryDescriptor       *memory;       /* pixels (buffer, physical or caller-owned) */
	IOOptionBits              mapCache;     /* cache mode for user mappings */
	IOBufferMemoryDescriptor *state;        /* shared state page, read-only for users */
	IOMemoryMap              *kernelMap;    /* lazily created for kernel copies */

	bool init() APPLE_KEXT_OVERRIDE { return OSObject::init(); }
	void free() APPLE_KEXT_OVERRIDE;

	/* Kernel address of the pixels (mapped on first use), or 0. */
	uintptr_t kernelAddress();

	/* Physical address of the first pixel, when the pixels are one
	 * physically contiguous buffer this kernel allocated; 0 otherwise.
	 * The display overlays fetch from a physical address, so a surface
	 * that cannot name one is presented with the CPU copy. */
	uint64_t physicalAddress();
};

/* Retained surface for an id, or NULL. */
SweetSurface *sweet_surface_copy(uint32_t id);

/* Retain and mark a queued/displayed frame in use atomically with lookup.
 * The matching end releases both. An object retain alone does not stop CA
 * from drawing into the surface again. */
SweetSurface *sweet_surface_begin_use(uint32_t id);
void sweet_surface_end_use(SweetSurface *surface);

/*
 * Publish physical memory (the panel framebuffer) as a surface. Returns a
 * retained surface that stays in the id table until released by the caller
 * and every user.
 */
SweetSurface *sweet_surface_create_physical(uint64_t phys, uint32_t width,
    uint32_t height, uint32_t bytesPerRow, uint32_t pixelFormat);

/* Send right for the surface in task (IOSurfaceLookupFromMachPort). */
mach_port_name_t sweet_surface_port_in_task(SweetSurface *surface, task_t task);

#endif /* SWEET */
#endif /* _SWEET_SURFACE_H */

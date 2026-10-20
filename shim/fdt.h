#pragma once
#include <stdint.h>

/*
 * Just enough flattened-device-tree parsing to read what the bootloader
 * left in the live tree. Big-endian, 4-byte aligned tokens; values are
 * returned as pointers into the (Device-memory) blob, so callers must use
 * the be32 helpers below rather than dereferencing them directly.
 */
uint32_t fdt_be32(uintptr_t p);
uint64_t fdt_be64(uintptr_t p);

/* Look up `prop` in the node at `path` (e.g. "/chosen"). 1 on success. */
int fdt_prop(uintptr_t fdt, const char *path, const char *prop,
	     const void **val, uint32_t *len);

/* First cell of a u32 property, or `def` when absent/short. */
uint32_t fdt_u32(uintptr_t fdt, const char *path, const char *prop, uint32_t def);

/* 32- or 64-bit property (4-byte cells are accepted), or `def`. */
uint64_t fdt_u64(uintptr_t fdt, const char *path, const char *prop, uint64_t def);

/* NUL-terminated string property, or NULL. */
const char *fdt_str(uintptr_t fdt, const char *path, const char *prop);

/* reg = <addr_hi addr_lo size_hi size_lo>; returns 0 when absent/malformed. */
int fdt_reg(uintptr_t fdt, const char *path, uint64_t *addr, uint64_t *size);

/* idx-th (addr, size) pair of a multi-entry reg property. */
int fdt_reg_n(uintptr_t fdt, const char *path, int idx, uint64_t *addr, uint64_t *size);

/* idx-th string of a string-list property (compatible, clock-names, ...). */
const char *fdt_str_n(uintptr_t fdt, const char *path, const char *prop, int idx);

/* Full path of the first node whose compatible list contains `want`. */
int fdt_find_compatible(uintptr_t fdt, const char *want, char *path, int pathlen);

/* /chosen/linux,initrd-start/end as set by the bootloader. */
int fdt_initrd(uintptr_t fdt, uint64_t *start, uint64_t *end);

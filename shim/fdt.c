/*
 * Flattened-device-tree reader.
 *
 * Both bootloaders we care about (Qualcomm ABL on sweet, MediaTek LK on
 * matisse) hand us a live FDT in x0 and leave the same /chosen contract, so
 * everything the shim needs about the board is read from the tree instead of
 * being hardcoded per SoC:
 *   - /chosen linux,initrd-start/end  (payload location)
 *   - /memory reg                     (RAM to hand to XNU)
 *   - reserved-memory regs           (carve-outs to keep out of the way)
 *   - the GIC node                    (distributor + redistributor bases)
 *   - the ufshci node                 (UFS host base)
 *   - /chosen atag,videolfb*          (framebuffer base/size, MTK)
 *
 * Strings are compared byte by byte because the blob lives in Device memory
 * with the MMU off; only aligned words are ever loaded.
 */
#include "fdt.h"

#define FDT_MAGIC	0xd00dfeed
#define FDT_BEGIN_NODE	1
#define FDT_END_NODE	2
#define FDT_PROP	3
#define FDT_NOP		4
#define FDT_END		9

#define MAX_DEPTH	16

uint32_t fdt_be32(uintptr_t p)
{
	return __builtin_bswap32(*(volatile uint32_t *)p);
}

uint64_t fdt_be64(uintptr_t p)
{
	return (uint64_t)fdt_be32(p) << 32 | fdt_be32(p + 4);
}

static uintptr_t align4(uintptr_t a)
{
	return (a + 3) & ~(uintptr_t)3;
}

static uintptr_t skip_str(uintptr_t p)
{
	while (*(volatile char *)p)
		p++;
	return p + 1;
}

static int streq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

/* Split a node path into components; returns the component count. */
static int split_path(const char *path, const char **comp, int max)
{
	int n = 0;

	while (*path == '/')
		path++;
	while (*path) {
		if (n == max)
			return -1;
		comp[n++] = path;
		while (*path && *path != '/')
			path++;
		if (*path == '/')
			path++;
	}
	return n;
}

/* Length of a path component (up to '/' or NUL). */
static int comp_len(const char *c)
{
	int n = 0;

	while (c[n] && c[n] != '/')
		n++;
	return n;
}

static int name_is(const char *name, const char *comp, int len)
{
	for (int i = 0; i < len; i++)
		if (name[i] != comp[i])
			return 0;
	return name[len] == 0;
}

/*
 * Walk the structure block, tracking whether the current node is on the
 * path to the target. `mstack[d]` is 1 when the node at depth d matched its
 * path component, so a subtree that diverges is skipped without ever
 * comparing names again.
 */
struct walk {
	const char *comp[MAX_DEPTH];
	int ncomp;
	int mstack[MAX_DEPTH + 1];
	int in_target;
};

static int walk_start(struct walk *w, const char *path)
{
	w->ncomp = split_path(path, w->comp, MAX_DEPTH);
	if (w->ncomp < 0 || w->ncomp > MAX_DEPTH)
		return 0;
	w->mstack[0] = 1;
	w->in_target = 0;
	return 1;
}

typedef int (*prop_cb)(const char *name, uintptr_t val, uint32_t len, void *arg);

/*
 * Visit every property of `path`. The callback returns 1 to stop the walk
 * (fdt_walk then returns 1) or 0 to continue (returns 0 at END_NODE).
 */
static int fdt_walk(uintptr_t fdt, const char *path, prop_cb cb, void *arg)
{
	struct walk w;
	uintptr_t p, strings;
	int depth = 0;

	if (!fdt || fdt_be32(fdt) != FDT_MAGIC || !walk_start(&w, path))
		return 0;
	p = fdt + fdt_be32(fdt + 8);
	strings = fdt + fdt_be32(fdt + 12);

	for (;;) {
		uint32_t tok = fdt_be32(p);
		p += 4;
		switch (tok) {
		case FDT_BEGIN_NODE: {
			const char *name = (const char *)p;
			/* `depth` counts components: the root node is depth 0 */
			if (depth > MAX_DEPTH)
				return 0;
			w.mstack[depth] = depth == 0 ? 1 :
				(w.mstack[depth - 1] &&
				 name_is(name, w.comp[depth - 1],
					 comp_len(w.comp[depth - 1])));
			w.in_target = w.mstack[depth] && depth == w.ncomp;
			depth++;
			p = align4(skip_str(p));
			break;
		}
		case FDT_END_NODE:
			if (w.in_target)
				return 0;
			depth--;
			break;
		case FDT_PROP: {
			uint32_t len = fdt_be32(p), nameoff = fdt_be32(p + 4);
			uintptr_t val = p + 8;
			if (w.in_target && cb((const char *)(strings + nameoff), val, len, arg))
				return 1;
			p = align4(val + len);
			break;
		}
		case FDT_NOP:
			break;
		default:
			return 0;
		}
	}
}

struct prop_find {
	const char *name;
	const void *val;
	uint32_t len;
	int found;
};

static int cb_find(const char *name, uintptr_t val, uint32_t len, void *arg)
{
	struct prop_find *f = arg;

	if (!streq(name, f->name))
		return 0;
	f->val = (const void *)val;
	f->len = len;
	f->found = 1;
	return 1;
}

int fdt_prop(uintptr_t fdt, const char *path, const char *prop,
	     const void **val, uint32_t *len)
{
	struct prop_find f = { prop, 0, 0, 0 };

	if (!fdt_walk(fdt, path, cb_find, &f))
		return 0;
	if (val)
		*val = f.val;
	if (len)
		*len = f.len;
	return 1;
}

uint32_t fdt_u32(uintptr_t fdt, const char *path, const char *prop, uint32_t def)
{
	const void *v;
	uint32_t len;

	if (!fdt_prop(fdt, path, prop, &v, &len) || len < 4)
		return def;
	return fdt_be32((uintptr_t)v);
}

uint64_t fdt_u64(uintptr_t fdt, const char *path, const char *prop, uint64_t def)
{
	const void *v;
	uint32_t len;

	if (!fdt_prop(fdt, path, prop, &v, &len))
		return def;
	if (len == 8)
		return fdt_be64((uintptr_t)v);
	if (len == 4)
		return fdt_be32((uintptr_t)v);
	return def;
}

const char *fdt_str(uintptr_t fdt, const char *path, const char *prop)
{
	const void *v;
	uint32_t len;

	if (!fdt_prop(fdt, path, prop, &v, &len) || !len)
		return 0;
	return (const char *)v;
}

int fdt_reg(uintptr_t fdt, const char *path, uint64_t *addr, uint64_t *size)
{
	const void *v;
	uint32_t len;

	if (!fdt_prop(fdt, path, "reg", &v, &len))
		return 0;
	if (len == 16) {
		*addr = fdt_be64((uintptr_t)v);
		*size = fdt_be64((uintptr_t)v + 8);
		return 1;
	}
	if (len >= 8) {		/* 1-address-cell form */
		*addr = fdt_be32((uintptr_t)v);
		*size = fdt_be32((uintptr_t)v + 4);
		return 1;
	}
	return 0;
}

int fdt_reg_n(uintptr_t fdt, const char *path, int idx, uint64_t *addr, uint64_t *size)
{
	const void *v;
	uint32_t len;

	if (!fdt_prop(fdt, path, "reg", &v, &len))
		return 0;
	if (len % 16 == 0) {			/* 2 address + 2 size cells */
		if ((uint32_t)(idx + 1) * 16 > len)
			return 0;
		*addr = fdt_be64((uintptr_t)v + idx * 16);
		*size = fdt_be64((uintptr_t)v + idx * 16 + 8);
		return 1;
	}
	if (len % 8 == 0) {			/* 1 + 1 cells */
		if ((uint32_t)(idx + 1) * 8 > len)
			return 0;
		*addr = fdt_be32((uintptr_t)v + idx * 8);
		*size = fdt_be32((uintptr_t)v + idx * 8 + 4);
		return 1;
	}
	return 0;
}

const char *fdt_str_n(uintptr_t fdt, const char *path, const char *prop, int idx)
{
	const void *v;
	uint32_t len;
	uintptr_t p, end;

	if (!fdt_prop(fdt, path, prop, &v, &len))
		return 0;
	p = (uintptr_t)v;
	end = p + len;
	for (int i = 0; p < end; i++) {
		if (i == idx)
			return (const char *)p;
		p = skip_str(p);
	}
	return 0;
}

/* Does the node at `p` (just past BEGIN_NODE) list `want` in compatible? */
static int node_has_compatible(uintptr_t p, uintptr_t strings, const char *want)
{
	for (;;) {
		uint32_t tok = fdt_be32(p);

		if (tok != FDT_PROP)
			return 0;
		uint32_t len = fdt_be32(p), nameoff = fdt_be32(p + 4);
		uintptr_t val = p + 8;
		if (streq((const char *)(strings + nameoff), "compatible")) {
			uintptr_t q = val, end = val + len;
			while (q < end) {
				if (streq((const char *)q, want))
					return 1;
				q = skip_str(q);
			}
			return 0;
		}
		p = align4(val + len);
	}
}

int fdt_find_compatible(uintptr_t fdt, const char *want, char *path, int pathlen)
{
	uintptr_t p, strings;
	char cur[192];
	int len = 0, depth = 0;

	if (!fdt || fdt_be32(fdt) != FDT_MAGIC)
		return 0;
	p = fdt + fdt_be32(fdt + 8);
	strings = fdt + fdt_be32(fdt + 12);
	cur[0] = 0;

	for (;;) {
		uint32_t tok = fdt_be32(p);
		p += 4;
		switch (tok) {
		case FDT_BEGIN_NODE: {
			const char *name = (const char *)p;
			int n = 0;

			while (name[n])
				n++;
			if (depth > 0 && len + n + 2 < (int)sizeof(cur)) {
				cur[len++] = '/';
				for (int i = 0; i < n; i++)
					cur[len++] = name[i];
				cur[len] = 0;
			}
			depth++;
			uintptr_t props = align4(p + n + 1);
			if (depth > 1 && node_has_compatible(props, strings, want)) {
				int i = 0;
				for (; i < len && i < pathlen - 1; i++)
					path[i] = cur[i];
				path[i] = 0;
				return 1;
			}
			p = props;
			break;
		}
		case FDT_END_NODE:
			depth--;
			if (depth == 0)
				return 0;
			while (len > 0 && cur[len - 1] != '/')
				len--;
			if (len > 0)
				len--;
			cur[len] = 0;
			break;
		case FDT_PROP: {
			uint32_t l = fdt_be32(p), nameoff = fdt_be32(p + 4);
			(void)nameoff;
			p = align4(p + 8 + l);
			break;
		}
		case FDT_NOP:
			break;
		default:
			return 0;
		}
	}
}

int fdt_initrd(uintptr_t fdt, uint64_t *start, uint64_t *end)
{
	const void *v;
	uint32_t len;

	if (!fdt_prop(fdt, "/chosen", "linux,initrd-start", &v, &len))
		return 0;
	*start = len == 8 ? fdt_be64((uintptr_t)v) : fdt_be32((uintptr_t)v);
	if (!fdt_prop(fdt, "/chosen", "linux,initrd-end", &v, &len))
		return 0;
	*end = len == 8 ? fdt_be64((uintptr_t)v) : fdt_be32((uintptr_t)v);
	return 1;
}

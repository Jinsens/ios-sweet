/*
 * UFS host driver for the shim: read-only probing, GPT lookup and a small
 * log writer.
 *
 * Runs with the MMU off (all memory Device, so DMA is coherent with no cache
 * maintenance). The bootloader has already brought the link up and read the
 * kernel from LUN 0, so we only take over the transfer request list and
 * issue SCSI commands. Register layout and descriptor formats: JEDEC
 * UFSHCI (JESD223); both the Qualcomm (sweet) and MediaTek (matisse) hosts
 * implement it.
 */
#include <stdint.h>
#include "board.h"
#include "console.h"
#include "ufs.h"

#define REG_CAP		0x00
#define REG_VER		0x08
#define REG_IS		0x20
#define REG_HCS		0x30
#define REG_HCE		0x34
#define REG_UTRLBA	0x50
#define REG_UTRLBAU	0x54
#define REG_UTRLDBR	0x58
#define REG_UTRLCLR	0x5c
#define REG_UTRLRSR	0x60

#define HCS_DP		(1u << 0)
#define HCS_UTRLRDY	(1u << 1)
#define HCS_UCRDY	(1u << 3)
#define HCS_UCCS	(0x1fu << 8)

#define BLOCK		4096u

static uint64_t ufs_base(void)
{
	return brd.ufs_base;
}

static inline uint32_t ufs_rd(uint32_t off) { return *(volatile uint32_t *)(ufs_base() + off); }
static inline void ufs_wr(uint32_t off, uint32_t v) { *(volatile uint32_t *)(ufs_base() + off) = v; }

/* UTP transfer request descriptor (32 bytes). */
struct utrd {
	uint32_t dw0;		/* [31:28] CT, [26:25] DD, [24] I */
	uint32_t dw1;
	uint32_t dw2;		/* [7:0] OCS */
	uint32_t dw3;
	uint32_t ucdba;		/* command descriptor base, 128-byte aligned */
	uint32_t ucdbau;
	uint32_t resp;		/* [31:16] response UPIU offset, [15:0] length (DWORDs) */
	uint32_t prdt;		/* [31:16] PRDT offset, [15:0] entries */
};

/* UTP command descriptor: command UPIU, response UPIU, PRDT. */
struct ucd {
	uint8_t cmd[512];
	uint8_t resp[512];
	struct {
		uint32_t addr_lo, addr_hi, rsvd, dbc;	/* dbc = byte count - 1 */
	} prdt[1];
};

static struct utrd utrl[32] __attribute__((aligned(1024)));
static struct ucd ucd __attribute__((aligned(128)));
static uint8_t data[8 * BLOCK] __attribute__((aligned(4096)));

volatile uint64_t ufs_test_stage;
volatile uint64_t ufs_test_info[4];

static uint64_t ticks(void)
{
	uint64_t t;
	__asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(t));
	return t;
}

static void zero(void *p, unsigned n)
{
	volatile uint8_t *b = p;
	for (unsigned i = 0; i < n; i++)
		b[i] = 0;
}

static uint32_t be32(const uint8_t *p)
{
	return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static uint32_t le32(const uint8_t *p)
{
	return (uint32_t)p[3] << 24 | (uint32_t)p[2] << 16 | (uint32_t)p[1] << 8 | p[0];
}

static uint64_t le64(const uint8_t *p)
{
	uint64_t v = 0;
	for (int i = 7; i >= 0; i--)
		v = v << 8 | p[i];
	return v;
}

int ufs_ready(void)
{
	uint32_t hcs = ufs_rd(REG_HCS), hce = ufs_rd(REG_HCE);

	return (hce & 1) && (hcs & HCS_DP) && (hcs & HCS_UTRLRDY) && !ufs_rd(REG_UTRLDBR);
}

/*
 * Issue one SCSI command in slot 0.
 * dir: 0 = no data, 1 = device to host, 2 = host to device.
 * Returns 0 on success, otherwise a code describing the failure stage.
 */
static uint32_t scsi(uint8_t lun, const uint8_t *cdb, unsigned cdb_len, void *buf, uint32_t len,
    int dir, uint32_t *ocs_out, uint32_t *status_out)
{
	static uint8_t tag;
	zero(&utrl[0], sizeof(utrl[0]));
	zero(&ucd, sizeof(ucd));

	/* Command UPIU: header (12 bytes), expected length, CDB. */
	ucd.cmd[0] = 0x01;			/* transaction type: COMMAND */
	ucd.cmd[1] = dir == 1 ? 0x40 : dir == 2 ? 0x20 : 0x00;	/* R / W data direction */
	ucd.cmd[2] = lun;
	ucd.cmd[3] = ++tag;
	ucd.cmd[12] = len >> 24;
	ucd.cmd[13] = len >> 16;
	ucd.cmd[14] = len >> 8;
	ucd.cmd[15] = len;
	for (unsigned i = 0; i < cdb_len; i++)
		ucd.cmd[16 + i] = cdb[i];

	uintptr_t ucd_pa = (uintptr_t)&ucd;
	uint32_t dd = dir == 1 ? 2u : dir == 2 ? 1u : 0u;
	utrl[0].dw0 = (1u << 28) | (dd << 25) | (1u << 24);	/* UFS storage, interrupt */
	utrl[0].dw2 = 0x0f;			/* OCS: invalid until the host updates it */
	utrl[0].ucdba = (uint32_t)ucd_pa;
	utrl[0].ucdbau = (uint32_t)(ucd_pa >> 32);
	utrl[0].resp = (512u / 4) << 16 | (512u / 4);
	if (len) {
		uintptr_t buf_pa = (uintptr_t)buf;
		ucd.prdt[0].addr_lo = (uint32_t)buf_pa;
		ucd.prdt[0].addr_hi = (uint32_t)(buf_pa >> 32);
		ucd.prdt[0].dbc = len - 1;
		utrl[0].prdt = (1024u / 4) << 16 | 1;
	}
	__asm__ volatile("dsb sy" ::: "memory");

	ufs_wr(REG_UTRLDBR, 1);
	uint64_t start = ticks();
	uint64_t freq;
	__asm__ volatile("isb; mrs %0, cntfrq_el0" : "=r"(freq));
	while (ufs_rd(REG_UTRLDBR) & 1) {
		if (ticks() - start > freq) {		/* 1 s at the real CNTFRQ */
			ufs_wr(REG_UTRLCLR, ~1u);	/* clear slot 0 (write 0 to the bit) */
			return 1;			/* timeout */
		}
	}
	__asm__ volatile("dsb sy" ::: "memory");
	*ocs_out = utrl[0].dw2 & 0xff;
	*status_out = ucd.resp[7];		/* SCSI status in the response UPIU */
	if (*ocs_out)
		return 2;
	if (ucd.resp[0] != 0x21)		/* RESPONSE UPIU */
		return 3;
	return *status_out ? 4 : 0;
}

static uint32_t read_blocks(uint32_t lba, uint32_t count, void *buf)
{
	uint8_t cdb[10] = { 0x28, 0, lba >> 24, lba >> 16, lba >> 8, lba, 0, count >> 8, count, 0 };
	uint32_t ocs, st;
	return scsi(0, cdb, sizeof(cdb), buf, count * BLOCK, 1, &ocs, &st);
}

static uint32_t write_blocks(uint32_t lba, uint32_t count, const void *buf)
{
	uint8_t cdb[10] = { 0x2a, 0, lba >> 24, lba >> 16, lba >> 8, lba, 0, count >> 8, count, 0 };
	uint32_t ocs, st;
	return scsi(0, cdb, sizeof(cdb), (void *)buf, count * BLOCK, 2, &ocs, &st);
}

/* Take over the transfer request list; returns 0 when the host is ours. */
static int claim_host(void)
{
	if (!ufs_ready())
		return 1;
	ufs_wr(REG_UTRLRSR, 0);
	uintptr_t list = (uintptr_t)utrl;
	ufs_wr(REG_UTRLBA, (uint32_t)list);
	ufs_wr(REG_UTRLBAU, (uint32_t)(list >> 32));
	ufs_wr(REG_UTRLRSR, 1);
	/*
	 * Never ring a doorbell unless the host really uses our list: otherwise
	 * it would execute whatever the bootloader left in its own slot 0.
	 */
	if (ufs_rd(REG_UTRLBA) != (uint32_t)list || ufs_rd(REG_UTRLBAU) != (uint32_t)(list >> 32) ||
	    !(ufs_rd(REG_UTRLRSR) & 1))
		return 2;
	return 0;
}

/* Name the filesystem whose superblock starts at block lba (f2fs/ext4). */
static const char *fs_type(uint32_t lba)
{
	if (read_blocks(lba, 1, data))
		return "read error";
	const uint8_t *sb = data + 1024;
	if (sb[0] == 0x10 && sb[1] == 0x20 && sb[2] == 0xf5 && sb[3] == 0xf2)
		return "f2fs";
	if (sb[56] == 0x53 && sb[57] == 0xef)
		return "ext4";
	return "other";
}

static int gpt_entries(uint32_t *entry_lba, uint32_t *nent)
{
	uint8_t cdb[10] = { 0x28, 0, 0, 0, 0, 0, 0, 0, 2, 0 };
	uint32_t ocs, st;

	if (scsi(0, cdb, sizeof(cdb), data, 2 * BLOCK, 1, &ocs, &st))
		return 1;
	const uint8_t *gpt = data + BLOCK;
	if (gpt[0] != 'E' || gpt[1] != 'F' || gpt[2] != 'I' || gpt[3] != ' ' ||
	    gpt[4] != 'P' || gpt[5] != 'A' || gpt[6] != 'R' || gpt[7] != 'T')
		return 1;
	uint64_t entry = le64(gpt + 72);		/* GPT fields are little-endian */
	*nent = le32(gpt + 80);
	if (entry > 0xffffffffu || le32(gpt + 84) != 128)
		return 1;
	*entry_lba = (uint32_t)entry;
	return *nent > 128 ? 1 : 0;
}

int ufs_gpt_find(const char *name, uint64_t *first_lba, uint64_t *blocks)
{
	static uint8_t ents[128 * 128] __attribute__((aligned(4096)));
	uint32_t entry_lba, nent;

	if (gpt_entries(&entry_lba, &nent))
		return 0;
	uint32_t nblk = (nent * 128 + BLOCK - 1) / BLOCK;
	if (nblk > sizeof(ents) / BLOCK || read_blocks(entry_lba, nblk, ents))
		return 0;
	for (uint32_t i = 0; i < nent; i++) {
		const uint8_t *e = ents + i * 128;
		uint64_t first = le64(e + 32), last = le64(e + 40);
		char nm[17];
		int ok = 1;

		if (!first)
			continue;
		for (int c = 0; c < 16; c++) {
			uint8_t ch = e[56 + 2 * c];
			nm[c] = (char)ch;
			if (!ch)
				break;
		}
		nm[16] = 0;
		for (int c = 0; name[c] || nm[c]; c++)
			if (name[c] != nm[c]) {
				ok = 0;
				break;
			}
		if (!ok)
			continue;
		*first_lba = first;
		*blocks = last - first + 1;
		return 1;
	}
	return 0;
}

/* One line per GPT entry of at least 64 MiB: name, first LBA, size in MiB. */
static void list_partitions(void)
{
	static uint8_t ents[128 * 128] __attribute__((aligned(4096)));
	uint32_t entry_lba, nent;

	if (gpt_entries(&entry_lba, &nent)) {
		con_str("UFS PART", "no GPT");
		return;
	}
	uint32_t nblk = (nent * 128 + BLOCK - 1) / BLOCK;
	if (nblk > sizeof(ents) / BLOCK || read_blocks(entry_lba, nblk, ents)) {
		con_str("UFS PART", "read error");
		return;
	}
	for (uint32_t i = 0; i < nent; i++) {
		const uint8_t *e = ents + i * 128;
		uint64_t first = le64(e + 32), last = le64(e + 40);
		if (!first)
			continue;
		char name[17];
		for (int c = 0; c < 16; c++) {
			uint8_t ch = e[56 + 2 * c];
			name[c] = (ch >= 32 && ch < 127) ? ch : (ch ? '?' : 0);
		}
		name[16] = 0;
		if (((last - first + 1) >> 8) < 64)	/* keep the screen to the big ones */
			continue;
		con_hex2(name, "lba=", (uint32_t)first, "MiB=", (uint32_t)((last - first + 1) >> 8));
		if (!__builtin_strcmp(name, "userdata") || !__builtin_strcmp(name, "cust") ||
		    !__builtin_strcmp(name, "cache"))
			con_str(" fs", fs_type((uint32_t)first));
	}
}

/* Put the bootloader's transfer list back so a following Linux/XNU boot sees
 * the same host state LK left. Slot 0 is cleared first; UTRLCLR clears a slot
 * by writing 0 to its doorbell bit. */
static void
restore_host(uint32_t ba, uint32_t bau, uint32_t rsr)
{
	ufs_wr(REG_UTRLCLR, ~1u);
	ufs_wr(REG_UTRLRSR, 0);
	ufs_wr(REG_UTRLBA, ba);
	ufs_wr(REG_UTRLBAU, bau);
	ufs_wr(REG_UTRLRSR, rsr & 1u);
}

void test_ufs(void)
{
	uint32_t orig_ba = ufs_rd(REG_UTRLBA), orig_bau = ufs_rd(REG_UTRLBAU);
	uint32_t orig_rsr = ufs_rd(REG_UTRLRSR);

	ufs_test_stage = 0;
	for (int i = 0; i < 4; i++)
		ufs_test_info[i] = 0;
	con_str("UFS", "probe (read-only)");
	uint32_t cap = ufs_rd(REG_CAP), hcs = ufs_rd(REG_HCS), hce = ufs_rd(REG_HCE);
	con_hex2("UFS", "ver=", ufs_rd(REG_VER), "cap=", cap);
	con_hex2("UFS", "hce=", hce, "hcs=", hcs);
	con_hex2("UFS", "is=", ufs_rd(REG_IS), "rsr=", orig_rsr);
	con_hex2("UFS", "utrlba=", orig_ba, "hi=", orig_bau);
	con_hex2("UFS", "dbr=", ufs_rd(REG_UTRLDBR), "nutrs=", (cap & 0x1f) + 1);
	con_hex2("UFS", "uccs=", (hcs & HCS_UCCS) >> 8, "base=", (uint32_t)ufs_base());
	ufs_test_stage = 1;
	ufs_test_info[0] = ((uint64_t)hcs << 32) | hce;
	ufs_test_info[1] = ((uint64_t)orig_bau << 32) | orig_ba;
	ufs_test_info[2] = ((uint64_t)ufs_rd(REG_UTRLDBR) << 32) | orig_rsr;

	if (!(hce & 1) || !(hcs & HCS_DP) || !(hcs & HCS_UTRLRDY)) {
		con_str("UFS", "host not enabled/ready: stop");
		return;
	}
	if (ufs_rd(REG_UTRLDBR)) {
		con_str("UFS", "requests outstanding: stop");
		return;
	}

	int rc = claim_host();
	con_hex2("UFS", "claim=", rc, "rsr=", ufs_rd(REG_UTRLRSR));
	ufs_test_stage = 2;
	ufs_test_info[0] = (uint64_t)rc;
	ufs_test_info[1] = ufs_rd(REG_UTRLBA);
	ufs_test_info[2] = ufs_rd(REG_UTRLBAU);
	ufs_test_info[3] = ufs_rd(REG_UTRLRSR);
	if (rc) {
		restore_host(orig_ba, orig_bau, orig_rsr);
		return;
	}

	uint32_t ocs = 0, st = 0;
	static const uint8_t tur[6] = { 0x00 };
	/* A first command may report a unit attention; retry a few times. */
	for (int i = 0; i < 3; i++) {
		rc = scsi(0, tur, sizeof(tur), 0, 0, 0, &ocs, &st);
		con_hex2("UFS TUR", "rc=", rc, "ocs/st=", ocs << 8 | st);
		if (!rc)
			break;
	}
	ufs_test_stage = 3;
	ufs_test_info[0] = (uint64_t)rc;
	ufs_test_info[1] = (uint64_t)ocs << 8 | st;

	static const uint8_t cap10[10] = { 0x25 };	/* READ CAPACITY(10) */
	rc = scsi(0, cap10, sizeof(cap10), data, 8, 1, &ocs, &st);
	con_hex2("UFS CAP", "rc=", rc, "ocs/st=", ocs << 8 | st);
	if (!rc)
		con_hex2("UFS LUN0", "lastlba=", be32(data), "blk=", be32(data + 4));
	ufs_test_stage = 4;
	ufs_test_info[0] = (uint64_t)rc;
	ufs_test_info[1] = (uint64_t)ocs << 8 | st;
	ufs_test_info[2] = be32(data);
	ufs_test_info[3] = be32(data + 4);

	list_partitions();
	ufs_test_stage = 5;
	restore_host(orig_ba, orig_bau, orig_rsr);
}

int ufs_mark(const char *part, uint32_t block, const char *tag)
{
	static uint8_t m[BLOCK] __attribute__((aligned(4096)));
	uint64_t first = 0, blocks = 0;
	uint32_t regs[8];
	int i;

	for (i = 0; i < (int)sizeof(m); i++)
		m[i] = 0;
	m[0] = 'S';
	m[1] = 'W';
	m[2] = 'M';
	m[3] = 'K';
	m[4] = (uint8_t)block;
	m[5] = (uint8_t)(block >> 8);
	m[6] = (uint8_t)(block >> 16);
	m[7] = (uint8_t)(block >> 24);
	for (i = 0; i < 15 && tag[i]; i++)
		m[8 + i] = (uint8_t)tag[i];

	/* Host state, readable even when the link is down */
	regs[0] = ufs_rd(REG_CAP);
	regs[1] = ufs_rd(REG_VER);
	regs[2] = ufs_rd(REG_IS);
	regs[3] = ufs_rd(REG_HCS);
	regs[4] = ufs_rd(REG_HCE);
	regs[5] = ufs_rd(REG_UTRLBA);
	regs[6] = ufs_rd(REG_UTRLDBR);
	regs[7] = ufs_rd(REG_UTRLRSR);
	for (i = 0; i < 8; i++) {
		m[32 + i * 4] = (uint8_t)regs[i];
		m[33 + i * 4] = (uint8_t)(regs[i] >> 8);
		m[34 + i * 4] = (uint8_t)(regs[i] >> 16);
		m[35 + i * 4] = (uint8_t)(regs[i] >> 24);
	}

	if (!ufs_ready() || claim_host())
		return 1;
	if (!ufs_gpt_find(part, &first, &blocks))
		return 4;
	if (block >= blocks)
		return 5;
	return write_blocks((uint32_t)(first + block), 1, m) ? 3 : 0;
}

int ufs_part_read(const char *part, uint64_t off_blocks, void *buf, uint32_t nblocks)
{
	uint64_t first = 0, blocks = 0;

	if (!ufs_ready() || claim_host())
		return 1;
	if (!ufs_gpt_find(part, &first, &blocks))
		return 4;
	if (off_blocks + nblocks > blocks || nblocks > 8)
		return 5;
	return read_blocks((uint32_t)(first + off_blocks), nblocks, buf) ? 3 : 0;
}

int ufs_part_write(const char *part, uint64_t off_blocks, const void *buf, uint32_t nblocks)
{
	uint64_t first = 0, blocks = 0;

	if (!ufs_ready() || claim_host())
		return 1;
	if (!ufs_gpt_find(part, &first, &blocks))
		return 4;
	if (off_blocks + nblocks > blocks || nblocks > 8)
		return 5;
	return write_blocks((uint32_t)(first + off_blocks), nblocks, buf) ? 3 : 0;
}

int ufs_log_write(const char *part, uint64_t off_blocks, const char *text, uint32_t len)
{
	uint64_t first = 0, blocks = 0;

	/* Claim first: the GPT read rings the doorbell, and that must never
	 * happen while the host still points at the bootloader's list. */
	if (!ufs_ready() || claim_host())
		return 1;
	if (!ufs_gpt_find(part, &first, &blocks))
		return 4;
	if (off_blocks + 8 > blocks)
		return 5;

	if (len > sizeof(data) - 8)
		len = sizeof(data) - 8;
	uint32_t nblk = (len + 8 + BLOCK - 1) / BLOCK;
	if (!nblk)
		return 2;

	zero(data, sizeof(data));
	data[0] = 'S';
	data[1] = 'W';
	data[2] = 'L';
	data[3] = 'G';
	data[4] = (uint8_t)len;
	data[5] = (uint8_t)(len >> 8);
	data[6] = (uint8_t)(len >> 16);
	data[7] = (uint8_t)(len >> 24);
	for (uint32_t i = 0; i < len; i++)
		data[8 + i] = (uint8_t)text[i];

	return write_blocks((uint32_t)(first + off_blocks), nblk, data) ? 3 : 0;
}

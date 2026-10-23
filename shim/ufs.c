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
#define REG_XOUFS_CTRL	0x140
#define REG_REFCLK_CTRL	0x144
#define REG_UIC_CMD	0x90
#define REG_UIC_ARG1	0x94
#define REG_UIC_ARG2	0x98
#define REG_UIC_ARG3	0x9c
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

/* ti,syscon-reset write: change only the masked bits (like regmap_write_bits). */
static void
mmio_bits(uintptr_t base, uint32_t off, uint32_t mask, uint32_t value)
{
	volatile uint32_t *p = (volatile uint32_t *)(base + off);
	uint32_t v = *p;
	*p = (v & ~mask) | (value & mask);
	__asm__ volatile("dsb sy" ::: "memory");
}

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

static struct utrd utrl[32] __attribute__((aligned(4096)));
static struct ucd ucd __attribute__((aligned(4096)));
static uint8_t data[8 * BLOCK] __attribute__((aligned(4096)));

volatile uint64_t ufs_test_stage;
volatile uint64_t ufs_test_info[8];
volatile uint8_t ufs_scsi_resp[8];

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

static uint32_t __attribute__((unused)) be32(const uint8_t *p)
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
 * Issue one UPIU in slot 0.
 * upiu_type: 0x00 NOP OUT, 0x01 SCSI COMMAND.
 * ctype: UTP_CMD_TYPE_SCSI (0) or UTP_CMD_TYPE_UFS_STORAGE (1).
 * dir: 0 = no data, 1 = device to host, 2 = host to device.
 * Returns 0 on success, otherwise a code describing the failure stage.
 */
static uint32_t
scsi_ex(uint8_t lun, const uint8_t *cdb, unsigned cdb_len, void *buf,
    uint32_t len, int dir, uint8_t upiu_type, uint32_t ctype,
    uint32_t *ocs_out, uint32_t *status_out)
{
	static uint8_t tag;
	zero(&utrl[0], sizeof(utrl[0]));
	zero(&ucd, sizeof(ucd));

	ucd.cmd[0] = upiu_type;
	ucd.cmd[3] = ++tag;
	if (upiu_type == 0x01) {
		ucd.cmd[1] = dir == 1 ? 0x40 : dir == 2 ? 0x20 : 0x00;
		ucd.cmd[2] = lun;
		ucd.cmd[12] = len >> 24;
		ucd.cmd[13] = len >> 16;
		ucd.cmd[14] = len >> 8;
		ucd.cmd[15] = len;
		for (unsigned i = 0; i < cdb_len; i++)
			ucd.cmd[16 + i] = cdb[i];
	}

	uintptr_t ucd_pa = (uintptr_t)&ucd;
	uint32_t dd = dir == 1 ? 2u : dir == 2 ? 1u : 0u;
	/* UTRD DW0 (vendor ufshci.h): bit24 interrupt, bits25-26 data
	 * direction, bits28-31 command type. CT=0 is SCSI, 1 is UFS
	 * storage (NOP OUT / queries). */
	utrl[0].dw0 = (ctype << 28) | (dd << 25) | (1u << 24);
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
			for (int i = 0; i < 8; i++)
				ufs_scsi_resp[i] = 0;
			return 1;			/* timeout */
		}
	}
	__asm__ volatile("dsb sy" ::: "memory");
	*ocs_out = utrl[0].dw2 & 0xff;
	*status_out = ucd.resp[7];		/* SCSI status in the response UPIU */
	for (int i = 0; i < 8; i++)
		ufs_scsi_resp[i] = ucd.resp[i];
	if (*ocs_out)
		return 2;
	uint8_t expect = upiu_type == 0x00 ? 0x20 : 0x21;  /* NOP IN / RESPONSE */
	if (ucd.resp[0] != expect)
		return 3;
	return *status_out ? 4 : 0;
}

static void
wait_ms(unsigned ms)
{
	uint64_t f;
	__asm__ volatile("isb; mrs %0, cntfrq_el0" : "=r"(f));
	uint64_t s = ticks();
	while (ticks() - s < f * ms / 1000)
		__asm__ volatile("yield");
}

/*
 * One UFS query request (UPIU type 0x16) in slot 0. opcode 0x01/0x02 read
 * and write descriptors, 0x05 read flag. dir: 0 none, 1 device to host,
 * 2 host to device. On success *value gets the response value field.
 */
static uint32_t
query_ex(uint8_t opcode, uint8_t idn, uint8_t index, uint8_t selector,
    void *buf, uint32_t len, int dir, uint32_t *value, uint32_t *status_out)
{
	static uint8_t tag;
	zero(&utrl[0], sizeof(utrl[0]));
	zero(&ucd, sizeof(ucd));

	ucd.cmd[0] = 0x16;
	ucd.cmd[3] = ++tag;
	ucd.cmd[12] = opcode;
	ucd.cmd[13] = idn;
	ucd.cmd[14] = index;
	ucd.cmd[15] = selector;

	uintptr_t ucd_pa = (uintptr_t)&ucd;
	uint32_t dd = dir == 1 ? 2u : dir == 2 ? 1u : 0u;
	utrl[0].dw0 = (1u << 28) | (dd << 25) | (1u << 24);
	utrl[0].dw2 = 0x0f;
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
		if (ticks() - start > freq) {
			ufs_wr(REG_UTRLCLR, ~1u);
			return 1;
		}
	}
	__asm__ volatile("dsb sy" ::: "memory");
	uint32_t ocs = utrl[0].dw2 & 0xff;
	*status_out = ucd.resp[7];
	*value = be32(&ucd.resp[24]);
	if (ocs)
		return 2;
	if (ucd.resp[0] != 0x36)
		return 3;
	return (ucd.resp[6] || ucd.resp[7]) ? 4 : 0;
}

/*
 * A UFS device asserts fDeviceInit after a reset; until the host writes the
 * Configuration Descriptor back, the device answers most SCSI commands with
 * a target failure. Linux does the same sequence in ufshcd_device_init().
 * Returns 0 when the device reports init complete (or was already init).
 */
static uint32_t
device_init(void)
{
	uint32_t v = 0, st = 0;

	uint32_t rc = query_ex(0x05, 0x0b, 0, 0, 0, 0, 0, &v, &st);
	if (rc)
		return 0x10 | rc;
	if (!(v & 1))
		return 0;			/* already initialized */

	zero(data, 512);
	rc = query_ex(0x01, 0x01, 0, 0, data, 512, 1, &v, &st);
	if (rc)
		return 0x20 | rc;
	uint32_t len = v ? v : 512;
	if (len > 512)
		len = 512;
	if (data[0] < 0x10 || data[0] > len)
		return 0x30;			/* implausible descriptor */
	rc = query_ex(0x02, 0x01, 0, 0, data, len, 2, &v, &st);
	if (rc)
		return 0x40 | rc;
	for (int i = 0; i < 20; i++) {
		v = 0;
		rc = query_ex(0x05, 0x0b, 0, 0, 0, 0, 0, &v, &st);
		if (!rc && !(v & 1))
			return 0;
		wait_ms(1);
	}
	return 0x50;
}

static uint32_t
scsi(uint8_t lun, const uint8_t *cdb, unsigned cdb_len, void *buf, uint32_t len,
    int dir, uint32_t *ocs_out, uint32_t *status_out)
{
	/* Vendor ufshcd_comp_scsi_upiu: UFSHCI > 1.1 (ours is 2.0)
	 * uses UTP_CMD_TYPE_UFS_STORAGE, not UTP_CMD_TYPE_SCSI. */
	return scsi_ex(lun, cdb, cdb_len, buf, len, dir, 0x01, 1, ocs_out, status_out);
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

static uint64_t
tick_freq(void)
{
	uint64_t f;
	__asm__ volatile("isb; mrs %0, cntfrq_el0" : "=r"(f));
	return f;
}

/* One UIC command; returns the UIC result byte (0 = success, 0xff = no
 * completion). Completion is IS bit 10 (UIC_COMMAND_COMPL); ARG3 is the
 * DME_GET value. 500 ms matches Linux UIC_CMD_TIMEOUT. */
static uint32_t
uic(uint32_t op, uint32_t arg1, uint32_t arg2, uint32_t arg3, uint32_t *out3)
{
	uint32_t is = ufs_rd(REG_IS);
	ufs_wr(REG_UIC_ARG1, arg1);
	ufs_wr(REG_UIC_ARG2, arg2);
	ufs_wr(REG_UIC_ARG3, arg3);
	ufs_wr(REG_IS, is);		/* W1C: clear the previous status */
	ufs_wr(REG_UIC_CMD, op);
	uint64_t start = ticks(), freq = tick_freq();
	while (!(ufs_rd(REG_IS) & 0x400) && ticks() - start < freq / 2)
		__asm__ volatile("yield");
	if (out3)
		*out3 = ufs_rd(REG_UIC_ARG3);
	if (!(ufs_rd(REG_IS) & 0x400))
		return 0xff;
	return ufs_rd(REG_UIC_ARG2) & 0xff;
}

static uint32_t
uic_cmd(uint32_t op)
{
	return uic(op, 0, 0, 0, 0);
}

/* Request the UFS reference clock and wait for the ACK handshake
 * (ufs_mtk_setup_ref_clk: REQUEST bit0, ACK bit1). Returns the final
 * register value; bits [1:0] == 3 means requested and acked. */
static uint32_t
refclk_on(void)
{
	uint32_t v = ufs_rd(REG_REFCLK_CTRL);
	if ((v & 3) == 3)
		return v;
	ufs_wr(REG_REFCLK_CTRL, v | 1u);
	uint64_t start = ticks(), freq = tick_freq();
	do {
		v = ufs_rd(REG_REFCLK_CTRL);
		if ((v & 3) == 3)
			break;
	} while (ticks() - start < freq / 10);
	return v;
}

/* MediaTek UFS device reset via the secure monitor:
 * arm_smccc_smc(MTK_SIP_UFS_CONTROL=0x82000276, UFS_MTK_SIP_DEVICE_RESET=2,
 * high). The reset line is active low: 0 asserts, 1 releases. Linux holds
 * it low >=10 us and waits 10-15 ms after release. Returns the SMC a0. */
static uint64_t
smc3(uint64_t a0, uint64_t a1, uint64_t a2)
{
	register uint64_t x0 asm("x0") = a0;
	register uint64_t x1 asm("x1") = a1;
	register uint64_t x2 asm("x2") = a2;
	asm volatile("mov x3, xzr; mov x4, xzr; mov x5, xzr; mov x6, xzr;"
	    " mov x7, xzr; smc #0" : "+r"(x0), "+r"(x1), "+r"(x2)
	    : : "x3", "x4", "x5", "x6", "x7", "memory");
	return x0;
}

static uint32_t
mtk_device_reset(void)
{
	uint64_t start, freq = tick_freq();
	/* ARM_SMCCC_CALL_VAL(FAST_CALL, SMC_64, OWNER_SIP, 0x276) is
	 * 0xc2000276; the SMC32 form 0x82000276 was used earlier and may
	 * be an unimplemented call. Try the arm64 ID first, fall back. */
	uint64_t id = 0xc2000276ull;
	uint64_t first = smc3(id, 2, 0);
	if (first != 0) {
		id = 0x82000276ull;
		smc3(id, 2, 0);
	}
	start = ticks();
	while (ticks() - start < freq / 100000)	/* >=10 us */
		__asm__ volatile("yield");
	uint64_t r = smc3(id, 2, 1);
	start = ticks();
	while (ticks() - start < freq / 100)	/* 10 ms */
		__asm__ volatile("yield");
	ufs_test_info[4] = first;		/* 0 = 0xc2 accepted */
	return (uint32_t)r;
}

/* MediaTek pre-link: permit UniPro power down and disable deep stall. */
static uint32_t
mtk_pre_link(void)
{
	uint32_t v = 0, r;
	r = uic(0x02, 0xD0A8u << 16, 0, 0, 0);	/* VS_UNIPROPOWERDOWNCONTROL=0 */
	if (uic(0x01, 0xD0A6u << 16, 0, 0, &v) == 0 && (v & (1u << 6)))
		uic(0x02, 0xD0A6u << 16, 0, v & ~(1u << 6), 0);
	return r;
}

/* MediaTek full host reset (vendor ufs_mtk_host_reset + hce_enable_notify).
 * The reset controller is ti,syscon-reset on ufs_ao_cfg (UFS base + 0x8000)
 * and ufs_pdn_cfg (+0xC000); live FDT ti,reset-bits:
 *   unipro (ao 0): assert 0x44 bit 1, deassert 0x48 bit 1
 *   crypto (pdn 0): assert 0x44 bit 0, deassert 0x48 bit 0
 *   hci    (pdn 1): assert 0x44 bit 1, deassert 0x48 bit 1
 * Then XOUFS_CTRL (0x140) |= 0x80 and the standard HCE enable cycle. */
static int
mtk_host_reset(void)
{
	uintptr_t ao = ufs_base() + 0x8000, pdn = ufs_base() + 0xc000;
	uint64_t start, freq = tick_freq();
	/* After HCE only the UIC layer is ready. UTRLRDY appears once the
	 * request list base/RSR are programmed (claim_host), DP after link
	 * startup, so waiting on them here would fail a healthy host. */
	const uint32_t ready = HCS_UCRDY;

	mmio_bits(pdn, 0x44, 1u << 1, 1u << 1);
	mmio_bits(pdn, 0x44, 1u << 0, 1u << 0);
	mmio_bits(ao, 0x44, 1u << 1, 1u << 1);
	start = ticks();
	while (ticks() - start < freq / 10000)	/* ~100 us */
		__asm__ volatile("yield");
	mmio_bits(ao, 0x48, 1u << 1, 1u << 1);
	mmio_bits(pdn, 0x48, 1u << 0, 1u << 0);
	mmio_bits(pdn, 0x48, 1u << 1, 1u << 1);

	ufs_wr(REG_XOUFS_CTRL, ufs_rd(REG_XOUFS_CTRL) | 0x80);
	ufs_wr(REG_UTRLRSR, 0);
	ufs_wr(REG_HCE, 0);
	start = ticks();
	while ((ufs_rd(REG_HCE) & 1) && ticks() - start < freq / 100)
		__asm__ volatile("yield");
	ufs_wr(REG_HCE, 1);
	start = ticks();
	while ((ufs_rd(REG_HCS) & ready) != ready && ticks() - start < freq / 10)
		__asm__ volatile("yield");
	return (ufs_rd(REG_HCS) & ready) == ready ? 0 : 1;
}

static uint32_t
link_start(void)
{
	uint32_t ur = mtk_pre_link();
	uint32_t ls = uic_cmd(0x16);	/* DME_LINK_STARTUP */
	if (ls != 0xff)
		ur = ls;		/* record link startup result */
	claim_host();
	return ur;
}

/* Bring the UniPro link up and leave the host usable for XNU's own driver.
 * LK hands over with the host registers clocked but the link and device
 * powered down; the vendor probe order (host reset, SMC device reset, ref
 * clock, pre-link, link startup) is required before the kernel can issue
 * its first TUR. The request list is left programmed with the shim's
 * low-memory list and RSR=1 so XNU's takeover sees UTRLRDY; XNU replaces
 * the list before ringing any doorbell. */
void
init_ufs_link(void)
{
	ufs_test_stage = 0;
	ufs_test_info[0] = 0;
	if (mtk_host_reset()) {
		ufs_test_stage = 1;
		return;
	}
	mtk_device_reset();
	refclk_on();
	mtk_pre_link();
	uint32_t ls = uic_cmd(0x16);		/* DME_LINK_STARTUP */
	if (ls != 0) {
		ufs_test_stage = 2;
		return;
	}
	/* Probe whether the host keeps a >4 GiB UTRL base. XNU's contiguous
	 * DMA lands in the high DDR bank; if UTRLBAU does not stick there,
	 * the driver's readback fails with EIO. Write a zero UTRD at the
	 * start of the high bank, program that base, read it back, and
	 * restore the low list before any doorbell. */
	volatile uint64_t *high = (volatile uint64_t *)0x100000000ull;
	for (int i = 0; i < 512; i++)
		high[i] = 0;
	__asm__ volatile("dsb sy" ::: "memory");
	ufs_wr(REG_UTRLRSR, 0);
	ufs_wr(REG_UTRLBA, 0);
	ufs_wr(REG_UTRLBAU, 1);
	ufs_wr(REG_UTRLRSR, 1);
	ufs_test_info[1] = ((uint64_t)ufs_rd(REG_UTRLBAU) << 32) |
	    ufs_rd(REG_UTRLBA);
	ufs_test_info[2] = ufs_rd(REG_UTRLRSR);
	uintptr_t list = (uintptr_t)utrl;
	ufs_wr(REG_UTRLRSR, 0);
	ufs_wr(REG_UTRLBA, (uint32_t)list);
	ufs_wr(REG_UTRLBAU, (uint32_t)(list >> 32));
	ufs_wr(REG_UTRLRSR, 1);
	uint64_t start = ticks(), freq = tick_freq();
	while (!(ufs_rd(REG_HCS) & HCS_UTRLRDY) && ticks() - start < freq / 100)
		__asm__ volatile("yield");
	ufs_test_info[0] = ((uint64_t)ufs_rd(REG_HCS) << 32) | ufs_rd(REG_UTRLRSR);
	ufs_test_stage = 3;

	/* Finish device init (fDeviceInit) before XNU's driver arrives. */
	ufs_test_info[5] = device_init();

	/* The first command after a device reset returns CHECK CONDITION
	 * (unit attention). XNU's polled driver retries the TUR without a
	 * REQUEST SENSE, so clear it here: TUR, sense if needed, TUR. */
	static const uint8_t tur[6] = { 0x00 };
	uint32_t ocs = 0, st = 0;
	int rc = scsi(0, tur, sizeof(tur), 0, 0, 0, &ocs, &st);
	if (rc) {
		uint8_t sense[6] = { 0x03, 0, 0, 0, 18, 0 };
		(void)scsi(0, sense, sizeof(sense), data, 18, 1, &ocs, &st);
	}
	rc = scsi(0, tur, sizeof(tur), 0, 0, 0, &ocs, &st);
	ufs_test_info[3] = (uint64_t)rc | (uint64_t)ocs << 8 |
	    (uint64_t)st << 16 | (uint64_t)ufs_scsi_resp[0] << 24;
	ufs_test_stage = rc == 0 ? 4 : 5;
}

void test_ufs(void)
{
	uint32_t orig_ba = ufs_rd(REG_UTRLBA), orig_bau = ufs_rd(REG_UTRLBAU);
	uint32_t orig_rsr = ufs_rd(REG_UTRLRSR);

	ufs_test_stage = 0;
	for (int i = 0; i < 8; i++)
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

	uint32_t is_before = ufs_rd(REG_IS), hcs_before = ufs_rd(REG_HCS);
	ufs_test_info[4] = is_before;
	ufs_test_info[5] = hcs_before;

	/* Try link wake strategies and one TUR after each. Result packing:
	 * rc | OCS<<8 | status<<16 | resp0<<24 | resp6<<32 | resp7<<40 |
	 * UIC result<<48 | post-HCS low nibble<<56.
	 *   0 baseline, 1 refclk + pre-link + link startup,
	 *   2 refclk + host reset + refclk + pre-link + link startup,
	 *   3 host reset + device reset (SMC) + refclk + link + NOP + TUR. */
	uint32_t refclk = 0, smc = 0, nop_rc = 0, nop_ocs = 0;
	for (int s = 0; s < 4; s++) {
		uint32_t ur = 0xff;
		if (s == 1) {
			refclk = refclk_on();
			ur = link_start();
		} else if (s == 2) {
			refclk = refclk_on();
			if (mtk_host_reset()) {
				ufs_test_info[s] = 0xfe;
				continue;
			}
			refclk = refclk_on();
			ur = link_start();
		} else if (s == 3) {
			if (mtk_host_reset()) {
				ufs_test_info[s] = 0xfe;
				continue;
			}
			smc = mtk_device_reset();
			refclk = refclk_on();
			ur = link_start();
		}
		if (s == 3) {
			uint32_t n0 = 0, s0 = 0;
			nop_rc = scsi_ex(0, 0, 0, 0, 0, 0, 0x00, 1, &n0, &s0);
			nop_ocs = n0;
		}
		uint32_t ocs = 0, st = 0;
		static const uint8_t tur[6] = { 0x00 };
		int rc = scsi(0, tur, sizeof(tur), 0, 0, 0, &ocs, &st);
		con_hex2("UFS TRY", "s=", s, "rc/ocs=", (uint32_t)rc << 8 | ocs);
		ufs_test_info[s] = (uint64_t)rc | (uint64_t)ocs << 8 |
		    (uint64_t)st << 16 | (uint64_t)ufs_scsi_resp[0] << 24 |
		    (uint64_t)ufs_scsi_resp[6] << 32 |
		    (uint64_t)ufs_scsi_resp[7] << 40 |
		    (uint64_t)ur << 48 | (uint64_t)(ufs_rd(REG_HCS) & 0xf) << 56;
	}
	ufs_test_info[6] = (uint64_t)(smc & 0xff) << 32 |
	    (uint64_t)(nop_ocs & 0xff) << 16 | (uint64_t)(nop_rc & 0xff) << 8 |
	    refclk;	/* smc (bits32-39), NOP rc/OCS, refclk */
	ufs_test_info[7] = ufs_rd(REG_HCS);

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

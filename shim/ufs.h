#pragma once
#include <stdint.h>

/* Probe the UFS host, print what it finds, and list the big GPT partitions. */
void test_ufs(void);

#define UFS_QUERY_FULL	0
#define UFS_QUERY_NONE	1
#define UFS_QUERY_READ	2
#define UFS_QUERY_SET	3

/* Reset host/device and start the UniPro link before the XNU jump. Leaves
 * the host claimed with the shim's UTRL so XNU's driver passes its
 * readiness check and then takes the list over. */
void init_ufs_link(unsigned query_mode);

/*
 * test_ufs() progress for building a compact, banner-readable result.
 * stage: 0 before the readiness gate, 1 ready, 2 list claimed, 3 TUR done,
 * 4 READ CAPACITY done, 5 partition scan done. info[] contents per stage:
 *   1: CAP|VER implicit in HCS/HCE; [0]=HCS<<32|HCE, [1]=UTRLBAU<<32|UTRLBA,
 *      [2]=UTRLDBR<<32|UTRLRSR
 *   2: [0]=claim rc, [1]=UTRLBA, [2]=UTRLBAU, [3]=UTRLRSR
 *   3: [0]=TUR result packed as rc | OCS<<8 | status<<16 |
 *      response[0]<<24 | response[6]<<32 | response[7]<<40
 *   4: [1]=READ CAPACITY result with the same packing, [2]=last LBA,
 *      [3]=block size
 *   4: [4]=IS before the DME_HIBERNATE_EXIT, [5]=IS after,
 *      [6]=UIC ARG2 (result in [7:0]), [7]=physical UTRL address
 */
extern volatile uint64_t ufs_test_stage;
extern volatile uint64_t ufs_test_info[8];

/* 1 when the host is enabled, the link is up and no request is outstanding. */
int ufs_ready(void);

/* Find a GPT partition by name; returns 0 when absent. */
int ufs_gpt_find(const char *name, uint64_t *first_lba, uint64_t *blocks);

/*
 * Write a one-block progress marker: magic "SWMK", the tag, and a dump of
 * the host registers, so a boot that dies early still leaves evidence.
 * Returns 0 when the block reached the partition.
 */
int ufs_mark(const char *part, uint32_t block, const char *tag);

/* Read/write whole 4 KiB blocks of a named GPT partition. 0 on success. */
int ufs_part_read(const char *part, uint64_t off_blocks, void *buf, uint32_t nblocks);
int ufs_part_write(const char *part, uint64_t off_blocks, const void *buf, uint32_t nblocks);

/*
 * Write the console text into `part` at `off_blocks`: a 4 KiB header
 * (magic "SWLG", length) followed by the text, padded to whole blocks.
 * Returns 0 on success.
 */
int ufs_log_write(const char *part, uint64_t off_blocks, const char *text, uint32_t len);

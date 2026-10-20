#pragma once
#include <stdint.h>

/* Probe the UFS host, print what it finds, and list the big GPT partitions. */
void test_ufs(void);

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

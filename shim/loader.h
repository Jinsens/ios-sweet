#pragma once
#include <stdint.h>

/* Boot an embedded boot-v4 SWEETXNU payload (legacy v2: initrd scan).
 * Returns only on error. */
const char *loader_boot(uintptr_t dtb);

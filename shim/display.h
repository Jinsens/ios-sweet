#pragma once
#include <stdint.h>

/* Push the shim console to an MT6983 command-mode DSC panel. */
int display_probe(uint64_t dtb, uint64_t fb, uint32_t stride);
void display_refresh(void);
int display_active(void);

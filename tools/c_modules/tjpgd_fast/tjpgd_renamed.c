/*
 * TJpgDec compiled under prefixed names.
 *
 * st7789_mpy bundles its own TJpgDec fork (st7789/jpg/tjpgd565.c) whose
 * public symbols jd_prepare/jd_decomp would collide at link time. The two
 * copies are configured differently (workspace size, struct layout), so
 * they cannot be shared — compile our copy under tdeck_-prefixed names
 * instead. tjpgd.c is included from tools/natmod/tjpgd_fast unmodified.
 */

#define jd_prepare tdeck_jd_prepare
#define jd_decomp  tdeck_jd_decomp

#include "tjpgd.c"

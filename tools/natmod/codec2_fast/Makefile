# Build codec2_fast native module for MicroPython
#
# Usage:
#   make ARCH=xtensawin   (uses default xtensa-esp32-elf toolchain)
#
# Result: codec2_fast.mpy

MPY_DIR = /Users/milen/micropython
MOD = codec2_fast
SRC = main.c c2_codec2.c c2_fft.c c2_kiss_fft.c c2_kiss_fftr.c \
      c2_lpc.c c2_lsp.c c2_lsp_cb.c c2_ge_cb.c c2_nlp.c \
      c2_sine.c c2_phase.c c2_interp.c c2_postfilter.c \
      c2_quantise.c c2_mbest.c c2_pack.c libc_stubs.c
ARCH = xtensawin

CFLAGS += -O0 -DNDEBUG -fno-builtin -I.
CFLAGS += -Wno-unused-variable -Wno-unused-but-set-variable
LINK_RUNTIME = 1

include $(MPY_DIR)/py/dynruntime.mk

# Use cleaned libm (s_lib_ver.o removed — has .data section)
LIBM_CLEAN = /tmp/libm_clean/libm_clean.a
MPY_LD_FLAGS := $(subst $(LIBM_PATH),$(LIBM_CLEAN),$(MPY_LD_FLAGS))

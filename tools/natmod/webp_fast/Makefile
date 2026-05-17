# Build webp_fast native module for MicroPython
#
# Usage:
#   make ARCH=xtensawin   # ESP32-S3
#
# Result: webp_fast.mpy

MPY_DIR = /Users/milen/micropython
MOD = webp_fast
SRC = main.c libc_stubs.c
ARCH = xtensawin

CFLAGS += -Os -DNDEBUG -fno-builtin -I.
CFLAGS += -DSIMPLEWEBP_IMPLEMENTATION -DSIMPLEWEBP_DISABLE_STDIO
LINK_RUNTIME = 1

include $(MPY_DIR)/py/dynruntime.mk

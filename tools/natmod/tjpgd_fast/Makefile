# Build tjpgd_fast native module for MicroPython
#
# Usage:
#   make ARCH=xtensawin   # ESP32-S3
#
# Result: tjpgd_fast.mpy

MPY_DIR = /Users/milen/micropython
MOD = tjpgd_fast
SRC = main.c tjpgd.c libc_stubs.c
ARCH = xtensawin

CFLAGS += -Os -DNDEBUG -fno-builtin -I.
LINK_RUNTIME = 1

include $(MPY_DIR)/py/dynruntime.mk

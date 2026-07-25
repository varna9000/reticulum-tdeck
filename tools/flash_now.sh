#!/bin/bash
# One-shot: write the T-Deck app partition (bootloader/table already flashed).
# Chip must be in ROM download mode.
set -e
set -o pipefail
FB="/Users/milen/Documents/Projects/computer-related/reticulum-tdeck/tools/firmware_build"
PY="$HOME/.espressif/python_env/idf5.2_py3.12_env/bin/python"
PORT=$(/bin/ls /dev/cu.usbmodem* 2>/dev/null | head -1)
if [ -z "$PORT" ]; then echo "ERROR: no usbmodem port present"; exit 1; fi
echo "=== Using port $PORT ==="
"$PY" -m esptool --chip esp32s3 --port "$PORT" -b 460800 \
    --before no_reset --after hard_reset write_flash \
    --flash_mode dio --flash_size 8MB --flash_freq 80m \
    0x10000 "$FB/micropython.bin"
echo "=== FLASH COMPLETE — device is rebooting ==="

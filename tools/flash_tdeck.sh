#!/bin/bash
# Flash complete T-Deck firmware + files in one step.
# The user runs this single script — nothing else needed.
#
# Usage:
#   bash flash_tdeck.sh [PORT]
#   bash flash_tdeck.sh /dev/tty.usbmodem1101
#
# What this does:
#   1. Flashes firmware (frozen app + st7789 C driver)
#   2. Waits for reboot
#   3. Copies natmod .mpy files and data files via mpremote

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TDECK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD="$SCRIPT_DIR/firmware_build"
BOOTLOADER="$BUILD/bootloader.bin"
PARTITIONS="$BUILD/partition-table.bin"
APP="$BUILD/micropython.bin"
PORT="${1:-auto}"

# --- Validate ---
if [ ! -f "$APP" ]; then
    echo "ERROR: micropython.bin not found. Run build_firmware.sh first."
    exit 1
fi

if ! command -v esptool.py &>/dev/null; then
    echo "ERROR: esptool.py not found. Install with: pip install esptool"
    exit 1
fi

if ! command -v mpremote &>/dev/null; then
    echo "ERROR: mpremote not found. Install with: pip install mpremote"
    exit 1
fi

# --- Resolve port ---
if [ "$PORT" = "auto" ]; then
    PORT=$(ls /dev/cu.usbmodem* /dev/cu.usbserial* /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | head -1)
    if [ -z "$PORT" ]; then
        echo "ERROR: No USB serial port found. Connect T-Deck and retry."
        exit 1
    fi
fi
echo "=== Using port: $PORT ==="

# --- Step 1: Flash firmware ---
echo ""
echo "=== Step 1/3: Erasing flash ==="
esptool.py --chip esp32s3 --port "$PORT" erase_flash

echo ""
echo "=== Step 2/3: Flashing firmware (bootloader + partitions + app) ==="
esptool.py --chip esp32s3 --port "$PORT" -b 460800 \
    --before default_reset --after hard_reset \
    write_flash --flash_mode dio --flash_size 8MB --flash_freq 80m \
    0x0      "$BOOTLOADER" \
    0x8000   "$PARTITIONS" \
    0x10000  "$APP"

# --- Step 2: Wait for reboot ---
echo ""
echo "=== Waiting for T-Deck to reboot (5s) ==="
sleep 5

# --- Step 3: Copy files via mpremote ---
echo ""
echo "=== Step 3/3: Uploading natmod + data files ==="

MPREMOTE="mpremote connect $PORT"

# Create /lib directory
$MPREMOTE mkdir :lib 2>/dev/null || true

# Natmod .mpy files (native code — can't be frozen)
NATMODS=(
    "ed25519_fast_xtensawin.mpy"
    "bz2_fast_xtensawin.mpy"
    "codec2_fast_xtensawin.mpy"
    "tjpgd_fast_xtensawin.mpy"
    "webp_fast_xtensawin.mpy"
)

for f in "${NATMODS[@]}"; do
    SRC="$TDECK_ROOT/lib/$f"
    if [ -f "$SRC" ]; then
        echo "  Copying $f"
        $MPREMOTE cp "$SRC" ":lib/$f"
    else
        echo "  Skipping $f (not found)"
    fi
done

# Data files
if [ -f "$TDECK_ROOT/logo.jpg" ]; then
    echo "  Copying logo.jpg"
    $MPREMOTE cp "$TDECK_ROOT/logo.jpg" :logo.jpg
fi

# Font .mpy (in case not frozen, or as override)
if [ -f "$TDECK_ROOT/lib/vga2_8x16.mpy" ]; then
    echo "  Copying vga2_8x16.mpy"
    $MPREMOTE cp "$TDECK_ROOT/lib/vga2_8x16.mpy" ":lib/vga2_8x16.mpy"
fi

echo ""
echo "=== DONE ==="
echo "T-Deck is ready. Reset the device or run:"
echo "  mpremote connect $PORT reset"

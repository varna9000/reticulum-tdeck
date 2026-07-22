#!/bin/bash
# Build custom MicroPython firmware for T-Deck v1 (ESP32-S3)
# with Russ Hughes st7789_mpy C driver + frozen app modules.
#
# Prerequisites: cmake, ninja, python3
#   brew install cmake ninja dfu-util
#
# Usage:
#   cd tools && bash build_firmware.sh          # C driver + frozen app
#   cd tools && bash build_firmware.sh --no-freeze  # C driver only
#
# Output: bootloader.bin, partition-table.bin, micropython.bin in firmware_build/
# Flash with: bash flash_tdeck.sh

set -e
set -o pipefail  # `make | tail` must fail the script when make fails

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export TDECK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/firmware_build"
MPY_VERSION="v1.24.1"       # match .mpy version 6
IDF_VERSION="v5.2.3"        # required by MicroPython 1.24
ST7789_REPO="https://github.com/russhughes/st7789_mpy.git"
BOARD="ESP32_GENERIC_S3"
VARIANT="SPIRAM_OCT"
FREEZE=1

if [ "$1" = "--no-freeze" ]; then
    FREEZE=0
    echo "=== Building WITHOUT frozen modules ==="
fi

echo "=== Build dir: $BUILD_DIR ==="
echo "=== T-Deck root: $TDECK_ROOT ==="
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# --- Step 0: Pin a Python supported by ESP-IDF 5.2 ---
# IDF's detect_python.sh takes the first `python3` on PATH. Homebrew's
# python3 moved to 3.14, which IDF 5.2.3 predates (its venv creation and
# constraints break). Pin the newest 3.10-3.12 via a PATH shim so both
# install.sh and export.sh see it as `python3`.
IDF_PY=""
for cand in python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
        IDF_PY="$(command -v $cand)"
        break
    fi
done
if [ -z "$IDF_PY" ]; then
    echo "ERROR: need python 3.10-3.12 for ESP-IDF $IDF_VERSION (brew install python@3.12)"
    exit 1
fi
mkdir -p "$BUILD_DIR/pyshim"
ln -sf "$IDF_PY" "$BUILD_DIR/pyshim/python3"
export PATH="$BUILD_DIR/pyshim:$PATH"
echo "=== ESP-IDF python pinned to $IDF_PY ==="

# --- Step 1: ESP-IDF ---
if [ ! -d "esp-idf" ]; then
    echo "=== Cloning ESP-IDF $IDF_VERSION ==="
    git clone -b "$IDF_VERSION" --recursive --depth 1 \
        https://github.com/espressif/esp-idf.git
    cd esp-idf
    ./install.sh esp32s3
    cd ..
else
    echo "=== ESP-IDF already present ==="
fi

# Ensure all required ESP-IDF submodules are present
# (--depth 1 clone sometimes misses nested submodules)
echo "=== Checking ESP-IDF submodules ==="
cd esp-idf
git submodule update --init --recursive \
    components/bootloader/subproject/components/micro-ecc/micro-ecc \
    components/bt/controller/lib_esp32c3_family \
    2>/dev/null || true
cd "$BUILD_DIR"

# Source ESP-IDF environment
source esp-idf/export.sh

# --- Step 2: MicroPython ---
if [ ! -d "micropython" ]; then
    echo "=== Cloning MicroPython $MPY_VERSION ==="
    git clone -b "$MPY_VERSION" --depth 1 \
        https://github.com/micropython/micropython.git
else
    echo "=== MicroPython already present ==="
fi

# Build mpy-cross (suppress Apple Clang VLA warning)
echo "=== Building mpy-cross ==="
make -C micropython/mpy-cross CFLAGS_EXTRA="-Wno-error=gnu-folding-constant" -j$(sysctl -n hw.ncpu 2>/dev/null || nproc) 2>&1 | tail -3

# Ensure MicroPython submodules are present
echo "=== Checking MicroPython submodules ==="
cd micropython
git submodule update --init \
    lib/berkeley-db-1.xx \
    lib/micropython-lib \
    lib/tinyusb
cd "$BUILD_DIR"

# --- Step 3: st7789_mpy C driver ---
if [ ! -d "st7789_mpy" ]; then
    echo "=== Cloning st7789_mpy ==="
    git clone "$ST7789_REPO"
else
    echo "=== st7789_mpy already present ==="
fi

# All user C modules (st7789 display driver + the five former natmods,
# now built-in so their code executes from flash instead of internal IRAM).
USERMODS_CMAKE="$SCRIPT_DIR/c_modules/micropython.cmake"

# --- Step 4: Build firmware ---
# BOARD_DIR points at the in-repo board dir (stock ESP32_GENERIC_S3 plus
# the T-Deck sdkconfig overrides: WiFi/LWIP buffers in PSRAM).
MAKE_ARGS="BOARD=$BOARD BOARD_VARIANT=$VARIANT BOARD_DIR=$SCRIPT_DIR/board_tdeck USER_C_MODULES=$USERMODS_CMAKE"

if [ "$FREEZE" = "1" ]; then
    echo "=== Freezing app modules into firmware ==="
    MAKE_ARGS="$MAKE_ARGS FROZEN_MANIFEST=$SCRIPT_DIR/tdeck_manifest.py"
fi

echo "=== Building firmware ($BOARD + $VARIANT + st7789) ==="
cd "$BUILD_DIR/micropython/ports/esp32"

# IDF resolves CONFIG_PARTITION_TABLE_CUSTOM_FILENAME relative to the
# project dir (ports/esp32) — copy the T-Deck partition table in each run
# so a fresh micropython clone still builds.
cp "$SCRIPT_DIR/board_tdeck/partitions-tdeck-8MiB.csv" .

make $MAKE_ARGS -j$(sysctl -n hw.ncpu 2>/dev/null || nproc) all 2>&1 | tail -20

BUILD_OUT="$BUILD_DIR/micropython/ports/esp32/build-${BOARD}-${VARIANT}"

if [ -f "$BUILD_OUT/micropython.bin" ]; then
    cp "$BUILD_OUT/micropython.bin"                "$BUILD_DIR/"
    cp "$BUILD_OUT/bootloader/bootloader.bin"      "$BUILD_DIR/"
    cp "$BUILD_OUT/partition_table/partition-table.bin" "$BUILD_DIR/"

    APP_SIZE=$(stat -f%z "$BUILD_DIR/micropython.bin" 2>/dev/null || stat -c%s "$BUILD_DIR/micropython.bin")
    echo ""
    echo "=== SUCCESS ==="
    echo "App size: $((APP_SIZE / 1024)) KB"
    echo ""
    echo "Output files in $BUILD_DIR/:"
    echo "  bootloader.bin      (0x0)"
    echo "  partition-table.bin (0x8000)"
    echo "  micropython.bin     (0x10000)"
    echo ""
    echo "Flash with:"
    echo "  bash $SCRIPT_DIR/flash_tdeck.sh [PORT]"
    if [ "$FREEZE" = "1" ]; then
        echo ""
        echo "Frozen modules are in ROM. flash_tdeck.sh will upload:"
        echo "  - Natmod .mpy files (ed25519_fast, bz2_fast, codec2_fast, tjpgd_fast, webp_fast)"
        echo "  - logo.jpg (splash image)"
    fi
else
    echo "=== BUILD FAILED ==="
    echo "Check logs in: $BUILD_OUT/log/"
    exit 1
fi

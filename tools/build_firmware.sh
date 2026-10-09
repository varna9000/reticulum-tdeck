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
#   cd tools && bash build_firmware.sh --pro    # T-Deck Pro (quad PSRAM, e-ink)
#
# Output: bootloader.bin, partition-table.bin, micropython.bin in firmware_build/
# (firmware_build/pro/ for --pro, as tdeck_pro_firmware.bin / tdeck_pro_update.bin)
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
# Which hardware layer tdeck_manifest.py freezes and which files build_vfs.py
# ships. The Pro build is a separate variant (own build dir, quad PSRAM --
# see board_tdeck/mpconfigvariant_TDECK_PRO.cmake) and its outputs land in
# firmware_build/pro/ so a v1 build never clobbers them, or vice versa.
export TDECK_BOARD="tdeck_v1"
OUT_DIR="$SCRIPT_DIR/firmware_build"
IMG_NAME="tdeck"

for arg in "$@"; do
    case "$arg" in
        --no-freeze)
            FREEZE=0
            echo "=== Building WITHOUT frozen modules ===" ;;
        --pro)
            VARIANT="TDECK_PRO"
            TDECK_BOARD="tdeck_pro"
            OUT_DIR="$SCRIPT_DIR/firmware_build/pro"
            IMG_NAME="tdeck_pro"
            echo "=== Building for the T-Deck Pro ===" ;;
        *)
            echo "unknown option: $arg"; exit 1 ;;
    esac
done

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

# Build mpy-cross. Each -Wno-error is compiler-specific and unknown ones are
# hard errors under -Werror, so probe before use -- and the probe must carry
# -Werror itself, exactly as the real mpy-cross build does. Without it, clang
# treats an unknown -Wno-error=... as a soft warning and still exits 0, so the
# probe would "pass" and add a flag that then aborts the real -Werror build.
# gnu-folding-constant is Apple-Clang-only (VLA warning);
# unterminated-string-initialization arrived in GCC 15 and fires -Werror on
# MicroPython v1.24 sources.
echo "=== Building mpy-cross ==="
MPYCROSS_CFLAGS=""
for _flag in gnu-folding-constant unterminated-string-initialization; do
    if echo 'int main(void){return 0;}' | \
       cc -fsyntax-only -Werror "-Wno-error=$_flag" -x c - 2>/dev/null; then
        MPYCROSS_CFLAGS="$MPYCROSS_CFLAGS -Wno-error=$_flag"
    fi
done
make -C micropython/mpy-cross CFLAGS_EXTRA="$MPYCROSS_CFLAGS" -j$(sysctl -n hw.ncpu 2>/dev/null || nproc) 2>&1 | tail -3

# Ensure MicroPython submodules are present
echo "=== Checking MicroPython submodules ==="
cd micropython
git submodule update --init \
    lib/berkeley-db-1.xx \
    lib/micropython-lib \
    lib/tinyusb
cd "$BUILD_DIR"

# Pin the tinyusb managed component. esp_tinyusb 1.0 (MicroPython v1.24's
# pin) references CFG_TUD_CDC_EP_BUFSIZE, which tinyusb 0.17 renamed — but
# its manifest allows tinyusb 0.*, so the IDF component manager pulls an
# incompatible release on a fresh resolve. Constrain it and force a
# re-resolve if the constraint was missing.
MAIN_YML="micropython/ports/esp32/main_esp32s3/idf_component.yml"
if [ -f "$MAIN_YML" ] && ! grep -q 'espressif/tinyusb' "$MAIN_YML"; then
    # awk, not sed: inserting a line portably is beyond BSD sed's -i
    awk '{print} /^  espressif\/esp_tinyusb:/{print "  espressif/tinyusb: \"<0.17.0\""}' \
        "$MAIN_YML" > "$MAIN_YML.tmp" && mv "$MAIN_YML.tmp" "$MAIN_YML"
    rm -f  micropython/ports/esp32/dependencies.lock
    rm -rf micropython/ports/esp32/managed_components
fi

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
    mkdir -p "$OUT_DIR"
    cp "$BUILD_OUT/micropython.bin"                "$OUT_DIR/"
    cp "$BUILD_OUT/bootloader/bootloader.bin"      "$OUT_DIR/"
    cp "$BUILD_OUT/partition_table/partition-table.bin" "$OUT_DIR/"
    # The app-only update image (M5Launcher OTA slot / JTAG slot flash) is
    # byte-identical to the app image; releases ship it under this name.
    cp "$BUILD_OUT/micropython.bin" "$OUT_DIR/${IMG_NAME}_update.bin"

    APP_SIZE=$(stat -f%z "$OUT_DIR/micropython.bin" 2>/dev/null || stat -c%s "$OUT_DIR/micropython.bin")

    # --- Step 5: VFS image + single-flash merge ---
    # Optional: without littlefs-python you still get the three-part flash,
    # you just don't get the one-file image the releases ship.
    MERGED=""
    if python3 -c "import littlefs" 2>/dev/null; then
        echo ""
        echo "=== Building VFS image ==="
        python3 "$SCRIPT_DIR/build_vfs.py" --board "$TDECK_BOARD" --out "$OUT_DIR/vfs.bin"

        # Offsets come from the partition table; keep them in step with
        # board_tdeck/partitions-tdeck-8MiB.csv if that table ever moves.
        VFS_OFF=$(python3 -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
from build_vfs import parse_partition, PART_CSV
print(hex(parse_partition(PART_CSV)[0]))")
        echo "=== Merging single-flash image (vfs @ $VFS_OFF) ==="
        python3 -m esptool --chip esp32s3 merge_bin -o "$OUT_DIR/${IMG_NAME}_firmware.bin" \
            --flash_mode dio --flash_size 8MB --flash_freq 80m \
            0x0 "$OUT_DIR/bootloader.bin" \
            0x8000 "$OUT_DIR/partition-table.bin" \
            0x10000 "$OUT_DIR/micropython.bin" \
            "$VFS_OFF" "$OUT_DIR/vfs.bin" 2>&1 | tail -2
        MERGED=1
    else
        echo ""
        echo "=== Skipping single-flash image (pip install littlefs-python) ==="
    fi

    echo ""
    echo "=== SUCCESS ==="
    echo "App size: $((APP_SIZE / 1024)) KB"
    echo ""
    echo "Output files in $OUT_DIR/:"
    echo "  bootloader.bin      (0x0)"
    echo "  partition-table.bin (0x8000)"
    echo "  micropython.bin     (0x10000)"
    echo "  ${IMG_NAME}_update.bin  (app-only, same bytes as micropython.bin)"
    if [ -n "$MERGED" ]; then
        echo "  vfs.bin             ($VFS_OFF)"
        echo "  ${IMG_NAME}_firmware.bin  (0x0, single-flash)"
    fi
    echo ""
    echo "Flash with:"
    echo "  bash $SCRIPT_DIR/flash_tdeck.sh [PORT]"
    if [ -n "$MERGED" ]; then
        echo "or, wiping the filesystem (backs out /rns identity too):"
        echo "  esptool --chip esp32s3 write-flash 0x0 $OUT_DIR/${IMG_NAME}_firmware.bin"
    fi
    if [ "$FREEZE" = "1" ]; then
        echo ""
        echo "Frozen modules are in ROM; the codec/crypto modules are built"
        echo "into the app image. The filesystem only carries main.py,"
        echo "tdeck_config.py and logo.jpg."
    fi
else
    echo "=== BUILD FAILED ==="
    echo "Check logs in: $BUILD_OUT/log/"
    exit 1
fi

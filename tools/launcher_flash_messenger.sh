#!/bin/bash
# ============================================================================
# M5LAUNCHER DEVICE ONLY.  Flashes the messenger at ota_0 / 0x1a0000.
# ============================================================================
# Flash the T-Deck MESSENGER firmware onto the M5Launcher device, at the
# ota_0 / tdeckf offset 0x1a0000 via openocd/JTAG, with verify.
#
# DO NOT confuse with flash_now.sh / flash_tdeck.sh — those are the VANILLA
# scripts: they hardcode 0x10000 (and flash_tdeck.sh does a full erase_flash).
# On a Launcher device 0x10000 is the M5Launcher itself, so those clobber it,
# overrun the messenger, and (erase) wipe the custom partition table + data.
# This script writes ONLY the messenger partition; the Launcher (0x10000) and
# the vfs data (0x3a0000) are left untouched.
#
# Prereqs: this device has FOUR messenger slots (dumped 2026-09-26):
#   tdeckf ota_0 @0x1a0000 2M | tdeckl ota_1 @0x4a0000 2M
#   tdeck1 ota_2 @0x6a0000 2M | tdeck2 ota_3 @0x8a0000 1856K
# Each Launcher install moves to the next one, and the partition table does
# NOT tell you which one boots. Find the live one FIRST, with the messenger
# running (USB PID 0x4001):
#
#   mpremote connect /dev/cu.usbmodemNNNN exec \
#     "from esp32 import Partition; print(Partition(Partition.RUNNING).info())"
#
# The third field of the tuple is the offset. Pass it as $1. On 2026-09-20 this
# device booted tdeckl @ 0x4a0000; flashing the other slot verified OK and
# changed nothing, because a verify only proves the bytes landed where you
# aimed -- not that anything boots them.
#
# Then power-cycle to the M5Launcher menu (USB PID 0x1001 = JTAG loader).
# If openocd reports "Failed to run flasher stub (-302)", the device is running
# an app, not in the loader: reset it to the Launcher menu and retry.
#
#   ! bash tools/launcher_flash_messenger.sh 0x4a0000
set -e
set -o pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/tools/firmware_build/micropython.bin"
OFFSET="${1:-}"        # REQUIRED. The slot this device actually boots -- see above.
if [ -z "$OFFSET" ]; then
    echo "ERROR: no offset given, and this script will not guess."
    echo "       This device has four messenger slots and the wrong one verifies"
    echo "       OK while changing nothing. Find the live slot first:"
    echo ""
    echo "         mpremote connect /dev/cu.usbmodemNNNN exec \\"
    echo "           \"from esp32 import Partition; print(Partition(Partition.RUNNING).info())\""
    echo ""
    echo "       then:  bash tools/launcher_flash_messenger.sh <offset>"
    exit 1
fi
if [ "$OFFSET" = "0x10000" ]; then
    echo "ABORT: 0x10000 is the M5Launcher itself. Refusing."
    exit 1
fi
# Size of the slot at $OFFSET: writing past it lands in the next partition
# (vfs = your data, after tdeckf). tdeck2 is smaller than the others.
case "$OFFSET" in
    0x1a0000|0x4a0000|0x6a0000) CAP=2097152 ;;
    0x8a0000)                   CAP=1900544 ;;   # 1856K
    *) echo "ABORT: $OFFSET is not a known messenger slot on this device"
       echo "       (0x1a0000 0x4a0000 0x6a0000 0x8a0000). Dump the partition table"
       echo "       with tools/launcher_dump_parttable.sh and update this script."
       exit 1 ;;
esac
OOROOT="$HOME/.espressif/tools/openocd-esp32/v0.12.0-esp32-20260304/openocd-esp32"
OCD="$OOROOT/bin/openocd"
OCDS="$OOROOT/share/openocd/scripts"

[ -x "$OCD" ] || { echo "ERROR: openocd 20260304 not found at $OCD"; exit 1; }
[ -f "$BIN" ] || { echo "ERROR: $BIN not found — run 'bash tools/build_firmware.sh' first"; exit 1; }

SZ=$(stat -f%z "$BIN" 2>/dev/null || stat -c%s "$BIN")
echo "=== image: $BIN"
echo "=== size: $SZ bytes  |  offset: $OFFSET  |  partition cap: $CAP bytes ==="
if [ "$SZ" -gt "$CAP" ]; then
    echo "ABORT: image ($SZ B) exceeds the slot at $OFFSET ($CAP B)."
    echo "Flashing would overrun into the next partition. NOT flashing."
    exit 1
fi

echo "=== Flashing messenger at $OFFSET via openocd (with verify) ==="
echo "=== Only $OFFSET..+$SZ is written; Launcher @0x10000 and vfs @0x3a0000 are NOT touched ==="
"$OCD" -s "$OCDS" -f board/esp32s3-builtin.cfg \
    -c "init" -c "reset halt" \
    -c "program_esp $BIN $OFFSET verify" \
    -c "reset run" -c "shutdown"

echo ""
echo "=== FLASH COMPLETE (verified). ==="
echo "Now power-cycle the T-Deck (unplug/replug — soft reset does not cleanly reboot"
echo "this Launcher device), then launch the messenger from the M5Launcher menu."
echo ""
echo "Then CONFIRM it is really running what you just flashed -- a verify does not:"
echo "  mpremote connect /dev/cu.usbmodemNNNN exec \\"
echo "    \"from esp32 import Partition; print(Partition(Partition.RUNNING).info())\""

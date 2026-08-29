#!/usr/bin/env bash
# Deploy the full LXMF messenger UI to a T-Deck Pro running stock MicroPython
# v1.24.1. This is deploy_pro_headless.sh plus the display, the keyboard and
# the app itself.
#
#   ./tools/deploy_pro.sh [/dev/cu.usbmodemXXX]
#   ./tools/deploy_pro.sh --check [/dev/cu.usbmodemXXX]   # verify, write nothing
#   mpremote connect /dev/cu.usbmodem101 exec 'import tdeck_node'
#
# Every file goes through tools/push_file.py, never `mpremote cp`. cp sends a
# file as one transfer and fails destructively on this board's USB-JTAG bridge:
# it opens the remote file for writing before the transfer dies, so a failure
# leaves a truncated file and a retry loop leaves a shorter one. It once cut
# ui.py to 5120 bytes on both decks and left neither able to boot. push_file.py
# writes to a temporary name in verified chunks and swaps it in by rename.
#
# It also hashes what is already on the device and skips anything that matches,
# so this doubles as the verification pass: a run that reports every file up to
# date has confirmed the whole install, not just what it wrote. A redeploy that
# changed one file costs one file.
#
# The v1 gets none of this: it runs from frozen firmware built by
# build_firmware.sh, where tools/tdeck_manifest.py freezes board_tdeck_v1.py
# and leaves board_id.py at its checked-in "tdeck_v1".
set -euo pipefail

CHECK=""
if [ "${1:-}" = "--check" ]; then
    CHECK="--check"
    shift
fi

PORT="${1:-/dev/cu.usbmodem101}"
MPR="$HOME/.local/bin/mpremote"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
FW="$SRC/vendor/uP-reticulum/firmware"

run() { "$MPR" connect "$PORT" "$@"; }

# src dst pairs, handed to push_file.py in one invocation at the end.
PAIRS=()
add() { PAIRS+=("$1" "$2"); }

if [ -n "$CHECK" ]; then
    echo "== checking the install on $PORT, writing nothing =="
else
echo "== clearing stale root copies of /lib modules =="
# sys.path is ['', '.frozen', '/lib'], so a leftover copy in the device root
# shadows the one this script installs -- silently, and forever. An earlier
# bring-up session left eink_shim.py, eink_gdeq031t10.py, tca8418.py and
# spleen_8x16.py there, and every deploy after it updated /lib while the device
# went on running the old code. Also clears .part and .bak files an interrupted
# push may have left behind.
run exec "
import os
stale = ('eink_gdeq031t10.py', 'eink_shim.py', 'tca8418.py', 'spleen_8x16.py',
         'bq27220.py', 'adc_reader.py', 'shell_4x6.py', 'shell_5x8.py',
         'shell_6x10.py', 'shell_6x12.py')
for f in stale:
    try:
        os.remove('/' + f)
        print('removed shadowing /' + f)
    except OSError:
        pass
for f in os.listdir('/'):
    if f.endswith('.part'):
        try:
            os.remove('/' + f)
            print('removed leftover /' + f)
        except OSError:
            pass
"
fi

echo "== building the file list =="
for f in "$FW"/urns/*.py;            do add "$f" "urns/$(basename "$f")"; done
for f in "$FW"/urns/crypto/*.py;     do add "$f" "urns/crypto/$(basename "$f")"; done
for f in "$FW"/urns/interfaces/*.py; do add "$f" "urns/interfaces/$(basename "$f")"; done

# Native modules, xtensawin only.
for f in "$FW"/lib/*xtensawin.mpy "$FW"/lib/ed25519_iram.mpy; do
    [ -e "$f" ] && add "$f" "lib/$(basename "$f")"
done

# The SX126x driver. Without it the LoRa interface still registers and the node
# looks healthy, but it is offline forever ("no module named 'lora'") and every
# announce is silently dropped. txb stays 0.
for f in "$SRC"/lib/lora/*.py; do add "$f" "lora/$(basename "$f")"; done

for f in eink_gdeq031t10.py eink_shim.py tca8418.py bq27220.py spleen_8x16.py; do
    add "$SRC/lib/$f" "lib/$f"
done
for f in "$SRC"/lib/shell_*.py; do add "$f" "lib/$(basename "$f")"; done
# ui.py imports adc_reader by bare name; it self-disables on a board that
# declares no battery, which the Pro does not (it has a BQ27220 instead).
add "$FW/peripherals/adc_reader.py" "lib/adc_reader.py"

add "$SRC/tdeck_pro_config.py"         "tdeck_pro_config.py"
add "$SRC/board.py"                    "board.py"
add "$SRC/board_tdeck_pro.py"          "board_tdeck_pro.py"
add "$SRC/board_geometry_tdeck_pro.py" "board_geometry.py"
add "$FW/lora_boards.py"               "lora_boards.py"

# The marker that selects the whole hardware layer. Checked in as "tdeck_v1",
# so it is generated here rather than copied -- a Pro that boots the v1
# bring-up drives the radio's chip select as a trackball axis.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
printf '# Written by tools/deploy_pro.sh -- see board.py.\nBOARD = "tdeck_pro"\n' \
    > "$TMP/board_id.py"
add "$TMP/board_id.py" "board_id.py"

for f in ui.py sound.py micron.py nomad_browser.py rnsh_proto.py rnsh_client.py \
         terminal.py es7210.py tdeck_node.py; do
    add "$SRC/$f" "$f"
done

if [ -n "$CHECK" ]; then
    echo "== verifying $(( ${#PAIRS[@]} / 2 )) files =="
    python3 "$SRC/tools/push_file.py" --check "$PORT" "${PAIRS[@]}"
    echo
    echo "Every file on the device matches this checkout."
    exit 0
fi

echo "== pushing $(( ${#PAIRS[@]} / 2 )) files (unchanged ones are verified and skipped) =="
python3 "$SRC/tools/push_file.py" "$PORT" "${PAIRS[@]}"

echo
echo "Deployed and verified. Run it with:"
echo "  $MPR connect $PORT exec 'import tdeck_node'"
echo
echo "Re-verify later without writing anything:"
echo "  ./tools/deploy_pro.sh --check $PORT"

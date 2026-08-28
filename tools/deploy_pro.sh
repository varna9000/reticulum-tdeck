#!/usr/bin/env bash
# Deploy the full LXMF messenger UI to a T-Deck Pro running stock MicroPython
# v1.24.1. This is deploy_pro_headless.sh plus the display, the keyboard and
# the app itself.
#
#   ./tools/deploy_pro.sh [/dev/cu.usbmodemXXX]
#   mpremote connect /dev/cu.usbmodem101 exec 'import tdeck_node'
#
# The v1 gets none of this: it runs from frozen firmware built by
# build_firmware.sh, where tools/tdeck_manifest.py freezes board_tdeck_v1.py
# and leaves board_id.py at its checked-in "tdeck_v1".
set -euo pipefail

PORT="${1:-/dev/cu.usbmodem101}"
MPR="$HOME/.local/bin/mpremote"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
FW="$SRC/vendor/uP-reticulum/firmware"

run() { "$MPR" connect "$PORT" "$@"; }

echo "== creating directories =="
for d in urns urns/crypto urns/interfaces lib lora; do
    run fs mkdir ":$d" 2>/dev/null || true
done

echo "== uReticulum stack =="
for f in "$FW"/urns/*.py;            do run cp "$f" ":urns/$(basename "$f")"; done
for f in "$FW"/urns/crypto/*.py;     do run cp "$f" ":urns/crypto/$(basename "$f")"; done
for f in "$FW"/urns/interfaces/*.py; do run cp "$f" ":urns/interfaces/$(basename "$f")"; done

echo "== native modules (xtensawin only) =="
for f in "$FW"/lib/*xtensawin.mpy "$FW"/lib/ed25519_iram.mpy; do
    [ -e "$f" ] && run cp "$f" ":lib/$(basename "$f")"
done

echo "== SX126x radio driver =="
# Without this the LoRa interface still registers and the node looks healthy,
# but it is offline forever ("no module named 'lora'") and every announce is
# silently dropped. txb stays 0.
for f in "$SRC"/lib/lora/*.py; do run cp "$f" ":lora/$(basename "$f")"; done

echo "== drivers =="
run cp "$SRC/lib/eink_gdeq031t10.py" :lib/eink_gdeq031t10.py
run cp "$SRC/lib/eink_shim.py"       :lib/eink_shim.py
run cp "$SRC/lib/tca8418.py"         :lib/tca8418.py
run cp "$SRC/lib/spleen_8x16.py"     :lib/spleen_8x16.py
for f in "$SRC"/lib/shell_*.py; do run cp "$f" ":lib/$(basename "$f")"; done
# ui.py imports adc_reader by bare name; it self-disables on a board that
# declares no battery, which the Pro does not (it has a BQ27220 instead).
run cp "$FW/peripherals/adc_reader.py" :lib/adc_reader.py

echo "== board selection =="
run cp "$SRC/tdeck_pro_config.py"          :tdeck_pro_config.py
run cp "$SRC/board.py"                     :board.py
run cp "$SRC/board_tdeck_pro.py"           :board_tdeck_pro.py
run cp "$SRC/board_geometry_tdeck_pro.py"  :board_geometry.py
run cp "$FW/lora_boards.py"                :lora_boards.py
# The marker that selects the whole hardware layer. Checked in as "tdeck_v1",
# so it is rewritten here rather than copied -- a Pro that boots the v1
# bring-up drives the radio's chip select as a trackball axis.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
printf '# Written by tools/deploy_pro.sh -- see board.py.\nBOARD = "tdeck_pro"\n' \
    > "$TMP/board_id.py"
run cp "$TMP/board_id.py" :board_id.py

echo "== app =="
for f in ui.py sound.py micron.py nomad_browser.py rnsh_proto.py rnsh_client.py \
         terminal.py es7210.py tdeck_node.py; do
    run cp "$SRC/$f" ":$f"
done

echo
echo "Deployed. Run it with:"
echo "  $MPR connect $PORT exec 'import tdeck_node'"

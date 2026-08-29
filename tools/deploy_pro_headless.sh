#!/usr/bin/env bash
# Deploy a headless uReticulum LoRa node to a T-Deck Pro running stock
# MicroPython v1.24.1. This is Phase 1: prove the radio on air before the
# display and keyboard are involved at all.
#
#   ./tools/deploy_pro_headless.sh [/dev/cu.usbmodemXXX]
#
# Files go through tools/push_file.py, not `mpremote cp` -- see the note at the
# top of deploy_pro.sh for why cp is not safe on this board.
set -euo pipefail

PORT="${1:-/dev/cu.usbmodem101}"
MPR="$HOME/.local/bin/mpremote"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
FW="$SRC/vendor/uP-reticulum/firmware"

PAIRS=()
add() { PAIRS+=("$1" "$2"); }

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

add "$FW/lora_boards.py"                  "lora_boards.py"
add "$SRC/tdeck_pro_config.py"            "tdeck_pro_config.py"
add "$SRC/tools/tdeck_pro_headless.py"    "tdeck_pro_headless.py"

echo "== pushing $(( ${#PAIRS[@]} / 2 )) files (unchanged ones are verified and skipped) =="
python3 "$SRC/tools/push_file.py" "$PORT" "${PAIRS[@]}"

echo
echo "Deployed and verified. Run it with:"
echo "  $MPR connect $PORT run $SRC/tools/tdeck_pro_headless.py"

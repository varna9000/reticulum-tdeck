#!/usr/bin/env bash
# Deploy a headless uReticulum LoRa node to a T-Deck Pro running stock
# MicroPython v1.24.1. This is Phase 1: prove the radio on air before the
# display and keyboard are involved at all.
#
#   ./tools/deploy_pro_headless.sh [/dev/cu.usbmodemXXX]
set -euo pipefail

PORT="${1:-/dev/cu.usbmodem101}"
MPR="$HOME/.local/bin/mpremote"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
FW="$SRC/vendor/uP-reticulum/firmware"

run() { "$MPR" connect "$PORT" "$@"; }

echo "== creating directories =="
for d in urns urns/crypto urns/interfaces lib lora peripherals; do
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

echo "== board config =="
run cp "$FW/lora_boards.py" :lora_boards.py
run cp "$SRC/tdeck_pro_config.py" :tdeck_pro_config.py

echo "== headless node =="
run cp "$SRC/tools/tdeck_pro_headless.py" :tdeck_pro_headless.py

echo
echo "Deployed. Run it with:"
echo "  $MPR connect $PORT run $SRC/tools/tdeck_pro_headless.py"

# MicroPython frozen manifest for T-Deck
# This freezes app .py files into firmware ROM.
#
# Benefits:
#   - Zero filesystem RAM usage (executed from ROM)
#   - Instant import (no filesystem read + parse)
#   - Replaces all .mpy bytecode files (but NOT natmod .mpy files)
#
# Natmod .mpy files (ed25519_fast, bz2_fast, codec2_fast, tjpgd_fast, webp_fast)
# must STAY on the filesystem — they contain native machine code that can't be frozen.

# Include the default ESP32-S3 manifest first
include("$(PORT_DIR)/boards/manifest.py")

# Project root — the manifest path is this file's own location.
# makemanifest.py resolves the path before exec, so we can extract it
# from the -v args. Use TDECK_ROOT env var set by build_firmware.sh.
import os
_root = os.environ["TDECK_ROOT"]

# --- App modules (top-level) ---
# tdeck_config.py  — on filesystem, user-editable
# tdeck_node.py    — on filesystem as main.py, user-editable
freeze(_root, "ui.py")
freeze(_root, "sound.py")
freeze(_root, "es7210.py")

# --- Font ---
freeze(_root + "/lib", "vga2_8x16.py")

# --- urns (Reticulum stack) ---
freeze(_root + "/lib", "urns/__init__.py")
freeze(_root + "/lib", "urns/bz2dec.py")
freeze(_root + "/lib", "urns/const.py")
freeze(_root + "/lib", "urns/destination.py")
freeze(_root + "/lib", "urns/identity.py")
freeze(_root + "/lib", "urns/link.py")
freeze(_root + "/lib", "urns/log.py")
freeze(_root + "/lib", "urns/lxmf.py")
freeze(_root + "/lib", "urns/packet.py")
freeze(_root + "/lib", "urns/resource.py")
freeze(_root + "/lib", "urns/reticulum.py")
freeze(_root + "/lib", "urns/transport.py")
freeze(_root + "/lib", "urns/umsgpack.py")

# --- urns/crypto ---
freeze(_root + "/lib", "urns/crypto/__init__.py")
freeze(_root + "/lib", "urns/crypto/aes.py")
freeze(_root + "/lib", "urns/crypto/ed25519.py")
freeze(_root + "/lib", "urns/crypto/hashes.py")
freeze(_root + "/lib", "urns/crypto/hkdf.py")
freeze(_root + "/lib", "urns/crypto/hmac.py")
freeze(_root + "/lib", "urns/crypto/pkcs7.py")
freeze(_root + "/lib", "urns/crypto/sha512.py")
freeze(_root + "/lib", "urns/crypto/token.py")
freeze(_root + "/lib", "urns/crypto/x25519.py")

# --- urns/crypto/pure25519 ---
freeze(_root + "/lib", "urns/crypto/pure25519/__init__.py")
freeze(_root + "/lib", "urns/crypto/pure25519/_ed25519.py")
freeze(_root + "/lib", "urns/crypto/pure25519/basic.py")
freeze(_root + "/lib", "urns/crypto/pure25519/ed25519_oop.py")
freeze(_root + "/lib", "urns/crypto/pure25519/eddsa.py")

# --- urns/interfaces ---
freeze(_root + "/lib", "urns/interfaces/__init__.py")
freeze(_root + "/lib", "urns/interfaces/e32.py")
freeze(_root + "/lib", "urns/interfaces/lora.py")
freeze(_root + "/lib", "urns/interfaces/serial.py")
freeze(_root + "/lib", "urns/interfaces/tcp.py")
freeze(_root + "/lib", "urns/interfaces/udp.py")

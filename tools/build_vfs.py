#!/usr/bin/env python3
"""Build the T-Deck VFS partition image (vfs.bin).

The firmware freezes almost everything into ROM, so the filesystem only
carries what has to stay user-editable or is read as data:

    main.py          <- tdeck_node.py, the app entry point
    tdeck_config.py  <- pin map / radio preset
    logo.jpg         <- splash image

The five former natmods (ed25519/bz2/codec2/tjpgd/webp) are built into the
app image as user C modules since the 3MiB-factory rebuild, so /lib carries
no *module the firmware already has* -- a stale same-named copy there would
only be dead weight, since built-in modules win the import search.

One deliberate exception: lib/ed25519_iram.mpy. Built-in C modules execute
from flash XIP, which for the Ed25519/X25519 hot loops measures ~80x slower
than IRAM on this board (verify 1461ms vs 17.6ms -- see FR.md). The natmod
build of the same Monocypher code loads into IRAM at import. It ships under
a name no built-in registers (ed25519_iram), so it is importable at all, and
urns/crypto/ed25519.py prefers it and falls back to the built-in if the file
is missing or IRAM is exhausted -- worst case is slow crypto, never the
v1.1.0 no-radio failure mode.

The corollary, and the reason this file lists so little: an import that is
neither built in nor in tools/tdeck_manifest.py has nowhere left to come
from. That is how the SX1262 driver (micropython-lib lora*, once mip-installed
into /lib/lora) fell out of a release image -- it is pure Python, so it is
frozen now rather than shipped here. Add imports to the manifest, not to
CONTENTS; this list is for files a user is meant to edit or that are read as
data.

Filesystem is **littlefs2**, not FAT, whatever the partition csv's subtype
column says: MicroPython's inisetup.setup() formats a partition labelled
"vfs" as littlefs2 (it only reaches for VfsFat on the label "ffat"), so a
device that formats its own filesystem ends up with lfs2. Shipping the same
thing keeps a flashed device and a self-formatted one identical. _boot.py
autodetects on mount either way.

Geometry is read from the partition table rather than hardcoded -- that
table has already moved once (factory 2MiB -> 3MiB shrank vfs 6MiB -> 5MiB)
and a stale block count here would produce an image that mounts and then
corrupts as it fills.

Usage:
    python3 build_vfs.py [--out firmware_build/vfs.bin]
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PART_CSV = os.path.join(HERE, "board_tdeck", "partitions-tdeck-8MiB.csv")

# (source path relative to repo root, name on the device)
CONTENTS = [
    ("tdeck_node.py", "main.py"),
    ("tdeck_config.py", "tdeck_config.py"),
    ("logo.jpg", "logo.jpg"),
    ("vendor/uP-reticulum/firmware/lib/ed25519_iram.mpy", "lib/ed25519_iram.mpy"),
]

BLOCK_SIZE = 4096  # esp32 flash sector, and what VfsLfs2 uses on a Partition


def parse_partition(csv_path, name="vfs"):
    """Return (offset, size) of a partition, resolving K/M suffixes."""
    def num(tok):
        tok = tok.strip()
        if tok.lower().endswith("k"):
            return int(tok[:-1], 0) * 1024
        if tok.lower().endswith("m"):
            return int(tok[:-1], 0) * 1024 * 1024
        return int(tok, 0)

    with open(csv_path) as f:
        for line in f:
            line = line.split("#")[0].strip()
            if not line:
                continue
            cols = [c.strip() for c in line.split(",")]
            if len(cols) >= 5 and cols[0] == name:
                return num(cols[3]), num(cols[4])
    raise SystemExit("no '%s' partition in %s" % (name, csv_path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "firmware_build", "vfs.bin"))
    args = ap.parse_args()

    try:
        from littlefs import LittleFS
    except ImportError:
        raise SystemExit("need littlefs-python: pip install littlefs-python")

    offset, size = parse_partition(PART_CSV)
    block_count = size // BLOCK_SIZE
    print("=== vfs partition @ 0x%X, %d KB -> %d blocks of %d ==="
          % (offset, size // 1024, block_count, BLOCK_SIZE))

    fs = LittleFS(block_size=BLOCK_SIZE, block_count=block_count)
    total = 0
    for src_rel, dest in CONTENTS:
        src = os.path.join(ROOT, src_rel)
        if not os.path.exists(src):
            raise SystemExit("missing %s" % src)
        with open(src, "rb") as f:
            data = f.read()
        if "/" in dest:
            fs.makedirs(dest.rsplit("/", 1)[0], exist_ok=True)
        with fs.open(dest, "wb") as f:
            f.write(data)
        total += len(data)
        print("  %-16s <- %-16s %6d B" % (dest, src_rel, len(data)))

    img = fs.context.buffer
    if len(img) != size:
        raise SystemExit("image is %d B, partition is %d B" % (len(img), size))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(img)
    print("=== wrote %s (%d KB image, %d B of payload) ==="
          % (args.out, len(img) // 1024, total))


if __name__ == "__main__":
    sys.exit(main())

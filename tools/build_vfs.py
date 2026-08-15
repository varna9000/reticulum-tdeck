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

lib/ed25519_iram.mpy used to be shipped here, because a built-in C module
executes from flash XIP and the Ed25519/X25519 hot loops measured ~80x slower
that way (verify 1461ms vs 17.6ms) while a natmod is committed to IRAM at
import. That is no longer needed: tools/c_modules/ed25519_fast now builds
Monocypher into its own archive and an ldgen `noflash` fragment places it in
IRAM at LINK time, so the built-in is the fast path. urns/crypto/ed25519.py
still tries `ed25519_iram` first, gets ImportError, and falls through to the
built-in -- which is now IRAM-resident, so the fallback costs nothing.

Dropping the .mpy is what makes an M5Launcher install viable at full speed:
the natmod could only ever be loaded from a filesystem, and it also cost a
second ~45KB IRAM allocation on top of the statically-placed copy.

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
    ("tdeck_config.py", "tdeck_config.py"),
    ("logo.jpg", "logo.jpg"),
]
# main.py is NOT here any more: tdeck_node.py is frozen as main.py by
# tools/tdeck_manifest.py so that an app-only update (which carries no
# filesystem payload, and therefore preserves /rns) also carries the entry
# point. Shipping a filesystem copy too would be dead weight — frozen wins in
# pyexec_file_if_exists — and actively misleading, since editing it would do
# nothing.

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

#!/usr/bin/env python3
"""Patch synthesized Unicode block-element glyphs into a generated font module.

Modern nomadnet/micron banner art uses the Unicode block elements beyond
CP437's set: eighth blocks (U+2581-2587, U+2589-258F), the upper/right
eighths (U+2594/2595) and the quadrants (U+2596-259F). The CP437+CP866 slot
layout keeps 0x00-0x1F blank (VGA dingbats a terminal never draws), so those
24 glyphs go there. They are pure geometry, so they are synthesized rather
than taken from a BDF -- a half block is a half block in any typeface.

Idempotent and conservative: every target slot must be blank (or already
hold exactly the glyph being written), everything else in the module is
preserved byte-for-byte. ui.py's _GFX table maps the codepoints to these
slots -- keep the two in sync.

Usage:
    python3 tools/gen_block_glyphs.py lib/spleen_8x16.py
"""

import re
import sys

# slot -> codepoint; mirrors ui.py _GFX (synthesized entries)
BLOCK_SLOTS = {
    0x01: 0x2581, 0x02: 0x2582, 0x03: 0x2583,   # lower 1/8, 2/8, 3/8
    0x04: 0x2585, 0x05: 0x2586, 0x06: 0x2587,   # lower 5/8, 6/8, 7/8
    0x07: 0x2589, 0x08: 0x258A, 0x09: 0x258B,   # left 7/8, 6/8, 5/8
    0x0A: 0x258D, 0x0B: 0x258E, 0x0C: 0x258F,   # left 3/8, 2/8, 1/8
    0x0D: 0x2594, 0x0E: 0x2595,                 # upper 1/8, right 1/8
    0x0F: 0x2596, 0x10: 0x2597, 0x11: 0x2598,   # quadrants
    0x12: 0x2599, 0x13: 0x259A, 0x14: 0x259B,
    0x15: 0x259C, 0x16: 0x259D, 0x17: 0x259E,
    0x18: 0x259F,
}


def synth(cp, w=8, h=16):
    """Block-element bitmap for an w x h cell -> h bytes, MSB-left."""
    full = (1 << w) - 1

    def left(n):    # leftmost n columns
        return (full << (w - n)) & full

    def right(n):   # rightmost n columns
        return (1 << n) - 1

    rows = [0] * h
    if 0x2581 <= cp <= 0x2587:            # lower n/8
        n = cp - 0x2580
        for y in range(h - (n * h) // 8, h):
            rows[y] = full
    elif 0x2589 <= cp <= 0x258F:          # left (8 - (cp - 0x2588))/8
        n = 8 - (cp - 0x2588)
        v = left((n * w) // 8)
        rows = [v] * h
    elif cp == 0x2594:                    # upper eighth
        for y in range(h // 8):
            rows[y] = full
    elif cp == 0x2595:                    # right eighth
        rows = [right(w // 8)] * h
    elif 0x2596 <= cp <= 0x259F:          # quadrants
        QUAD = {0x2596: "LL", 0x2597: "LR", 0x2598: "UL",
                0x2599: "UL LL LR", 0x259A: "UL LR", 0x259B: "UL UR LL",
                0x259C: "UL UR LR", 0x259D: "UR", 0x259E: "UR LL",
                0x259F: "UR LL LR"}
        parts = QUAD[cp].split()
        top = left(w // 2) if "UL" in parts else 0
        top |= right(w // 2) if "UR" in parts else 0
        bot = left(w // 2) if "LL" in parts else 0
        bot |= right(w // 2) if "LR" in parts else 0
        rows = [top] * (h // 2) + [bot] * (h - h // 2)
    else:
        raise SystemExit("no synthesis rule for U+%04X" % cp)
    return bytes(rows)


def main(path):
    src = open(path).read()
    m = re.search(r"_FONT =\\\n((?:\s*b'[^']*'\\?\n)+)", src)
    if not m:
        raise SystemExit("cannot locate _FONT literal in " + path)
    blob = eval(m.group(1).replace("\\\n", ""))  # concatenated bytes literal
    height = int(re.search(r"HEIGHT = (\d+)", src).group(1))
    first = int(re.search(r"FIRST = (0x[0-9a-fA-F]+|\d+)", src).group(1), 0)
    font = bytearray(blob)

    patched = 0
    for slot, cp in sorted(BLOCK_SLOTS.items()):
        g = synth(cp, 8, height)
        off = (slot - first) * height
        cur = bytes(font[off:off + height])
        if cur == g:
            continue                       # already patched (idempotent)
        if any(cur):
            raise SystemExit("slot 0x%02X not blank (holds a real glyph); "
                             "refusing to overwrite" % slot)
        font[off:off + height] = g
        patched += 1

    # Re-emit the literal in the generator's format: 16 bytes per line.
    lines = []
    for i in range(0, len(font), 16):
        chunk = "".join("\\x%02x" % b for b in font[i:i + 16])
        lines.append("    b'%s'\\\n" % chunk)
    new_literal = "_FONT =\\\n" + "".join(lines)
    src = src[:m.start()] + new_literal + src[m.end():]
    if "block-element glyphs" not in src:
        src = src.replace(
            "unchanged.\n", "unchanged. Slots 0x01-0x18 hold synthesized "
            "block-element glyphs\n(tools/gen_block_glyphs.py) for banner "
            "art; see ui.py _GFX.\n", 1)
    open(path, "w").write(src)
    print("patched %d block glyphs into %s" % (patched, path))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "lib/spleen_8x16.py")

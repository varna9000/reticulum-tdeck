#!/usr/bin/env python3
"""Generate a small bitmap font module for the rnsh shell screen.

The shell renders through its own framebuf row-compositor (ui.py), not through
st7789's text()/write() -- both of those cost one SPI window-set per glyph
(~210us measured), which puts an 80-column repaint at ~565ms. Compositing a
whole row in a framebuf and pushing it with one blit_buffer() is ~87ms for
80x32, faster than the current 40x12 text() screen.

The output format is deliberately the SAME as lib/vga2_8x16_cp866.py -- WIDTH,
HEIGHT, FIRST, LAST and a flat _FONT of HEIGHT bytes per glyph, MSB-left. That
is exactly what framebuf.MONO_HLSB reads, so a glyph is a zero-copy memoryview
slice, and ui.py's existing _tb() unicode->slot mapping keeps working unchanged.

Slot layout (256 slots, matching lib/vga2_8x16_cp866.py):
  0x00-0x1F  blank (the VGA ROM's dingbats; a terminal never draws them)
  0x20-0x7E  ASCII
  0x7F-0xFF  CP437, except the CP866 Cyrillic slots below
  0x80-0x9F  А-Я      0xA0-0xAF  а-п      0xE0-0xEF  р-я
  0xF0..0xF7 Ё ё Є є Ї ї Ў ў        0xFC/0xFD  Ѝ ѝ

Source fonts: X11 misc-fixed (4x6.bdf, 5x8.bdf, 6x10.bdf), public domain --
"Public domain font.  Share and enjoy."

A --fallback module (an already-generated font of the SAME cell size) fills
any slot the BDF has no glyph for. Spleen, for instance, carries no Bulgarian
stressed Ѝ/ѝ, which gen_cp866_font.py deliberately put in the CP866 layout;
falling back to vga2_8x16_cp866 keeps those slots populated instead of blank.

Usage:
    python3 tools/gen_shell_font.py 4x6.bdf  lib/shell_4x6.py
    python3 tools/gen_shell_font.py 5x8.bdf  lib/shell_5x8.py
    python3 tools/gen_shell_font.py spleen-8x16.bdf lib/spleen_8x16.py \\
            --fallback lib/vga2_8x16_cp866.py
"""

import sys
import os

# Byte slot -> unicode codepoint overrides (mirrors ui.py _CYR exactly).
SLOTS = {0xF0: 0x401, 0xF1: 0x451, 0xF2: 0x404, 0xF3: 0x454,
         0xF4: 0x407, 0xF5: 0x457, 0xF6: 0x40E, 0xF7: 0x45E,
         0xFC: 0x40D, 0xFD: 0x45D}
for _i in range(32):
    SLOTS[0x80 + _i] = 0x410 + _i          # А-Я
for _i in range(16):
    SLOTS[0xA0 + _i] = 0x430 + _i          # а-п
    SLOTS[0xE0 + _i] = 0x440 + _i          # р-я


def slot_codepoints():
    """-> {slot: codepoint} for all 256 slots. CP437 base + CP866 overrides."""
    cps = {}
    for slot in range(0x20, 0x100):
        if slot in SLOTS:
            cps[slot] = SLOTS[slot]
            continue
        # Python ships the cp437 codec, so the base layout is exact by
        # construction rather than hand-transcribed.
        try:
            cps[slot] = ord(bytes([slot]).decode("cp437"))
        except Exception:
            pass
    return cps


def parse_bdf(path):
    """-> (cell_w, cell_h, ascent, {codepoint: (bw, bh, xoff, yoff, [rows])})."""
    glyphs = {}
    cell_w = cell_h = ascent = None
    with open(path, encoding="latin-1") as fh:
        lines = iter(fh)
        for line in lines:
            if line.startswith("FONTBOUNDINGBOX"):
                p = line.split()
                cell_w, cell_h = int(p[1]), int(p[2])
            elif line.startswith("FONT_ASCENT"):
                ascent = int(line.split()[1])
            elif line.startswith("STARTCHAR"):
                enc, bbx, rows = None, None, None
                for gl in lines:
                    if gl.startswith("ENCODING"):
                        enc = int(gl.split()[1])
                    elif gl.startswith("BBX"):
                        p = gl.split()
                        bbx = (int(p[1]), int(p[2]), int(p[3]), int(p[4]))
                    elif gl.startswith("BITMAP"):
                        rows = []
                        for bl in lines:
                            if bl.startswith("ENDCHAR"):
                                break
                            rows.append(bl.strip())
                        break
                    elif gl.startswith("ENDCHAR"):
                        break
                if enc is not None and enc >= 0 and bbx and rows is not None:
                    glyphs[enc] = (bbx[0], bbx[1], bbx[2], bbx[3], rows)
    if cell_w is None or ascent is None:
        raise SystemExit("%s: missing FONTBOUNDINGBOX or FONT_ASCENT" % path)
    if cell_w > 8:
        raise SystemExit("cell width %d > 8; the row compositor packs one byte "
                         "per glyph row" % cell_w)
    return cell_w, cell_h, ascent, glyphs


def render(glyph, cell_w, cell_h, ascent):
    """Place a BDF glyph into a cell_w x cell_h cell -> cell_h bytes, MSB-left."""
    bw, bh, xoff, yoff, rows = glyph
    out = [0] * cell_h
    top = ascent - yoff - bh          # cell row holding the glyph's first row
    nbytes = (bw + 7) // 8
    for i, hexrow in enumerate(rows[:bh]):
        y = top + i
        if not (0 <= y < cell_h):
            continue                  # glyph taller than the cell -- clip
        val = int(hexrow, 16) >> (nbytes * 8 - bw)   # bw bits, right-aligned
        shift = cell_w - bw - xoff
        bits = (val << shift) if shift >= 0 else (val >> -shift)
        out[y] = (bits & ((1 << cell_w) - 1)) << (8 - cell_w)   # left-align
    return out


def load_fallback(path, cell_w, cell_h):
    """Glyph bytes of an already-generated font module of the same cell size."""
    ns = {}
    with open(path) as fh:
        exec(fh.read(), ns)          # generated modules are pure data
    if (ns.get("WIDTH"), ns.get("HEIGHT")) != (cell_w, cell_h):
        raise SystemExit("fallback %s is %sx%s, need %dx%d"
                         % (path, ns.get("WIDTH"), ns.get("HEIGHT"), cell_w, cell_h))
    return bytes(ns["_FONT"])


def main():
    argv = sys.argv[1:]
    fallback_path = None
    if "--fallback" in argv:
        i = argv.index("--fallback")
        fallback_path = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 2:
        raise SystemExit(__doc__)
    src, dst = argv
    cell_w, cell_h, ascent, glyphs = parse_bdf(src)
    cps = slot_codepoints()
    fallback = load_fallback(fallback_path, cell_w, cell_h) if fallback_path else None

    data = bytearray()
    missing = []
    filled = []
    for slot in range(256):
        cp = cps.get(slot)
        g = glyphs.get(cp) if cp is not None else None
        if g:
            data.extend(render(g, cell_w, cell_h, ascent))
            continue
        # 0x00-0x1F has no codepoint mapping — leave the VGA ROM's dingbats
        # out of a modern font; a terminal never draws them anyway.
        sub = (fallback[slot * cell_h:(slot + 1) * cell_h]
               if (fallback and cp is not None) else None)
        if sub and any(sub):
            data.extend(sub)
            filled.append((slot, cp))
        else:
            data.extend([0] * cell_h)
            if cp is not None:
                missing.append((slot, cp))

    name = os.path.basename(dst)
    with open(dst, "w") as fh:
        fh.write('"""%dx%d shell font -- CP437 base + CP866 Cyrillic slots.\n\n'
                 "Generated by tools/gen_shell_font.py from %s (X11 misc-fixed,\n"
                 "public domain). Do not edit by hand. Same module layout as\n"
                 "vga2_8x16_cp866.py, so ui.py's _tb() slot mapping applies\n"
                 'unchanged.\n"""\n' % (cell_w, cell_h, os.path.basename(src)))
        fh.write("WIDTH = %d\nHEIGHT = %d\nFIRST = 0x00\nLAST = 0xff\n" % (cell_w, cell_h))
        fh.write("_FONT =\\\n")
        for off in range(0, len(data), cell_h):
            row = bytes(data[off:off + cell_h])
            fh.write("    b'" + "".join("\\x%02x" % b for b in row) + "'\\\n")
        fh.write("\nFONT = memoryview(_FONT)\n")

    print("%s: %dx%d, %d bytes of glyph data (%d bytes/glyph)"
          % (name, cell_w, cell_h, len(data), cell_h))
    if filled:
        print("  %d slots filled from %s: %s"
              % (len(filled), os.path.basename(fallback_path),
                 " ".join("%02x=U+%04X" % f for f in filled[:12])))
    if missing:
        print("  %d slots with no glyph anywhere (left blank): %s"
              % (len(missing), " ".join("%02x=U+%04X" % m for m in missing[:12])))


if __name__ == "__main__":
    main()

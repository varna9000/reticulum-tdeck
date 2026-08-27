# Host-side test for the shell row compositor (ui._ShellFont).
#
# test_ui_shell.py runs without framebuf, so it only ever exercises the 8x16
# fallback. This one shims framebuf faithfully enough to run the REAL
# compositor against the REAL generated fonts, and checks the pixels it emits
# -- including the RGB565 byte order, which is invisible until it reaches the
# panel (blit_buffer ships bytes raw; tft.text() swaps internally).
#
# Run:  python3 tests/test_shell_font.py

import os
import sys
import types
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

_time.ticks_ms = lambda: int(_time.time() * 1000)
_time.ticks_diff = lambda a, b: a - b
_time.sleep_ms = lambda ms: None
sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))


class _Pin:
    IN = OUT = PULL_UP = IRQ_FALLING = IRQ_RISING = 0

    def __init__(self, *a, **k):
        pass

    def irq(self, *a, **k):
        pass

    def value(self, *a):
        return 1


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
sys.modules["machine"] = _machine


# --- framebuf shim ----------------------------------------------------------
# Mirrors MicroPython semantics: RGB565 pixels are stored in native (little-
# endian) order; MONO_HLSB rows are byte-padded (stride rounds up to 8 px).

_fbmod = types.ModuleType("framebuf")
RGB565, MONO_HLSB = 1, 3


class FrameBuffer:
    def __init__(self, buf, w, h, fmt):
        self.buf, self.w, self.h, self.fmt = buf, w, h, fmt

    def _get(self, x, y):
        if self.fmt == RGB565:
            i = (y * self.w + x) * 2
            return self.buf[i] | (self.buf[i + 1] << 8)
        stride = (self.w + 7) // 8
        return (self.buf[y * stride + (x >> 3)] >> (7 - (x & 7))) & 1

    def _set(self, x, y, c):
        if self.fmt == RGB565:
            i = (y * self.w + x) * 2
            self.buf[i] = c & 0xFF
            self.buf[i + 1] = (c >> 8) & 0xFF
            return
        stride = (self.w + 7) // 8
        idx, bit = y * stride + (x >> 3), 0x80 >> (x & 7)
        self.buf[idx] = (self.buf[idx] | bit) if c else (self.buf[idx] & ~bit & 0xFF)

    def fill(self, c):
        for y in range(self.h):
            for x in range(self.w):
                self._set(x, y, c)

    def pixel(self, x, y, c=None):
        if c is None:
            return self._get(x, y)
        self._set(x, y, c)

    def blit(self, src, x0, y0, key=-1, palette=None):
        for yy in range(src.h):
            for xx in range(src.w):
                c = src._get(xx, yy)
                if key >= 0 and c == key:
                    continue
                if palette is not None:
                    c = palette._get(c, 0)
                tx, ty = x0 + xx, y0 + yy
                if 0 <= tx < self.w and 0 <= ty < self.h:
                    self._set(tx, ty, c)


_fbmod.FrameBuffer = FrameBuffer
_fbmod.RGB565 = RGB565
_fbmod.MONO_HLSB = MONO_HLSB
sys.modules["framebuf"] = _fbmod

import ui   # noqa: E402


class _TFT:
    """Captures the last blit_buffer so the test can inspect emitted pixels."""

    def __init__(self):
        self.last = None

    def blit_buffer(self, buf, x, y, w, h):
        self.last = (bytes(buf), x, y, w, h)


_fails = []


def check(name, cond, detail=""):
    if cond:
        print("ok", name)
    else:
        print("FAIL", name, detail)
        _fails.append(name)


def px(buf, w, x, y):
    """Read a pixel back off the wire buffer (big-endian, as the panel sees it)."""
    i = (y * w + x) * 2
    return (buf[i] << 8) | buf[i + 1]


FG, BG = 0xC618, 0x0821


def test_geometry():
    for name, cols, rows, cw, ch in (("spleen_6x12", 53, 16, 6, 12),
                                     ("shell_6x12", 53, 16, 6, 12),
                                     ("shell_6x10", 53, 19, 6, 10),
                                     ("shell_5x8", 64, 24, 5, 8),
                                     ("shell_4x6", 80, 32, 4, 6)):
        f = ui._ShellFont(name, FG, BG)
        check("geometry %s" % name,
              (f.cols, f.rows, f.w, f.h) == (cols, rows, cw, ch),
              "got %r" % ((f.cols, f.rows, f.w, f.h),))


def test_every_ladder_rung_loads():
    """Everything ui offers in the menu must actually import and fit."""
    for name in ui._SHELL_FONTS:
        f = ui._ShellFont(name, FG, BG)
        check("ladder rung %s" % name,
              f.w <= 8 and f.cols * f.w <= ui.SCREEN_W
              and f.rows * f.h <= ui.BODY_ROWS * ui.CHAR_H,
              "%dx%d grid overflows the body" % (f.cols, f.rows))


def test_byte_order_on_the_wire():
    """blit_buffer sends raw bytes; the ST7789 reads them big-endian."""
    f = ui._ShellFont("shell_5x8", FG, BG)
    tft = _TFT()
    f.draw_row(tft, ui.UI._tb(" "), 0)          # a blank row is all background
    buf, _, _, w, _ = tft.last
    check("bg byte order", px(buf, w, 0, 0) == BG,
          "got 0x%04X want 0x%04X" % (px(buf, w, 0, 0), BG))
    check("row buffer size", len(buf) == 320 * 8 * 2, "got %d" % len(buf))


def test_glyph_pixels_match_font():
    """Every set bit of 'A' must land at the right pixel in the right colour."""
    import shell_5x8 as fnt
    f = ui._ShellFont("shell_5x8", FG, BG)
    tft = _TFT()
    f.draw_row(tft, ui.UI._tb("A"), 0)
    buf, _, _, w, _ = tft.last
    glyph = fnt.FONT[ord("A") * fnt.HEIGHT:(ord("A") + 1) * fnt.HEIGHT]
    bad = []
    for y in range(fnt.HEIGHT):
        for x in range(fnt.WIDTH):
            want = FG if glyph[y] & (0x80 >> x) else BG
            got = px(buf, w, x, y)
            if got != want:
                bad.append((x, y, hex(got), hex(want)))
    check("glyph A pixels", not bad, str(bad[:4]))


def test_column_placement_and_spaces():
    """Glyphs advance by exactly WIDTH, and a space leaves background."""
    f = ui._ShellFont("shell_4x6", FG, BG)
    tft = _TFT()
    f.draw_row(tft, ui.UI._tb(" A"), 0)
    buf, _, _, w, _ = tft.last
    import shell_4x6 as fnt
    glyph = fnt.FONT[ord("A") * fnt.HEIGHT:(ord("A") + 1) * fnt.HEIGHT]
    blank = all(px(buf, w, x, y) == BG
                for x in range(fnt.WIDTH) for y in range(fnt.HEIGHT))
    shifted = all(
        px(buf, w, fnt.WIDTH + x, y) == (FG if glyph[y] & (0x80 >> x) else BG)
        for x in range(fnt.WIDTH) for y in range(fnt.HEIGHT))
    check("space cell is background", blank)
    check("second cell starts at WIDTH", shifted)


def test_cyrillic_slot_mapping():
    """_tb() maps Cyrillic into CP866 slots; the shell fonts carry them."""
    import shell_5x8 as fnt
    f = ui._ShellFont("shell_5x8", FG, BG)
    tft = _TFT()
    f.draw_row(tft, ui.UI._tb("Д"), 0)          # Д -> slot 0x84
    buf, _, _, w, _ = tft.last
    glyph = fnt.FONT[0x84 * fnt.HEIGHT:(0x84 + 1) * fnt.HEIGHT]
    check("cyrillic slot non-blank", any(glyph), "slot 0x84 is empty")
    ok = all(px(buf, w, x, y) == (FG if glyph[y] & (0x80 >> x) else BG)
             for x in range(fnt.WIDTH) for y in range(fnt.HEIGHT))
    check("cyrillic renders via _tb", ok)


def test_full_width_row_does_not_overflow():
    f = ui._ShellFont("shell_4x6", FG, BG)
    tft = _TFT()
    f.draw_row(tft, ui.UI._tb("W" * f.cols), 0)
    buf, _, _, w, h = tft.last
    check("full row fits exactly", (w, h) == (320, 6) and len(buf) == 320 * 6 * 2,
          "got w=%d h=%d len=%d" % (w, h, len(buf)))


"""Integration: the real draw_shell() driving the real compositor."""


class _FullTFT:
    """Records every blit_buffer / text call draw_shell makes."""

    def __init__(self):
        self.blits = []
        self.texts = []

    def blit_buffer(self, buf, x, y, w, h):
        self.blits.append((x, y, w, h))

    def text(self, font, s, x, y, fg=0, bg=0):
        self.texts.append((s, x, y))

    def fill_rect(self, *a):
        pass

    def fill(self, *a):
        pass


def _mkui():
    g = ui.UI(_FullTFT(), object(), lambda: b"\x00", node_name="t")
    g._screen_on = True
    g.connects = []
    g.resizes = []
    g.on_shell_connect = lambda d, c, r: g.connects.append((d, c, r))
    g.on_shell_input = lambda d: None
    g.on_shell_disconnect = lambda: None
    g.on_shell_resize = lambda r, c: g.resizes.append((r, c))
    return g


def test_draw_shell_uses_compositor():
    g = _mkui()
    g._start_shell(b"\xaa" * 16)
    sf = g._shell_font
    check("default font is _SHELL_FONTS[0]", sf.name == ui._SHELL_FONTS[0], sf.name)
    check("connect got the live grid", g.connects[-1][1:] == (sf.cols, sf.rows),
          str(g.connects))
    check("terminal wrapped at font cols", g._terminal.cols == sf.cols)
    g.shell_feed(1, ("\r\n".join("row %d" % i for i in range(40)) + "\r\n").encode())
    g.tft.blits = []
    g.tft.texts = []
    g.draw_shell()
    check("composited every visible row", len(g.tft.blits) == sf.rows,
          "%d blits for %d rows" % (len(g.tft.blits), sf.rows))
    check("rows are full width",
          all(b[2] == 320 and b[3] == sf.h for b in g.tft.blits),
          str(g.tft.blits[:3]))
    check("no 8x16 body text", not any(t[2] < ui.INPUT_Y for t in g.tft.texts),
          str(g.tft.texts[:3]))
    # second draw with nothing new must repaint nothing (row cache holds)
    g.tft.blits = []
    g.draw_shell()
    check("clean redraw is a no-op", g.tft.blits == [], str(g.tft.blits))


def test_font_toggle_resizes_and_notifies():
    g = _mkui()
    g._start_shell(b"\xaa" * 16)
    g._shell_menu = True
    g._shell_menu_idx = [i for i, it in enumerate(ui._SHELL_CTRL_ITEMS)
                         if it[1] == "font"][0]
    g._shell_menu_exec()
    nxt = g._shell_font
    check("toggled to the next rung", nxt.name == ui._SHELL_FONTS[1], nxt.name)
    check("menu stays open on font change", g._shell_menu is True)
    check("pty told the new size", g.resizes[-1] == (nxt.rows, nxt.cols),
          str(g.resizes))
    check("terminal rewrapped", g._terminal.cols == nxt.cols)
    check("cache resized", len(g._shell_cache) == nxt.rows, str(len(g._shell_cache)))
    # cycling all the way round must come back to the default
    for _ in range(len(ui._SHELL_FONTS) - 1):
        g._shell_menu_exec()          # menu stayed open; just click again
    check("cycle wraps to default", g._shell_font.name == ui._SHELL_FONTS[0],
          g._shell_font.name)


def test_menu_label_shows_live_grid():
    """The font row must read out the grid, and update the moment it changes."""
    g = _mkui()
    g._start_shell(b"\xaa" * 16)
    idx = [i for i, it in enumerate(ui._SHELL_CTRL_ITEMS) if it[1] == "font"][0]
    sf = g._shell_font
    label = g._shell_menu_label(idx)
    check("label carries current grid", "%dx%d" % (sf.cols, sf.rows) in label, label)
    check("other labels untouched",
          g._shell_menu_label(0) == ui._SHELL_CTRL_ITEMS[0][0],
          g._shell_menu_label(0))
    g._shell_menu = True
    g._shell_menu_idx = idx
    g._shell_menu_exec()
    nxt = g._shell_font
    after = g._shell_menu_label(idx)
    check("label updates immediately", "%dx%d" % (nxt.cols, nxt.rows) in after
          and after != label, "%r -> %r" % (label, after))
    # the menu repaints from a cleared cache, so the new label actually lands
    check("menu cache invalidated", g._cache == [''] * 15)


def test_scroll_math_follows_font_rows():
    g = _mkui()
    g._start_shell(b"\xaa" * 16)
    g.shell_feed(1, ("\r\n".join("l%d" % i for i in range(100)) + "\r\n").encode())
    rows = g._shell_font.rows
    check("_shell_rows is font rows", g._shell_rows() == rows)
    g._scroll_up()
    check("scroll up moves one line", g._shell_view == 1)
    g._shell_view = 0
    g._shell_scroll(10 ** 6)                 # clamp to available scrollback
    mx = max(0, len(g._terminal.lines) - rows)
    check("scroll clamps to scrollback", g._shell_view == mx,
          "%d != %d" % (g._shell_view, mx))


def test_leave_shell_releases_font():
    g = _mkui()
    g._start_shell(b"\xaa" * 16)
    g._leave_shell()
    check("font released", g._shell_font is None)
    check("cache released", g._shell_cache == [])


for fn in (test_geometry, test_every_ladder_rung_loads, test_byte_order_on_the_wire, test_glyph_pixels_match_font,
           test_column_placement_and_spaces, test_cyrillic_slot_mapping,
           test_full_width_row_does_not_overflow,
           test_draw_shell_uses_compositor, test_font_toggle_resizes_and_notifies,
           test_menu_label_shows_live_grid,
           test_scroll_math_follows_font_rows, test_leave_shell_releases_font):
    fn()

if _fails:
    print("\n%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\nall shell-font tests passed")

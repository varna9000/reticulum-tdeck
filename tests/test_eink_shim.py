# Host-side tests for the T-Deck Pro e-ink shim. Run:
#   python3 tests/test_eink_shim.py
#
# The shim stands in for the ST7789 object ui.py draws through, so these tests
# pin down the three things that decide whether the Pro's screen is readable:
# ink polarity (the app is dark-themed, paper is not), glyph rendering against
# the real font module, and the refresh batching that keeps a 700 ms panel
# usable.

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

# --- MicroPython shims (before importing the shim) -------------------------

_mp = types.ModuleType("micropython")
_mp.const = lambda x: x
_mp.viper = lambda f: f
sys.modules.setdefault("micropython", _mp)


class _FrameBuffer:
    """Minimal MONO_HLSB framebuf.FrameBuffer, enough for the shim."""

    def __init__(self, buf, width, height, fmt):
        self.buf = buf
        self.width = width
        self.height = height
        self.stride = width // 8

    def pixel(self, x, y, c=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return 0
        i = y * self.stride + (x >> 3)
        bit = 7 - (x & 7)
        if c is None:
            return (self.buf[i] >> bit) & 1
        if c:
            self.buf[i] |= 1 << bit
        else:
            self.buf[i] &= ~(1 << bit) & 0xFF

    def fill(self, c):
        v = 0xFF if c else 0x00
        for i in range(len(self.buf)):
            self.buf[i] = v

    def fill_rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.pixel(xx, yy, c)

    def blit(self, src, x, y, key=-1):
        for yy in range(src.height):
            for xx in range(src.width):
                v = src.pixel(xx, yy)
                if key >= 0 and v == key:
                    continue
                self.pixel(x + xx, y + yy, v)


_fbmod = types.ModuleType("framebuf")
_fbmod.FrameBuffer = _FrameBuffer
_fbmod.MONO_HLSB = 3
sys.modules.setdefault("framebuf", _fbmod)

import eink_shim  # noqa: E402
import spleen_8x16 as font  # noqa: E402

DARK = 0x0821    # the app's background
WHITE = 0xFFFF
BLACK = 0x0000

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakePanel:
    """Records panel traffic instead of driving hardware."""

    WIDTH = 240
    HEIGHT = 320

    def __init__(self):
        self.buf = bytearray(self.WIDTH // 8 * self.HEIGHT)
        self.fb = _FrameBuffer(self.buf, self.WIDTH, self.HEIGHT, 3)
        self.full = 0
        self.rects = []

    def init(self):
        pass

    def flush(self):
        self.full += 1

    def flush_rect(self, x, y, w, h):
        self.rects.append((x, y, w, h))

    def power_off(self):
        pass

    def hibernate(self):
        pass


def new():
    p = FakePanel()
    return p, eink_shim.EinkShim(p)


# --- polarity --------------------------------------------------------------

def test_polarity():
    print("polarity")
    p, s = new()
    s.fill(DARK)
    check("dark background becomes paper, not ink",
          all(b == 0x00 for b in p.buf),
          "got %r" % p.buf[:4])

    p, s = new()
    s.fill(WHITE)
    check("white fill becomes ink",
          all(b == 0xFF for b in p.buf))


# --- glyphs ----------------------------------------------------------------

def test_text_glyph():
    print("text glyphs")
    p, s = new()
    s.fill(DARK)                      # paper
    s.text(font, "A", 0, 0, WHITE, DARK)   # light text on dark == ink on paper

    ch = ord("A")
    idx = (ch - font.FIRST) * font.HEIGHT
    expected = [font.FONT[idx + r] for r in range(font.HEIGHT)]
    got = [p.buf[r * 30] for r in range(font.HEIGHT)]
    check("glyph 'A' matches the font bitmap", got == expected,
          "\n    expected %s\n    got      %s" % (expected, got))

    nonblank = sum(1 for b in got if b)
    check("glyph actually drew something", nonblank > 0)


def test_text_offset_x():
    print("text at unaligned x")
    p, s = new()
    s.fill(DARK)
    s.text(font, "A", 3, 0, WHITE, DARK)
    ch = ord("A")
    idx = (ch - font.FIRST) * font.HEIGHT
    row = 8
    want = font.FONT[idx + row]
    # 8 pixels starting at x=3 straddle two bytes.
    got = ((p.buf[row * 30] << 8) | p.buf[row * 30 + 1]) >> (16 - 8 - 3) & 0xFF
    check("unaligned glyph lands on the right pixels", got == want,
          "expected %02x got %02x" % (want, got))


def test_text_inverted():
    print("inverted text")
    p, s = new()
    s.fill(WHITE)                     # ink page
    s.text(font, "A", 0, 0, DARK, WHITE)   # dark text on light == paper on ink
    ch = ord("A")
    idx = (ch - font.FIRST) * font.HEIGHT
    expected = [font.FONT[idx + r] ^ 0xFF for r in range(font.HEIGHT)]
    got = [p.buf[r * 30] for r in range(font.HEIGHT)]
    check("reverse-video glyph is the inverse", got == expected,
          "\n    expected %s\n    got      %s" % (expected, got))


# --- refresh batching ------------------------------------------------------

def test_text_on_an_ink_background_is_paper():
    """Bright text on a bright band (a selected row, the unread pill) must
    come out as paper glyphs on ink, not ink on ink."""
    print("inverted rows")
    p, s = new()
    s.fill(DARK)
    s.text(font, "A", 0, 0, WHITE, WHITE)          # bright on bright
    cell = [[s.fb.pixel(x, y) for x in range(8)] for y in range(16)]
    inked = sum(sum(r) for r in cell)
    check("the cell is mostly ink (the band)", inked > 64, str(inked))
    check("and the glyph is paper inside it", inked < 128, str(inked))
    s.text(font, "A", 8, 0, WHITE, DARK)           # the normal case
    cell2 = [[s.fb.pixel(8 + x, y) for x in range(8)] for y in range(16)]
    check("ink on paper is unchanged", 0 < sum(sum(r) for r in cell2) < 64)
    check("the two are each other's inverse",
          all(cell[y][x] != cell2[y][x] for y in range(16) for x in range(8)))


def test_no_flush_while_drawing():
    print("batching")
    p, s = new()
    for i in range(20):
        s.text(font, "hello", 0, i * 16, WHITE, DARK)
    check("drawing never touches the panel", p.full == 0 and not p.rects,
          "full=%d rects=%d" % (p.full, len(p.rects)))


def test_small_change_is_partial():
    p, s = new()
    s.fill(DARK)
    s.flush()                      # initial full
    p.full = 0
    p.rects = []
    s.text(font, "hi", 0, 0, WHITE, DARK)
    s.flush()
    check("a small change uses a partial refresh",
          p.full == 0 and len(p.rects) == 1,
          "full=%d rects=%r" % (p.full, p.rects))


def test_large_change_is_full():
    p, s = new()
    s.flush()
    p.full = 0
    s.fill(DARK)                   # whole panel dirty
    s.flush(force_full=True)       # what ui.py does on a screen change
    check("a screen change is a full refresh", p.full == 1,
          "full=%d rects=%r" % (p.full, p.rects))
    p.full = 0
    p.rects = []
    s.fill(WHITE)                  # whole panel again, but no debt and not forced
    s.flush(user=True)
    check("a large user redraw without debt is a quiet partial (a line scroll shifts every row)",
          p.full == 0 and len(p.rects) == 1, "full=%d rects=%r" % (p.full, p.rects))


def test_background_change_never_flashes():
    """An announce changes the tab count at the top of the node list and
    appends a row near the bottom. The bounding box of the two spans most
    of the panel, which used to buy a full refresh -- a black/white flash
    over a screen the user was reading, for two small changes. A redraw
    nobody asked for gets one quiet partial of the changed rows, however
    far apart they are; the flash is saved for a redraw the user caused."""
    print("background redraws")
    p, s = new()
    s.fill(DARK)
    s.flush()
    p.full = 0
    p.rects = []
    s.text(font, "MSG7", 0, 20, WHITE, DARK)
    s.text(font, "newbie", 0, 280, WHITE, DARK)
    s.flush()
    check("two far-apart changes from the background are one partial",
          p.full == 0 and len(p.rects) == 1,
          "full=%d rects=%r" % (p.full, p.rects))
    check("the partial covers both changes",
          p.rects and p.rects[0][1] <= 24 and p.rects[0][1] + p.rects[0][3] >= 288,
          "got %r" % (p.rects,))

    s.fill(WHITE)                   # even a whole-panel background change
    p.rects = []
    s.flush()
    check("a whole-panel background change is still a partial",
          p.full == 0 and len(p.rects) == 1,
          "full=%d rects=%r" % (p.full, p.rects))


def test_owed_full_refresh_waits_for_the_user():
    """The ghosting budget still exists, but spending it must not flash a
    screen nobody touched: once it is used up, background redraws keep
    going as partials and the full refresh happens on the next redraw the
    user causes, which is when they are changing the screen anyway."""
    p, s = new()
    s.fill(DARK)
    s.flush()
    p.full = 0
    p.rects = []
    for i in range(eink_shim._FAST_REFRESH_LIMIT + 3):
        s.text(font, chr(ord("a") + i), 0, 0, WHITE, DARK)
        s.flush()
    check("an exhausted budget does not flash from the background",
          p.full == 0, "full=%d partials=%d" % (p.full, len(p.rects)))
    n = len(p.rects)
    s.text(font, "k", 0, 0, WHITE, DARK)
    s.flush(user=True)
    check("a cursor-sized user redraw does not pay the debt (no flash per keypress)",
          p.full == 0 and len(p.rects) == n + 1,
          "full=%d partials=%d" % (p.full, len(p.rects)))
    s.fill_rect(0, 40, 240, 120, WHITE)           # a page's worth changes
    s.flush(user=True)
    check("the owed full refresh lands on the next sizeable user redraw",
          p.full == 1 and len(p.rects) == n + 1,
          "full=%d partials=%d" % (p.full, len(p.rects)))
    p.full = 0
    s.fill_rect(0, 40, 240, 120, DARK)
    s.flush(user=True)
    check("and is owed only once", p.full == 0 and len(p.rects) == n + 2,
          "full=%d partials=%d" % (p.full, len(p.rects)))


def test_unattended_panel_still_clears_ghosting():
    """A deck left on the desk ticks its clock once a minute and may never
    see a key press. Ghosting cannot be left to accumulate forever, so a
    much larger hard cap forces the full refresh even from the background."""
    p, s = new()
    s.fill(DARK)
    s.flush()
    p.full = 0
    for i in range(eink_shim._FAST_REFRESH_HARD_LIMIT + 1):
        s.text(font, chr(ord("a") + i % 26), 0, 0, WHITE, DARK)
        s.flush()
    check("the hard cap forces one full refresh unattended", p.full == 1,
          "full=%d" % p.full)


def test_fast_refresh_budget():
    p, s = new()
    s.fill(DARK)
    s.flush()                      # establishes the baseline
    p.full = 0
    p.rects = []
    # Each pass has to change something, or the shim rightly skips the
    # refresh and the budget never advances -- it counts refreshes the panel
    # actually performed, which is what ghosting accumulates from.
    for i in range(12):
        s.text(font, chr(ord("a") + i), 0, 0, WHITE, DARK)
        s.flush(user=True)
    s.fill_rect(0, 40, 240, 120, WHITE)           # then a page change
    s.flush(user=True)
    check("ghosting budget forces a full refresh on the next page-sized redraw",
          p.full == 1,
          "full=%d partials=%d" % (p.full, len(p.rects)))


def test_dirty_rect_union():
    p, s = new()
    s.fill(DARK)
    s.flush()                      # establishes the baseline
    p.rects = []
    p.full = 0
    s.fill_rect(10, 10, 8, 8, WHITE)
    s.fill_rect(50, 60, 8, 8, WHITE)
    s.flush()
    check("dirty rectangles are unioned into one refresh",
          len(p.rects) == 1 and p.rects[0] == (10, 10, 48, 58),
          "got %r" % (p.rects,))


def test_redraw_of_identical_content_is_skipped():
    """ui.py repaints the navbar, the body frame and the scrollbar lane on
    every pass. On a TFT that is free. Here each one would mark the screen
    dirty and buy a refresh of a screen that looks exactly the same, so the
    shim compares against what the panel is actually showing."""
    p, s = new()
    s.fill(DARK)
    s.text(font, "hello", 0, 0, WHITE, DARK)
    s.flush()
    p.full = 0
    p.rects = []

    # Same content, drawn again: dirty, but nothing moved.
    s.text(font, "hello", 0, 0, WHITE, DARK)
    s.flush()
    check("redrawing identical content touches nothing",
          p.full == 0 and not p.rects,
          "full=%d rects=%r" % (p.full, p.rects))

    # And a real change still gets through.
    s.text(font, "world", 0, 0, WHITE, DARK)
    s.flush()
    check("a real change still refreshes", p.full or p.rects,
          "full=%d rects=%r" % (p.full, p.rects))


def test_refresh_narrows_to_the_rows_that_moved():
    """The dirty rectangle records where drawing happened; the rows compared
    against the panel record where pixels changed. Refreshing the second is
    the whole point -- a full-width erase followed by one changed line should
    not cost a full-panel refresh."""
    p, s = new()
    s.fill(DARK)
    s.flush()
    p.full = 0
    p.rects = []

    # Erase a tall band, then put the same blank content back except one row.
    s.fill_rect(0, 0, 240, 160, DARK)
    s.text(font, "x", 0, 96, WHITE, DARK)
    s.flush()
    check("a mostly unchanged band refreshes only the changed rows",
          len(p.rects) == 1 and p.rects[0][1] >= 96
          and p.rects[0][1] + p.rects[0][3] <= 112,
          "got %r (full=%d)" % (p.rects, p.full))


def _rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def _ink_fraction(p, s, x, y, w, h):
    n = 0
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            n += s.fb.pixel(xx, yy)
    return n / float(w * h)


def _flat(w, h, color):
    return bytes([color >> 8, color & 0xFF]) * (w * h)


def test_dither_keeps_the_tone_and_the_right_polarity():
    """blit_dither is for photographs: dark is ink (not the UI's inverted
    rule), white is paper, and a mid grey comes out about half ink spread
    through the area rather than all one or all the other."""
    print("dithering")
    p, s = new()
    s.fill(DARK)
    s.blit_dither(_flat(32, 16, _rgb565(255, 255, 255)), 8, 8, 32, 16)
    check("white is paper", _ink_fraction(p, s, 8, 8, 32, 16) == 0.0)
    s.blit_dither(_flat(32, 16, _rgb565(0, 0, 0)), 8, 8, 32, 16)
    check("black is ink", _ink_fraction(p, s, 8, 8, 32, 16) == 1.0)
    s.blit_dither(_flat(32, 16, _rgb565(128, 128, 128)), 8, 8, 32, 16)
    f = _ink_fraction(p, s, 8, 8, 32, 16)
    check("mid grey is about half ink", 0.4 <= f <= 0.6, "ink fraction %.2f" % f)
    row = [s.fb.pixel(xx, 12) for xx in range(8, 40)]
    check("and mixed within a row, not banded", 0 in row and 1 in row, str(row))
    s.blit_dither(_flat(32, 16, _rgb565(64, 64, 64)), 8, 8, 32, 16)
    f = _ink_fraction(p, s, 8, 8, 32, 16)
    check("a dark grey is mostly ink", 0.65 <= f <= 0.85, "ink fraction %.2f" % f)
    check("pixels outside the image are untouched",
          s.fb.pixel(7, 8) == 0 and s.fb.pixel(40, 8) == 0 and s.fb.pixel(8, 24) == 0)
    s.flush()
    check("the dithered area reaches the panel", p.full == 1)


def test_blit_buffer_thresholds_big_endian_rgb565():
    """The shell compositor stores big-endian RGB565 (pre-swapped for the
    ST7789); the blit must read it that way, bright as ink, and clip."""
    p, s = new()
    s.fill(DARK)
    row = bytes([0xC6, 0x18] * 4 + [0x08, 0x21] * 4)     # BODY_FG x4, BG_DARK x4
    s.blit_buffer(row, 0, 0, 8, 1)
    check("bright pixels are ink", all(s.fb.pixel(x, 0) == 1 for x in range(4)))
    check("dark pixels are paper", all(s.fb.pixel(x, 0) == 0 for x in range(4, 8)))
    s.blit_buffer(bytes([0xFF, 0xFF] * 16), 232, 0, 16, 1)
    check("a blit over the edge is clipped", s.fb.pixel(239, 0) == 1 and s.fb.pixel(0, 1) == 0)


def test_dither_clips_to_the_panel():
    p, s = new()
    s.fill(DARK)
    s.blit_dither(_flat(16, 16, _rgb565(0, 0, 0)), 232, 312, 16, 16)
    check("an image over the edge is clipped, not wrapped",
          s.fb.pixel(239, 319) == 1 and s.fb.pixel(0, 0) == 0)


def test_first_flush_is_full():
    """Nothing has been pushed yet, so what the panel holds is unknown and
    there is nothing to diff against."""
    p, s = new()
    s.text(font, "hi", 0, 0, WHITE, DARK)
    s.flush()
    check("the first flush refreshes the whole panel", p.full == 1,
          "full=%d rects=%r" % (p.full, p.rects))


def test_flush_noop_when_clean():
    p, s = new()
    s.flush()
    p.full = 0
    p.rects = []
    s.flush()
    check("flushing with nothing pending does nothing",
          p.full == 0 and not p.rects)


if __name__ == "__main__":
    test_polarity()
    test_text_glyph()
    test_text_offset_x()
    test_text_inverted()
    test_text_on_an_ink_background_is_paper()
    test_no_flush_while_drawing()
    test_small_change_is_partial()
    test_large_change_is_full()
    test_background_change_never_flashes()
    test_owed_full_refresh_waits_for_the_user()
    test_unattended_panel_still_clears_ghosting()
    test_fast_refresh_budget()
    test_dirty_rect_union()
    test_redraw_of_identical_content_is_skipped()
    test_refresh_narrows_to_the_rows_that_moved()
    test_dither_keeps_the_tone_and_the_right_polarity()
    test_blit_buffer_thresholds_big_endian_rgb565()
    test_dither_clips_to_the_panel()
    test_first_flush_is_full()
    test_flush_noop_when_clean()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

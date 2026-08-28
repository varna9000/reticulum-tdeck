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
    s.flush()
    check("a full-screen change uses a full refresh", p.full == 1,
          "full=%d rects=%r" % (p.full, p.rects))


def test_fast_refresh_budget():
    p, s = new()
    s.flush()
    p.full = 0
    p.rects = []
    for i in range(12):
        s.text(font, "x", 0, 0, WHITE, DARK)
        s.flush()
    check("ghosting budget forces a full refresh within 11 partials",
          p.full >= 1,
          "full=%d partials=%d" % (p.full, len(p.rects)))


def test_dirty_rect_union():
    p, s = new()
    s.flush()
    p.rects = []
    p.full = 0
    s.fill_rect(10, 10, 8, 8, WHITE)
    s.fill_rect(50, 60, 8, 8, WHITE)
    s.flush()
    check("dirty rectangles are unioned into one refresh",
          len(p.rects) == 1 and p.rects[0] == (10, 10, 48, 58),
          "got %r" % (p.rects,))


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
    test_no_flush_while_drawing()
    test_small_change_is_partial()
    test_large_change_is_full()
    test_fast_refresh_budget()
    test_dirty_rect_union()
    test_flush_noop_when_clean()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

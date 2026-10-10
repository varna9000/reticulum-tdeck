# The full-screen image viewer. The native decoders stretch to the exact size
# they are given, and the viewer used to hand them the screen size: fine for a
# 4:3 photo on the v1's 320x240, a squeezed portrait on the Pro's 240x320.
# Now the viewer fits the image to the panel preserving aspect (never
# enlarging), rotates it 90 degrees when the image's orientation differs from
# the panel's and the turn gains size (the user turns the deck), lets `r`
# toggle that, and says so on a bar along the very bottom line.
#
# Run:  python3 tests/test_image_viewer.py

import os
import sys
import types
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

import ui  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


print("layout: fit, never enlarge, rotate when the turn gains size")
L = ui._viewer_layout
check("landscape photo on the portrait Pro: rotated, fills 320x240",
      L((640, 480), 240, 320, None) == (320, 240, True), L((640, 480), 240, 320, None))
check("portrait photo on the Pro: upright, fills 240x320",
      L((480, 640), 240, 320, None) == (240, 320, False), L((480, 640), 240, 320, None))
check("portrait photo on the landscape v1: rotated, fills 240x320",
      L((480, 640), 320, 240, None) == (240, 320, True), L((480, 640), 320, 240, None))
check("landscape photo on the v1: upright, 320x240",
      L((640, 480), 320, 240, None) == (320, 240, False), L((640, 480), 320, 240, None))
check("3:2 photo on the v1 keeps its aspect (not stretched to 4:3)",
      L((600, 400), 320, 240, None) == (320, 213, False), L((600, 400), 320, 240, None))
check("small image is never enlarged, and not rotated for nothing",
      L((100, 50), 240, 320, None) == (100, 50, False), L((100, 50), 240, 320, None))
check("square image: upright", L((500, 500), 240, 320, None) == (240, 240, False), L((500, 500), 240, 320, None))
check("forced upright", L((640, 480), 240, 320, False) == (240, 180, False), L((640, 480), 240, 320, False))
check("forced rotated", L((480, 640), 240, 320, True) == (180, 240, True), L((480, 640), 240, 320, True))
check("unknown native size: the panel, upright", L(None, 240, 320, None) == (240, 320, False), L(None, 240, 320, None))

print("rotate90: clockwise, 16-bit pixels copied intact")
# 3 wide x 2 tall, pixel values 1..6 as little-endian uint16
src = bytearray()
for v in (1, 2, 3, 4, 5, 6):
    src += bytes((v & 0xFF, v >> 8))
dst = ui._rotate90(src, 3, 2)
vals = [dst[i] | (dst[i + 1] << 8) for i in range(0, len(dst), 2)]
check("output is 2 wide x 3 tall", len(dst) == 12, len(dst))
check("top row of the image lands on the right edge", vals == [4, 1, 5, 2, 6, 3], vals)


class FakeTFT:
    mono = False

    def __init__(self):
        self.calls = []

    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("latin-1")
        self.calls.append(("text", s, x, y, fg, bg))

    def fill_rect(self, x, y, w, h, c):
        self.calls.append(("fill_rect", x, y, w, h, c))

    def fill(self, c):
        self.calls.append(("fill", c))

    def blit_buffer(self, buf, x, y, w, h):
        self.calls.append(("blit_buffer", len(buf), x, y, w, h))

    def blit_dither(self, buf, x, y, w, h):
        self.calls.append(("blit_dither", len(buf), x, y, w, h))


class FakeDecoder:
    def __init__(self):
        self.asked = []

    def decode(self, data, tw, th):
        self.asked.append((tw, th))
        return tw, th, bytearray(tw * th * 2)


def mkui(mono, native):
    FakeTFT.mono = mono
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    g._viewing_image = b"\xff\xd8" + b"\x00" * 64       # looks like a JPEG
    g._image_drawn = False
    g._image_rotate = None
    g.state = ui.STATE_IMAGE
    dec = FakeDecoder()
    sys.modules["tjpgd_fast_xtensawin"] = dec
    ui._img_native_size = lambda d: native
    return g, dec


print("viewer on the host's 320x240 geometry: the bottom line is the bar, the picture gets 320x224")
g, dec = mkui(mono=False, native=(480, 640))            # portrait photo on a landscape panel
g.draw_image(lambda: None, lambda: None)
check("decoded to the rotated fit (224 wide x 298 tall before the turn)", dec.asked == [(224, 298)], dec.asked)
blits = [c for c in g.tft.calls if c[0] == "blit_buffer"]
check("blitted rotated: 298 wide x 224 tall, centred above the bar",
      blits and blits[0][4:6] == (298, 224) and blits[0][2:4] == (11, 0), blits)
bars = [c for c in g.tft.calls if c[0] == "fill_rect" and c[2] == ui.SCREEN_H - ui.CHAR_H and c[4] == ui.CHAR_H]
check("hint bar is the very bottom line, full width, black on a colour panel",
      bars and bars[-1][1] == 0 and bars[-1][3] == ui.SCREEN_W and bars[-1][5] == 0x0000, bars)
hints = [c for c in g.tft.calls if c[0] == "text" and c[3] == ui.SCREEN_H - ui.CHAR_H]
check("hint names r for rotate in white on black",
      hints and "r" in hints[-1][1] and "rotate" in hints[-1][1] and hints[-1][4] == 0xFFFF and hints[-1][5] == 0x0000, hints)
check("hint still says how to get back", hints and "back" in hints[-1][1], hints)

g, dec = mkui(mono=True, native=(480, 640))
g.draw_image(lambda: None, lambda: None)
dith = [c for c in g.tft.calls if c[0] == "blit_dither"]
check("mono panel dithers the rotated image", dith and dith[0][4:6] == (298, 224), dith)
bars = [c for c in g.tft.calls if c[0] == "fill_rect" and c[2] == ui.SCREEN_H - ui.CHAR_H]
check("mono: the bar is filled bright, which the shim renders as ink", bars and bars[-1][5] == 0xFFFF, bars)
hints = [c for c in g.tft.calls if c[0] == "text" and c[3] == ui.SCREEN_H - ui.CHAR_H]
check("mono: hint text drawn on the ink background (paper glyphs)", hints and hints[-1][5] == 0xFFFF, hints)

print("r toggles the rotation and redraws; any other key leaves")
g, dec = mkui(mono=False, native=(480, 640))
g.draw_image(lambda: None, lambda: None)
g.handle_key(b"r")
check("r keeps the viewer open and marks it for redraw", g.state == ui.STATE_IMAGE and not g._image_drawn)
g.draw_image(lambda: None, lambda: None)
check("second draw is upright", dec.asked[-1] == (168, 224), dec.asked)
g.handle_key(b"r")
g.draw_image(lambda: None, lambda: None)
check("r again rotates back", dec.asked[-1] == (224, 298), dec.asked)
g.handle_key(b" ")
check("another key leaves the viewer", g.state != ui.STATE_IMAGE)

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")

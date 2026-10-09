# ST7789-compatible surface over a mono e-paper panel.
#
# ui.py touches the display through exactly five methods -- text, fill_rect,
# fill, blit_buffer and write -- so the whole T-Deck UI can be driven on the
# T-Deck Pro's e-ink by re-implementing those five over a 1-bit framebuffer,
# with no changes to the drawing code itself.
#
# Two things differ from a TFT and both are handled here rather than in ui.py:
#
# 1. Cost. A partial refresh on this panel is ~700 ms and a full one ~1100 ms.
#    Drawing must therefore never touch the panel. Every call below writes to
#    the framebuffer and records a dirty rectangle; the panel is updated only
#    when the app calls flush(). One flush per rendered screen, not per draw.
#
# 2. Polarity. The app is dark-themed: it paints a near-black background
#    (0x0821) and light text. Rendering that literally on paper would flood the
#    panel with ink, which is slow, ghosts badly and looks wrong. So luminance
#    is inverted: bright source colours become ink, dark ones become paper.

import framebuf
import micropython
from micropython import const

try:
    ptr8                      # viper type names, resolved by the compiler
except NameError:
    # Host tests: a bytearray and an array('i') index the way ptr8 and
    # ptr32 over them do, so the casts can be identities there. Harmless on
    # the device: the viper compiler recognises the names before any lookup.
    ptr8 = ptr32 = lambda x: x

_INK = const(1)      # set bit in the framebuffer means black
_PAPER = const(0)

# A bright RGB565 colour becomes ink. Threshold sits below mid-grey so the
# app's various greys land on paper rather than smearing the page.
_LUM_THRESHOLD = const(60)

# Consecutive partial refreshes before a full one is owed to clear ghosting.
# Matches the limit Meshtastic uses for this panel. Owed, not forced: the
# full refresh is a black/white flash of the whole panel, so flush() pays
# the debt on the next redraw the user caused (they are changing the screen
# anyway) and never from a background redraw -- an announce landing in the
# node list, the navbar clock ticking -- which would flash a screen someone
# is reading. The hard limit is the backstop for a deck nobody touches: the
# clock alone is a partial a minute, and ghosting cannot run forever.
_FAST_REFRESH_LIMIT = const(10)
_FAST_REFRESH_HARD_LIMIT = const(40)

# When the ghosting debt is owed, a user-caused redraw pays it only if it is
# already a sizeable one -- a page or screen change, not a cursor step or a
# one-line scroll. A cursor step changes two rows (7680 px); this is a bit
# more. The area alone never buys a full refresh: a partial update costs the
# same ~700 ms whatever its window, the panel drives only the pixels that
# differ from its previous frame, and a line scroll that shifts the whole
# body is still a quiet partial rather than a flash on every keypress. The
# flash that clears ghosting lands on a screen change (ui.py flushes those
# with force_full) or on the owed-and-sizeable rule, and the hard cap is the
# backstop.
_OWED_MIN_PIXELS = const(9600)        # 1/8 of the 240x320 panel


def _lum(color):
    """Rough luminance of an RGB565 value, 0..187."""
    r = (color >> 11) & 0x1F
    g = (color >> 5) & 0x3F
    b = color & 0x1F
    return (r << 1) + g + (b << 1)


class EinkShim:
    """Drop-in replacement for the ST7789 object ui.py expects."""

    # 1-bit panel: there is ink or there is paper, and nothing between. ui.py
    # reads this to avoid drawing "off" states in a dim colour, which on this
    # threshold comes out identical to the lit one.
    mono = True

    def __init__(self, panel):
        self._p = panel
        self.fb = panel.fb
        self._buf = panel.buf
        self.width = panel.WIDTH
        self.height = panel.HEIGHT

        # Dirty rectangle, in panel coordinates. None means nothing pending.
        self._dx0 = self._dy0 = self._dx1 = self._dy1 = 0
        self._dirty = False
        self._partials = 0
        self._full_owed = False

        # Copy of the framebuffer as the panel last saw it. The dirty rectangle
        # records where drawing happened, not where pixels changed, and ui.py
        # repaints several things unconditionally every pass -- the navbar, the
        # body frame, the scrollbar lane. On a TFT that is a few hundred
        # microseconds; here it would turn every redraw into a ~1.3 s refresh
        # of a screen that looks exactly the same. Comparing 9600 bytes to find
        # out costs a few milliseconds, so it is always worth doing.
        self._stride = panel.WIDTH // 8
        self._shown = bytearray(len(panel.buf))
        self._shown_valid = False

        # Reusable 8x16 glyph buffer, so text() allocates nothing per character.
        self._gbuf = bytearray(16)
        self._gfb = framebuf.FrameBuffer(self._gbuf, 8, 16, framebuf.MONO_HLSB)

    # --- dirty tracking ----------------------------------------------------

    def _mark(self, x, y, w, h):
        if w <= 0 or h <= 0:
            return
        x1 = x + w
        y1 = y + h
        if not self._dirty:
            self._dx0, self._dy0, self._dx1, self._dy1 = x, y, x1, y1
            self._dirty = True
            return
        if x < self._dx0:
            self._dx0 = x
        if y < self._dy0:
            self._dy0 = y
        if x1 > self._dx1:
            self._dx1 = x1
        if y1 > self._dy1:
            self._dy1 = y1

    # --- the five methods ui.py uses ---------------------------------------

    def fill(self, color):
        self.fb.fill(_INK if _lum(color) > _LUM_THRESHOLD else _PAPER)
        self._mark(0, 0, self.width, self.height)

    def fill_rect(self, x, y, width, height, color):
        c = _INK if _lum(color) > _LUM_THRESHOLD else _PAPER
        self.fb.fill_rect(x, y, width, height, c)
        self._mark(x, y, width, height)

    def text(self, font, text, x0, y0, color=0xFFFF, background=0x0000):
        """Bitmap font rendering, same glyph layout as st7789py._text8.

        Glyphs are (ch - FIRST) * HEIGHT bytes into font.FONT, one byte per
        row, bit 7 leftmost. Only the 8-pixel-wide fonts the app actually uses
        are supported.
        """
        if font.WIDTH != 8:
            raise ValueError("eink_shim supports 8px fonts only")

        bg = _INK if _lum(background) > _LUM_THRESHOLD else _PAPER
        # Glyphs on an ink background are always paper: the app's bright
        # text on a bright band (a selected row, the unread pill, a micron
        # heading bar) would otherwise vanish ink-on-ink. This is what makes
        # a selected row read as inverted on the panel.
        fg = _PAPER if bg == _INK else (_INK if _lum(color) > _LUM_THRESHOLD else _PAPER)
        h = font.HEIGHT
        first = font.FIRST
        last = font.LAST
        src = font.FONT
        gbuf = self._gbuf
        gfb = self._gfb
        start_x = x0

        for char in text:
            ch = char if isinstance(char, int) else ord(char)
            if not (first <= ch < last):
                x0 += 8
                continue
            if x0 + 8 > self.width or y0 + h > self.height:
                break

            self.fb.fill_rect(x0, y0, 8, h, bg)
            if fg != bg:
                idx = (ch - first) * h
                for r in range(h):
                    gbuf[r] = src[idx + r]
                # blit() treats source pixels equal to `key` as transparent.
                # For ink-on-paper the glyph's set bits are already the value
                # we want, so blit as-is and let the clear bits fall through.
                # For paper-on-ink the glyph is inverted first, so the strokes
                # become 0 and the surrounding 1s are the transparent ones.
                if fg == _INK:
                    self.fb.blit(gfb, x0, y0, 0)
                else:
                    for r in range(h):
                        gbuf[r] = src[idx + r] ^ 0xFF
                    self.fb.blit(gfb, x0, y0, 1)
            x0 += 8

        self._mark(start_x, y0, x0 - start_x, h)

    def write(self, font, string, x, y, fg=0xFFFF, bg=0x0000):
        """Proportional (converted true-type) font rendering.

        Same MAP/OFFSETS/WIDTHS/BITMAPS layout as st7789py.write. ui.py calls
        this once, so this stays a straightforward per-pixel loop.
        """
        paper = _INK if _lum(bg) > _LUM_THRESHOLD else _PAPER
        ink = _PAPER if paper == _INK else (_INK if _lum(fg) > _LUM_THRESHOLD else _PAPER)
        h = font.HEIGHT
        start_x = x

        for character in string:
            try:
                char_index = font.MAP.index(character)
            except ValueError:
                continue
            offset = char_index * font.OFFSET_WIDTH
            bs_bit = font.OFFSETS[offset]
            if font.OFFSET_WIDTH > 1:
                bs_bit = (bs_bit << 8) + font.OFFSETS[offset + 1]
            if font.OFFSET_WIDTH > 2:
                bs_bit = (bs_bit << 8) + font.OFFSETS[offset + 2]

            char_width = font.WIDTHS[char_index]
            if x + char_width > self.width:
                break
            bitmaps = font.BITMAPS
            for row in range(h):
                for col in range(char_width):
                    on = bitmaps[bs_bit >> 3] & (1 << (7 - (bs_bit & 7)))
                    self.fb.pixel(x + col, y + row, ink if on else paper)
                    bs_bit += 1
            x += char_width

        self._mark(start_x, y, x - start_x, h)

    def blit_buffer(self, buffer, x, y, width, height):
        """Threshold an RGB565 buffer into the mono framebuffer.

        Two bytes per pixel, big-endian: what tjpgd hands back and what the
        shell's row compositor stores (it pre-swaps for the ST7789, which
        reads the same order). Same inverted polarity as text(): bright is
        ink. The loop is viper -- the shell blits a 240x12 row per changed
        line, and a Python per-pixel loop made a burst of output crawl.
        """
        if x < 0 or y < 0:
            return
        if x + width > self.width:
            width = self.width - x
        if y + height > self.height:
            height = self.height - y
        if width <= 0 or height <= 0:
            return
        from array import array
        geom = array("i", (self._stride, x, y, width, height))
        _blit_threshold(buffer, self._buf, geom)
        self._mark(x, y, width, height)

    def blit_dither(self, buffer, x, y, width, height):
        """Floyd-Steinberg an RGB565 buffer into the mono framebuffer.

        For photographs, where blit_buffer's threshold is wrong twice over:
        it has no grey, and it inverts (bright becomes ink), which is the
        rule the UI's dark theme wants and a picture does not. Here dark
        pixels are ink and the error of every rounding is pushed onto the
        neighbours (7/16 right, 3/16 below-left, 5/16 below, 1/16
        below-right), which is what keeps the average tone of an area.

        The pixel loop runs as viper native code: 76,800 pixels through the
        bytecode interpreter is several seconds, a few hundred ms this way.
        The image is clipped to the panel; it is expected to be placed on it
        whole, which is how the viewer centres a scaled decode.
        """
        if x < 0 or y < 0:
            return
        if x + width > self.width:
            width = self.width - x
        if y + height > self.height:
            height = self.height - y
        if width <= 0 or height <= 0:
            return
        from array import array
        # Two error rows of width+2 ints each, a one-pixel guard on both
        # sides so the diffusion needs no edge tests.
        err = array("i", bytes(4 * 2 * (width + 2)))
        geom = array("i", (self._stride, x, y, width, height))
        _dither(buffer, self._buf, err, geom)
        self._mark(x, y, width, height)

    # --- panel control -----------------------------------------------------

    def init(self):
        self._p.init()

    def _changed_rows(self, y0, y1):
        """Rows in [y0, y1) whose bytes differ from what the panel shows.

        Returns (first, last_exclusive), or None when the region is identical.
        Row granularity rather than pixel: the panel's own write window is
        byte-aligned anyway, and this keeps the comparison to one slice per
        row.
        """
        stride = self._stride
        buf = self._buf
        shown = self._shown
        first = -1
        last = y0
        for y in range(y0, y1):
            o = y * stride
            if buf[o:o + stride] != shown[o:o + stride]:
                if first < 0:
                    first = y
                last = y + 1
        if first < 0:
            return None
        return first, last

    def flush(self, force_full=False, user=False):
        """Push pending changes to the panel. This is the only slow call.

        `user` says the redraw was caused by a key press (ui.py tracks that);
        anything else -- an announce, a received message, the clock -- is a
        background redraw. Refreshes just the rows that actually differ from
        what is on the panel, which is often none of them, and then this
        returns without touching the hardware.

        A full refresh (the whole-panel flash) happens when asked (ui.py asks
        on a screen change), on the first push, or on a sizeable user redraw
        that finds a ghosting debt owed. Everything else -- a cursor step, a
        scroll, a background redraw however large -- is a quiet partial that
        at most notes the debt. The hard limit is the backstop for a panel
        that sees nothing but small partials.
        """
        if not self._dirty and not force_full:
            return

        x0, y0, x1, y1 = self._dx0, self._dy0, self._dx1, self._dy1
        self._dirty = False

        if self._shown_valid and not force_full:
            rows = self._changed_rows(y0, y1)
            if rows is None:
                return          # drawn over, but nothing actually moved
            y0, y1 = rows

        area = (x1 - x0) * (y1 - y0)
        if self._partials >= _FAST_REFRESH_LIMIT:
            self._full_owed = True
        if force_full or not self._shown_valid or \
                self._partials >= _FAST_REFRESH_HARD_LIMIT or \
                (user and self._full_owed and area >= _OWED_MIN_PIXELS):
            self._partials = 0
            self._full_owed = False
            self._p.flush()
        else:
            self._partials += 1
            self._p.flush_rect(x0, y0, x1 - x0, y1 - y0)

        self._shown[:] = self._buf
        self._shown_valid = True

    # --- compatibility no-ops ---------------------------------------------

    def set_backlight(self, *_a):
        """E-ink has no backlight. The keyboard backlight is driven separately."""

    def off(self):
        self._p.power_off()

    def sleep_mode(self, value):
        if value:
            self._p.hibernate()


@micropython.viper
def _blit_threshold(src: ptr8, fb: ptr8, geom: ptr32):
    """Inner loop of EinkShim.blit_buffer: luminance threshold, bright = ink.
    geom = (stride, x, y, w, h). The source rows are w pixels wide even when
    the blit was clipped on the right, so unused source bytes are skipped."""
    stride = geom[0]
    x0 = geom[1]
    y0 = geom[2]
    w = geom[3]
    h = geom[4]
    si = 0
    for row in range(h):
        fo = (y0 + row) * stride
        px = x0
        for i in range(w):
            c = (src[si] << 8) | src[si + 1]
            si += 2
            lum = (((c >> 11) & 0x1F) << 1) + ((c >> 5) & 0x3F) + ((c & 0x1F) << 1)
            bi = fo + (px >> 3)
            mask = 0x80 >> (px & 7)
            if lum > 60:
                fb[bi] = fb[bi] | mask
            else:
                fb[bi] = fb[bi] & (mask ^ 0xFF)
            px += 1


@micropython.viper
def _dither(src: ptr8, fb: ptr8, err: ptr32, geom: ptr32):
    """Inner loop of EinkShim.blit_dither. geom = (stride, x, y, w, h).

    src: RGB565 big-endian, w*h pixels. fb: the MONO_HLSB framebuffer, bit
    7 of a byte is its leftmost pixel, a set bit is ink. err: 2*(w+2) ints,
    the current and the next row's accumulated error, index i+1 for pixel i.
    """
    stride = geom[0]
    x0 = geom[1]
    y0 = geom[2]
    w = geom[3]
    h = geom[4]
    span = w + 2
    si = 0
    for row in range(h):
        cur = (row & 1) * span
        nxt = span - cur
        for i in range(span):
            err[nxt + i] = 0
        fo = (y0 + row) * stride
        px = x0
        for i in range(w):
            c = (src[si] << 8) | src[si + 1]
            si += 2
            # 8-bit luma, Rec.601 weights. Each 5/6-bit channel is widened
            # by replicating its top bits, so full white is 255 exactly and
            # carries no error -- a plain shift leaves it at 250, and that
            # 2% deficit accumulates into stray ink across a white area.
            r = (c >> 11) & 0x1F
            gg = (c >> 5) & 0x3F
            b = c & 0x1F
            g = (((r << 3) | (r >> 2)) * 77 + ((gg << 2) | (gg >> 4)) * 151
                 + ((b << 3) | (b >> 2)) * 28) >> 8
            v = g + err[cur + i + 1]
            bi = fo + (px >> 3)
            mask = 0x80 >> (px & 7)
            if v < 128:
                fb[bi] = fb[bi] | mask          # ink
                e = v
            else:
                fb[bi] = fb[bi] & (mask ^ 0xFF)  # paper
                e = v - 255
            # Written out in full: viper does not do augmented assignment
            # through a pointer subscript.
            k = cur + i + 2
            err[k] = err[k] + ((e * 7) >> 4)
            k = nxt + i
            err[k] = err[k] + ((e * 3) >> 4)
            k = nxt + i + 1
            err[k] = err[k] + ((e * 5) >> 4)
            k = nxt + i + 2
            err[k] = err[k] + (e >> 4)
            px += 1

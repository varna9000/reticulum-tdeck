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
from micropython import const

_INK = const(1)      # set bit in the framebuffer means black
_PAPER = const(0)

# A bright RGB565 colour becomes ink. Threshold sits below mid-grey so the
# app's various greys land on paper rather than smearing the page.
_LUM_THRESHOLD = const(60)

# Consecutive partial refreshes before a full one is forced to clear ghosting.
# Matches the limit Meshtastic uses for this panel.
_FAST_REFRESH_LIMIT = const(10)

# Dirty area beyond which a full refresh is cheaper than several partials.
_FULL_REFRESH_PIXELS = const(38400)   # half the 240x320 panel


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

        fg = _INK if _lum(color) > _LUM_THRESHOLD else _PAPER
        bg = _INK if _lum(background) > _LUM_THRESHOLD else _PAPER
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
        ink = _INK if _lum(fg) > _LUM_THRESHOLD else _PAPER
        paper = _INK if _lum(bg) > _LUM_THRESHOLD else _PAPER
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

        Used for the JPEG splash. Two bytes per pixel, big-endian, matching
        what tjpgd hands back.
        """
        i = 0
        for row in range(height):
            py = y + row
            if py >= self.height:
                break
            for col in range(width):
                px = x + col
                if px < self.width:
                    color = (buffer[i] << 8) | buffer[i + 1]
                    self.fb.pixel(px, py,
                                  _INK if _lum(color) > _LUM_THRESHOLD else _PAPER)
                i += 2
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

    def flush(self, force_full=False):
        """Push pending changes to the panel. This is the only slow call.

        Chooses a full refresh when the changed area is large, when the fast
        refresh budget is spent, or when asked. Otherwise refreshes just the
        rows that actually differ from what is on the panel -- which is often
        none of them, and then this returns without touching the hardware.
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
        if force_full or area >= _FULL_REFRESH_PIXELS or \
                not self._shown_valid or \
                self._partials >= _FAST_REFRESH_LIMIT:
            self._partials = 0
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

# T-Deck GUI Module
# Async state machine: node list + chat screens
# Diff-based drawing: only redraws changed rows. Async yields between rows.

import time
import gc
import uasyncio as asyncio
from machine import Pin

# Screen states
STATE_NODES    = 0
STATE_CHAT     = 1
STATE_SETTINGS = 2
STATE_IMAGE    = 3
STATE_RECORDING = 4
STATE_BROWSER  = 5
STATE_SHELL    = 6
STATE_RRC_ROOMS = 7
STATE_RRC_CHAT  = 8

# Node-screen tabs
TAB_MSG = 0
TAB_NET = 1
TAB_SSH = 2
TAB_RRC = 3
N_TABS  = 4

MAX_RRC_HUBS = 16
RRC_SCROLLBACK = 120      # lines kept per session, RAM only
MAX_FAVORITES = 5

# Shell control-key menu (trackball-click overlay — needs no special keyboard
# keys, which the T-Deck lacks: no Esc/Tab/Ctrl/~). (label, kind, payload).
_SHELL_CTRL_ITEMS = (
    ("Ctrl-C   interrupt", "send", b"\x03"),
    ("Ctrl-D   EOF",       "send", b"\x04"),
    ("Ctrl-Z   suspend",   "send", b"\x1a"),
    ("Tab      complete",  "send", b"\t"),
    ("Up       history",   "send", b"\x1b[A"),
    ("Down",               "send", b"\x1b[B"),
    ("Esc",                "send", b"\x1b"),
    ("Line/Char mode",     "mode", None),
    # Label is computed live by _shell_menu_label() — it carries the grid.
    ("Font",               "font", None),
    ("Quit shell",         "quit", None),
    ("Close menu",         "close", None),
)

# Shell terminal fonts, cycled largest-first from the control menu, all in the
# CP437+CP866 slot layout _tb() maps into:
#   spleen_6x12 -> 53x16   shell_6x10 -> 53x19
#   shell_5x8   -> 64x24   shell_4x6  -> 80x32
# Default is Spleen (BSD-2) to match the system font; the denser rungs are X11
# misc-fixed (public domain), which is the only family carrying Cyrillic at
# those sizes. 6x12 is the largest cell that still clears the old 40-column
# grid and divides the 192px body evenly.
_SHELL_FONTS = ("spleen_6x12", "shell_6x10", "shell_5x8", "shell_4x6")

# Settings sub-pages
_SET_MAIN      = 0
_SET_WIFI_SCAN = 1
_SET_WIFI_PASS = 2
_SET_TCP_HOST  = 3
_SET_NODE_NAME = 4
_SET_RADIO     = 5
_SET_LORA      = 6   # editable radio params (freq/bw/sf/cr/tx)
_SET_LORA_FREQ = 7   # numeric entry sub-page for the frequency

# LoRa radio config: allowed values for the editor. BW cycles through every
# bandwidth the SX1262 has (the exact key set of the sx126x driver's
# configure() table, ascending) -- a mesh on a custom plan may sit on 62.5 or
# 31.25 kHz (issue #9). Free text would only snap to one of these anyway.
# Below 62.5 kHz both radios need TCXO-grade frequency accuracy; a peer on a
# plain 20 ppm crystal is ~17 kHz off at 868 MHz. SF/CR/TX clamp at their
# bounds; freq is stepped or typed in kHz. These are the ranges the driver
# accepts (SF 6-12, CR 4/5-4/8), narrowed to sane mesh values.
_LORA_FIELDS = ("freq_khz", "bw", "sf", "coding_rate", "tx_power")
_LORA_BW_CHOICES = ("7.8", "10.4", "15.6", "20.8", "31.25", "41.7", "62.5",
                    "125", "250", "500")
_LORA_SF_MIN = 7
_LORA_SF_MAX = 12
_LORA_CR_MIN = 5     # 4/5
_LORA_CR_MAX = 8     # 4/8
_LORA_TX_MIN = 0
_LORA_TX_MAX = 22
_LORA_FREQ_STEP = 100      # kHz per trackball step
_LORA_FREQ_MIN = 137000    # kHz — SX1262 usable low end
_LORA_FREQ_MAX = 1020000   # kHz — SX1262 usable high end

# Layout constants. The T-Deck v1 is 320x240 landscape; the T-Deck Pro is
# 240x320 portrait. Everything below is derived from the screen size so one
# layout serves both, and a board with a different panel only has to declare
# its geometry. Absent that module the v1 numbers are reproduced exactly.
try:
    from board_geometry import SCREEN_W, SCREEN_H
except ImportError:
    SCREEN_W = 320
    SCREEN_H = 240
CHAR_W = 8
CHAR_H = 16
COLS = SCREEN_W // CHAR_W        # 40 at 320 wide, 30 at 240
NAV_H = 20        # navbar height (1 row + 4px padding)
NAV_TY = 2        # navbar text y offset (2px top padding)
INPUT_Y = SCREEN_H - CHAR_H      # status bar, flush with the bottom edge
BODY_Y = 26       # main area start (6px gap below navbar for frame line)
SEP_Y = INPUT_Y - 2              # separator line just above the input bar
BODY_ROWS = (SEP_Y - 4 - BODY_Y) // CHAR_H

# Row cache slots: the navbar, one per body row, then the footer hint line and
# the input line. The body rows have to scale with the panel or a taller screen
# indexes past the end of the list -- the T-Deck Pro has 17 body rows where the
# v1 has 12. On the v1 these come out 15 / 13 / 14, the constants they replace.
CACHE_ROWS = BODY_ROWS + 3
FOOT_SLOT = BODY_ROWS + 1
INPUT_SLOT = BODY_ROWS + 2

# Scrollbar lane — kept 1px clear of the frame's right corner arm at x=319
SBAR_X = SCREEN_W - 3   # 317
SBAR_W = 2

IMG_GAP = 2   # px of breathing room top/bottom inside an inline image block

# Voice recording
REC_MAX_SECS = 15  # matches tdeck_node _rec_buf sizing

# Data limits
MAX_PEERS = 16
MAX_HISTORY = 30  # per peer
MAX_CACHED_IMAGES = 3  # max JPEG payloads kept in RAM

# Trackball debounce (horizontal slower: tab switches, not scrolling)
# Vertical debounce filters contact bounce only (~ms-scale); 80ms here made
# fast rolls drop most of their detents — with draws now ~100ms and steps
# coalescing into the next frame, 30ms tracks the ball instead of eating it.
# Horizontal stays high: tab switches should be deliberate.
_TB_DEBOUNCE_MS = 30
_TB_H_DEBOUNCE_MS = 150
# Trackball click held at least this long toggles the device lock
_TB_LONG_PRESS_MS = 700

# Screen power-off timeout options (ms); 0 = never sleep
_SCREEN_TIMEOUT_MS = 10000
_TIMEOUT_CHOICES = (10000, 30000, 60000, 0)

# Unicode -> font glyph index for the display driver. The font
# (lib/vga2_8x16_cp866.py) keeps the CP437 base and adds Cyrillic in the
# CP866 slots, plus Bulgarian Ѝ/ѝ at 0xFC/0xFD. Generated together with the
# font by tools/gen_cp866_font.py — keep the two in sync.
_CYR = {0x401: 0xF0, 0x451: 0xF1,   # Ё ё
        0x404: 0xF2, 0x454: 0xF3,   # Є є
        0x407: 0xF4, 0x457: 0xF5,   # Ї ї
        0x40E: 0xF6, 0x45E: 0xF7,   # Ў ў
        0x40D: 0xFC, 0x45D: 0xFD}   # Ѝ ѝ
for _i in range(32):
    _CYR[0x410 + _i] = 0x80 + _i    # А-Я
for _i in range(16):
    _CYR[0x430 + _i] = 0xA0 + _i    # а-п
    _CYR[0x440 + _i] = 0xE0 + _i    # р-я
del _i

# Unicode box-drawing / block-element -> font slot. The CP437 half of the
# slot layout natively holds the single/double box set, the shade blocks and
# the half blocks (0xB0-0xDF, plus ° ∙ · √ ■ at 0xF8-0xFE); the eighth
# blocks and quadrants that modern banner art also uses are synthesized into
# the blank dingbat slots 0x01-0x18 by tools/gen_block_glyphs.py — keep the
# two tables in sync. Heavy (┃━┏…) and rounded (╭╮╯╰) variants alias to
# their single-line slots; ╱╲╳ alias to / \ X.
_GFX = {
    0x00B0: 0xF8, 0x00B7: 0xFA, 0x2219: 0xF9, 0x221A: 0xFB,
    0x2500: 0xC4, 0x2501: 0xC4, 0x2502: 0xB3, 0x2503: 0xB3,
    0x250C: 0xDA, 0x250F: 0xDA, 0x2510: 0xBF, 0x2513: 0xBF,
    0x2514: 0xC0, 0x2517: 0xC0, 0x2518: 0xD9, 0x251B: 0xD9,
    0x251C: 0xC3, 0x2523: 0xC3, 0x2524: 0xB4, 0x252B: 0xB4,
    0x252C: 0xC2, 0x2533: 0xC2, 0x2534: 0xC1, 0x253B: 0xC1,
    0x253C: 0xC5, 0x254B: 0xC5, 0x2550: 0xCD, 0x2551: 0xBA,
    0x2552: 0xD5, 0x2553: 0xD6, 0x2554: 0xC9, 0x2555: 0xB8,
    0x2556: 0xB7, 0x2557: 0xBB, 0x2558: 0xD4, 0x2559: 0xD3,
    0x255A: 0xC8, 0x255B: 0xBE, 0x255C: 0xBD, 0x255D: 0xBC,
    0x255E: 0xC6, 0x255F: 0xC7, 0x2560: 0xCC, 0x2561: 0xB5,
    0x2562: 0xB6, 0x2563: 0xB9, 0x2564: 0xD1, 0x2565: 0xD2,
    0x2566: 0xCB, 0x2567: 0xCF, 0x2568: 0xD0, 0x2569: 0xCA,
    0x256A: 0xD8, 0x256B: 0xD7, 0x256C: 0xCE, 0x256D: 0xDA,
    0x256E: 0xBF, 0x256F: 0xD9, 0x2570: 0xC0, 0x2571: 0x2F,
    0x2572: 0x5C, 0x2573: 0x58, 0x2580: 0xDF, 0x2581: 0x01,
    0x2582: 0x02, 0x2583: 0x03, 0x2584: 0xDC, 0x2585: 0x04,
    0x2586: 0x05, 0x2587: 0x06, 0x2588: 0xDB, 0x2589: 0x07,
    0x258A: 0x08, 0x258B: 0x09, 0x258C: 0xDD, 0x258D: 0x0A,
    0x258E: 0x0B, 0x258F: 0x0C, 0x2590: 0xDE, 0x2591: 0xB0,
    0x2592: 0xB1, 0x2593: 0xB2, 0x2594: 0x0D, 0x2595: 0x0E,
    0x2596: 0x0F, 0x2597: 0x10, 0x2598: 0x11, 0x2599: 0x12,
    0x259A: 0x13, 0x259B: 0x14, 0x259C: 0x15, 0x259D: 0x16,
    0x259E: 0x17, 0x259F: 0x18, 0x25A0: 0xFE,
}

# Single lookup table for _tb()'s hot path: Cyrillic + graphics together.
# _CYR stays separate — _ascii() uses it as its keep-filter for names.
_SLOT_MAP = dict(_CYR)
_SLOT_MAP.update(_GFX)


# Keep displayable chars (ASCII + mapped Cyrillic) and drop everything else,
# control codes included. Spacing is left exactly as the sender wrote it, so
# this is the filter for prose that is meant to be rendered verbatim — a
# hub's space-aligned "/list" table, say. _row() rstrips the tail, so trailing
# padding costs nothing.
def _ascii_keep_spacing(s):
    return ''.join(c for c in s if 32 <= ord(c) < 127 or ord(c) in _CYR)


# The same filter, then whitespace collapsed — for names and other single-line
# labels, where emoji/CJK removal would otherwise leave gaps.
def _ascii(s):
    return ' '.join(_ascii_keep_spacing(s).split())

# Pad string to exact width (no clearing needed)
def _pad(s, width=COLS):
    if len(s) >= width:
        return s[:width]
    return s + ' ' * (width - len(s))


def _img_native_size(data):
    """(width, height) from a WebP or JPEG header, or None if unparseable.

    The native decoders stretch to whatever target they're handed, so the
    caller needs the source size to scale to fit *without* upscaling."""
    try:
        if len(data) >= 30 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            fmt = data[12:16]
            if fmt == b'VP8 ':                       # lossy
                return ((data[26] | (data[27] << 8)) & 0x3FFF,
                        (data[28] | (data[29] << 8)) & 0x3FFF)
            if fmt == b'VP8L' and data[20] == 0x2F:   # lossless
                b = data[21] | (data[22] << 8) | (data[23] << 16) | (data[24] << 24)
                return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
            if fmt == b'VP8X':                        # extended
                return (1 + (data[24] | (data[25] << 8) | (data[26] << 16)),
                        1 + (data[27] | (data[28] << 8) | (data[29] << 16)))
            return None
        if len(data) >= 4 and data[0] == 0xFF and data[1] == 0xD8:   # JPEG
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):  # SOFn
                    return ((data[i + 7] << 8) | data[i + 8],
                            (data[i + 5] << 8) | data[i + 6])
                i += 2 + ((data[i + 2] << 8) | data[i + 3])
    except Exception:
        pass
    return None


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# Wall clock is meaningful only after network time sync (no RTC battery)
def _clock_valid():
    return time.localtime()[0] >= 2024


def _fmt_clock():
    t = time.localtime()
    return "%02d:%02d" % (t[3], t[4])


def _fmt_time(ts):
    """Message timestamp: HH:MM today, MM-DD HH:MM otherwise. '' if the
    clock wasn't synced when the message was stored."""
    t = time.localtime(int(ts))
    if t[0] < 2024:
        return ""
    now = time.localtime()
    if (t[0], t[1], t[2]) == (now[0], now[1], now[2]):
        return "%02d:%02d" % (t[3], t[4])
    return "%02d-%02d %02d:%02d" % (t[1], t[2], t[3], t[4])


def _age(ts):
    """Compact age: now / 5m / 2h / 3d."""
    if not ts:
        return "?"
    d = int(time.time() - ts)
    if d < 60:
        return "now"
    if d < 3600:
        return str(d // 60) + "min"
    if d < 86400:
        return str(d // 3600) + "h"
    return str(d // 86400) + "d"


_HEX = "0123456789abcdef"


def _hops(n):
    return str(n) + (" hop" if n == 1 else " hops")


def _icon(rows):
    """Pixel icon -> (width, height, runs): one fill_rect per horizontal run."""
    runs = []
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            if row[x] == "#":
                k = x
                while k < len(row) and row[k] == "#":
                    k += 1
                runs.append((x, y, k - x))
                x = k
            else:
                x += 1
    return len(rows[0]), len(rows), tuple(runs)


_STAR = _icon(("....#....", "....#....", "...###...", "#########", ".#######.",
               "..#####..", "..##.##..", ".##...##.", ".#.....#."))
_GLOBE = _icon(("..#####..", ".#.#.#.#.", "#..#.#..#", "#..#.#..#", "#########",
                "#..#.#..#", "#..#.#..#", ".#.#.#.#.", "..#####.."))
_ANTENNA = _icon((".#.......#.", "#..#...#..#", "#.#..#..#.#", "#.#.###.#.#",
                  "#..#.#.#..#", ".#...#...#.", ".....#.....", "....###....",
                  "...#.#.#...", "..#..#..#.."))


def match_contacts(snap, q):
    """Find-contact filter: the (dest_hash, name, ts) entries of snap, in
    snap's order, whose name contains q (any case) or -- when q is hex --
    whose hash starts with it. A hex word like "dee" can hit both ways."""
    q = q.lower()
    if not q:
        return list(snap)
    is_hex = all(c in _HEX for c in q)
    return [e for e in snap
            if (is_hex and e[0].hex().startswith(q)) or q in e[1].lower()]


def _sw565(c):
    """Byte-swap an RGB565 colour for framebuf.

    framebuf stores 16-bit pixels in native (little-endian) order and
    blit_buffer() ships the bytes to the panel raw, but the ST7789 wants
    big-endian on the wire. tft.text() hides this by swapping internally
    (_swap_bytes in st7789.c); blit_buffer does not, so we pre-swap here."""
    return ((c << 8) | (c >> 8)) & 0xFFFF


class _SmallText:
    """Small-font text through a framebuf, pushed with one blit_buffer.

    The st7789 driver's text() only handles 8- and 16-pixel-wide fonts; a
    6-wide font through it draws garbage (solid boxes). Same technique as
    _ShellFont: 1-bit MONO_HLSB glyph views over the font's bytes, blitted
    through a 2-colour palette into an RGB565 strip."""

    def __init__(self, mod):
        import framebuf
        self._fbm = framebuf
        self.w = mod.WIDTH
        self.h = mod.HEIGHT
        self._mv = memoryview(bytearray(mod.FONT))
        self._glyphs = [None] * 256
        self._buf = bytearray(SCREEN_W * self.h * 2)
        self._pal = framebuf.FrameBuffer(bytearray(4), 2, 1, framebuf.RGB565)

    def draw(self, tft, slots, x, y, fg, bg):
        n = min(len(slots), (SCREEN_W - x) // self.w)
        if n <= 0:
            return
        w = n * self.w
        fbm = self._fbm
        fb = fbm.FrameBuffer(self._buf, w, self.h, fbm.RGB565)
        b = _sw565(bg)
        fb.fill(b)
        self._pal.pixel(0, 0, b)
        self._pal.pixel(1, 0, _sw565(fg))
        h = self.h
        for i in range(n):
            c = slots[i]
            if c == 0x20:
                continue
            g = self._glyphs[c]
            if g is None:
                g = fbm.FrameBuffer(self._mv[c * h:(c + 1) * h], self.w, h, fbm.MONO_HLSB)
                self._glyphs[c] = g
            fb.blit(g, i * self.w, 0, -1, self._pal)
        tft.blit_buffer(memoryview(self._buf)[:w * h * 2], x, y, w, h)


class _ShellFont:
    """Glyph cache + row compositor for the rnsh terminal.

    The shell deliberately does NOT render through tft.text() or tft.write().
    Both cost one SPI window-set per glyph — measured at ~210us on this board
    — so a body repaint costs 135ms for today's 40x12 text() screen and 565ms
    for 80x32 via write(). Compositing a whole row into a framebuf and pushing
    it with a single blit_buffer() measures 93ms at 64x24 and 102ms at 80x32
    on real shell output: up to five times the characters, still faster than
    the screen it replaces. tft.text() stays in use everywhere else.

    Glyphs are 1-bit MONO_HLSB views over the font module's own bytes, drawn
    through a 2-entry palette. That is ~9ms/screen slower than pre-rendered
    RGB565 sprites, but it lets the foreground vary per cell — which is what
    SGR colour needs, and we declare TERM=vt100 so those codes already arrive.
    """

    def __init__(self, modname, fg, bg):
        import framebuf
        self._fb = framebuf
        mod = __import__(modname)
        self.name = modname
        self.w = mod.WIDTH
        self.h = mod.HEIGHT
        self.cols = SCREEN_W // self.w
        self.rows = (BODY_ROWS * CHAR_H) // self.h   # same 192px body
        # One writable copy of the glyph data; per-slot FrameBuffers are
        # memoryview slices over it, built on first use — shell output touches
        # ~95 of the 256 slots and each FrameBuffer object costs ~50 bytes.
        self._glyphdata = bytearray(mod.FONT)
        self._mv = memoryview(self._glyphdata)
        self._glyphs = [None] * 256
        self._rowbuf = bytearray(SCREEN_W * self.h * 2)
        self._rowfb = framebuf.FrameBuffer(self._rowbuf, SCREEN_W, self.h,
                                           framebuf.RGB565)
        self._palbuf = bytearray(4)
        self._pal = framebuf.FrameBuffer(self._palbuf, 2, 1, framebuf.RGB565)
        self.set_colors(fg, bg)

    def set_colors(self, fg, bg):
        self._bg = _sw565(bg)
        self._pal.pixel(0, 0, self._bg)
        self._pal.pixel(1, 0, _sw565(fg))

    def _glyph(self, slot):
        g = self._glyphs[slot]
        if g is None:
            h = self.h
            g = self._fb.FrameBuffer(self._mv[slot * h:(slot + 1) * h],
                                     self.w, h, self._fb.MONO_HLSB)
            self._glyphs[slot] = g
        return g

    def draw_row(self, tft, slots, y):
        """Composite one row of glyph-index bytes (ui._tb output) and push it
        with a single blit_buffer. Spaces are skipped — the row is already
        background — so short lines cost proportionally less."""
        fb = self._rowfb
        fb.fill(self._bg)
        pal = self._pal
        w = self.w
        x = 0
        for s in slots:
            if s != 0x20:
                fb.blit(self._glyph(s), x, 0, -1, pal)
            x += w
        tft.blit_buffer(self._rowbuf, 0, y, SCREEN_W, self.h)


class UI:

    def __init__(self, tft, font, get_key_func, node_name="T-Deck",
                 trackball=True):
        self.tft = tft
        self.font = font
        self.get_key = get_key_func
        self.node_name = node_name

        # Panels that are written directly have nothing to push; an e-ink panel
        # does, and the cost is a ~700 ms refresh. So drawing never touches it
        # and _flush() is called once per rendered screen instead. Resolved
        # once here rather than per redraw.
        self._panel_flush = getattr(tft, "flush", None)

        # On a 1-bit panel there is no dim: DIM_CYAN and NEON_GREEN both land
        # on the ink side of the shim's luminance threshold (80 and 63 on its
        # 0..187 scale), so an unlit battery segment drawn dim comes out just
        # as black as a lit one and the pack always reads full. Unlit has to be
        # background there. Colour displays keep the dim shade.
        self._mono = bool(getattr(tft, "mono", False))

        # Cyberpunk color palette (RGB565)
        self.YELLOW     = 0xFFE0
        self.BG_DARK    = 0x0821  # very dark blue-grey — main background
        self.NEON_CYAN  = 0x07FF  # primary text, borders
        self.NEON_GREEN = 0x07E0  # "me>" prefix, input prompt, active items
        self.NEON_MAG   = 0xF81F  # unread markers, accents
        self.DIM_CYAN   = 0x0514  # secondary/dimmed text
        self.HEADER_BG  = 0x0011  # very dark blue — navbar background
        self.SEL_BG     = 0x2966  # selection highlight — bright blue tint
        self.TAB_BG     = 0x02AA  # teal — selected tab, and the Find band under it
        self.ORANGE     = 0xFD20  # favourite star

        # Small font for secondary text: header, footer, hashes, ages, pill
        # counts. The e-ink shim draws 8px-wide fonts only, so the Pro keeps
        # the main font; every layout below measures with SW/SH, not 6/12.
        self.sfont = font
        self._small = None
        if not self._mono:
            try:
                import spleen_6x12
                self.sfont = spleen_6x12
                self._small = _SmallText(spleen_6x12)
            except ImportError:
                pass            # host tests: no framebuf; text() stands in
        self.SW = getattr(self.sfont, "WIDTH", CHAR_W)
        self.SH = getattr(self.sfont, "HEIGHT", CHAR_H)
        self.BODY_FG    = 0xC618  # light grey — message body text (matches micron)

        # State
        self.state = STATE_NODES
        self.dirty = True
        self._input_dirty = False
        self._prev_state = -1  # force full clear on first draw
        self._state_change_ms = 0  # debounce rapid state flips

        # Row cache: navbar + one slot per body row + footer + input.
        # Compared before drawing — skip SPI if row unchanged.
        self._cache = [''] * CACHE_ROWS
        self._nav_bat_cache = ''
        self._nav_mid_cache = ''

        # Peers: dest_hash_bytes -> {"name": str, "rssi": int,
        #                            "hops": int|None, "via": str|None, "seen": ts}
        self.peers = {}
        self._peer_keys = []  # ordered list of dest_hash_bytes
        self.selected_idx = 0
        self.node_scroll = 0
        self._route_cache = ''   # footer route/ping line cache (node list)
        self.ping_status = None  # transient "ping: 2.4s" text
        self._ping_status_ms = 0
        self.ping_pending = False  # True while a ping is awaiting its receipt

        # NET tab: NomadNet nodes (populated by nomad_browser)
        self.node_tab = 0        # 0 = MSG (LXMF peers), 1 = NET (nomad nodes), 2 = SSH (rnsh)
        self.nomad_nodes = {}    # dest_hash -> {"name": str, "hops": int|None, "seen": ts}
        self._node_keys = []     # ordered list of dest_hash_bytes
        self.net_idx = 0
        self.net_scroll = 0

        # SSH tab: rnsh listeners (populated by rnsh_client)
        self.shell_nodes = {}    # dest_hash -> {"name": str, "hops": int|None, "seen": ts}
        self._shell_keys = []    # ordered list of dest_hash_bytes
        self.ssh_idx = 0
        self.ssh_scroll = 0
        # Manual hex-entry sub-mode, shared by the SSH and RRC tabs: same
        # 32-hex-char destination, same screen, same key handler; the tab in
        # force decides what Enter opens.
        # Find sub-mode, (m) on any tab: typeahead over every heard address of
        # the tab's kind (snapshot taken on open), filtered into _find_res; a
        # full 32-hex hash with no match is added as-is.
        self._find = False
        self._find_q = ""
        self._find_snap = []
        self._find_res = []
        self._find_sel = 0
        self._find_scroll = 0

        # Shell session (STATE_SHELL)
        self._terminal = None        # terminal.Terminal, created on connect
        self._shell_status = None    # transient status/footer text
        self._shell_line_mode = True # True: local line-edit; False: char-at-a-time
        self._shell_input = bytearray()   # local line buffer (line mode)
        self._shell_at_line_start = True  # for the ~. disconnect escape
        self._shell_escape = False        # saw '~' at line start
        self._shell_view = 0              # scrollback offset from bottom (0 = live)
        self._shell_connected = False
        self._shell_dest = None
        self._shell_menu = False          # control-key menu overlay open?
        self._shell_menu_idx = 0
        self._shell_font = None           # _ShellFont, created on connect
        self._shell_font_idx = 0          # index into _SHELL_FONTS
        self._shell_cache = []            # body-row cache (own grid, own size)

        # Browser page view (STATE_BROWSER)
        self.browser_lines = []   # micron.render() rows of styled spans
        self.browser_links = []   # [(url, label)]
        self.browser_title = ""
        self.browser_path = ""
        self.browser_scroll = 0
        self.browser_cursor = -1  # -1 = inactive, else visible-row index
        self.browser_status = None  # transient status/error footer text
        self._browser_can_back = False
        self._page_gen = 0        # bumped per page; keys the row cache
        self._browser_link_rows = {}  # visible row -> link index

        # Inline page images (colour TFT only). link_idx -> state dict;
        # doc_row -> (link_idx, subrow). Populated by _expand_image_blocks().
        self._page_images = {}
        self._browser_image_rows = {}
        self._img_lru = []          # link_idx order, for decoded-buffer eviction

        # Chat: dest_hash_bytes -> [(is_mine, text, timestamp, status), ...]
        # status: 0=none, 1=pending, 2=delivered, 3=failed
        self.chat_history = {}
        self.chat_scroll = 0
        self.chat_cursor = -1  # -1 = inactive (at bottom), else index into visible lines
        self.selected_peer = None  # dest_hash_bytes of current chat peer

        # Input
        self.cmd_buf = bytearray()

        # Navbar state
        self.bat_v = 0.0
        self.rssi = None
        self.snr = None
        self.lora_online = True
        self.transfer_progress = None  # (received, total) or None
        self._audio_status = None      # None, "decoding", or "playing"
        self._rec_seconds = 0          # recording duration counter (driven by node)
        self._rec_max = REC_MAX_SECS   # max recording length in seconds
        self._rec_level = 0            # live mic peak, 0..10 bar units
        self._rec_warming = False      # True while the ADC warms before capture
        self._progress_dirty = False
        self.announce_flash = 0  # timestamp of last announce flash

        # IRQ counters (written by ISR, drained by main loop)
        self._irq_up = 0
        self._irq_down = 0
        self._irq_click = 0
        self._irq_long_click = 0
        self._irq_left = 0
        self._irq_right = 0
        # ISR debounce timestamps (ticks_ms is ISR-safe)
        self._irq_last_scroll = 0
        self._irq_last_click = 0
        self._irq_last_h = 0
        self._irq_press_ms = 0  # falling edge of the click currently held
        self._irq_pressed = 0   # 1 between a press and its release

        # Trackball pins, and the hardware interrupts that feed the counters
        # above. Boards without a trackball pass trackball=False and drive the
        # same counters through nav_event() instead -- on the T-Deck Pro these
        # GPIOs are LoRa CS, GPS PPS, the keyboard interrupt and the
        # vibration motor, so claiming them as pulled-up inputs would break
        # the radio. The click pin needs both edges: press starts the timer,
        # release decides short vs. long (the long-press lock).
        if trackball:
            self._tb_up    = Pin(3, Pin.IN, Pin.PULL_UP)
            self._tb_down  = Pin(15, Pin.IN, Pin.PULL_UP)
            self._tb_left  = Pin(1, Pin.IN, Pin.PULL_UP)
            self._tb_right = Pin(2, Pin.IN, Pin.PULL_UP)
            self._tb_click = Pin(0, Pin.IN, Pin.PULL_UP)
            self._tb_up.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_up)
            self._tb_down.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_down)
            self._tb_click.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING,
                               handler=self._irq_handler_click)
            self._tb_left.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_left)
            self._tb_right.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_right)
        else:
            self._tb_up = self._tb_down = self._tb_left = None
            self._tb_right = self._tb_click = None

        # Battery voltage comes from adc_reader (board-declared pin/divider,
        # initialized by tdeck_node via adc_reader.init_battery)

        # Unread message tracking: dest_hash -> count
        self.unread = {}

        # Settings state
        self._settings_page = _SET_MAIN
        self._settings_idx = 0
        self._wifi_networks = []     # [(ssid, rssi), ...]
        self._wifi_scanning = False
        self._wifi_ssid = ""         # selected SSID for password entry
        self._wifi_connected = False
        self._wifi_ssid_current = ""
        self._wifi_ip = ""
        self._tcp_enabled = False
        self._tcp_target = ""  # "host:port" string, set from saved settings on boot
        self._tcp_default = ""  # "host:port" from TCP_CONFIG, set by tdeck_node.py
        self._settings_scroll = 0
        self._radio_rows = 0  # row count of the radio stats page (for scroll clamp)
        self._volume = 8  # 0-10, synced with sound.volume
        # The keyboard backlight has a preference and a hardware state, and on
        # boards with no display backlight they are not the same thing: the
        # light follows the screen in and out of its inactivity sleep, while
        # _kbd_bl stays whatever the user chose. See _kbd_tracks_screen().
        self._kbd_bl = False      # user preference (restored from settings)
        self._kbd_bl_lit = False  # what the hardware is actually doing
        self._auto_announce = False   # periodic re-announce toggle
        self._wifi_connecting = False  # True while an async WiFi connect is running
        self._wifi_err = ""            # last connect failure note (shown on scan page)
        self._tcp_connecting = False   # True while an async TCP connect is running
        self._screen_timeout_ms = _SCREEN_TIMEOUT_MS  # configurable inactivity sleep
        self._wake_mode = 0  # 0 = messages only, 1 = messages + announces, 2 = never

        # LoRa radio config editor (Settings > LoRa cfg). _lora_cfg mirrors the
        # live interface params (pushed in by tdeck_node.py via set_lora_config);
        # _lora_edit is a working copy held only while the page is open, so
        # backing out without Apply discards. _lora_field is the selected row
        # (0-4 = fields, 5 = the Apply & Save row).
        self._lora_cfg = {"freq_khz": 868000, "bw": "125", "sf": 7,
                          "coding_rate": 5, "tx_power": 14}
        self._lora_edit = None
        self._lora_field = 0
        self._lora_applying = ""   # "", "applied", or "failed" — transient status

        # Message ids. Every cache and callback below refers to a message by
        # id, never by list position: trimming history at MAX_HISTORY shifts
        # every position, so positions silently point at the wrong message
        # once a chat fills up. Ids are unique per boot and never reused.
        self._next_mid = 0

        # Image viewer state
        self._image_cache = {}  # (peer_hash, msg_id) -> jpeg/webp bytes
        self._audio_cache = {}  # (peer_hash, msg_id) -> (codec2_bytes, mode)
        self._image_cache_order = []  # LRU order of (peer_hash, msg_id) keys
        self._viewing_image = None  # jpeg_bytes currently displayed
        self._image_drawn = False  # True once JPEG has been blitted
        self._visible_image_lines = {}  # display_row -> msg_id (populated by draw_chat)
        self._visible_msg_lines = {}    # display_row -> msg_id for ALL messages

        # Chat lines cache (avoids rebuilding word-wrapped lines on every draw)
        self._chat_lines_cache = None
        self._chat_lines_peer = None

        # Screen power management
        self._last_activity = time.ticks_ms()
        self._screen_on = True
        self._bl = None  # backlight pin, set by set_backlight()
        self.locked = False  # long-press lock: screen off, all input dropped

        # Node identity/info (set by tdeck_node.py)
        self.my_address = None       # own LXMF address hex string
        self.my_identity_hash = None # own identity hash (for rnsh -a auth lists)
        self.get_radio_stats = None  # () -> [(label, value), ...] for radio page

        # Callbacks (set by tdeck_node.py)
        self.on_send = None       # on_send(dest_hash_bytes, text)
        self.on_announce = None   # on_announce()
        self.on_ping = None       # on_ping(dest_hash_bytes)
        self.on_contact_snapshot = None  # (tab) -> [(dest_hash, name, ts)], newest first
        self.on_add_contact = None       # (dest_hash) -> None — added by Find; seek a path
        self.on_wifi_scan = None      # () -> [(ssid, rssi), ...]
        self.on_wifi_connect = None   # (ssid, password) -> None — async; calls set_wifi_result
        self.on_tcp_toggle = None     # (enabled, host, port) -> bool — sync OFF path
        self.on_tcp_connect = None    # (host, port) -> None — async; calls set_tcp_result
        self.on_node_name = None      # (name) -> None
        self.on_lora_reset = None     # () -> bool
        self.on_lora_config = None    # (params) -> bool — live-apply + persist radio params
        self.on_volume = None         # (level) -> None
        self.on_kbd_backlight = None  # (enabled) -> bool; persists the setting
        self.on_kbd_backlight_drive = None  # (on) -> bool; drives only, no save
        self.on_battery = None        # () -> volts | None, set from the board
        self.on_audio_play = None     # (codec2_bytes, mode) -> None
        self.on_record_start = None   # () -> None
        self.on_record_stop = None    # (send: bool) -> None
        self.on_browse = None         # (dest_hash_bytes) -> None — open node index page
        self.on_browse_follow = None  # (url_str) -> None — follow a micron link
        self.on_browse_back = None    # () -> bool — went back (False: at stack bottom)
        self.on_browse_refresh = None # () -> None
        self.on_browser_exit = None   # () -> None — left the browser (free the link)
        self.on_fetch_page_image = None   # (link_idx, src) -> None; async fetch
        self.on_net_seed = None       # () -> None — populate nomad_nodes from storage
        self.on_screen_timeout = None # (ms) -> None — persist inactivity timeout
        self.on_wake_mode = None      # (mode) -> None — persist auto-wake policy
        self.on_auto_announce = None  # (enabled) -> None — start/stop periodic announce
        self.on_delete_peer = None    # (dest_hash_bytes) -> None — forget a peer/node
        # rnsh shell callbacks (wired by tdeck_node.py)
        self.on_shell_connect = None    # (dest_hash, cols, rows) -> None
        self.on_shell_input = None      # (bytes) -> None — send stdin to remote
        self.on_shell_disconnect = None # () -> None — tear down the session
        self.on_shell_seed = None       # () -> None — populate shell_nodes from storage
        self.on_shell_resize = None     # (rows, cols) -> None
        # RRC (Reticulum Relay Chat) callbacks (wired by tdeck_node.py)
        self.on_rrc_connect = None      # (dest_hash) -> None
        self.on_rrc_join = None         # (room, key) -> None
        self.on_rrc_say = None          # (text) -> None
        self.on_rrc_part = None         # () -> None
        self.on_rrc_list = None         # () -> None — re-request /list
        self.on_rrc_disconnect = None   # () -> None
        self.on_rrc_seed = None         # () -> None
        self.on_rrc_mention = None      # (identity_hash) -> "@token" string
        self.on_rrc_cap = None          # () -> composer byte budget (int)

        self.rrc_hubs = {}              # dest_hash -> {"name", "hops", "seen"}
        self._rrc_keys = []             # hub order, newest announce last
        self._rrc_idx = 0
        self._rrc_scroll = 0
        self._rrc_lines = []            # (kind, nick, text), RAM only
        self._rrc_flat = None           # rrc_ui._flatten() cache; None = stale
        self._rrc_scroll_chat = 0
        self._rrc_input = ""
        self._rrc_room = None
        self._rrc_hub_name = None
        self._rrc_members = 0
        self._rrc_members_exact = False  # is the count the room, or just who we saw?
        self._rrc_status = ""
        self._rrc_panel = False         # a panel overlay is open (trackball click)
        self._rrc_panel_kind = "members"  # "members" in a room, "rooms" in the console
        self._rrc_panel_idx = 0
        self._rrc_panel_scroll = 0
        self._rrc_roster = []           # [(identity_hash, nick_or_None), ...]
        self._rrc_rooms = []            # [(bare_name, topic), ...] from /list
        self._rrc_list_seen = False     # has any /list reply ever landed?
        self._rrc_list_ms = 0           # last /list request, for the refresh throttle
        self._rrc_prompt = False

    # --- Screen power management ---

    def set_backlight(self, bl_pin):
        self._bl = bl_pin

    def _kbd_tracks_screen(self):
        """True where the keyboard backlight should follow the screen's sleep.

        Gated on there being no display backlight, which is the reason it
        matters rather than a proxy for it. A board with one already dims on
        sleep and already saves the power; a board without -- the e-ink Pro --
        blanks nothing, holds its last frame with no power, and gives no sign
        at all of whether it is awake. There the keyboard light is the only
        thing that can show it, and it is the largest discretionary draw on
        the board, so leaving it lit through an hour of sleep is the one place
        the timeout could save real current and currently does not.
        """
        return self._bl is None

    def _drive_kbd_backlight(self, on):
        """Drive the light for a sleep or a wake, leaving the preference alone.

        Deliberately not on_kbd_backlight: that one persists the setting, and
        going through it here would rewrite settings.json on every idle and
        every keypress that wakes the deck.
        """
        cb = self.on_kbd_backlight_drive
        if cb is None or not cb(on):
            return False
        self._kbd_bl_lit = bool(on)
        return True

    def toggle_kbd_backlight(self):
        """Alt+B. Goes through the preference so the Settings screen agrees,
        the choice is persisted, and sleep/wake keep tracking it."""
        return self.set_kbd_backlight_pref(not self._kbd_bl)

    def restore_kbd_backlight(self, on):
        """Boot: write the saved preference to the keyboard, on OR off.

        The v1 keyboard is its own MCU and keeps its backlight state across
        an S3 restart; M5Launcher leaves it lit (issue #10). Only driving an
        ON here, and trusting the keyboard to power up dark, let that light
        survive a saved OFF. Drives without persisting: the setting is what
        we are restoring from. Returns False when the write was refused, in
        which case the recorded state is left as it was.
        """
        on = bool(on)
        if not self._drive_kbd_backlight(on):
            return False
        self._kbd_bl = on
        return True

    def set_kbd_backlight_pref(self, on):
        """The user changed the setting: persist it, and match the hardware."""
        on = bool(on)
        if self.on_kbd_backlight is None or not self.on_kbd_backlight(on):
            return False
        self._kbd_bl = on
        self._kbd_bl_lit = on
        return True

    def _flush(self):
        """Push a completed frame to the panel, where the panel needs it.

        Every call site sits between spi_acquire_display() and
        spi_release_display(), because on e-ink this is the transfer.
        """
        if self._panel_flush is not None:
            self._panel_flush()

    def wake_screen(self, force=False):
        # One guard for every wake source — including the message/announce
        # wakes tdeck_node.py fires from the RX path. Only unlocking forces.
        if self.locked and not force:
            return
        if not self._screen_on:
            if self._bl:
                self._bl.value(1)
            self._screen_on = True
            self.dirty = True
            self._cache = [''] * CACHE_ROWS
            if self._kbd_tracks_screen() and self._kbd_bl and not self._kbd_bl_lit:
                self._drive_kbd_backlight(True)
        self._last_activity = time.ticks_ms()

    def sleep_screen(self):
        if self._screen_on:
            if self._bl:
                self._bl.value(0)
            self._screen_on = False
            if self._kbd_tracks_screen() and self._kbd_bl_lit:
                self._drive_kbd_backlight(False)

    def lock(self):
        """Blank the screen and ignore input until the next trackball click.
        Sounds, unread counters and the radio keep running."""
        self.locked = True
        self.sleep_screen()

    def unlock(self):
        self.locked = False
        self.wake_screen(force=True)
        self._cache = [''] * CACHE_ROWS  # row caches are stale after the screen blanked
        self.dirty = True

    # --- Drawing helpers ---

    @staticmethod
    def _tb(text):
        """Convert text to glyph-index bytes for the display driver (one
        glyph per byte — a str would be consumed as UTF-8 and mangle
        anything > 0x7F). ASCII and raw CP437 positions (like the \\xfb
        checkmark) pass through; Cyrillic transcodes via _CYR into the
        vga2_8x16_cp866 slots; anything unmapped renders as '?'."""
        if isinstance(text, str):
            return bytes([o if o < 0x80 else _SLOT_MAP.get(o, o if o < 0x100 else 0x3F)
                          for o in [ord(c) for c in text]])
        return text

    @staticmethod
    def _marker_span(text, keyword):
        """Locate a '[keyword...]' marker in a chat row and return
        (start_col, length). Tolerates trailing metadata like '[voice 3s]'.
        Returns (-1, 0) when absent."""
        p = text.find("[" + keyword)
        if p < 0:
            return (-1, 0)
        e = text.find("]", p)
        if e < 0:
            return (p, len(text) - p)
        return (p, e - p + 1)

    def _draw_row_cached(self, idx, text, y, fg, bg=None):
        """Draw row only if content changed. Returns True if drawn.

        The stored key for an empty row is ' ' (never ''): '' doubles as
        the invalidation marker, and if it also matched empty text, blank
        rows would be skipped right after a cache wipe — leaving stale
        pixels from a previous page (e.g. radio stats rows under a shorter
        settings menu)."""
        key = text or ' '
        if self._cache[idx] == key:
            return False
        self._cache[idx] = key
        self._row(text, y, fg, bg)
        return True

    def _row(self, text, y, fg, bg=None):
        """Draw a row and erase to full width — overwrites old content, no
        flicker. Glyph compositing in the C driver costs ~220us per cell,
        so padding with trailing spaces made every row pay for all COLS
        cells; drawing the bare text and erasing the tail with fill_rect
        (SPI-only, ~6x cheaper than space glyphs) roughly halves a typical
        scroll frame."""
        bg = bg or self.BG_DARK
        g = self._tb(text[:COLS].rstrip()) if text else b''
        if g:
            self.tft.text(self.font, g, 0, y, fg, bg)
        w = len(g) * CHAR_W
        if w < SCREEN_W:
            self.tft.fill_rect(w, y, SCREEN_W - w, 16, bg)

    def _sdraw(self, text, x, y, fg, bg):
        """Small-font text with its top at y."""
        if self._small:
            self._small.draw(self.tft, self._tb(text), x, y, fg, bg)
        else:
            self.tft.text(self.sfont, self._tb(text), x, y, fg, bg)

    def _stext(self, text, x, y, fg, bg=None):
        """Small-font text vertically centred in the 16px row at y."""
        self._sdraw(text, x, y + (CHAR_H - self.SH) // 2, fg, bg or self.BG_DARK)

    def _bitmap(self, icon, x, y, c):
        for dx, dy, w in icon[2]:
            self.tft.fill_rect(x + dx, y + dy, w, 1, c)

    def _pill(self, n, xr, y, bg):
        """Magenta unread-count pill ending at x=xr in the row at y; returns
        its width. Two digits max (99)."""
        t = str(min(n, 99))
        w = len(t) * self.SW + 2
        x = xr - w
        top = y + (CHAR_H - self.SH) // 2
        self.tft.fill_rect(x, top, w, self.SH, self.NEON_MAG)
        self._sdraw(t, x + 1, top, self.BG_DARK, self.NEON_MAG)
        for cx in (x, x + w - 1):          # rounded ends
            self.tft.fill_rect(cx, top, 1, 1, bg)
            self.tft.fill_rect(cx, top + self.SH - 1, 1, 1, bg)
        return w

    def _text(self, text, x, y, fg, bg=None):
        """Draw text at pixel position."""
        self.tft.text(self.font, self._tb(text), x, y, fg, bg or self.BG_DARK)

    def _draw_input_line(self, inp):
        """Draw the '> ...' input at the bottom, showing the tail of a long
        buffer with a '<' continuation marker so typing past the visible
        width stays visible (the caret is always the last cell)."""
        avail = COLS - 3  # cols after "> ", minus 1 for the caret
        if len(inp) > avail:
            visible = "<" + inp[-(avail - 1):]
        else:
            visible = inp
        text_padded = _pad(visible + "_", COLS - 2)
        self.tft.text(self.font, "> ", 0, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
        self.tft.text(self.font, self._tb(text_padded), 2 * CHAR_W, INPUT_Y,
                      self.NEON_CYAN, self.BG_DARK)

    def _draw_frame(self):
        """Neon body frame — top/bottom rails + short corner arms. Drawn last
        each redraw (over content) so it stays crisp on every screen and the
        corners never get chewed by full-width rows or the scrollbar."""
        _cx = self.NEON_CYAN
        _L = 12  # corner arm length
        _top = NAV_H                  # right under the navbar, no gap
        _bot = BODY_Y + BODY_ROWS * CHAR_H + 1
        self.tft.fill_rect(0, _top, SCREEN_W, 1, _cx)
        self.tft.fill_rect(0, _bot, SCREEN_W, 1, _cx)
        self.tft.fill_rect(0, _top, 1, _L, _cx)
        self.tft.fill_rect(SCREEN_W - 1, _top, 1, _L, _cx)
        self.tft.fill_rect(0, _bot - _L + 1, 1, _L, _cx)
        self.tft.fill_rect(SCREEN_W - 1, _bot - _L + 1, 1, _L, _cx)

    # --- Navbar ---

    def draw_navbar(self):
        # Above ~4.3V the pack is on USB/charge — a LiPo never rests that
        # high, so a bare voltage there reads like a bug. Show "USB" instead.
        if self.bat_v >= 4.3:
            bat_v_str = "USB"
        elif self.bat_v <= 0.0:
            # Nothing has reported a voltage yet. "0.0V" reads as a flat pack;
            # this reads as what it is, an unknown.
            bat_v_str = "--"
        else:
            bat_v_str = "{:.1f}V".format(self.bat_v)
        name = self.node_name[:10]
        ann = ">>>" if (self.announce_flash and time.ticks_diff(time.ticks_ms(), self.announce_flash) < 2000) else ""

        # Center: transfer / voice status as text, else an interface icon --
        # globe for TCP, antenna for LoRa (magenta when the radio failed) --
        # with the SNR; then the mesh clock once time is synced.
        icon = None
        if self._audio_status:
            center = "[" + self._audio_status + "]"
        elif self.transfer_progress:
            rcv, tot = self.transfer_progress
            center = "RX " + str(rcv) + "/" + str(tot)
        elif self._tcp_enabled:
            icon, center = _GLOBE, ""
        elif not self.lora_online:
            icon, center = _ANTENNA, "FAIL"
        elif self.rssi is not None:
            icon, center = _ANTENNA, "snr " + str(self.snr or 0)
        else:
            icon, center = _ANTENNA, ""
        if _clock_valid():
            center = (center + "  " if center else "") + _fmt_clock()

        right_str = (ann + " " if ann else "") + name
        center_color = self.NEON_MAG if (not self._tcp_enabled and not self.lora_online) else self.DIM_CYAN
        hb = self.HEADER_BG
        sw = self.SW
        ny = (NAV_H - self.SH) // 2
        left_end = 4 * CHAR_W + 5 * sw          # battery icon + voltage

        bl = 3 if self.bat_v > 3.9 else (2 if self.bat_v > 3.6 else (1 if self.bat_v > 3.3 else 0))
        bat_key = bat_v_str + str(bl)
        mid_key = ("G" if icon is _GLOBE else "A" if icon else "") + center + right_str

        if self._cache[0] == '':
            self._nav_bat_cache = ''
            self._nav_mid_cache = ''
            self.tft.fill_rect(0, 0, SCREEN_W, NAV_H, hb)

        # Battery section (icon + voltage text)
        if self._nav_bat_cache != bat_key:
            self._nav_bat_cache = bat_key
            gr = self.NEON_GREEN
            dm = self.BG_DARK if self._mono else self.DIM_CYAN
            self.tft.fill_rect(1, 4, 26, 12, gr)
            self.tft.fill_rect(2, 5, 24, 10, hb)
            self.tft.fill_rect(27, 7, 2, 6, gr)
            self.tft.fill_rect(3,  6, 7, 8, gr if bl >= 1 else dm)
            self.tft.fill_rect(11, 6, 7, 8, gr if bl >= 2 else dm)
            self.tft.fill_rect(19, 6, 7, 8, gr if bl >= 3 else dm)
            self.tft.fill_rect(4 * CHAR_W, 0, 5 * sw, NAV_H, hb)
            self._sdraw(bat_v_str, 4 * CHAR_W, ny, self.NEON_GREEN, hb)

        # Center + right (announce flash, node name): repainted together, as
        # the name's length decides how much room the center gets.
        if self._nav_mid_cache != mid_key:
            self._nav_mid_cache = mid_key
            self.tft.fill_rect(left_end, 0, SCREEN_W - left_end, NAV_H, hb)
            right_x = SCREEN_W - sw - len(right_str) * sw     # one-char right margin
            self._sdraw(right_str, right_x, ny, self.NEON_CYAN, hb)
            if ann:
                self._sdraw(ann, right_x, ny, self.NEON_MAG, hb)
            lo, hi = left_end + sw, right_x - sw
            iw = icon[0] + 4 if icon else 0
            fit = max(0, (hi - lo - iw) // sw)
            center = center[:fit]
            cw = iw + len(center) * sw
            x = max(lo, min((SCREEN_W - cw) // 2, hi - cw))
            if icon:
                self._bitmap(icon, x, (NAV_H - icon[1]) // 2, center_color)
            if center:
                self._sdraw(center, x + iw, ny, center_color, hb)

        self._cache[0] = bat_key + mid_key

    # --- Node list screen ---

    def _tab_labels(self):
        """Tab labels sized to the panel.

        The wide, padded form (" MSG(n) NET(n) RNSH(n) RRC(n) ") is 33
        columns at the widest single-digit counts and still only 38 at two
        digits, so the v1's 40 columns always fit it. The Pro's panel is
        only 30 columns wide, which the wide form never fits, so narrow
        panels fall back to a compact form that drops the padding, the
        parentheses and the longer "RNSH" name. COLS is read here rather
        than captured, since the Pro sets a different screen geometry at
        import.
        """
        un = 0
        for v in self.unread.values():
            un += v
        counts = (str(len(self._peer_keys)) + ("*" if un else ""),
                  str(len(self._node_keys)),
                  str(len(self._shell_keys)),
                  str(len(self._rrc_keys)))
        wide = (" MSG(" + counts[0] + ") ", " NET(" + counts[1] + ") ",
                " RNSH(" + counts[2] + ") ", " RRC(" + counts[3] + ") ")
        if sum(len(x) for x in wide) <= COLS:
            return wide
        return (" MSG" + counts[0], " NET" + counts[1],
                " SSH" + counts[2], " RRC" + counts[3] + " ")

    def _draw_tab_bar(self):
        """MSG/NET/RNSH/RRC tab bar — first body row of the node screen."""
        tabs = self._tab_labels()
        cache_key = str(self.node_tab) + ("F" if self._find else "") + "".join(tabs)
        if self._cache[1] == cache_key:
            return
        self._cache[1] = cache_key
        bg = self.BG_DARK
        top = NAV_H + 1                  # just under the frame's top rail
        line = BODY_Y + CHAR_H - 1
        ly = BODY_Y - 2                  # labels centred in top..line
        sw = SCREEN_W // len(tabs)
        self.tft.fill_rect(0, top, SCREEN_W, line - top + 1, bg)
        for i, label in enumerate(tabs):
            label = label.strip()
            x = i * sw
            tx = x + (sw - len(label) * CHAR_W) // 2
            if i == self.node_tab:
                # In Find the tab runs straight into the band below it.
                self.tft.fill_rect(x, top, sw, line - top + (1 if self._find else 0), self.TAB_BG)
                self.tft.text(self.font, label, tx, ly, self.NEON_GREEN, self.TAB_BG)
            else:
                self.tft.text(self.font, label, tx, ly, self.DIM_CYAN, bg)
        if not self._find:
            self.tft.fill_rect(0, line, SCREEN_W, 1, self.DIM_CYAN)

    def _draw_list_rows(self, keys, table, scroll, sel_idx, show_unread, empty_lines):
        """Shared list body for both tabs: 11 rows below the tab bar."""
        _rows = BODY_ROWS - 1
        if not keys:
            _mid = _rows // 2 - 1
            for i in range(_rows):
                y = BODY_Y + (i + 1) * CHAR_H
                if i == _mid:
                    self._draw_row_cached(i + 2, empty_lines[0].center(COLS), y, self.NEON_CYAN)
                elif i == _mid + 1:
                    self._draw_row_cached(i + 2, empty_lines[1].center(COLS), y, self.DIM_CYAN)
                else:
                    self._draw_row_cached(i + 2, "", y, self.NEON_CYAN)
            return
        visible = keys[scroll:scroll + _rows]
        sw = self.SW
        hash_x = SCREEN_W - 8 - 8 * sw          # small-font hash, 8px right margin
        for i in range(_rows):
            y = BODY_Y + (i + 1) * CHAR_H
            ci = i + 2
            if i < len(visible):
                key = visible[i]
                entry = table[key]
                name = _ascii(entry.get("name") or "?")
                fav = entry.get("fav") == True
                hsh = key.hex()[:8]
                uc = self.unread.get(key, 0) if show_unread else 0
                pw = (len(str(min(uc, 99))) * sw + 2 + 6) if uc else 0
                name = name[:(hash_x - 6 - pw - 2 * CHAR_W) // CHAR_W]
                sel = scroll + i == sel_idx
                cache_key = ('\x01' if sel else '') + name + '\x00' + hsh + str(uc) + ('*' if fav else '')
                if self._cache[ci] == cache_key:
                    continue
                self._cache[ci] = cache_key
                bg = self.SEL_BG if sel else self.BG_DARK
                self._row("  " + name, y, self.YELLOW if sel else self.NEON_CYAN, bg)
                self._stext(hsh, hash_x, y, self.YELLOW if sel else self.DIM_CYAN, bg)
                if uc:
                    self._pill(uc, hash_x - 6, y, bg)
                if fav:
                    # small orange star in the left slot
                    self._bitmap(_STAR, 3, y + (CHAR_H - _STAR[1]) // 2, self.ORANGE)
                elif sel:
                    # Accent bar in the blank left margin
                    self.tft.fill_rect(0, y, 3, CHAR_H, self.NEON_MAG)
            else:
                self._draw_row_cached(ci, "", y, self.NEON_CYAN)

    def draw_node_list(self):
        self._draw_tab_bar()
        _rows = BODY_ROWS - 1
        if self._find:
            self._draw_find()
            return
        if self.node_tab == TAB_MSG:
            self._draw_list_rows(self._peer_keys, self.peers, self.node_scroll,
                                 self.selected_idx, True,
                                 ("No peers yet.", "Waiting for announces..."))
            _total = len(self._peer_keys)
            _scroll = self.node_scroll
        elif self.node_tab == TAB_NET:
            self._draw_list_rows(self._node_keys, self.nomad_nodes, self.net_scroll,
                                 self.net_idx, False,
                                 ("No nodes yet.", "Waiting for node announces..."))
            _total = len(self._node_keys)
            _scroll = self.net_scroll
        elif self.node_tab == TAB_RRC:
            self._draw_list_rows(self._rrc_keys, self.rrc_hubs, self._rrc_scroll,
                                 self._rrc_idx, False,
                                 ("No RRC hubs.", "(m) to find or add one"))
            _total = len(self._rrc_keys)
            _scroll = self._rrc_scroll
        else:  # TAB_SSH
            self._draw_list_rows(self._shell_keys, self.shell_nodes, self.ssh_scroll,
                                 self.ssh_idx, False,
                                 ("No rnsh nodes.", "(m) to find or add one"))
            _total = len(self._shell_keys)
            _scroll = self.ssh_scroll

        # Scroll indicator on right edge (below the tab bar)
        _track_h = _rows * CHAR_H
        self.tft.fill_rect(SBAR_X, BODY_Y + CHAR_H, SBAR_W, _track_h, self.BG_DARK)
        if _total > _rows:
            _bar_h = max(6, _track_h * _rows // _total)
            _bar_y = BODY_Y + CHAR_H + _scroll * _track_h // _total
            self.tft.fill_rect(SBAR_X, _bar_y, SBAR_W, _bar_h, self.DIM_CYAN)

        # Footer hints — drawn once per state/tab change (frame is drawn
        # centrally by draw()).
        _nf_key = "NF" + str(self.node_tab)
        if self._cache[FOOT_SLOT] != _nf_key:
            self._cache[FOOT_SLOT] = _nf_key
            self.tft.fill_rect(0, INPUT_Y, SCREEN_W, CHAR_H, self.BG_DARK)
            # One footer for every tab, in the small font. (p)ing and (d)el
            # still work on MSG but go unadvertised: only urns nodes answer
            # probes, so ping times out on Sideband/MeshChat peers.
            sw = self.SW
            x = 0
            for k, rest in (("A", "nnc"), ("S", "etup"), ("F", "av"), ("M", "hash")):
                self._stext("(", x, INPUT_Y, self.DIM_CYAN)
                self._stext(k, x + sw, INPUT_Y, self.NEON_GREEN)
                self._stext(")" + rest, x + 2 * sw, INPUT_Y, self.DIM_CYAN)
                x += (len(rest) + 4) * sw
            self._foot_x = x
            self._route_cache = ''

        # Dynamic footer info, right-aligned with a one-char margin, clear of
        # the hints: transient ping result, else the selected entry's hops,
        # RSSI (peers) and last-seen, e.g. "2 hops -87dB 5min".
        info = ""
        entry = None
        if self.node_tab == TAB_MSG:
            if self.ping_status:
                info = self.ping_status
            elif self._peer_keys and self.selected_idx < len(self._peer_keys):
                entry = self.peers.get(self._peer_keys[self.selected_idx])
        elif self.node_tab == TAB_NET:
            if self._node_keys and self.net_idx < len(self._node_keys):
                entry = self.nomad_nodes.get(self._node_keys[self.net_idx])
        elif self.node_tab == TAB_RRC:
            if self._rrc_keys and self._rrc_idx < len(self._rrc_keys):
                entry = self.rrc_hubs.get(self._rrc_keys[self._rrc_idx])
        else:  # TAB_SSH
            if self._shell_keys and self.ssh_idx < len(self._shell_keys):
                entry = self.shell_nodes.get(self._shell_keys[self.ssh_idx])
        if entry:
            bits = []
            if entry.get("hops"):
                bits.append(_hops(entry["hops"]))
            if entry.get("rssi") is not None:
                bits.append(str(entry["rssi"]) + "dB")
            bits.append(_age(entry.get("seen")))
            info = " ".join(bits)
        sw = self.SW
        x0 = getattr(self, "_foot_x", 0) + sw
        room = (SCREEN_W - sw - x0) // sw
        if len(info) > room:
            info = info.replace("dB", "")
        info = info[:room]
        if self._route_cache != info:
            self._route_cache = info
            self.tft.fill_rect(x0, INPUT_Y, SCREEN_W - x0, CHAR_H, self.BG_DARK)
            self._stext(info, SCREEN_W - sw - len(info) * sw, INPUT_Y, self.DIM_CYAN)

    # --- Browser page view ---

    def draw_browser(self):
        # Header row: node name left, page path right (dim), separator
        title = "> " + _ascii(self.browser_title)[:20]
        path = self.browser_path
        if len(path) > 16:
            path = "..." + path[-13:]
        cache_key = title + path
        if self._cache[1] != cache_key:
            self._cache[1] = cache_key
            self.tft.text(self.font, self._tb(_pad(title)), 0, BODY_Y, self.NEON_CYAN, self.BG_DARK)
            self.tft.text(self.font, ">", 0, BODY_Y, self.NEON_GREEN, self.BG_DARK)
            px = (COLS - len(path) - 1) * CHAR_W
            self.tft.text(self.font, self._tb(path), px, BODY_Y, self.DIM_CYAN, self.BG_DARK)
        self.tft.fill_rect(0, BODY_Y + CHAR_H - 1, SCREEN_W, 1, self.DIM_CYAN)

        _rows = BODY_ROWS - 1
        lines = self.browser_lines
        max_scroll = max(0, len(lines) - _rows)
        if self.browser_scroll > max_scroll:
            self.browser_scroll = max_scroll
        visible = lines[self.browser_scroll:self.browser_scroll + _rows]
        if self.browser_cursor >= len(visible):
            self.browser_cursor = len(visible) - 1

        self._browser_link_rows = {}
        for i in range(_rows):
            y = BODY_Y + (i + 1) * CHAR_H
            ci = i + 2
            if i < len(visible):
                d = self.browser_scroll + i
                if d in self._browser_image_rows:
                    li, subrow = self._browser_image_rows[d]
                    img = self._page_images.get(li)
                    st = img["state"] if img else "failed"
                    ck = "img:%d:%d:%d:%s" % (self._page_gen, li, subrow, st)
                    if self._cache[ci] == ck:
                        continue
                    self._cache[ci] = ck
                    self._draw_image_row(li, subrow, y)   # defined in Task 8
                    continue
                spans = visible[i]
                for s in spans:
                    if s[4] is not None:
                        self._browser_link_rows[i] = s[4]
                        break
                hl = (i == self.browser_cursor)
                ck = str(self._page_gen) + ":" + str(self.browser_scroll + i) + (":h" if hl else "")
                if self._cache[ci] == ck:
                    continue
                self._cache[ci] = ck
                row_bg = self.SEL_BG if hl else self.BG_DARK
                self.tft.text(self.font, _pad(""), 0, y, self.NEON_CYAN, row_bg)
                for col, text, fg, bg, link in spans:
                    # span bg wins (e.g. h1 bar); default bg follows highlight
                    bg_use = row_bg if bg == self.BG_DARK else bg
                    self.tft.text(self.font, self._tb(text), col * CHAR_W, y, fg, bg_use)
                # Cursor accent bar (magenta on link rows, dim otherwise)
                if hl:
                    ac = self.NEON_MAG if i in self._browser_link_rows else self.DIM_CYAN
                    self.tft.fill_rect(0, y, 3, CHAR_H, ac)
            else:
                self._draw_row_cached(ci, "", y, self.NEON_CYAN)

        # Scroll indicator on right edge (below the header)
        _track_h = _rows * CHAR_H
        self.tft.fill_rect(SBAR_X, BODY_Y + CHAR_H, SBAR_W, _track_h, self.BG_DARK)
        if len(lines) > _rows:
            _bar_h = max(6, _track_h * _rows // len(lines))
            _bar_y = BODY_Y + CHAR_H + self.browser_scroll * _track_h // len(lines)
            self.tft.fill_rect(SBAR_X, _bar_y, SBAR_W, _bar_h, self.DIM_CYAN)

        # Footer: transient status/error, link position, else key hints
        if self.browser_status:
            foot = self.browser_status[:COLS]
            fcol = self.NEON_MAG
        else:
            _li = self._browser_link_rows.get(self.browser_cursor)
            if _li is not None and self.browser_links:
                foot = "link %d/%d  click=open  <back" % (_li + 1, len(self.browser_links))
            else:
                foot = "(r)load (n)ext (p)rev  click  <back"
            fcol = self.DIM_CYAN
        if self._cache[FOOT_SLOT] != foot:
            self._cache[FOOT_SLOT] = foot
            self.tft.text(self.font, self._tb(_pad(foot)), 0, INPUT_Y, fcol, self.BG_DARK)

    def _draw_image_row(self, li, subrow, y):
        img = self._page_images.get(li)
        self.tft.fill_rect(0, y, SCREEN_W, CHAR_H, self.BG_DARK)
        if img is None:
            return
        if img["state"] == "idle":
            img["state"] = "loading"
            if self.on_fetch_page_image:
                self.on_fetch_page_image(li, img["src"])
        n = BODY_ROWS - 1
        mid = n // 2
        if img["state"] in ("idle", "loading") and subrow == mid:
            self._center_text("loading image...", y, self.DIM_CYAN)
        elif img["state"] == "failed" and subrow == mid:
            self._center_text("[image failed]", y, self.NEON_MAG)
        if img["state"] == "ready" and img["buf"] is not None:
            n = BODY_ROWS - 1
            block_h = n * CHAR_H
            voff = (block_h - img["h"]) // 2          # centre vertically
            row_top = subrow * CHAR_H
            top = max(row_top, voff)
            bot = min(row_top + CHAR_H, voff + img["h"])
            if bot > top:
                src_y = top - voff
                strip_h = bot - top
                xoff = (SCREEN_W - img["w"]) // 2
                off = src_y * img["w"] * 2
                strip = memoryview(img["buf"])[off:off + strip_h * img["w"] * 2]
                self.tft.blit_buffer(strip, xoff, y + (top - row_top),
                                     img["w"], strip_h)

    # --- Chat screen ---

    def _invalidate_chat_lines(self):
        """Invalidate cached chat lines — call when messages change."""
        self._chat_lines_cache = None

    def _build_chat_lines(self):
        """Build word-wrapped display lines for current chat.
        Returns list of (is_mine, text, is_first, suffix_len, status, msg_id, has_image, has_audio)"""
        if self.selected_peer is None:
            return []

        # Return cached result if still valid
        if self._chat_lines_cache is not None and self._chat_lines_peer == self.selected_peer:
            return self._chat_lines_cache

        # 1 pending, 2 delivered, 3 failed, 4 queued (route discovery),
        # 5 sent — awaiting delivery proof (DIRECT transfers)
        _suffix_map = {1: " ..", 2: " \xfb", 3: " !", 4: " ~", 5: " >"}
        msgs = self.chat_history.get(self.selected_peer, [])
        lines = []
        for msg in msgs:
            is_mine = msg[0]
            text = msg[1]
            status = msg[3]
            has_image = msg[4]
            has_audio = msg[5]
            mid = msg[6]
            if is_mine:
                prefix = "me> "
            else:
                peer = self.peers.get(self.selected_peer)
                pname = _ascii(peer.get("name") or "?")[:8]
                prefix = pname + "> "
            # Reserve space for suffix on last wrapped line
            suffix = _suffix_map.get(status, "") if is_mine else ""
            wrapped = self._wrap_text(prefix + text, COLS - len(suffix) if suffix else COLS)
            if suffix:
                wrapped[-1] = wrapped[-1] + suffix
            for j, wl in enumerate(wrapped):
                # status_suffix_len: how many chars of suffix on this line
                slen = len(suffix) if (suffix and j == len(wrapped) - 1) else 0
                lines.append((is_mine, wl, j == 0, slen, status, mid, has_image, has_audio))
        self._chat_lines_cache = lines
        self._chat_lines_peer = self.selected_peer
        return lines

    def draw_chat(self):
        # Chat header row: "< PeerName" left, "[hash]" right
        peer = self.peers.get(self.selected_peer)
        pname = _ascii(peer.get("name") or "?")[:20] if peer else "?"
        phash = "[" + self.selected_peer.hex()[:8] + "]"
        header = "< " + pname
        cache_key = header + phash
        if self._cache[1] != cache_key:
            self._cache[1] = cache_key
            self.tft.text(self.font, self._tb(_pad(header)), 0, BODY_Y, self.NEON_CYAN, self.BG_DARK)
            self.tft.text(self.font, "<", 0, BODY_Y, self.NEON_GREEN, self.BG_DARK)
            hx = (COLS - len(phash) - 1) * CHAR_W
            self.tft.text(self.font, phash, hx, BODY_Y, self.DIM_CYAN, self.BG_DARK)
        # Separator under header
        self.tft.fill_rect(0, BODY_Y + CHAR_H - 1, SCREEN_W, 1, self.DIM_CYAN)

        lines = self._build_chat_lines()
        _chat_rows = BODY_ROWS - 1  # 11 rows for messages

        # Clamp scroll. Max is total-_chat_rows so the top-most position still
        # fills the window with lines[0:_chat_rows]; scrolling further would
        # shrink the view and make messages vanish off the bottom.
        total = len(lines)
        max_scroll = max(0, total - _chat_rows)
        if self.chat_scroll > max_scroll:
            self.chat_scroll = max_scroll

        # Apply scroll — show last N lines, scrollable up
        view_end = max(0, total - self.chat_scroll)
        view_start = max(0, view_end - _chat_rows)
        visible = lines[view_start:view_end]

        # Track which visible lines have images/audio
        self._visible_image_lines = {}  # display_row -> msg_id
        self._visible_audio_lines = {}  # display_row -> msg_id
        self._visible_msg_lines = {}    # display_row -> msg_id (all messages)

        # Clamp chat_cursor to visible range
        if self.chat_cursor >= len(visible):
            self.chat_cursor = len(visible) - 1

        _status_color = {1: self.YELLOW, 2: self.NEON_GREEN, 3: self.NEON_MAG,
                         4: self.DIM_CYAN, 5: self.YELLOW}
        for i in range(_chat_rows):
            y = BODY_Y + (i + 1) * CHAR_H
            ci = i + 2  # cache index (1=header, 2..12=chat rows)
            if i < len(visible):
                is_mine, text, is_first, slen, status, mid, has_image, has_audio = visible[i]

                # Track image/audio lines for click detection
                if has_image and is_first:
                    self._visible_image_lines[i] = mid
                if has_audio and is_first:
                    self._visible_audio_lines[i] = mid
                self._visible_msg_lines[i] = mid

                is_highlighted = (i == self.chat_cursor)
                in_cache = (self.selected_peer, mid) in self._image_cache if has_image and is_first else True

                # Cache check: skip row if text and highlight state unchanged
                ck = text + ("\x01" if is_highlighted else "\x00")
                if self._cache[ci] == ck:
                    continue
                self._cache[ci] = ck

                row_bg = self.SEL_BG if is_highlighted else self.BG_DARK

                padded = _pad(text)
                self.tft.text(self.font, self._tb(padded), 0, y, self.BODY_FG, row_bg)
                if is_first:
                    if is_mine:
                        self.tft.text(self.font, text[:4], 0, y, self.NEON_GREEN, row_bg)
                    else:
                        gt = text.find(">")
                        if gt >= 0:
                            self.tft.text(self.font, self._tb(text[:gt + 1]), 0, y, self.NEON_MAG, row_bg)
                    # Image rendering
                    if has_image:
                        img_pos, img_len = self._marker_span(text, "image")
                        if img_pos >= 0:
                            seg = text[img_pos:img_pos + img_len]
                            if not in_cache:
                                # Expired: dim + strikethrough
                                self.tft.text(self.font, self._tb(seg), img_pos * CHAR_W, y,
                                              self.DIM_CYAN, row_bg)
                                self.tft.fill_rect(img_pos * CHAR_W, y + 7,
                                                   img_len * CHAR_W, 1, self.DIM_CYAN)
                            elif is_highlighted:
                                # Highlighted: yellow + accent bar
                                self.tft.text(self.font, self._tb(seg), img_pos * CHAR_W, y,
                                              self.YELLOW, row_bg)
                                self.tft.fill_rect(0, y, 3, CHAR_H, self.NEON_MAG)
                            else:
                                # Normal: magenta
                                self.tft.text(self.font, self._tb(seg), img_pos * CHAR_W, y,
                                              self.NEON_MAG, row_bg)
                    # Voice rendering
                    if has_audio:
                        vpos, vlen = self._marker_span(text, "voice")
                        if vpos >= 0:
                            vc = self.YELLOW if is_highlighted else self.NEON_GREEN
                            self.tft.text(self.font, self._tb(text[vpos:vpos + vlen]),
                                          vpos * CHAR_W, y, vc, row_bg)
                            if is_highlighted:
                                self.tft.fill_rect(0, y, 3, CHAR_H, self.NEON_GREEN)
                if slen > 0 and status in _status_color:
                    sx = (len(text) - slen) * CHAR_W
                    self.tft.text(self.font, self._tb(text[-slen:]), sx, y, _status_color[status], row_bg)
            else:
                self._draw_row_cached(ci, "", y, self.NEON_CYAN)

        # Scroll indicator on right edge (cached)
        if total > _chat_rows:
            _track_h = _chat_rows * CHAR_H
            _track_y = BODY_Y + CHAR_H
            _bar_h = max(6, _track_h * _chat_rows // total)
            _pos = max_scroll - self.chat_scroll if max_scroll else 0
            _bar_y = _track_y + _pos * (_track_h - _bar_h) // max(1, max_scroll)
            _sk = str(_bar_y) + ":" + str(_bar_h)
            if self._cache[FOOT_SLOT] != _sk:
                self._cache[FOOT_SLOT] = _sk
                self.tft.fill_rect(SBAR_X, _track_y, SBAR_W, _track_h, self.BG_DARK)
                self.tft.fill_rect(SBAR_X, _bar_y, SBAR_W, _bar_h, self.DIM_CYAN)

        # Input line drawn by draw() after draw_chat() returns

    def draw_input(self):
        inp = self.cmd_buf.decode()
        if inp:
            ik = "> " + inp
            if self._cache[INPUT_SLOT] == ik:
                return
            self._cache[INPUT_SLOT] = ik
            self._draw_input_line(inp)
        else:
            _on_image = self.chat_cursor >= 0 and self.chat_cursor in self._visible_image_lines
            # Timestamp of the highlighted message (empty pre-time-sync)
            _ts_txt = ""
            if self.chat_cursor >= 0 and self.chat_cursor in self._visible_msg_lines:
                try:
                    _hist = self.chat_history[self.selected_peer]
                    _i = self._msg_index(_hist, self._visible_msg_lines[self.chat_cursor])
                    _ts_txt = _fmt_time(_hist[_i][2]) if _i >= 0 else ""
                except Exception:
                    _ts_txt = ""
            # Record hint only when not navigating messages (0 = mic key), and
            # only where there is a microphone to record with. on_record_start
            # is left unset by boards that have none -- the T-Deck Pro has no
            # ES7210 -- so offering the key there advertises a dead end.
            _show_rec = self.chat_cursor < 0 and self.on_record_start is not None
            ik = ("IMG" if _on_image else "BACK") + _ts_txt + ("R" if _show_rec else "")
            if self._cache[INPUT_SLOT] == ik:
                return
            self._cache[INPUT_SLOT] = ik
            self.tft.text(self.font, _pad("> _"), 0, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            if _ts_txt:
                self.tft.text(self.font, _pad(_ts_txt, 16), 6 * CHAR_W, INPUT_Y,
                              self.DIM_CYAN, self.BG_DARK)
            elif _show_rec:
                self.tft.text(self.font, "[", 4 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
                self.tft.text(self.font, "0", 5 * CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
                self.tft.text(self.font, "=rec]", 6 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            if _on_image:
                _hx = (COLS - 12) * CHAR_W
                self.tft.text(self.font, "[", _hx, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
                self.tft.text(self.font, "click", _hx + CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
                self.tft.text(self.font, "=view]", _hx + 6 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            else:
                _hx = (COLS - 12) * CHAR_W
                self.tft.text(self.font, "[", _hx, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
                self.tft.text(self.font, "bksp", _hx + CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
                self.tft.text(self.font, "=back]", _hx + 5 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)

    # --- Image viewer ---

    def _enter_image_view(self, mid):
        """Enter full-screen image view for the message with this id."""
        cache_key = (self.selected_peer, mid)
        jpeg_data = self._image_cache.get(cache_key)
        if jpeg_data is None:
            return  # image expired from cache
        self._viewing_image = jpeg_data
        self._image_drawn = False
        self._prev_image_state = self.state
        self.state = STATE_IMAGE
        self._state_change_ms = time.ticks_ms()
        self.dirty = True

    def view_page_image(self, data):
        """Full-screen a page image (raw bytes) fetched from a node's /media.
        Returns to whatever state we came from (the browser)."""
        self._viewing_image = data
        self._image_drawn = False
        self._prev_image_state = self.state
        self.state = STATE_IMAGE
        self._state_change_ms = time.ticks_ms()
        self.dirty = True

    def page_image_loaded(self, li, data):
        """Called by the browser when a /media fetch for inline image `li`
        completes. `data` is bytes, or None on failure."""
        img = self._page_images.get(li)
        if img is None:
            return
        if data is None:
            img["state"] = "failed"
        else:
            img["_raw"] = data
            self._decode_page_image(li)     # defined in Task 8
        self.dirty = True

    def _decode_page_image(self, li):
        """Decode img['_raw'] to an RGB565 buffer scaled to fit the page
        viewport (width x block height), preserving aspect ratio. Colour TFT
        only; the decoders are native modules present on that firmware."""
        img = self._page_images.get(li)
        if img is None:
            return
        data = img.pop("_raw", None)
        if not data:
            img["state"] = "failed"
            return
        n = BODY_ROWS - 1
        box_h = n * CHAR_H - 2 * IMG_GAP
        box_w = SBAR_X - 2                      # leave the scrollbar lane clear
        try:
            if len(data) > 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                import webp_fast_xtensawin as _dec
            elif len(data) > 2 and data[0] == 0xFF and data[1] == 0xD8:
                import tjpgd_fast_xtensawin as _dec
            else:
                img["state"] = "failed"
                return
            # The native decoder stretches to the exact target it's given, so
            # scale to fit the viewport ourselves — preserving aspect ratio and
            # never enlarging a small image past its native size.
            nsz = _img_native_size(data)
            if nsz:
                nw, nh = nsz
                scale = min(box_w / nw, box_h / nh, 1.0)
                tw, th = max(1, int(nw * scale)), max(1, int(nh * scale))
            else:
                tw, th = box_w, box_h
            w, h, buf = _dec.decode(data, tw, th)
            img["w"], img["h"], img["buf"], img["state"] = w, h, buf, "ready"
            self._img_lru_touch(li)
        except Exception:
            img["state"] = "failed"
        finally:
            gc.collect()

    def _img_lru_touch(self, li):
        """Keep at most MAX_CACHED_IMAGES decoded buffers; free the rest."""
        if li in self._img_lru:
            self._img_lru.remove(li)
        self._img_lru.append(li)
        while len(self._img_lru) > MAX_CACHED_IMAGES:
            old = self._img_lru.pop(0)
            oimg = self._page_images.get(old)
            if oimg is not None:
                oimg["buf"] = None
                oimg["state"] = "idle"   # re-fetch/re-decode if scrolled back to

    def _center_text(self, msg, y, fg):
        self.tft.text(self.font, msg, max(0, (SCREEN_W - len(msg) * CHAR_W) // 2),
                      y, fg, 0x0000)

    def draw_image(self, spi_acquire_display, spi_release_display):
        """Render full-screen image: decode+scale to 320x240 in C, blit."""
        if self._image_drawn or self._viewing_image is None:
            return
        gc.collect()
        spi_acquire_display()
        try:
            try:
                data = self._viewing_image
                # Pick the decoder from the magic bytes, never from the type
                # the sender declared — MeshChat labels JPEGs "jpeg" and
                # passes through whatever the browser's file picker reported.
                _dec = None
                if len(data) > 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                    import webp_fast_xtensawin as _dec
                elif len(data) > 2 and data[0] == 0xFF and data[1] == 0xD8:
                    import tjpgd_fast_xtensawin as _dec
                if _dec is None:
                    _fmt = ("PNG" if data[:4] == b'\x89PNG'
                            else "GIF" if data[:3] == b'GIF' else "This format")
                    self.tft.fill(0x0000)
                    self._center_text(_fmt + " is not supported", 104, self.NEON_MAG)
                    self._center_text("JPEG and WebP only", 128, self.DIM_CYAN)
                else:
                    # Decode and scale to screen size in native C
                    w, h, rgb565 = _dec.decode(data, SCREEN_W, SCREEN_H)
                    self.tft.fill(0x0000)
                    # Center the decoded image (may be smaller than the screen)
                    _ix = (SCREEN_W - w) // 2 if w < SCREEN_W else 0
                    _iy = (SCREEN_H - h) // 2 if h < SCREEN_H else 0
                    self.tft.blit_buffer(rgb565, _ix, _iy, w, h)
                    del rgb565
                    gc.collect()
            except ImportError:
                self.tft.fill(0x0000)
                self._center_text("No image decoder", 104, self.NEON_MAG)
                self._center_text("decoder natmod missing", 128, self.DIM_CYAN)
            except Exception:
                self.tft.fill(0x0000)
                self._center_text("Image decode error", 112, self.NEON_MAG)
            # Hint bar at the bottom — on the error screens too, or nothing on
            # display tells the user how to get back to the chat.
            self.tft.fill_rect(0, SCREEN_H - 18, SCREEN_W, 18, 0x0000)
            self._center_text("any key = back", SCREEN_H - 17, self.DIM_CYAN)
        finally:
            self._flush()
            spi_release_display()
        self._image_drawn = True

    def _exit_image_view(self):
        """Return from the image viewer to wherever it was entered from."""
        self._viewing_image = None
        self._image_drawn = False
        dest = getattr(self, "_prev_image_state", STATE_CHAT)
        self.state = dest
        if dest == STATE_CHAT:
            self.chat_cursor = -1
            self._cache = [''] * CACHE_ROWS
        self._prev_state = -1  # force full screen clear in draw()
        self._state_change_ms = time.ticks_ms()
        self.dirty = True

    # --- Text wrapping ---

    @staticmethod
    def _wrap_text(text, width):
        """Simple word wrap. Returns list of strings."""
        if len(text) <= width:
            return [text]
        lines = []
        while text:
            if len(text) <= width:
                lines.append(text)
                break
            idx = text.rfind(" ", 0, width)
            if idx <= 0:
                idx = width
            lines.append(text[:idx])
            text = text[idx:].lstrip(" ")
        return lines

    # --- Favorite handling ---
    def _save_favorites(self, data, type=None):
        file = "/rns/"
        if None == type:
            return # Unknown favorite usecase
        elif "peer" == type:
            file += "peers.json"
        elif "node" == type:
            file += "nodes.json"
        elif "hub" == type:
            file += "hubs.json"
        elif "shell" == type:
            file += "shells.json"
        try:
            import json
            with open(file, 'w') as f:
                json.dump(data, f)
                return True
        except Exception as e:
            return False

    def _get_favorites(self, type=None):
        file = "/rns/"
        if None == type:
            return {} # Unknown favorite usecase
        elif "peer" == type:
            file += "peers.json"
        elif "node" == type:
            file += "nodes.json"
        elif "hub" == type:
            file += "hubs.json"
        elif "shell" == type:
            file += "shells.json"
        try:
            import json
            with open(file, 'r') as f:
                return json.load(f)
        except Exception as e:
            return {}

    def _favorite_key_exists(self, key, type=None):
        if "peer" == type:
            if key in self._peer_keys:
                return True
        elif "node" == type:
            if key in self._node_keys:
                return True
        elif "hub" == type:
            if key in self._rrc_keys:
                return True
        elif "shell" == type:
            if key in self._shell_keys:
                return True
        return False

    def _favorite_add_entity(self, key, entity, type=None):
        if "peer" == type:
            self.add_peer(key, name=entity.get("name"), fav=True)
        elif "node" == type:
            self.add_nomad_node(key, entity.get("name"), fav=True)
        elif "hub" == type:
            self.add_rrc_hub(key, entity.get("name"), fav=True)
        elif "shell" == type:
            self.add_shell_node(key, entity.get("name"), fav=True)
        else:
            return False # Unknown favorite usecase
        return True

    def _load_favorite(self, type=None):
        if type not in ["peer", "node", "hub", "shell"]:
            return False # Unknown favorite usecase
        favs = self._get_favorites(type)
        for i in range(MAX_FAVORITES):
            try:
                entity = favs.get(type + "_" + str(i))
                if None != entity:
                    ek = favs.get(type + "Key_" + str(i))
                    if None != ek:
                        if self._favorite_key_exists(bytes.fromhex(ek), type):
                            continue # Entity already exists
                        self._favorite_add_entity(bytes.fromhex(ek), entity, type)
                    else:
                        # Something is wrong, discard favorite
                        favs[type + "_" + str(i)] = None
                        favs[type + "Key_" + str(i)] = None
                        self._save_favorites(favs, type)
                else:
                    continue # Slot empty
            except Exception as e:
                return

    def load_favorites(self):
        for type in ["peer", "node", "hub", "shell"]:
            self._load_favorite(type)

    def _favorite(self, type=None):
        if "peer" == type:
            if not self._peer_keys or 0 > self.selected_idx or len(self._peer_keys) <= self.selected_idx:
                return False # Unknown peer
            selected_key = self._peer_keys[self.selected_idx]
        elif "node" == type:
            if not self._node_keys or 0 > self.net_idx or len(self._node_keys) <= self.net_idx:
                return False # Unknown peer
            selected_key = self._node_keys[self.net_idx]
        elif "hub" == type:
            if not self._rrc_keys or 0 > self._rrc_idx or len(self._rrc_keys) <= self._rrc_idx:
                return False # Unknown hub
            selected_key = self._rrc_keys[self._rrc_idx]
        elif "shell" == type:
            if not self._shell_keys or 0 > self.ssh_idx or len(self._shell_keys) <= self.ssh_idx:
                return False # Unknown shell
            selected_key = self._shell_keys[self.ssh_idx]
        else:
            return False # Unknown type
        favs = self._get_favorites(type)
        empty_slot_idx = -1
        for i in range(MAX_FAVORITES):
            slot = favs.get(type + "Key_" + str(i))
            if slot == None:
                empty_slot_idx = i
            elif slot == selected_key.hex():
                # Already a favorite, unfavorite it
                if "peer" == type:
                    self.peers[selected_key]["fav"] = False
                elif "node" == type:
                    self.nomad_nodes[selected_key]["fav"] = False
                elif "hub" == type:
                    self.rrc_hubs[selected_key]["fav"] = False
                elif "shell" == type:
                    self.shell_nodes[selected_key]["fav"] = False
                else:
                    return False # Unknown error
                favs[type + "Key_" + str(i)] = None
                favs[type + "_" + str(i)] = None
                self._save_favorites(favs, type)
                self.dirty = True
                return True
        if 0 <= empty_slot_idx and empty_slot_idx < MAX_FAVORITES:
            # Empty slot, add to favorite
            favs[type + "Key_" + str(empty_slot_idx)] = selected_key.hex()
            if "peer" == type:
                self.peers[selected_key]["fav"] = True
                favs[type + "_" + str(empty_slot_idx)] = self.peers.get(selected_key)
            elif "node" == type:
                self.nomad_nodes[selected_key]["fav"] = True
                favs[type + "_" + str(empty_slot_idx)] = self.nomad_nodes.get(selected_key)
            elif "hub" == type:
                self.rrc_hubs[selected_key]["fav"] = True
                favs[type + "_" + str(empty_slot_idx)] = self.rrc_hubs.get(selected_key)
            elif "shell" == type:
                self.shell_nodes[selected_key]["fav"] = True
                favs[type + "_" + str(empty_slot_idx)] = self.shell_nodes.get(selected_key)
            else:
                return False # Unknown error
            self._save_favorites(favs, type)
            self.dirty = True
            return True       
        return False

    # --- Input handling ---

    # Screens that consume typed characters. Everywhere else, e and x are
    # free -- the node list binds a, s, b, m, d and p, the browser r, n, p, b,
    # g and G, and neither takes e or x -- so they can move the selection
    # without the alt layer. See _bare_nav_key().
    _TEXT_ENTRY_PAGES = (_SET_WIFI_PASS, _SET_TCP_HOST, _SET_NODE_NAME,
                         _SET_LORA_FREQ)

    def _accepting_text(self):
        """True where a letter key has to mean the letter."""
        if self.state == STATE_CHAT or self.state == STATE_SHELL:
            return True
        if self.state == STATE_RECORDING:
            return True
        if self.state == STATE_RRC_CHAT:
            # The room composer takes every letter, so e and x have to stay
            # letters here: without this a bare 'e' was eaten as a scroll
            # event before rrc_ui.handle_key() ever saw it, and "hex test"
            # arrived as "h tst".
            return True
        if self.state == STATE_RRC_ROOMS:
            # The hub console has no text field -- until (j) opens the
            # room-name prompt, which does. Room names carry e and x
            # (#mesh, #mesh-extra), so the prompt could not be typed at all.
            return self._rrc_prompt
        if self.state == STATE_NODES:
            # The node list is pure navigation -- except in Find, a text
            # field for names and hashes, where 'e' is a hex DIGIT. Roughly
            # seven in eight 16-byte hashes contain one, and a bare e used to
            # be eaten as a scroll event before the entry ever saw it.
            return self._find
        if self.state == STATE_SETTINGS:
            return self._settings_page in self._TEXT_ENTRY_PAGES
        return False

    def _bare_nav_key(self, key):
        """Map e/x to up/down on screens that are not accepting text.

        Navigation is on the alt layer -- alt+E and alt+X -- with a sticky
        modifier, so moving one row down a list is two keystrokes on a device
        whose main screen is a list. On a screen with no text field there is
        nothing for a bare e or x to collide with, so they move the selection
        there. Alt+E and alt+X keep working everywhere, including in chat and
        the shell, where a bare letter has to stay a letter.
        """
        if self._accepting_text():
            return None
        if key == b'e' or key == b'E':
            return "up"
        if key == b'x' or key == b'X':
            return "down"
        return None

    def handle_key(self, key):
        """Handle a keyboard key press. Returns True if UI needs redraw."""
        ch = key[0]

        if ch == 0:
            return False

        if self.state == STATE_IMAGE:
            # Any key closes the image, e and x included.
            self._exit_image_view()
            return True

        nav = self._bare_nav_key(key)
        if nav is not None:
            self.nav_event(nav)
            return True
        elif self.state == STATE_RECORDING:
            return self._handle_key_recording(ch, key)
        elif self.state == STATE_NODES:
            return self._handle_key_nodes(ch, key)
        elif self.state == STATE_SETTINGS:
            return self._handle_key_settings(ch, key)
        elif self.state == STATE_BROWSER:
            return self._handle_key_browser(ch, key)
        elif self.state == STATE_SHELL:
            return self._handle_key_shell(ch, key)
        elif self.state in (STATE_RRC_ROOMS, STATE_RRC_CHAT):
            import rrc_ui
            return rrc_ui.handle_key(self, ch, key)
        else:
            return self._handle_key_chat(ch, key)

    def _enter_chat(self):
        """Enter chat for the currently selected peer (trackball click only)."""
        if self._peer_keys and 0 <= self.selected_idx < len(self._peer_keys):
            self.selected_peer = self._peer_keys[self.selected_idx]
            self.unread.pop(self.selected_peer, None)
            self.chat_scroll = 0
            self.chat_cursor = -1
            self.cmd_buf = bytearray()
            self._invalidate_chat_lines()
            self.state = STATE_CHAT
            self._state_change_ms = time.ticks_ms()
            self.dirty = True

    def _handle_key_nodes(self, ch, key):
        # Find sub-mode captures all keys.
        if self._find:
            return self._handle_find_key(ch)
        if key == b'a' or key == b'A':
            if self.on_announce:
                self.on_announce()
            self.announce_flash = time.ticks_ms()
            self.dirty = True
            return True
        elif key == b's' or key == b'S':
            self._settings_page = _SET_MAIN
            self._settings_idx = 0
            self.state = STATE_SETTINGS
            self._state_change_ms = time.ticks_ms()
            self.dirty = True
            return True
        elif key == b'b' or key == b'B':
            # keyboard fallback for trackball left/right tab switch
            self._switch_tab((self.node_tab + 1) % N_TABS)
            return True
        elif key == b'm' or key == b'M':
            self._find_open()
            return True
        elif key == b'd' or key == b'D':
            self.delete_selected()
            return True
        elif key == b'p' or key == b'P':
            if (self.node_tab == TAB_MSG and self.on_ping and self._peer_keys
                    and self.selected_idx < len(self._peer_keys)):
                self.ping_status = "ping..."
                self.ping_pending = True
                self._ping_status_ms = time.ticks_ms()
                self.dirty = True
                self.on_ping(self._peer_keys[self.selected_idx])
            return True
        elif ch == 0x0D:  # Enter mirrors the trackball click
            if self.node_tab == TAB_MSG:
                self._enter_chat()
            elif self.node_tab == TAB_NET:
                self._open_selected_node()
            elif self.node_tab == TAB_RRC:
                import rrc_ui
                rrc_ui.open_selected_hub(self)
            else:  # TAB_SSH
                self._open_selected_shell()
            return True
        elif key == b'f' or key == b'F': # F to favorite the node
            if self.node_tab == TAB_MSG:
                self._favorite("peer")
            elif self.node_tab == TAB_NET:
                self._favorite("node")
            elif self.node_tab == TAB_RRC:
                self._favorite("hub")
            elif self.node_tab == TAB_SSH:
                self._favorite("shell")
            return True
        return False

    def _switch_tab(self, tab):
        """Switch MSG/NET/SSH tab on the node screen."""
        tab = tab % N_TABS
        if tab == self.node_tab:
            return
        self.node_tab = tab
        self._find = False
        if tab == TAB_NET and self.on_net_seed:
            try:
                self.on_net_seed()  # populate from persisted announces (once)
            except Exception:
                pass
        elif tab == TAB_SSH and self.on_shell_seed:
            try:
                self.on_shell_seed()
            except Exception:
                pass
        elif tab == TAB_RRC and self.on_rrc_seed:
            try:
                self.on_rrc_seed()
            except Exception:
                pass
        self._cache = [''] * CACHE_ROWS  # rows, tab bar and footer all change
        self.dirty = True

    def _open_selected_node(self):
        """NET tab click/Enter: fetch the selected node's index page."""
        if not (self._node_keys and 0 <= self.net_idx < len(self._node_keys)):
            return
        dest = self._node_keys[self.net_idx]
        node = self.nomad_nodes.get(dest)
        title = (node.get("name") if node else None) or dest.hex()[:8]
        # Enter the page view immediately with an empty page; the fetch
        # fills it in (or leaves an error in browser_status).
        self.show_page(title, "/page/index.mu", [], [], can_back=False)
        self.browser_status = "connecting..."
        if self.on_browse:
            self.on_browse(dest)

    # --- rnsh shell (SSH tab + STATE_SHELL) ---------------------------------

    def _open_selected_shell(self):
        """SSH tab click/Enter: connect to the selected rnsh listener."""
        if not (self._shell_keys and 0 <= self.ssh_idx < len(self._shell_keys)):
            return
        self._start_shell(self._shell_keys[self.ssh_idx])

    def _set_shell_font(self, notify=False):
        """(Re)build the shell row compositor for the current _shell_font_idx.
        With notify set, the new geometry is also pushed to the remote pty.

        Returns the _ShellFont, or None if no font module could be imported —
        draw_shell then falls back to the 8x16 tft.text() grid so a missing
        font file degrades the shell instead of breaking it."""
        self._shell_font = None
        gc.collect()
        for _ in range(len(_SHELL_FONTS)):
            name = _SHELL_FONTS[self._shell_font_idx]
            try:
                self._shell_font = _ShellFont(name, self.BODY_FG, self.BG_DARK)
                break
            except Exception as e:
                print("shell font", name, "unavailable:", e)
                self._shell_font_idx = (self._shell_font_idx + 1) % len(_SHELL_FONTS)
        sf = self._shell_font
        cols = sf.cols if sf else COLS
        rows = sf.rows if sf else BODY_ROWS
        self._shell_cache = [''] * rows
        if self._terminal is not None:
            self._terminal.cols = cols
        if notify and self.on_shell_resize:
            try:
                self.on_shell_resize(rows, cols)
            except Exception:
                pass
        self._shell_view = 0
        self._cache = [''] * CACHE_ROWS
        self.dirty = True
        return sf

    def _start_shell(self, dest_hash):
        """Enter the shell screen and kick off the connection."""
        self._shell_dest = dest_hash
        self._shell_connected = False
        self._shell_status = "connecting..."
        self._shell_input = bytearray()
        self._shell_at_line_start = True
        self._shell_escape = False
        self._shell_view = 0
        self._shell_menu = False
        self._terminal = None
        import terminal
        sf = self._set_shell_font()
        cols = sf.cols if sf else COLS
        rows = sf.rows if sf else BODY_ROWS
        self._terminal = terminal.Terminal(cols=cols)
        self.state = STATE_SHELL
        self._state_change_ms = time.ticks_ms()
        self._cache = [''] * CACHE_ROWS
        self.dirty = True
        if self.on_shell_connect:
            self.on_shell_connect(dest_hash, cols, rows)

    # --- Find: search heard addresses, or add one by hash (m, any tab) ---

    def _tab_list(self):
        """(keys, table) behind the current tab's list."""
        if self.node_tab == TAB_MSG:
            return self._peer_keys, self.peers
        if self.node_tab == TAB_NET:
            return self._node_keys, self.nomad_nodes
        if self.node_tab == TAB_RRC:
            return self._rrc_keys, self.rrc_hubs
        return self._shell_keys, self.shell_nodes

    def _find_open(self):
        """Snapshot every heard address of this tab's kind, plus any listed
        entry the snapshot lacks (e.g. one added by hash, still waiting on
        its announce)."""
        snap = None
        if self.on_contact_snapshot:
            try:
                snap = self.on_contact_snapshot(self.node_tab)
            except Exception:
                pass
        snap = list(snap or ())
        have = set(e[0] for e in snap)
        keys, table = self._tab_list()
        for k in keys:
            if k not in have:
                p = table[k]
                snap.append((k, p.get("name") or "?", p.get("seen", 0)))
        self._find_snap = snap
        self._find = True
        self._find_q = ""
        self._find_filter()
        self._cache = [''] * CACHE_ROWS

    def _find_filter(self):
        self._find_res = match_contacts(self._find_snap, self._find_q)
        self._find_sel = 0
        self._find_scroll = 0
        self.dirty = True

    def _find_close(self):
        self._find = False
        self._find_snap = []
        self._find_res = []
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def _find_rows(self):
        return BODY_ROWS - 3

    def _find_move(self, d):
        n = len(self._find_res)
        if not n:
            return
        self._find_sel = max(0, min(n - 1, self._find_sel + d))
        rows = self._find_rows()
        if self._find_sel < self._find_scroll:
            self._find_scroll = self._find_sel
        elif self._find_sel >= self._find_scroll + rows:
            self._find_scroll = self._find_sel - rows + 1
        self.dirty = True

    def _handle_find_key(self, ch):
        if ch == 0x1B:   # Esc
            self._find_close()
        elif ch == 0x08:   # Backspace
            if self._find_q:
                self._find_q = self._find_q[:-1]
                self._find_filter()
        elif ch == 0x0D:   # Enter
            self._find_pick()
        elif 0x20 <= ch < 0x7F and len(self._find_q) < 32:
            self._find_q += chr(ch)
            self._find_filter()
        return True

    def _find_pick(self):
        """Add the highlighted result -- or, with no result, a full 32-hex
        hash nobody has announced yet -- to this tab's list and select it.
        Opening it is the usual click away; nothing connects by surprise."""
        if self._find_res:
            dest, name = self._find_res[self._find_sel][:2]
        else:
            q = self._find_q.lower()
            if len(q) != 32 or not all(c in _HEX for c in q):
                return
            dest, name = bytes.fromhex(q), None
        keys, table = self._tab_list()
        new = dest not in table
        if new:
            if self.node_tab == TAB_MSG:
                self.add_peer(dest, name)
            elif self.node_tab == TAB_NET:
                self.add_nomad_node(dest, name)
            elif self.node_tab == TAB_RRC:
                self.add_rrc_hub(dest, name=name)
            else:
                self.add_shell_node(dest, name=name)
        self._find_close()
        i = keys.index(dest)
        rows = BODY_ROWS - 1
        if self.node_tab == TAB_MSG:
            self.selected_idx = i
            self.node_scroll = max(0, i - rows + 1) if i >= self.node_scroll + rows else min(self.node_scroll, i)
        elif self.node_tab == TAB_NET:
            self.net_idx = i
            self.net_scroll = max(0, i - rows + 1) if i >= self.net_scroll + rows else min(self.net_scroll, i)
        elif self.node_tab == TAB_RRC:
            self._rrc_idx = i
            self._rrc_scroll = max(0, i - rows + 1) if i >= self._rrc_scroll + rows else min(self._rrc_scroll, i)
        else:
            self.ssh_idx = i
            self.ssh_scroll = max(0, i - rows + 1) if i >= self.ssh_scroll + rows else min(self.ssh_scroll, i)
        self._route_cache = ''
        if new and self.on_add_contact:
            try:
                self.on_add_contact(dest)
            except Exception:
                pass

    _FIND_TITLES = ("Find peer: name or hash", "Find node: name or hash",
                    "Find listener: hash", "Find hub: name or hash")   # by TAB_*
    _FIND_NOT_HEARD = ("Not heard yet.", "Enter: and ask the network for a path.")
    _FIND_NO_MATCH = ("No match.", "Type the full 32-hex hash to add it.")

    def _draw_find(self):
        """Find screen: a teal title band continuing the selected tab,
        results (name, hash prefix, age), a count/keys row, and the query on
        the input line. With no results, a centred hint instead."""
        q = self._find_q
        ql = q.lower()
        y = BODY_Y + CHAR_H
        title = self._FIND_TITLES[self.node_tab]
        if self._cache[2] != title:
            self._cache[2] = title
            self.tft.fill_rect(0, y, SCREEN_W, CHAR_H, self.TAB_BG)
            self.tft.text(self.font, title, (COLS - len(title)) // 2 * CHAR_W, y,
                          self.NEON_GREEN, self.TAB_BG)
        rows = self._find_rows()
        res = self._find_res
        if not res:
            # word-wrap to the panel (the Pro has 30 columns), then centre
            # the block both ways in the results area
            msg = []
            for ln in (self._FIND_NOT_HEARD if len(ql) == 32 and all(c in _HEX for c in ql)
                       else self._FIND_NO_MATCH):
                cur = ""
                for w in ln.split(" "):
                    if cur and len(cur) + 1 + len(w) > COLS - 2:
                        msg.append(cur)
                        cur = w
                    else:
                        cur = cur + " " + w if cur else w
                msg.append(cur)
            top = (rows - len(msg)) // 2
        for i in range(rows):
            slot = i + 3
            y = BODY_Y + (i + 2) * CHAR_H
            if not res:
                t = msg[i - top] if 0 <= i - top < len(msg) else ""
                self._draw_row_cached(slot, " " * ((COLS - len(t)) // 2) + t if t else "",
                                      y, self.NEON_CYAN if i == top else self.DIM_CYAN)
                continue
            idx = self._find_scroll + i
            if idx >= len(res):
                self._draw_row_cached(slot, "", y, self.NEON_CYAN)
                continue
            dest, name, ts = res[idx]
            a = _age(ts)
            tail = dest.hex()[:8] + " " + " " * (5 - len(a)) + a
            tx = SCREEN_W - 8 - len(tail) * self.SW
            name = _ascii(name or "?")[:(tx - 4) // CHAR_W - 2]
            line = "  " + name
            sel = idx == self._find_sel
            key = ('\x01' if sel else '') + line + '\x00' + ql
            if self._cache[slot] == key:
                continue
            self._cache[slot] = key
            bg = self.SEL_BG if sel else self.BG_DARK
            self._row(line, y, self.YELLOW if sel else self.NEON_CYAN, bg)
            self._stext(tail, tx, y, self.YELLOW if sel else self.DIM_CYAN, bg)
            if ql:
                m = name.lower().find(ql)
                if m >= 0:
                    self.tft.text(self.font, name[m:m + len(ql)], (2 + m) * CHAR_W, y,
                                  self.NEON_GREEN, bg)
                elif tail.startswith(ql[:8]):
                    self._stext(ql[:8], tx, y, self.NEON_GREEN, bg)
            if sel:
                self.tft.fill_rect(0, y, 3, CHAR_H, self.NEON_MAG)
        n = str(len(self._find_snap)) + " heard"
        if q:
            n = str(len(res)) + "/" + n
        keys = "Ent=add Esc=back"
        if self._cache[BODY_ROWS] != n:
            self._cache[BODY_ROWS] = n
            y = BODY_Y + (BODY_ROWS - 1) * CHAR_H
            self._row("", y, self.DIM_CYAN)
            self._stext(n, 8, y, self.DIM_CYAN)
            self._stext(keys, SCREEN_W - 8 - len(keys) * self.SW, y, self.DIM_CYAN)
        if self._cache[INPUT_SLOT] != "F" + q:
            self._cache[INPUT_SLOT] = "F" + q
            self._draw_input_line(q)

    def add_shell_node(self, dest_hash, name=None, hops=None, seen=None, fav=False):
        """Add or update an rnsh listener (SSH tab, called by rnsh_client)."""
        prev = self.shell_nodes.get(dest_hash)
        if prev is None and len(self.shell_nodes) >= MAX_PEERS:
            oldest = min(filter(lambda k: not self.shell_nodes[k].get("fav", False), self._shell_keys),
                         key=lambda k: self.shell_nodes[k].get("seen", 0), default=None)
            del self.shell_nodes[oldest]
            self._shell_keys.remove(oldest)
            if self.ssh_idx >= len(self._shell_keys):
                self.ssh_idx = max(0, len(self._shell_keys) - 1)
        if prev:
            if name is None:
                name = prev.get("name")
            if seen is not None and prev.get("seen", 0) > seen:
                seen = prev["seen"]
            if fav is False:
                fav = prev["fav"]
        # rnsh announces carry no name — show a short hash so the row isn't "?"
        self.shell_nodes[dest_hash] = {"name": name or dest_hash.hex()[:10],
                                       "hops": hops,
                                       "seen": time.time() if seen is None else seen, "fav": fav}
        if dest_hash not in self._shell_keys:
            self._shell_keys.append(dest_hash)
        if self.state == STATE_NODES and self.node_tab == TAB_SSH:
            self.dirty = True

    def clear_shell_nodes(self):
        """Clear the SSH tab (interface switch)."""
        self.shell_nodes.clear()
        self._shell_keys.clear()
        self._load_favorite("shell")
        self.ssh_idx = 0
        self.ssh_scroll = 0
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    # Callbacks invoked by rnsh_client -------------------------------------

    def shell_status(self, text):
        self._shell_status = text
        if self.state == STATE_SHELL:
            self.dirty = True

    def shell_feed(self, stream_id, data):
        """Remote stdout/stderr bytes -> terminal scrollback. When at the live
        bottom (_shell_view==0) new output shows immediately; if the user has
        scrolled up (>0) their position persists — scroll down (or type) to
        follow along again."""
        if self._terminal is not None:
            self._terminal.feed(data)
            if self.state == STATE_SHELL:
                self.dirty = True
                self.wake_screen()

    def shell_connected(self):
        self._shell_connected = True
        self._shell_status = None
        if self.state == STATE_SHELL:
            self.dirty = True

    def shell_exited(self, code):
        if self._terminal is not None:
            self._terminal.feed(("\r\n[process exited: %s]\r\n" % str(code)).encode())
        self._shell_connected = False
        self._shell_status = "exited (%s) — Esc to leave" % str(code)
        if self.state == STATE_SHELL:
            self.dirty = True

    def shell_closed(self):
        self._shell_connected = False
        if self._shell_status is None or "exited" not in self._shell_status:
            self._shell_status = "disconnected — Esc to leave"
        if self.state == STATE_SHELL:
            self.dirty = True

    # --- RRC (Reticulum Relay Chat) GUI API, called by rrc_client ---------

    def add_rrc_hub(self, dest_hash, name=None, hops=None, fav=False):
        """Add or update a hub (RRC tab, called by rrc_client).

        Eviction is least-recently-SEEN, like add_shell_node(): FIFO drops
        a hub that is still announcing in favour of one that went quiet.
        And the selection is carried across the eviction by identity --
        pop(0) used to shift every index down one while _rrc_idx stayed
        put, so the highlighted row silently became a different hub and the
        next click opened that one instead.
        """
        entry = self.rrc_hubs.get(dest_hash)
        if entry is None:
            selected = (self._rrc_keys[self._rrc_idx]
                        if 0 <= self._rrc_idx < len(self._rrc_keys) else None)
            entry = {"name": name, "hops": hops, "seen": time.time(), "fav": fav}
            self.rrc_hubs[dest_hash] = entry
            self._rrc_keys.append(dest_hash)
            while len(self._rrc_keys) > MAX_RRC_HUBS:
                oldest = min(filter(lambda k: not self.rrc_hubs[k].get("fav", False), self._rrc_keys),
                             key=lambda k: self.rrc_hubs[k].get("seen", 0), default=None)
                self.rrc_hubs.pop(oldest, None)
                self._rrc_keys.remove(oldest)
            if selected is not None and selected in self._rrc_keys:
                self._rrc_idx = self._rrc_keys.index(selected)
            elif self._rrc_idx >= len(self._rrc_keys):
                self._rrc_idx = max(0, len(self._rrc_keys) - 1)
            _max_scroll = max(0, len(self._rrc_keys) - (BODY_ROWS - 1))
            if self._rrc_scroll > _max_scroll:
                self._rrc_scroll = _max_scroll
        else:
            if name:
                entry["name"] = name
            if hops is not None:
                entry["hops"] = hops
            entry["seen"] = time.time()
            if fav is True:
                entry["fav"] = fav
        self.dirty = True

    def clear_rrc_hubs(self):
        self.rrc_hubs = {}
        self._rrc_keys = []
        self._load_favorite("hub")
        self._rrc_idx = 0
        self._rrc_scroll = 0
        self.dirty = True

    def rrc_status(self, text):
        self._rrc_status = text
        self.dirty = True

    def rrc_line(self, kind, nick, text):
        self._rrc_lines.append((kind, nick, text))
        while len(self._rrc_lines) > RRC_SCROLLBACK:
            self._rrc_lines.pop(0)
        self._rrc_flat = None            # the wrapped view is now stale
        if self._rrc_scroll_chat > 0:
            # The user is deliberately reading back. Snapping to the newest
            # line here yanked them to the bottom on every arrival, which a
            # busy room, a long /list reply or the MOTD burst makes constant
            # -- the LXMF view only snaps for your own messages. Hold the
            # anchor exactly instead: the window is measured back from the
            # end (_visible_lines), so keeping the same rows on screen means
            # adding the new line's *wrapped* height, not 1. That is exact
            # whether or not the ring just evicted an older line, because
            # eviction shifts the window and the content by the same amount
            # -- until the ring is FULL. Then the flattened length plateaus
            # while an unclamped anchor keeps climbing: the view sticks on
            # the oldest screen, and _scroll_down needs one trackball tick
            # per excess line to get back, with nothing on screen to say
            # why. Clamp to the top of the scrollback.
            import rrc_ui
            anchor = self._rrc_scroll_chat + len(rrc_ui._wrap_line(kind, nick,
                                                                   text))
            top = max(0, len(rrc_ui._flatten(self)) - (BODY_ROWS - 1))
            self._rrc_scroll_chat = max(0, min(anchor, top))
        self.dirty = True

    def rrc_roster(self, count, exact=True):
        """Member count, and whether it is the room or only what we saw.

        `exact` is defaulted so any caller predating the member panel keeps
        working. rrc_client passes it from _roster_exact: True when the hub
        volunteered its member list or answered /who, False when the roster
        is just who we watched arrive.
        """
        self._rrc_members = count
        self._rrc_members_exact = exact
        self.dirty = True

    def rrc_rooms(self, rooms):
        """Room snapshot from a /list reply: [(bare_name, topic), ...].

        Names arrive without the "#" -- rrc_client.join() owns that strip
        and the wire must never carry one -- so the picker adds it for
        display exactly like every other room name on screen.

        _rrc_list_seen records that a reply landed at all, which is what
        separates "this hub has no registered public rooms" (a normal
        state on a hub whose rooms are all on-demand) from "we never
        asked". The picker only re-requests in the second case.
        """
        self._rrc_rooms = rooms
        self._rrc_list_seen = True
        if self._rrc_panel and self._rrc_panel_kind == "rooms":
            import rrc_ui
            rrc_ui.panel_clamp(self)
        self.dirty = True

    def rrc_members(self, members):
        """Roster snapshot: [(identity_hash, nick_or_None), ...]. The member
        panel (rrc_ui.draw_member_panel) reads ui._rrc_roster directly; the
        selection has to be re-clamped here too, or a roster that shrank
        while the panel was open leaves _rrc_panel_idx pointing past the
        end for the next draw.

        The scroll offset is clamped against the top of the valid window
        (len(members) - PANEL_ROWS), not against the selection index: a
        mass PART can shrink the roster to fewer members than fit on
        screen while idx and scroll are both still deep in a long list, and
        clamping scroll to idx would leave it stranded above 0, hiding
        members that now fit. Clamp scroll first, then idx, so both land
        correctly regardless of which was further out of range."""
        import rrc_ui
        self._rrc_roster = members
        self._rrc_members = len(members)
        max_scroll = max(0, len(members) - rrc_ui.PANEL_ROWS)
        if self._rrc_panel_scroll > max_scroll:
            self._rrc_panel_scroll = max_scroll
        if self._rrc_panel_idx >= len(members):
            self._rrc_panel_idx = max(0, len(members) - 1)
        self.dirty = True

    def rrc_joined(self, room):
        self._rrc_room = room
        self.state = STATE_RRC_CHAT
        # Stamped like every other state change (see _state_change_ms uses):
        # rrc_ui's "empty composer + backspace parts the room" reads this to
        # ignore a phantom keyboard byte arriving on the switch, which would
        # otherwise part the room the instant it was joined.
        self._state_change_ms = time.ticks_ms()
        self.dirty = True

    def rrc_welcome(self, hub_name):
        self._rrc_hub_name = hub_name
        self.state = STATE_RRC_ROOMS
        self._state_change_ms = time.ticks_ms()
        self.dirty = True

    def rrc_closed(self):
        self._rrc_room = None
        self.dirty = True

    # --- Browser page view ---

    def _row_image_link(self, row):
        """Return the link index if `row` is an image marker (a link whose
        URL carries the \\x01 sentinel), else None."""
        for s in row:
            li = s[4]
            if (li is not None and li < len(self.browser_links)
                    and self.browser_links[li][0].startswith("\x01")):
                return li
        return None

    def _expand_image_blocks(self):
        """Replace each single image-marker row with a full page-viewport
        block of rows, so the image scrolls row-by-row. Colour TFT only."""
        n = BODY_ROWS - 1
        out = []
        self._browser_image_rows = {}
        self._page_images = {}
        for row in self.browser_lines:
            li = self._row_image_link(row)
            if li is None:
                out.append(row)
                continue
            self._page_images[li] = {
                "src": self.browser_links[li][0][1:],  # strip \x01 sentinel
                "state": "idle", "buf": None, "w": 0, "h": 0,
            }
            base = len(out)
            for r in range(n):
                self._browser_image_rows[base + r] = (li, r)
                out.append([])   # purely visual; the draw loop blits the strip
        self.browser_lines = out

    def show_page(self, title, path, lines, links, can_back=False, keep_pos=False):
        """Display a rendered micron page (called by nomad_browser). keep_pos
        preserves the scroll/cursor across a reload of the same page."""
        self.browser_title = title
        self.browser_path = path
        self.browser_lines = lines
        self.browser_links = links
        self._browser_can_back = can_back
        if not self._mono:
            self._expand_image_blocks()   # reassigns self.browser_lines
        else:
            self._browser_image_rows = {}
            self._page_images = {}
        if keep_pos:
            # clamp the preserved scroll to the (possibly changed) content
            _rows = BODY_ROWS - 1
            self.browser_scroll = max(0, min(self.browser_scroll, max(0, len(self.browser_lines) - _rows)))
        else:
            self.browser_scroll = 0
            self.browser_cursor = -1
        self._browser_link_rows = {}
        self._page_gen += 1
        if self.state != STATE_BROWSER:
            self.state = STATE_BROWSER
            self._state_change_ms = time.ticks_ms()
        self.dirty = True

    def _browser_exit(self):
        """Leave the browser back to the node screen (NET tab)."""
        if self.on_browser_exit:
            try:
                self.on_browser_exit()
            except Exception:
                pass
        self.browser_status = None
        self.browser_lines = []
        self.browser_links = []
        self.node_tab = 1
        self.state = STATE_NODES
        self._state_change_ms = time.ticks_ms()
        self.dirty = True

    def _browser_back(self):
        went = False
        if self.on_browse_back:
            try:
                went = self.on_browse_back()
            except Exception:
                went = False
        if not went:
            self._browser_exit()

    def _browser_follow_cursor(self):
        """Open the first link on the cursor row."""
        if self.browser_cursor < 0:
            return
        li = self._browser_link_rows.get(self.browser_cursor)
        if li is None or li >= len(self.browser_links):
            return
        if self.on_browse_follow:
            self.browser_status = "opening..."
            self.dirty = True
            self.on_browse_follow(self.browser_links[li][0])

    def _row_has_link(self, idx):
        for s in self.browser_lines[idx]:
            if s[4] is not None:
                return True
        return False

    def _jump_next_link(self):
        """Move the cursor to the next document row containing a link."""
        _rows = BODY_ROWS - 1
        lines = self.browser_lines
        cur = self.browser_cursor if self.browser_cursor >= 0 else -1
        start = self.browser_scroll + cur + 1
        for idx in range(start, len(lines)):
            if self._row_has_link(idx):
                if idx < self.browser_scroll or idx >= self.browser_scroll + _rows:
                    self.browser_scroll = max(0, min(idx, len(lines) - _rows))
                self.browser_cursor = idx - self.browser_scroll
                self.dirty = True
                return
        self.dirty = True

    def _jump_prev_link(self):
        """Move the cursor to the previous document row containing a link."""
        _rows = BODY_ROWS - 1
        lines = self.browser_lines
        cur = self.browser_cursor if self.browser_cursor >= 0 else 0
        start = self.browser_scroll + cur - 1
        for idx in range(min(start, len(lines) - 1), -1, -1):
            if self._row_has_link(idx):
                if idx < self.browser_scroll or idx >= self.browser_scroll + _rows:
                    self.browser_scroll = max(0, min(idx, len(lines) - _rows))
                self.browser_cursor = idx - self.browser_scroll
                self.dirty = True
                return
        self.dirty = True

    def _browser_page(self, delta):
        """Page the viewport by delta rows, clamped; cursor stays in-window."""
        _rows = BODY_ROWS - 1
        max_scroll = max(0, len(self.browser_lines) - _rows)
        self.browser_scroll = max(0, min(self.browser_scroll + delta, max_scroll))
        if self.browser_cursor >= _rows:
            self.browser_cursor = _rows - 1
        self.dirty = True

    def _browser_goto(self, top):
        """Jump to the top or bottom of the page."""
        _rows = BODY_ROWS - 1
        self.browser_scroll = 0 if top else max(0, len(self.browser_lines) - _rows)
        self.browser_cursor = -1
        self.dirty = True

    def _handle_key_browser(self, ch, key):
        if ch == 0x08:    # Backspace — back, or exit at stack bottom
            self._browser_back()
            return True
        elif ch == 0x1B:  # Esc — straight out to the NET tab
            self._browser_exit()
            return True
        elif ch == 0x0D:  # Enter mirrors the trackball click
            self._browser_follow_cursor()
            return True
        elif ch == 0x20:  # Space — page down
            self._browser_page(BODY_ROWS - 2)
            return True
        elif key == b'r' or key == b'R':
            if self.on_browse_refresh and self.browser_path:
                self.browser_status = "reloading..."
                self.dirty = True
                self.on_browse_refresh()
            return True
        elif key == b'n' or key == b'N':
            self._jump_next_link()
            return True
        elif key == b'p' or key == b'P':
            self._jump_prev_link()
            return True
        elif key == b'b' or key == b'B':  # page up
            self._browser_page(-(BODY_ROWS - 2))
            return True
        elif key == b'g':  # top
            self._browser_goto(True)
            return True
        elif key == b'G':  # bottom
            self._browser_goto(False)
            return True
        return False

    # --- Shell screen (STATE_SHELL) -----------------------------------------

    @staticmethod
    def _safe_decode(buf):
        try:
            return bytes(buf).decode("utf-8")
        except Exception:
            return "".join(chr(b) if 32 <= b < 127 else "?" for b in buf)

    def draw_shell(self):
        if self._shell_menu:
            self._draw_shell_menu()
            return
        # Body grid comes from the shell font (64x24 or 80x32); the 8x16
        # tft.text() grid is the fallback when no font module could be loaded.
        sf = self._shell_font
        rows = sf.rows if sf else BODY_ROWS
        rh = sf.h if sf else CHAR_H
        cols = sf.cols if sf else COLS
        lines = self._terminal.lines if self._terminal else [""]
        n = len(lines)
        mx = max(0, n - rows)
        if self._shell_view > mx:          # clamp (scrollback may have trimmed)
            self._shell_view = mx
        start = max(0, n - rows - self._shell_view)
        visible = lines[start:start + rows]
        if len(self._shell_cache) != rows:
            self._shell_cache = [''] * rows
        cache = self._shell_cache
        for i in range(rows):
            y = BODY_Y + i * rh
            text = visible[i] if i < len(visible) else ""
            # cache key keys on absolute line index so a scroll shift forces redraw
            key = str(start + i) + "\x00" + text
            if cache[i] == key:
                continue
            cache[i] = key
            if sf:
                # Glyph-index bytes. Fast path: an all-ASCII line encodes 1:1
                # to UTF-8, so the C encoder does the work and _tb()'s per-char
                # Python loop is skipped — which is every line a shell emits
                # except ones carrying Cyrillic or CP437 box glyphs.
                clipped = text[:cols]
                slots = clipped.encode()
                if len(slots) != len(clipped):
                    slots = self._tb(clipped)
                sf.draw_row(self.tft, slots, y)
            else:
                self.tft.text(self.font, self._tb(_pad(text)), 0, y,
                              self.BODY_FG, self.BG_DARK)

        # Scroll indicator on the right edge (below the navbar), when there's
        # more scrollback than one screen. Drawn after the rows: a composited
        # row blits the full 320px width and would otherwise paint over it.
        _track_h = BODY_ROWS * CHAR_H
        self.tft.fill_rect(SBAR_X, BODY_Y, SBAR_W, _track_h, self.BG_DARK)
        if n > rows:
            _bar_h = max(6, _track_h * rows // n)
            _bar_y = BODY_Y + (n - rows - self._shell_view) * _track_h // n
            self.tft.fill_rect(SBAR_X, _bar_y, SBAR_W, _bar_h, self.DIM_CYAN)

        # Footer: transient status wins; else the input line (line mode, while
        # typing) or a hint that always advertises how to quit + scroll.
        if self._shell_status:
            foot = self._shell_status[:COLS]
            fkey = "S\x00" + foot
            if self._cache[FOOT_SLOT] != fkey:
                self._cache[FOOT_SLOT] = fkey
                self.tft.text(self.font, self._tb(_pad(foot)), 0, INPUT_Y, self.NEON_MAG, self.BG_DARK)
        elif self._shell_line_mode and self._shell_input:
            self._cache[FOOT_SLOT] = ''           # input redraws live; repaint on next status change
            self._draw_input_line(self._safe_decode(self._shell_input))
        else:
            if self._shell_line_mode:
                foot = "Bksp=quit click=keys trkbl=scrl"
            else:
                foot = "click=keys menu  trkbl=scroll"
            if self._shell_view:
                foot = "[+" + str(self._shell_view) + "] " + foot
            foot = foot[:COLS]
            if self._cache[FOOT_SLOT] != foot:
                self._cache[FOOT_SLOT] = foot
                self.tft.text(self.font, self._tb(_pad(foot)), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)

    def _shell_send(self, data):
        if data:
            self._shell_at_line_start = data[-1] in (0x0d, 0x0a)
        self._shell_view = 0   # sending input snaps the view back to the live bottom
        if self.on_shell_input:
            try:
                self.on_shell_input(bytes(data))
            except Exception:
                pass

    def _shell_rows(self):
        """Visible terminal rows for the active shell font (8x16 fallback)."""
        return self._shell_font.rows if self._shell_font else BODY_ROWS

    def _shell_scroll(self, delta):
        """Scroll the local terminal scrollback (delta>0 = toward older lines)."""
        if self._terminal is None:
            return
        mx = max(0, len(self._terminal.lines) - self._shell_rows())
        self._shell_view = max(0, min(self._shell_view + delta, mx))
        self.dirty = True

    # --- shell control-key menu (trackball-click overlay) -------------------

    def _shell_menu_open(self):
        self._shell_menu = True
        self._shell_menu_idx = 0
        self._cache = [''] * CACHE_ROWS
        # The menu paints over the body with the 8x16 font, so every composited
        # terminal row underneath it is stale once the menu closes.
        self._shell_cache = [''] * len(self._shell_cache)
        self.dirty = True

    def _shell_menu_move(self, delta):
        self._shell_menu_idx = (self._shell_menu_idx + delta) % len(_SHELL_CTRL_ITEMS)
        self.dirty = True

    def _shell_menu_close(self):
        self._shell_menu = False
        self._cache = [''] * CACHE_ROWS
        self._shell_cache = [''] * len(self._shell_cache)
        self.dirty = True

    def _shell_menu_label(self, i):
        """Menu row text. The font row shows the live grid, so the size can be
        read off — and watched change — without leaving the menu."""
        label, kind, _ = _SHELL_CTRL_ITEMS[i]
        if kind == "font":
            sf = self._shell_font
            return "Font     %dx%d" % (sf.cols if sf else COLS,
                                       sf.rows if sf else BODY_ROWS)
        return label

    def _shell_menu_exec(self):
        _, kind, payload = _SHELL_CTRL_ITEMS[self._shell_menu_idx]
        if kind == "font":
            # Cycle in place. The menu deliberately stays open so repeated
            # clicks step through the sizes with the label updating each time,
            # instead of closing and making you reopen it to try the next one.
            self._shell_font_idx = (self._shell_font_idx + 1) % len(_SHELL_FONTS)
            self._set_shell_font(notify=True)   # clears both row caches
            return
        self._shell_menu = False
        self._cache = [''] * CACHE_ROWS
        self._shell_cache = [''] * len(self._shell_cache)
        self.dirty = True
        if kind == "send":
            self._shell_send(payload)
        elif kind == "mode":
            self._shell_line_mode = not self._shell_line_mode
            self._shell_view = 0
        elif kind == "quit":
            self._leave_shell()
        # "close": already closed above

    def _draw_shell_menu(self):
        self._draw_row_cached(1, "Control keys (trackball):", BODY_Y, self.NEON_CYAN)
        rows = BODY_ROWS - 1
        n = len(_SHELL_CTRL_ITEMS)
        for i in range(rows):
            y = BODY_Y + (i + 1) * CHAR_H
            ci = i + 2
            if i < n:
                line = ("> " if i == self._shell_menu_idx else "  ") + self._shell_menu_label(i)
                if i == self._shell_menu_idx:
                    ck = "\x01" + line
                    if self._cache[ci] != ck:
                        self._cache[ci] = ck
                        self.tft.text(self.font, self._tb(_pad(line)), 0, y, self.YELLOW, self.SEL_BG)
                else:
                    self._draw_row_cached(ci, line, y, self.NEON_CYAN, self.BG_DARK)
            else:
                self._draw_row_cached(ci, "", y, self.NEON_CYAN)
        foot = "click=send  U/D=move  Bksp=close"
        if self._cache[FOOT_SLOT] != foot:
            self._cache[FOOT_SLOT] = foot
            self.tft.text(self.font, self._tb(_pad(foot)), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)

    def _leave_shell(self):
        if self.on_shell_disconnect:
            try:
                self.on_shell_disconnect()
            except Exception:
                pass
        self._terminal = None
        self._shell_status = None
        self._shell_connected = False
        self._shell_menu = False
        # Release the glyph cache + row buffer (~10KB) back to the heap.
        self._shell_font = None
        self._shell_cache = []
        gc.collect()
        self.node_tab = TAB_SSH
        self.state = STATE_NODES
        self._state_change_ms = time.ticks_ms()
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def _handle_key_shell(self, ch, key):
        if ch == 0:
            return False
        # Control-key menu open: Enter sends the selection, Backspace closes.
        if self._shell_menu:
            if ch == 0x0D:
                self._shell_menu_exec()
            elif ch == 0x08:
                self._shell_menu_close()
            return True
        # After the session ended, any key returns to the SSH tab.
        if not self._shell_connected and self._shell_status and "leave" in self._shell_status:
            self._leave_shell()
            return True
        # Control bytes straight from the keyboard (Ctrl-C=0x03, Ctrl-D=0x04,
        # Ctrl-Z=0x1a, ...) always go to the remote in either mode.
        if 0 < ch < 0x20 and ch not in (0x08, 0x09, 0x0d, 0x1b):
            self._shell_send(bytes([ch]))
            return True
        if self._shell_line_mode:
            return self._handle_shell_line(ch, key)
        return self._handle_shell_char(ch, key)

    def _handle_shell_line(self, ch, key):
        if ch == 0x08:                     # Backspace: edit the line, or leave when empty
            if self._shell_input:
                self._shell_input = self._shell_input[:-1]
                self._input_dirty = True
            elif time.ticks_diff(time.ticks_ms(), self._state_change_ms) > 500:
                # Empty input + Backspace = leave the shell (the T-Deck has no
                # Esc key; this matches how chat/browser exit).
                self._leave_shell()
            return True
        if ch == 0x1B:                     # Esc also leaves (if the keyboard ever emits it)
            self._leave_shell()
            return True
        if ch == 0x0D:                     # Enter — escapes, else send line
            line = bytes(self._shell_input)
            self._shell_input = bytearray()
            self._input_dirty = True
            if line == b"~.":
                self._leave_shell()
                return True
            if line == b"~l":
                self._shell_line_mode = False
                self._shell_view = 0
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            self._shell_send(line + b"\n")
            return True
        if ch == 0x09:                     # Tab (completion is remote-side)
            self._shell_input += b"\t"
            self._shell_view = 0
            self._input_dirty = True
            return True
        if 0x20 <= ch < 0x7F:
            self._shell_input += key
            self._shell_view = 0           # typing snaps to the live bottom
            self._input_dirty = True
            return True
        return True

    def _handle_shell_char(self, ch, key):
        # SSH-style ~ escape at the start of a line.
        if self._shell_at_line_start and not self._shell_escape and ch == 0x7E:  # '~'
            self._shell_escape = True
            return True
        if self._shell_escape:
            self._shell_escape = False
            if ch == 0x2E:                 # '.' -> quit
                self._leave_shell()
                return True
            if ch == 0x6C:                 # 'l' -> line mode
                self._shell_line_mode = True
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            if ch == 0x7E:                 # '~~' -> literal tilde
                self._shell_send(b"~")
                return True
            self._shell_send(b"~")         # not a command: send ~ then the char
        if ch == 0x0D:
            self._shell_send(b"\r")
        elif ch == 0x08:
            self._shell_send(b"\x7f")      # DEL — typical erase char
        elif ch == 0x09:
            self._shell_send(b"\t")
        elif ch == 0x1B:
            self._shell_send(b"\x1b")
        elif 0x20 <= ch < 0x7F:
            self._shell_send(key)
        return True

    def _handle_key_chat(self, ch, key):
        if ch == 0x08:  # Backspace
            if len(self.cmd_buf) > 0:
                self.cmd_buf = self.cmd_buf[:-1]
                self._input_dirty = True
            elif time.ticks_diff(time.ticks_ms(), self._state_change_ms) > 500:
                # Empty input + backspace = return to node list
                # (500ms guard prevents phantom keyboard bytes from flipping back)
                self.state = STATE_NODES
                self.chat_scroll = 0
                self._state_change_ms = time.ticks_ms()
                self.dirty = True
            return True
        elif ch == 0x0D:  # Enter — send message
            if len(self.cmd_buf) > 0 and self.selected_peer:
                text = self.cmd_buf.decode()
                self.cmd_buf = bytearray()
                mid = self.add_chat_message(self.selected_peer, True, text, status=1)
                if self.on_send:
                    self.on_send(self.selected_peer, text, mid)
                self.dirty = True
            return True
        elif ch == 0x1B:  # Escape — back to node list
            if time.ticks_diff(time.ticks_ms(), self._state_change_ms) > 500:
                self.state = STATE_NODES
                self.chat_scroll = 0
                self._state_change_ms = time.ticks_ms()
                self.dirty = True
            return True
        elif 0x20 <= ch < 0x7F:  # Printable
            # '0' (Sym+0 mic key) with empty input = start voice recording.
            # Only '0' triggers it so messages can start with r/R/other chars.
            if key == b'0' and len(self.cmd_buf) == 0 and self.selected_peer:
                self._enter_recording()
                return True
            self.cmd_buf += key
            self._input_dirty = True
            return True
        return False

    def _draw_recording(self):
        """Draw the recording screen. Deliberately STATIC — it is painted once
        when warming starts and once when capture starts, and never updated
        during capture. Any redraw makes the C display driver hold the GIL,
        which starves the core-1 mic thread and hurts the audio, so there is no
        VU meter or live counter here (row cache skips the unchanged rows)."""
        if self._rec_warming:
            title, tcol = "Warming mic...", self.NEON_CYAN
            hint = "Esc=cancel"
        else:
            title, tcol = "* Recording *", self.NEON_MAG
            hint = "Enter=send   Esc=cancel"
        mid = BODY_ROWS // 2
        for i in range(BODY_ROWS):
            y = BODY_Y + i * CHAR_H
            if i == mid - 1:
                self._draw_row_cached(i + 1, title.center(COLS), y, tcol)
            elif i == mid + 1:
                self._draw_row_cached(i + 1, hint.center(COLS), y, self.DIM_CYAN)
            else:
                self._draw_row_cached(i + 1, "", y, self.NEON_CYAN)
        self.tft.text(self.font, _pad(""), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)

    def _enter_recording(self):
        """Switch to recording state. Shows '* Recording *' immediately —
        capture content includes audio from ~the keypress (DMA ring), so the
        user can speak at once; 'Warming mic...' appears only if the slow
        ADC re-warm fallback engages (driven by _recording_loop)."""
        # No callback means the board has no microphone. Entering the state
        # anyway would show a recording screen that never records.
        if self.on_record_start is None:
            return
        self._rec_seconds = 0
        self._rec_level = 0
        self._rec_warming = False
        self.state = STATE_RECORDING
        self._prev_state = -1
        self._state_change_ms = time.ticks_ms()
        self._cache = [''] * CACHE_ROWS
        self.dirty = True
        if self.on_record_start:
            self.on_record_start()

    def _handle_key_recording(self, ch, key):
        """Any key stops recording. Esc/Backspace=cancel, anything else=send."""
        if ch == 0x1B or ch == 0x08:  # Esc or Backspace — cancel
            send = False
        else:
            send = True  # Enter, space, any key — stop & send
        if self.on_record_stop:
            self.on_record_stop(send=send)
        self.state = STATE_CHAT
        self._state_change_ms = time.ticks_ms()
        self._cache = [''] * CACHE_ROWS
        self.dirty = True
        return True

    def _handle_key_settings(self, ch, key):
        if self._settings_page == _SET_MAIN:
            if ch == 0x1B or (ch == 0x08 and time.ticks_diff(time.ticks_ms(), self._state_change_ms) > 500):
                self.state = STATE_NODES
                self._state_change_ms = time.ticks_ms()
                self.dirty = True
                return True
            elif ch == 0x0D:  # Enter
                if self._settings_idx == 0:  # WiFi toggle
                    if self._wifi_connected:
                        self._wifi_scanning = False
                        # Toggle TCP as well - Will stop WiFI and Start LoRa
                        if self.on_tcp_toggle:
                            if self.on_tcp_toggle(False, None, None):
                                self._tcp_enabled = False
                                self._tcp_target = ""
                        self._cache = [''] * CACHE_ROWS
                        self.dirty = True
                        return True
                    self._settings_page = _SET_WIFI_SCAN
                    self._settings_idx = 0
                    self._settings_scroll = 0
                    self._wifi_networks = []
                    self._wifi_scanning = True
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    if self.on_wifi_scan:
                        asyncio.create_task(self._do_wifi_scan())
                    return True
                elif self._settings_idx == 1:  # TCP toggle
                    if not self._wifi_connected:
                        return True  # requires WiFi
                    if self._tcp_enabled:
                        # Toggle OFF
                        if self.on_tcp_toggle:
                            if self.on_tcp_toggle(False, None, None):
                                self._tcp_enabled = False
                                self._tcp_target = ""
                    else:
                        # Go to host entry sub-page
                        self._settings_page = _SET_TCP_HOST
                        self.cmd_buf = bytearray(self._tcp_target.encode()) if self._tcp_target else bytearray(self._tcp_default.encode())
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 2:  # Node name
                    self._settings_page = _SET_NODE_NAME
                    self.cmd_buf = bytearray(self.node_name.encode())
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 3:  # LoRa reset
                    if self.on_lora_reset:
                        self.on_lora_reset()
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 4:  # Volume
                    self._volume = (self._volume + 1) % 11  # cycle 0-10
                    if self.on_volume:
                        self.on_volume(self._volume)
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 5:  # Keyboard backlight toggle
                    self.set_kbd_backlight_pref(not self._kbd_bl)
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 6:  # Auto-announce toggle
                    self._auto_announce = not self._auto_announce
                    if self.on_auto_announce:
                        try:
                            self.on_auto_announce(self._auto_announce)
                        except Exception:
                            pass
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 7:  # Sleep timeout cycle
                    self._cycle_timeout(1)
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 8:  # Auto-wake policy cycle
                    self._cycle_wake(1)
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 9:  # Radio stats page
                    self._settings_page = _SET_RADIO
                    self._settings_scroll = 0
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                elif self._settings_idx == 10:  # LoRa radio config page
                    self._settings_page = _SET_LORA
                    self._lora_edit = dict(self._lora_cfg)
                    self._lora_field = 0
                    self._lora_applying = ""
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
                # idx 11 (address) is informational — Enter does nothing
        elif self._settings_page == _SET_RADIO:
            if ch == 0x1B or ch == 0x08:
                self._settings_page = _SET_MAIN
                self._settings_idx = 9
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
        elif self._settings_page == _SET_LORA:
            if ch == 0x1B or ch == 0x08:   # back — discard unapplied edits
                self._lora_edit = None
                self._lora_applying = ""
                self._settings_page = _SET_MAIN
                self._settings_idx = 10
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif ch == 0x0D:   # Enter / trackball click
                if self._lora_field == 0:          # Freq -> numeric entry page
                    self._settings_page = _SET_LORA_FREQ
                    self.cmd_buf = bytearray()     # type a fresh value; Enter empty keeps current
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                elif self._lora_field == 5:        # Apply & Save
                    self._lora_apply()
                else:                              # BW/SF/CR/TX -> cycle forward
                    self._lora_cycle(_LORA_FIELDS[self._lora_field], 1)
                    self._lora_applying = ""
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                return True
        elif self._settings_page == _SET_LORA_FREQ:
            if ch == 0x1B:   # cancel, keep old freq
                self._settings_page = _SET_LORA
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif ch == 0x08:   # Backspace (empty -> back)
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_LORA
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                return True
            elif ch == 0x0D:   # save — parse, clamp, store into the edit copy
                try:
                    v = int(self.cmd_buf.decode().strip())
                except Exception:
                    v = self._lora_edit["freq_khz"]
                self._lora_edit["freq_khz"] = _clamp(v, _LORA_FREQ_MIN, _LORA_FREQ_MAX)
                self._lora_applying = ""
                self.cmd_buf = bytearray()
                self._settings_page = _SET_LORA
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif 0x30 <= ch <= 0x39:   # digits only
                self.cmd_buf += key
                self._input_dirty = True
                return True
            return True   # swallow anything else on the numeric page
        elif self._settings_page == _SET_WIFI_SCAN:
            if ch == 0x1B or ch == 0x08:
                self._settings_page = _SET_MAIN
                self._settings_idx = 0
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif ch == 0x0D and self._wifi_networks:
                idx = self._settings_idx
                if 0 <= idx < len(self._wifi_networks):
                    self._wifi_ssid = self._wifi_networks[idx][0]
                    self._wifi_err = ""
                    self._settings_page = _SET_WIFI_PASS
                    self.cmd_buf = bytearray()
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    return True
            elif key == b'r' or key == b'R':  # rescan
                self._settings_idx = 0
                self._settings_scroll = 0
                self._wifi_err = ""
                self._wifi_networks = []
                self._wifi_scanning = True
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                if self.on_wifi_scan:
                    asyncio.create_task(self._do_wifi_scan())
                return True
        elif self._settings_page == _SET_WIFI_PASS:
            if self._wifi_connecting:
                return True  # ignore input while the async connect runs
            if ch == 0x1B:
                self._settings_page = _SET_WIFI_SCAN
                self._settings_idx = 0
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif ch == 0x08:  # Backspace
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_WIFI_SCAN
                    self._settings_idx = 0
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                return True
            elif ch == 0x0D:  # Enter — connect (async; UI shows "connecting")
                password = self.cmd_buf.decode()
                self.cmd_buf = bytearray()
                self._cache = [''] * CACHE_ROWS
                if self.on_wifi_connect:
                    self._wifi_connecting = True
                    self.dirty = True
                    self.on_wifi_connect(self._wifi_ssid, password)
                else:
                    self._settings_page = _SET_MAIN
                    self._settings_idx = 0
                    self.dirty = True
                return True
            elif 0x20 <= ch < 0x7F:  # Printable
                self.cmd_buf += key
                self._input_dirty = True
                return True
        elif self._settings_page == _SET_TCP_HOST:
            if self._tcp_connecting:
                return True  # ignore input while the async connect runs
            if ch == 0x1B:
                self._settings_page = _SET_MAIN
                self._settings_idx = 1
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif ch == 0x08:  # Backspace
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_MAIN
                    self._settings_idx = 1
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                return True
            elif ch == 0x0D:  # Enter — parse host:port and connect (async)
                addr = self.cmd_buf.decode().strip()
                self.cmd_buf = bytearray()
                host, port = None, None
                if ":" in addr:
                    parts = addr.rsplit(":", 1)
                    host = parts[0]
                    try:
                        port = int(parts[1])
                    except:
                        pass
                if host and port and self.on_tcp_connect:
                    self._tcp_connecting = True
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                    self.on_tcp_connect(host, port)
                    return True
                self._settings_page = _SET_MAIN
                self._settings_idx = 1
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif 0x20 <= ch < 0x7F:  # Printable
                self.cmd_buf += key
                self._input_dirty = True
                return True
        elif self._settings_page == _SET_NODE_NAME:
            if ch == 0x1B:
                self._settings_page = _SET_MAIN
                self._settings_idx = 2
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif ch == 0x08:  # Backspace
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_MAIN
                    self._settings_idx = 2
                    self._cache = [''] * CACHE_ROWS
                    self.dirty = True
                return True
            elif ch == 0x0D:  # Enter — save name
                name = self.cmd_buf.decode().strip()
                self.cmd_buf = bytearray()
                if name:
                    self.node_name = name
                    if self.on_node_name:
                        self.on_node_name(name)
                self._settings_page = _SET_MAIN
                self._settings_idx = 2
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
                return True
            elif 0x20 <= ch < 0x7F:  # Printable
                self.cmd_buf += key
                self._input_dirty = True
                return True
        return False

    # --- Settings drawing ---

    def draw_settings(self):
        if self._settings_page == _SET_MAIN:
            self._draw_settings_main()
        elif self._settings_page == _SET_WIFI_SCAN:
            self._draw_wifi_scan()
        elif self._settings_page == _SET_WIFI_PASS:
            self._draw_wifi_pass()
        elif self._settings_page == _SET_TCP_HOST:
            self._draw_tcp_host()
        elif self._settings_page == _SET_NODE_NAME:
            self._draw_node_name()
        elif self._settings_page == _SET_RADIO:
            self._draw_settings_radio()
        elif self._settings_page == _SET_LORA:
            self._draw_settings_lora()
        elif self._settings_page == _SET_LORA_FREQ:
            self._draw_lora_freq()

    def _timeout_label(self):
        ms = self._screen_timeout_ms
        return "never" if not ms else str(ms // 1000) + "s"

    def _wake_label(self):
        return ("msgs", "all", "never")[self._wake_mode]

    def _draw_settings_main(self):
        self._draw_row_cached(1, "Setup", BODY_Y, self.NEON_CYAN)

        if self._wifi_connected:
            wifi_status = self._wifi_ssid_current
            if self._wifi_ip:
                wifi_status += " [" + self._wifi_ip + "]"
        else:
            wifi_status = "not connected"
        wifi_line = "WiFi: " + wifi_status
        tcp_line = "TCP:  " + (self._tcp_target if self._tcp_enabled else "OFF")
        name_line = "Name: " + self.node_name
        lora_line = "LoRa: " + ("Online" if self.lora_online else "OFFLINE (click=reset)")

        vol_bar = "#" * self._volume + "." * (10 - self._volume)
        vol_line = "Vol:  [" + vol_bar + "] " + str(self._volume)
        kbbl_line = "KbBL: " + ("ON" if self._kbd_bl else "OFF")
        anc_line = "Announce: " + ("AUTO" if self._auto_announce else "manual")
        sleep_line = "Sleep: " + self._timeout_label()
        wake_line = "Wake: " + self._wake_label()
        radio_line = "Radio stats"
        c = self._lora_cfg
        loracfg_line = ("LoRa cfg: %dk SF%d BW%s"
                        % (c["freq_khz"], c["sf"], c["bw"]))
        addr_line = "Addr: " + (self.my_address or "?")
        items = [wifi_line, tcp_line, name_line, lora_line, vol_line,
                 kbbl_line, anc_line, sleep_line, wake_line, radio_line,
                 loracfg_line, addr_line]
        for i in range(BODY_ROWS - 1):
            y = BODY_Y + (i + 1) * CHAR_H
            if i < len(items):
                line = "  " + items[i]
                if i == self._settings_idx:
                    cache_key = '\x01' + line
                    if self._cache[i + 2] != cache_key:
                        self._cache[i + 2] = cache_key
                        self.tft.text(self.font, self._tb(_pad(line)), 0, y, self.YELLOW, self.SEL_BG)
                        self.tft.fill_rect(4, y, 3, CHAR_H, self.NEON_MAG)
                else:
                    self._draw_row_cached(i + 2, line, y, self.NEON_CYAN)
            else:
                self._draw_row_cached(i + 2, "", y, self.NEON_CYAN)

        self._draw_settings_bottom_bar()

    def _draw_settings_radio(self):
        stats = []
        if self.get_radio_stats:
            try:
                stats = self.get_radio_stats()
            except Exception:
                stats = [("error", "")]
        self._radio_rows = len(stats)
        _rows = BODY_ROWS - 1
        # clamp scroll to content (stats count varies as the page refreshes)
        max_scroll = max(0, len(stats) - _rows)
        if self._settings_scroll > max_scroll:
            self._settings_scroll = max_scroll
        hdr = "< Radio / Mesh"
        if len(stats) > _rows:
            hdr = hdr + "   (scroll)"
        self._draw_row_cached(1, hdr, BODY_Y, self.NEON_CYAN)
        visible = stats[self._settings_scroll:self._settings_scroll + _rows]
        for i in range(_rows):
            y = BODY_Y + (i + 1) * CHAR_H
            if i < len(visible):
                label, value = visible[i]
                line = "  " + _pad(str(label), 14) + str(value)
                self._draw_row_cached(i + 2, line, y, self.NEON_CYAN)
            else:
                self._draw_row_cached(i + 2, "", y, self.NEON_CYAN)
        self._draw_settings_bottom_bar()

    def _lora_field_lines(self):
        """Human-readable value strings for the five editable fields, in
        _LORA_FIELDS order. Reads the pending edit copy if the page is open."""
        e = self._lora_edit or self._lora_cfg
        return [
            "Freq   " + str(e["freq_khz"]) + " kHz",
            "BW     " + str(e["bw"]) + " kHz",
            "SF     " + str(e["sf"]),
            "CR     4/" + str(e["coding_rate"]),
            "TX     " + str(e["tx_power"]) + " dBm",
        ]

    def _draw_settings_lora(self):
        modified = self._lora_edit is not None and self._lora_edit != self._lora_cfg
        hdr = "< LoRa Radio" + ("   *modified" if modified else "")
        self._draw_row_cached(1, hdr, BODY_Y,
                              self.NEON_MAG if modified else self.NEON_CYAN)

        apply_line = "[ Apply & Save ]"
        if self._lora_applying == "applied":
            status = "applied -- now live & saved"
        elif self._lora_applying == "failed":
            status = "apply FAILED -- radio unchanged"
        else:
            status = "must match every peer you talk to"

        # (text, selectable field index or None)
        body = [(fl, fi) for fi, fl in enumerate(self._lora_field_lines())]
        body.append(("", None))
        body.append((apply_line, 5))
        body.append((status, None))

        for i in range(BODY_ROWS - 1):
            y = BODY_Y + (i + 1) * CHAR_H
            if i < len(body):
                text, field = body[i]
                line = "  " + text
                if field is not None and field == self._lora_field:
                    cache_key = '\x01' + line
                    if self._cache[i + 2] != cache_key:
                        self._cache[i + 2] = cache_key
                        self.tft.text(self.font, self._tb(_pad(line)), 0, y,
                                      self.YELLOW, self.SEL_BG)
                        self.tft.fill_rect(4, y, 3, CHAR_H, self.NEON_MAG)
                else:
                    if text == apply_line:
                        color = self.NEON_GREEN
                    elif self._lora_applying == "failed" and text == status:
                        color = self.NEON_MAG
                    else:
                        color = self.NEON_CYAN
                    self._draw_row_cached(i + 2, line, y, color)
            else:
                self._draw_row_cached(i + 2, "", y, self.NEON_CYAN)

        self._draw_settings_bottom_bar()

    def _draw_lora_freq(self):
        self._draw_row_cached(1, "LoRa Frequency (kHz)", BODY_Y, self.NEON_CYAN)
        cur = (self._lora_edit or self._lora_cfg)["freq_khz"]
        self._draw_row_cached(2, "  current " + str(cur) + " kHz",
                              BODY_Y + CHAR_H, self.NEON_CYAN)
        self._draw_row_cached(3, "  range " + str(_LORA_FREQ_MIN) + "-"
                              + str(_LORA_FREQ_MAX), BODY_Y + 2 * CHAR_H, self.DIM_CYAN)
        for i in range(3, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)
        self._draw_input_line(self.cmd_buf.decode())

    def _draw_settings_bottom_bar(self):
        self.tft.text(self.font, _pad(""), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
        self.tft.text(self.font, "(", 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
        self.tft.text(self.font, "click", CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
        self.tft.text(self.font, ")select", 6 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
        _hx = (COLS - 12) * CHAR_W
        self.tft.text(self.font, "[", _hx, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
        self.tft.text(self.font, "bksp", _hx + CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
        self.tft.text(self.font, "=back]", _hx + 5 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)

    def _draw_wifi_scan(self):
        _hdr = ("WiFi: " + self._wifi_err[:18] + "  (r)escan"
                if self._wifi_err else "WiFi Networks   (r)escan")
        self._draw_row_cached(1, _hdr, BODY_Y,
                              self.NEON_MAG if self._wifi_err else self.NEON_CYAN)

        if not self._wifi_networks:
            msg = "  Scanning..." if self._wifi_scanning else "  No networks found"
            self._draw_row_cached(2, msg, BODY_Y + CHAR_H, self.DIM_CYAN)
            for i in range(2, BODY_ROWS):
                self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)
        else:
            visible_start = self._settings_scroll
            visible = self._wifi_networks[visible_start:visible_start + BODY_ROWS - 1]
            for i in range(BODY_ROWS - 1):
                y = BODY_Y + (i + 1) * CHAR_H
                if i < len(visible):
                    ssid, rssi = visible[i]
                    line = "  {:<28}{}dBm".format(ssid[:28], rssi)
                    abs_idx = visible_start + i
                    if abs_idx == self._settings_idx:
                        cache_key = '\x01' + line
                        if self._cache[i + 2] != cache_key:
                            self._cache[i + 2] = cache_key
                            self.tft.text(self.font, self._tb(_pad(line)), 0, y, self.YELLOW, self.SEL_BG)
                            self.tft.fill_rect(4, y, 3, CHAR_H, self.NEON_MAG)
                    else:
                        self._draw_row_cached(i + 2, line, y, self.NEON_CYAN)
                else:
                    self._draw_row_cached(i + 2, "", y, self.NEON_CYAN)

        self._draw_settings_bottom_bar()

    def _draw_wifi_pass(self):
        self._draw_row_cached(1, "Connect to: " + self._wifi_ssid[:26], BODY_Y, self.NEON_CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)

        if self._wifi_connecting:
            self._draw_row_cached(5, "Connecting...".center(COLS),
                                  BODY_Y + 4 * CHAR_H, self.NEON_GREEN)
            self.tft.text(self.font, _pad(""), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
        else:
            # Password input line
            self._draw_input_line(self.cmd_buf.decode())

    async def _do_wifi_scan(self):
        """Run WiFi scan in async task so UI renders 'Scanning...' first."""
        await asyncio.sleep_ms(100)  # yield to let UI draw "Scanning..."
        try:
            results = self.on_wifi_scan()
            self._wifi_networks = results or []
        except Exception as e:
            self._wifi_networks = []
            msg = str(e)
            # 0x0101 = ESP_ERR_NO_MEM: WLAN init needs ~120KB internal RAM,
            # already claimed by the native codecs + I2S (see README).
            self._wifi_err = ("no RAM for WiFi" if "0x0101" in msg
                              else (msg or "scan failed"))
            print("[WiFi] scan failed:", repr(e))
        self._wifi_scanning = False
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def set_wifi_result(self, ip):
        """Async WiFi connect finished — ip string on success, None on fail."""
        self._wifi_connecting = False
        if ip:
            self._wifi_connected = True
            self._wifi_ssid_current = self._wifi_ssid
            self._wifi_ip = ip
            self._wifi_err = ""
            # Auto-jump to TCP host entry
            self._settings_page = _SET_TCP_HOST
            self.cmd_buf = (bytearray(self._tcp_target.encode()) if self._tcp_target
                            else bytearray(self._tcp_default.encode()))
        else:
            self._wifi_err = "connect failed"
            self._settings_page = _SET_WIFI_SCAN
            self._settings_idx = 0
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def set_tcp_result(self, ok, addr):
        """Async TCP connect finished — back to the settings menu either way."""
        self._tcp_connecting = False
        if ok:
            self._tcp_enabled = True
            self._tcp_target = addr
        self._settings_page = _SET_MAIN
        self._settings_idx = 1
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def _draw_node_name(self):
        self._draw_row_cached(1, "Node Name", BODY_Y, self.NEON_CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)

        # Name input line
        self._draw_input_line(self.cmd_buf.decode())

    def _draw_tcp_host(self):
        self._draw_row_cached(1, "TCP Server Address", BODY_Y, self.NEON_CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)

        if self._tcp_connecting:
            self._draw_row_cached(5, "Connecting...".center(COLS),
                                  BODY_Y + 4 * CHAR_H, self.NEON_GREEN)
            self.tft.text(self.font, _pad(""), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
        else:
            # Address input line
            self._draw_input_line(self.cmd_buf.decode())

    def nav_event(self, name):
        """Feed a navigation event from something other than a trackball.

        Boards constructed with trackball=False have no pointing device, so
        their key handler calls this for the arrow and select keys. It drives
        the same counters the ISRs do, which keeps every navigation path in
        this class identical across boards.
        """
        if name == "up":
            self._irq_up += 1
        elif name == "down":
            self._irq_down += 1
        elif name == "left":
            self._irq_left += 1
        elif name == "right":
            self._irq_right += 1
        elif name == "click":
            self._irq_click += 1

    def _irq_handler_up(self, pin):
        t = time.ticks_ms()
        if time.ticks_diff(t, self._irq_last_scroll) >= _TB_DEBOUNCE_MS:
            self._irq_last_scroll = t
            self._irq_up += 1

    def _irq_handler_down(self, pin):
        t = time.ticks_ms()
        if time.ticks_diff(t, self._irq_last_scroll) >= _TB_DEBOUNCE_MS:
            self._irq_last_scroll = t
            self._irq_down += 1

    def _irq_handler_click(self, pin):
        # Fires on both edges. Hard-IRQ context: int arithmetic and attribute
        # stores only — anything that allocates raises inside the ISR.
        t = time.ticks_ms()
        if pin.value():  # rising edge: button released, classify the press
            # A release we never saw the press of (button held across boot)
            # carries no valid hold time — drop it rather than read it as long.
            if self._irq_pressed:
                self._irq_pressed = 0
                if time.ticks_diff(t, self._irq_press_ms) >= _TB_LONG_PRESS_MS:
                    self._irq_last_click = t  # release bounce adds no click
                    self._irq_long_click += 1
                elif time.ticks_diff(t, self._irq_last_click) >= 200:
                    self._irq_last_click = t
                    self._irq_click += 1
        else:            # falling edge: button pressed, start the hold timer
            self._irq_press_ms = t
            self._irq_pressed = 1

    def _irq_handler_left(self, pin):
        t = time.ticks_ms()
        if time.ticks_diff(t, self._irq_last_h) >= _TB_H_DEBOUNCE_MS:
            self._irq_last_h = t
            self._irq_left += 1

    def _irq_handler_right(self, pin):
        t = time.ticks_ms()
        if time.ticks_diff(t, self._irq_last_h) >= _TB_H_DEBOUNCE_MS:
            self._irq_last_h = t
            self._irq_right += 1

    def handle_trackball(self):
        """Drain IRQ-captured trackball events."""
        # Read and reset counters — no disable_irq needed; worst case
        # an ISR fires between read and reset, losing one tick (harmless).
        long_click = self._irq_long_click;  self._irq_long_click = 0
        up = self._irq_up;  self._irq_up = 0
        down = self._irq_down;  self._irq_down = 0
        click = self._irq_click;  self._irq_click = 0
        left = self._irq_left;  self._irq_left = 0
        right = self._irq_right;  self._irq_right = 0

        # Locking takes a long click; getting back in takes any click. The
        # ball sits recessed and stiff enough that a pocket press is not a
        # real risk, and demanding a 0.7s hold to look at the screen reads as
        # a stuck device. Either way the event is swallowed, so the click
        # that unlocks cannot also act on whatever it landed on.
        if self.locked:
            if click or long_click:
                self.unlock()
                return True
            return False

        # Locking is refused while recording: the mic thread owns the CPU and
        # no display state may change until capture ends.
        if long_click:
            if self.state != STATE_RECORDING:
                self.lock()
            return True

        if not (up or down or click or left or right):
            return False

        # If screen is off, consume the event and just wake
        if not self._screen_on:
            self.wake_screen()
            return True

        self.wake_screen()

        # An open RRC panel takes the trackball entirely: scroll the rows,
        # click to act on the highlighted one, no page/tab switching and no
        # scrollback movement underneath it. Both panels behave this way --
        # members in a room, rooms in the console.
        if self._rrc_panel and self.state in (STATE_RRC_CHAT, STATE_RRC_ROOMS):
            import rrc_ui
            if up:
                rrc_ui.panel_scroll(self, -up)
            if down:
                rrc_ui.panel_scroll(self, down)
            if click:
                rrc_ui.panel_click(self)
            self.dirty = True
            return True

        # Diagonal-roll jitter: the ball emits stray pulses on the other
        # axis — only the dominant axis of this drain cycle counts
        # (vertical wins ties: scrolling is the common gesture).
        if up + down >= left + right:
            left = right = 0
        else:
            up = down = 0

        for _ in range(up):
            self._scroll_up()
        for _ in range(down):
            self._scroll_down()
        if left or right:
            if self.state == STATE_NODES:
                # Cycle MSG -> NET -> SSH -> MSG (left = previous, right = next).
                self._switch_tab((self.node_tab - 1 if left else self.node_tab + 1) % N_TABS)
            elif self.state == STATE_BROWSER:
                if left:
                    self._browser_back()
                else:
                    self._browser_page(BODY_ROWS - 2)  # right = page down
            elif self.state == STATE_CHAT:
                # left/right = page through chat history
                _page = BODY_ROWS - 2
                if left:
                    self.chat_scroll = min(self.chat_scroll + _page,
                                           max(0, len(self._build_chat_lines()) - (BODY_ROWS - 1)))
                else:
                    self.chat_scroll = max(0, self.chat_scroll - _page)
                self.chat_cursor = -1
                self.dirty = True
            elif self.state == STATE_SHELL:
                if not self._shell_menu:   # page scroll (menu ignores left/right)
                    _pg = self._shell_rows() - 1
                    self._shell_scroll(_pg if left else -_pg)
            elif self.state == STATE_SETTINGS:
                self._settings_adjust(-1 if left else 1)
        if click:
            if self.state == STATE_IMAGE:
                self._exit_image_view()
            elif self.state == STATE_NODES:
                if self._find:
                    self._find_pick()
                elif self.node_tab == TAB_MSG:
                    self._enter_chat()
                elif self.node_tab == TAB_NET:
                    self._open_selected_node()
                elif self.node_tab == TAB_RRC:
                    import rrc_ui
                    rrc_ui.open_selected_hub(self)
                elif self.node_tab == TAB_SSH:
                    self._open_selected_shell()
            elif self.state == STATE_BROWSER:
                self._browser_follow_cursor()
            elif self.state == STATE_CHAT:
                # If cursor is on an image line, open it
                if self.chat_cursor >= 0 and self.chat_cursor in self._visible_image_lines:
                    mid = self._visible_image_lines[self.chat_cursor]
                    cache_key = (self.selected_peer, mid)
                    if cache_key in self._image_cache:
                        self._enter_image_view(mid)
                # If cursor is on a voice line, play it
                elif self.chat_cursor >= 0 and self.chat_cursor in self._visible_audio_lines:
                    mid = self._visible_audio_lines[self.chat_cursor]
                    cache_key = (self.selected_peer, mid)
                    if cache_key in self._audio_cache and self.on_audio_play:
                        audio_data, audio_mode = self._audio_cache[cache_key]
                        self.on_audio_play(audio_data, audio_mode)
            elif self.state == STATE_SETTINGS:
                self.handle_key(b'\x0D')
            elif self.state == STATE_RRC_ROOMS:
                # The console's own footer has always promised click=open.
                # It is also the only way in now: this screen has no text
                # field, so nothing else claims the gesture -- except the
                # join prompt, which owns the screen while it is up.
                if not self._rrc_prompt:
                    import rrc_ui
                    rrc_ui.panel_toggle(self, "rooms")
            elif self.state == STATE_RRC_CHAT:
                # Same gesture, the list this screen is about. The keyboard
                # cannot reach it: alt+w is resolved inside the keyboard's
                # own firmware and arrives as a plain 'w'.
                import rrc_ui
                rrc_ui.panel_toggle(self, "members")
            elif self.state == STATE_SHELL:
                # click opens the control-key menu, or sends the selection
                if self._shell_menu:
                    self._shell_menu_exec()
                else:
                    self._shell_menu_open()

        self.dirty = True
        return True

    def _scroll_up(self):
        if self.state == STATE_IMAGE:
            return
        elif self.state == STATE_NODES:
            if self._find:
                self._find_move(-1)
            elif self.node_tab == TAB_MSG:
                if self.selected_idx > 0:
                    self.selected_idx -= 1
                    if self.selected_idx < self.node_scroll:
                        self.node_scroll = self.selected_idx
            elif self.node_tab == TAB_NET:
                if self.net_idx > 0:
                    self.net_idx -= 1
                    if self.net_idx < self.net_scroll:
                        self.net_scroll = self.net_idx
            elif self.node_tab == TAB_RRC:
                if self._rrc_idx > 0:
                    self._rrc_idx -= 1
                    if self._rrc_idx < self._rrc_scroll:
                        self._rrc_scroll = self._rrc_idx
            else:  # TAB_SSH
                if self.ssh_idx > 0:
                    self.ssh_idx -= 1
                    if self.ssh_idx < self.ssh_scroll:
                        self.ssh_scroll = self.ssh_idx
        elif self.state == STATE_SETTINGS:
            self._settings_scroll_up()
        elif self.state == STATE_BROWSER:
            # Move cursor up; scroll viewport when cursor reaches top
            if self.browser_cursor > 0:
                self.browser_cursor -= 1
            elif self.browser_cursor == 0 and self.browser_scroll > 0:
                self.browser_scroll -= 1
            elif self.browser_cursor < 0:
                self.browser_cursor = 0
        elif self.state == STATE_SHELL:
            if self._shell_menu:
                self._shell_menu_move(-1)
            else:
                self._shell_scroll(1)     # scroll terminal toward older output
        elif self.state in (STATE_RRC_CHAT, STATE_RRC_ROOMS):
            # Covers both RRC screens that render through _visible_lines --
            # the room view and the hub console (MOTD / "/list" replies,
            # both routinely longer than one screen). Panel-open is handled
            # earlier in handle_trackball and never reaches here. One tick
            # = one wrapped line toward older scrollback, clamped so the
            # window can't run past the top (same flatten _visible_lines
            # uses, so the clamp matches what actually gets drawn).
            import rrc_ui
            rows = BODY_ROWS - 1
            max_scroll = max(0, len(rrc_ui._flatten(self)) - rows)
            if self._rrc_scroll_chat < max_scroll:
                self._rrc_scroll_chat += 1
        elif self.state == STATE_CHAT:
            # Move cursor up; scroll viewport when cursor reaches top
            _chat_rows = BODY_ROWS - 1
            if self.chat_cursor < 0:
                self.chat_cursor = _chat_rows - 1  # activate at bottom
            elif self.chat_cursor > 0:
                self.chat_cursor -= 1
            else:
                # Cursor at top — scroll viewport up (bounded so the view
                # never shrinks past the oldest full window)
                if self.chat_scroll < max(0, len(self._build_chat_lines()) - _chat_rows):
                    self.chat_scroll += 1
        else:
            # A state that wants trackball scrolling must claim it above --
            # this used to be a bare `else:` that silently meant "the LXMF
            # chat view" (STATE_RRC_CHAT/STATE_RRC_ROOMS both fell into it
            # in turn before they got their own branch). Doing nothing here
            # is a screen that just doesn't scroll, which gets noticed;
            # inheriting chat_cursor/chat_scroll doesn't.
            pass

    def _scroll_down(self):
        if self.state == STATE_IMAGE:
            return
        elif self.state == STATE_NODES:
            _rows = BODY_ROWS - 1  # tab bar takes the first body row
            if self._find:
                self._find_move(1)
            elif self.node_tab == TAB_MSG:
                if self.selected_idx < len(self._peer_keys) - 1:
                    self.selected_idx += 1
                    if self.selected_idx >= self.node_scroll + _rows:
                        self.node_scroll = self.selected_idx - _rows + 1
            elif self.node_tab == TAB_NET:
                if self.net_idx < len(self._node_keys) - 1:
                    self.net_idx += 1
                    if self.net_idx >= self.net_scroll + _rows:
                        self.net_scroll = self.net_idx - _rows + 1
            elif self.node_tab == TAB_RRC:
                if self._rrc_idx < len(self._rrc_keys) - 1:
                    self._rrc_idx += 1
                    if self._rrc_idx >= self._rrc_scroll + _rows:
                        self._rrc_scroll = self._rrc_idx - _rows + 1
            else:  # TAB_SSH
                if self.ssh_idx < len(self._shell_keys) - 1:
                    self.ssh_idx += 1
                    if self.ssh_idx >= self.ssh_scroll + _rows:
                        self.ssh_scroll = self.ssh_idx - _rows + 1
        elif self.state == STATE_SETTINGS:
            self._settings_scroll_down()
        elif self.state == STATE_BROWSER:
            # Move cursor down; scroll viewport when cursor reaches bottom
            _rows = BODY_ROWS - 1
            max_scroll = max(0, len(self.browser_lines) - _rows)
            if self.browser_cursor < 0:
                self.browser_cursor = 0
            elif self.browser_cursor < _rows - 1:
                self.browser_cursor += 1
            elif self.browser_scroll < max_scroll:
                self.browser_scroll += 1
        elif self.state == STATE_SHELL:
            if self._shell_menu:
                self._shell_menu_move(1)
            else:
                self._shell_scroll(-1)    # scroll terminal toward newer output
        elif self.state in (STATE_RRC_CHAT, STATE_RRC_ROOMS):
            # One tick = one wrapped line toward the newest message; 0 is
            # the floor, and the only way back to it. rrc_line() no longer
            # snaps here on arrival -- it holds the reader's anchor instead
            # -- so this is what returns the view to the live tail.
            if self._rrc_scroll_chat > 0:
                self._rrc_scroll_chat -= 1
        elif self.state == STATE_CHAT:
            # Move cursor down; scroll viewport when cursor reaches bottom
            _chat_rows = BODY_ROWS - 1
            if self.chat_cursor < 0:
                self.chat_cursor = 0  # activate at top
            elif self.chat_cursor < _chat_rows - 1:
                self.chat_cursor += 1
            else:
                # Cursor at bottom — scroll viewport down
                if self.chat_scroll > 0:
                    self.chat_scroll -= 1
        else:
            # A state that wants trackball scrolling must claim it above --
            # see the matching comment in _scroll_up.
            pass

    def _settings_scroll_up(self):
        if self._settings_page == _SET_MAIN:
            if self._settings_idx > 0:
                self._settings_idx -= 1
        elif self._settings_page == _SET_WIFI_SCAN:
            if self._settings_idx > 0:
                self._settings_idx -= 1
                if self._settings_idx < self._settings_scroll:
                    self._settings_scroll = self._settings_idx
        elif self._settings_page == _SET_RADIO:
            if self._settings_scroll > 0:
                self._settings_scroll -= 1
        elif self._settings_page == _SET_LORA:
            if self._lora_field > 0:
                self._lora_field -= 1
                self.dirty = True

    def _settings_scroll_down(self):
        if self._settings_page == _SET_MAIN:
            # 12 items: WiFi TCP Name LoRa Vol KbBL Announce Sleep Wake Radio
            #           LoRaCfg Addr
            if self._settings_idx < 11:
                self._settings_idx += 1
        elif self._settings_page == _SET_WIFI_SCAN:
            if self._settings_idx < len(self._wifi_networks) - 1:
                self._settings_idx += 1
                max_visible = BODY_ROWS - 2  # header row + 0-indexed
                if self._settings_idx >= self._settings_scroll + max_visible:
                    self._settings_scroll = self._settings_idx - max_visible + 1
        elif self._settings_page == _SET_RADIO:
            if self._settings_scroll < max(0, self._radio_rows - (BODY_ROWS - 1)):
                self._settings_scroll += 1
        elif self._settings_page == _SET_LORA:
            if self._lora_field < 5:   # 0-4 fields, 5 = Apply row
                self._lora_field += 1
                self.dirty = True

    def _cycle_timeout(self, delta=1):
        """Step the screen inactivity timeout through the preset choices."""
        choices = _TIMEOUT_CHOICES
        try:
            i = choices.index(self._screen_timeout_ms)
        except ValueError:
            i = 0
        self._screen_timeout_ms = choices[(i + delta) % len(choices)]
        if self.on_screen_timeout:
            try:
                self.on_screen_timeout(self._screen_timeout_ms)
            except Exception:
                pass

    def _cycle_wake(self, delta=1):
        """Step the auto-wake policy: 0 = messages, 1 = everything, 2 = never."""
        self._wake_mode = (self._wake_mode + delta) % 3
        if self.on_wake_mode:
            try:
                self.on_wake_mode(self._wake_mode)
            except Exception:
                pass

    def set_lora_config(self, cfg):
        """Sync the live radio params into the UI (called on boot and after a
        successful apply). Keeps _lora_cfg as the truth the editor starts from.
        bw is normalised to str so it matches _LORA_BW_CHOICES."""
        for k in _LORA_FIELDS:
            if k in cfg and cfg[k] is not None:
                self._lora_cfg[k] = cfg[k]
        self._lora_cfg["bw"] = str(self._lora_cfg["bw"])

    def _lora_cycle(self, field, delta):
        """Step one pending field by delta (trackball L/R or click). BW wraps
        through the choice list; SF/CR/TX clamp; freq steps by _LORA_FREQ_STEP."""
        e = self._lora_edit
        if e is None:
            return
        if field == "freq_khz":
            e["freq_khz"] = _clamp(e["freq_khz"] + delta * _LORA_FREQ_STEP,
                                   _LORA_FREQ_MIN, _LORA_FREQ_MAX)
        elif field == "bw":
            choices = _LORA_BW_CHOICES
            try:
                i = choices.index(str(e["bw"]))
            except ValueError:
                i = 0
            e["bw"] = choices[(i + delta) % len(choices)]
        elif field == "sf":
            e["sf"] = _clamp(e["sf"] + delta, _LORA_SF_MIN, _LORA_SF_MAX)
        elif field == "coding_rate":
            e["coding_rate"] = _clamp(e["coding_rate"] + delta, _LORA_CR_MIN, _LORA_CR_MAX)
        elif field == "tx_power":
            e["tx_power"] = _clamp(e["tx_power"] + delta, _LORA_TX_MIN, _LORA_TX_MAX)

    def _lora_apply(self):
        """Push the pending edit to the radio via on_lora_config; on success
        commit it to _lora_cfg (so the main-page summary and a later reset use
        the new values). The page stays open showing the result."""
        params = dict(self._lora_edit)
        params["bw"] = str(params["bw"])
        ok = False
        if self.on_lora_config:
            try:
                ok = bool(self.on_lora_config(params))
            except Exception:
                ok = False
        if ok:
            self._lora_cfg = dict(self._lora_edit)
            self._lora_applying = "applied"
        else:
            self._lora_applying = "failed"
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def _settings_adjust(self, delta):
        """Trackball left/right on an adjustable settings row."""
        if self._settings_page == _SET_LORA:
            if 0 <= self._lora_field <= 4:
                self._lora_cycle(_LORA_FIELDS[self._lora_field], delta)
                self._lora_applying = ""
                self._cache = [''] * CACHE_ROWS
                self.dirty = True
            return
        if self._settings_page != _SET_MAIN:
            return
        if self._settings_idx == 4:      # Volume
            self._volume = max(0, min(10, self._volume + delta))
            if self.on_volume:
                self.on_volume(self._volume)
        elif self._settings_idx == 7:    # Sleep timeout
            self._cycle_timeout(delta)
        elif self._settings_idx == 8:    # Auto-wake policy
            self._cycle_wake(delta)
        else:
            return
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    # --- Data management ---

    def clear_peers(self):
        """Clear node list, chat history, and related state."""
        self.peers.clear()
        self._peer_keys.clear()
        self.chat_history.clear()
        self._load_favorite("peer")
        # Media is keyed by message id, which nothing will reference again
        # once the history holding those ids is gone.
        self._image_cache.clear()
        self._audio_cache.clear()
        self._image_cache_order = []
        self.unread = {}
        self.selected_peer = None
        self.selected_idx = 0
        self.node_scroll = 0
        self.chat_scroll = 0
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def _forget_peer_state(self, key):
        """Drop every local trace of a peer key: chat, unread, media caches."""
        self.chat_history.pop(key, None)
        self.unread.pop(key, None)
        if self._chat_lines_peer == key:
            self._chat_lines_cache = None
        for cache in (self._image_cache, self._audio_cache):
            for k in [ck for ck in cache if ck[0] == key]:
                cache.pop(k, None)
        self._image_cache_order = [ck for ck in self._image_cache_order if ck[0] != key]

    def add_peer(self, dest_hash, name, rssi=None, hops=None, via=None, fav=False):
        """Add or update a peer from an announce."""
        if dest_hash not in self.peers and len(self.peers) >= MAX_PEERS:
            # Evict the least-recently-seen peer — never index 0, which
            # message-bubbling makes the most active chat.
            sel_key = (self._peer_keys[self.selected_idx]
                       if self.selected_idx < len(self._peer_keys) else None)
            oldest = min(filter(lambda k: not self.peers[k].get("fav", False), self._peer_keys),
                         key=lambda k: self.peers[k].get("seen", 0), default=None)
            self._peer_keys.remove(oldest)
            del self.peers[oldest]
            self._forget_peer_state(oldest)
            if self.selected_peer == oldest:
                self.selected_peer = None
            # re-anchor the cursor to the same peer it was on
            if sel_key is not None and sel_key != oldest and sel_key in self._peer_keys:
                self.selected_idx = self._peer_keys.index(sel_key)
            elif self.selected_idx >= len(self._peer_keys):
                self.selected_idx = max(0, len(self._peer_keys) - 1)
        if fav is False and dest_hash in self.peers:
            fav = self.peers[dest_hash].get("fav")
        self.peers[dest_hash] = {"name": name or "?", "rssi": rssi,
                                 "hops": hops, "via": via, "seen": time.time(), "fav": fav}
        if dest_hash not in self._peer_keys:
            self._peer_keys.append(dest_hash)
        self._route_cache = ''  # selected-peer footer may show new route info
        self.dirty = True

    def delete_selected(self):
        """Forget the selected peer (MSG) / node (NET) / hub (RRC) / listener
        (SSH) and its local state. The entry re-appears on the next announce;
        this just clears clutter."""
        if self.node_tab == TAB_MSG:
            if not (0 <= self.selected_idx < len(self._peer_keys)):
                return
            if self.peers[self._peer_keys[self.selected_idx]].get("fav") == True:
                return # Don't delete favorites
            key = self._peer_keys.pop(self.selected_idx)
            self.peers.pop(key, None)
            self._forget_peer_state(key)
            if self.selected_peer == key:
                self.selected_peer = None
            if self.selected_idx >= len(self._peer_keys):
                self.selected_idx = max(0, len(self._peer_keys) - 1)
            if self.node_scroll > max(0, len(self._peer_keys) - (BODY_ROWS - 1)):
                self.node_scroll = max(0, len(self._peer_keys) - (BODY_ROWS - 1))
            if self.on_delete_peer:
                try:
                    self.on_delete_peer(key)
                except Exception:
                    pass
        elif self.node_tab == TAB_NET:
            if not (0 <= self.net_idx < len(self._node_keys)):
                return
            if self.nomad_nodes[self._node_keys[self.net_idx]].get("fav") == True:
                return # Don't delete favorites
            key = self._node_keys.pop(self.net_idx)
            self.nomad_nodes.pop(key, None)
            if self.net_idx >= len(self._node_keys):
                self.net_idx = max(0, len(self._node_keys) - 1)
            if self.net_scroll > max(0, len(self._node_keys) - (BODY_ROWS - 1)):
                self.net_scroll = max(0, len(self._node_keys) - (BODY_ROWS - 1))
        elif self.node_tab == TAB_RRC:
            if not (0 <= self._rrc_idx < len(self._rrc_keys)):
                return
            if self.rrc_hubs[self._rrc_keys[self._rrc_idx]].get("fav") == True:
                return # Don't delete favorites
            key = self._rrc_keys.pop(self._rrc_idx)
            self.rrc_hubs.pop(key, None)
            if self._rrc_idx >= len(self._rrc_keys):
                self._rrc_idx = max(0, len(self._rrc_keys) - 1)
            if self._rrc_scroll > max(0, len(self._rrc_keys) - (BODY_ROWS - 1)):
                self._rrc_scroll = max(0, len(self._rrc_keys) - (BODY_ROWS - 1))
        else:  # TAB_SSH
            if not (0 <= self.ssh_idx < len(self._shell_keys)):
                return
            if self.shell_nodes[self._shell_keys[self.ssh_idx]].get("fav") == True:
                return # Don't delete favorites
            key = self._shell_keys.pop(self.ssh_idx)
            self.shell_nodes.pop(key, None)
            if self.ssh_idx >= len(self._shell_keys):
                self.ssh_idx = max(0, len(self._shell_keys) - 1)
            if self.ssh_scroll > max(0, len(self._shell_keys) - (BODY_ROWS - 1)):
                self.ssh_scroll = max(0, len(self._shell_keys) - (BODY_ROWS - 1))
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    def add_nomad_node(self, dest_hash, name, hops=None, seen=None, fav=False):
        """Add or update a NomadNet node (NET tab, called by nomad_browser)."""
        prev = self.nomad_nodes.get(dest_hash)
        if prev is None and len(self.nomad_nodes) >= MAX_PEERS:
            oldest = min(filter(lambda k: not self.nomad_nodes[k].get("fav", False), self._node_keys), 
                         key=lambda k: self.nomad_nodes[k].get("seen", 0), default=None)
            del self.nomad_nodes[oldest]
            self._node_keys.remove(oldest)
            if self.net_idx >= len(self._node_keys):
                self.net_idx = max(0, len(self._node_keys) - 1)
        if prev:
            if name is None:
                name = prev.get("name")
            # never let a stale storage seed overwrite a fresher announce
            if seen is not None and prev.get("seen", 0) > seen:
                seen = prev["seen"]
            if fav is False:
                fav = prev["fav"]
        self.nomad_nodes[dest_hash] = {"name": name or "?", "hops": hops,
                                       "seen": time.time() if seen is None else seen, "fav": fav}
        if dest_hash not in self._node_keys:
            self._node_keys.append(dest_hash)
        self._route_cache = ''
        self.dirty = True

    def clear_nomad_nodes(self):
        """Clear the NET tab (interface switch)."""
        self.nomad_nodes.clear()
        self._node_keys.clear()
        self._load_favorite("node")
        self.net_idx = 0
        self.net_scroll = 0
        self._cache = [''] * CACHE_ROWS
        self.dirty = True

    @staticmethod
    def _msg_index(hist, mid):
        """List position of a message id, or -1 once it has aged out. Walks
        backwards: status updates and clicks target recent messages."""
        for i in range(len(hist) - 1, -1, -1):
            if hist[i][6] == mid:
                return i
        return -1

    def _drop_message(self, dest_hash, msg):
        """Release the media held by a message that just aged out of history.
        Ids are never reused, so nothing else would ever collect these."""
        key = (dest_hash, msg[6])
        self._image_cache.pop(key, None)
        self._audio_cache.pop(key, None)
        if key in self._image_cache_order:
            self._image_cache_order.remove(key)

    def add_chat_message(self, dest_hash, is_mine, text, status=0, image=None, audio=None, audio_mode=None):
        """Add a message to chat history. Returns its id — a handle that stays
        valid while the chat scrolls, which a list position does not."""
        if dest_hash not in self.chat_history:
            self.chat_history[dest_hash] = []
        hist = self.chat_history[dest_hash]
        has_image = image is not None
        has_audio = audio is not None
        mid = self._next_mid
        self._next_mid += 1
        hist.append((is_mine, text, time.time(), status, has_image, has_audio, mid))
        while len(hist) > MAX_HISTORY:
            self._drop_message(dest_hash, hist.pop(0))

        # Cache audio data for replay
        if audio is not None:
            self._audio_cache[(dest_hash, mid)] = (audio, audio_mode)

        # Cache image data (LRU eviction)
        if image is not None:
            cache_key = (dest_hash, mid)
            self._image_cache[cache_key] = image
            self._image_cache_order.append(cache_key)
            while len(self._image_cache_order) > MAX_CACHED_IMAGES:
                old_key = self._image_cache_order.pop(0)
                self._image_cache.pop(old_key, None)

        # Track unread: incoming message when not viewing that chat
        if not is_mine:
            if self.state == STATE_NODES or self.selected_peer != dest_hash:
                self.unread[dest_hash] = self.unread.get(dest_hash, 0) + 1
            # Bubble peer to top of node list, keeping the cursor on the
            # same peer (not blindly on row 0, which could be a different one).
            if dest_hash in self._peer_keys:
                sel_key = (self._peer_keys[self.selected_idx]
                           if self.selected_idx < len(self._peer_keys) else None)
                self._peer_keys.remove(dest_hash)
                self._peer_keys.insert(0, dest_hash)
                if self.state == STATE_NODES:
                    if sel_key is not None and sel_key in self._peer_keys:
                        self.selected_idx = self._peer_keys.index(sel_key)
                    if self.selected_idx < self.node_scroll:
                        self.node_scroll = self.selected_idx

        # The line cache outlives the chat screen: leaving a chat keeps it
        # tagged with that peer, so a message arriving meanwhile (node list,
        # recording, image viewer) must drop it or reopening shows stale lines.
        if (self._chat_lines_peer == dest_hash
                and not (self.state == STATE_CHAT and self.selected_peer == dest_hash)):
            self._invalidate_chat_lines()

        if self.state == STATE_CHAT:
            if self.selected_peer == dest_hash:
                # Snap to bottom for our own sends or when already at the
                # bottom; otherwise keep the reader's position while scrolled up.
                if is_mine or self.chat_scroll == 0:
                    self.chat_scroll = 0
                    self._invalidate_chat_lines()
                elif (self._chat_lines_cache is not None
                      and self._chat_lines_peer == dest_hash):
                    old_n = len(self._chat_lines_cache)
                    self._invalidate_chat_lines()
                    new_n = len(self._build_chat_lines())
                    self.chat_scroll += max(0, new_n - old_n)
                else:
                    self._invalidate_chat_lines()
            # Invalidate body row cache — lines shift when new message arrives
            for i in range(1, BODY_ROWS + 1):
                self._cache[i] = ''
            self.dirty = True
        elif self.state != STATE_RECORDING:
            # On node list — mark dirty so unread indicator shows. Skip during
            # recording: the screen redraw would steal GIL from the mic thread;
            # the message is stored and shows when recording ends.
            self.dirty = True
        return mid

    def update_message_status(self, dest_hash, mid, status):
        """Update delivery status of a message, located by id: a long-running
        send outlives its row's position, and may outlive the row itself."""
        hist = self.chat_history.get(dest_hash)
        if not hist:
            return
        i = self._msg_index(hist, mid)
        if i < 0:
            return  # aged out of history while the send was in flight
        old = hist[i]
        hist[i] = (old[0], old[1], old[2], status, old[4], old[5], old[6])
        if self._chat_lines_peer == dest_hash:
            self._invalidate_chat_lines()
        if self.state == STATE_CHAT and self.selected_peer == dest_hash:
            for j in range(1, BODY_ROWS + 1):
                self._cache[j] = ''
            self.dirty = True

    def update_battery(self):
        """Read pack voltage, leaving the last good value alone on a miss.

        on_battery comes from the board, because the two boards sense the
        battery in completely different ways: the v1 through an ADC divider,
        the Pro through a BQ27220 gauge. Falls back to adc_reader so a board
        that sets no hook behaves as it always did.
        """
        v = None
        if self.on_battery is not None:
            try:
                v = self.on_battery()
            except Exception:
                v = None
        else:
            try:
                import adc_reader
                v = adc_reader.battery_voltage()
            except Exception:
                v = None
        if v is not None:
            self.bat_v = v

    # --- Main draw ---

    def draw(self):
        """Cached screen redraw — skips unchanged rows."""
        # Image state is handled separately in gui_loop (needs SPI acquire/release)
        if self.state == STATE_IMAGE:
            self.dirty = False
            return

        # Clear body + invalidate cache on screen state change
        if self.state != self._prev_state:
            self.tft.fill_rect(0, NAV_H, SCREEN_W, SCREEN_H - NAV_H, self.BG_DARK)
            self._cache = [''] * CACHE_ROWS  # invalidate all rows
            self._prev_state = self.state

        self.draw_navbar()
        if self.state == STATE_NODES:
            self.draw_node_list()
        elif self.state == STATE_SETTINGS:
            self.draw_settings()
        elif self.state == STATE_CHAT:
            self.draw_chat()
            self.draw_input()
        elif self.state == STATE_BROWSER:
            self.draw_browser()
        elif self.state == STATE_SHELL:
            self.draw_shell()
        elif self.state == STATE_RRC_ROOMS:
            import rrc_ui
            rrc_ui.draw_rooms(self)
        elif self.state == STATE_RRC_CHAT:
            import rrc_ui
            rrc_ui.draw_room(self)
        elif self.state == STATE_RECORDING:
            self._draw_recording()
        # Shared neon body frame — drawn last so corners stay crisp on every
        # screen (cheap: 2 rails + 4 short arms).
        self._draw_frame()
        self.dirty = False
        self._input_dirty = False

    # --- Async loops ---

    async def kbd_loop(self):
        """Fast keyboard + trackball polling — independent of drawing."""
        while True:
            for _ in range(5):  # drain up to 5 keys per cycle
                key = self.get_key()
                if key == b'\x00':
                    break
                if self.locked:
                    continue  # keep draining the I2C queue, drop the keys
                if not self._screen_on:
                    self.wake_screen()
                    # Drain remaining keys — first press only wakes
                    for _ in range(10):
                        if self.get_key() == b'\x00':
                            break
                    break
                self.wake_screen()
                self.handle_key(key)
            self.handle_trackball()
            await asyncio.sleep_ms(20)

    async def gui_loop(self, spi_acquire_display, spi_release_display):
        """Drawing + input loop. kbd_loop handles fast polling separately."""
        self._spi_acquire = spi_acquire_display
        self._spi_release = spi_release_display
        self._last_draw = 0

        # Initial draw
        spi_acquire_display()
        self.draw()
        self._flush()
        spi_release_display()

        while True:
            now = time.ticks_ms()

            # Screen timeout: turn off after inactivity (0 = never). Never
            # sleep mid-transfer or mid-audio — the user is waiting on it.
            _busy = self.transfer_progress is not None or self._audio_status is not None
            if (self._screen_on and self._screen_timeout_ms and not _busy
                    and time.ticks_diff(now, self._last_activity) > self._screen_timeout_ms):
                self.sleep_screen()
                spi_release_display()

            if not self._screen_on:
                await asyncio.sleep_ms(200)
                continue

            # Image view: render JPEG once, then idle until key press
            if self.state == STATE_IMAGE:
                if not self._image_drawn:
                    self.draw_image(spi_acquire_display, spi_release_display)
                await asyncio.sleep_ms(50)
                continue

            # Progress update: redraw center section only, no full clear
            if self._progress_dirty and not self.dirty:
                spi_acquire_display()
                self._nav_mid_cache = ''  # force center section redraw
                self.draw_navbar()
                self._flush()
                spi_release_display()
                self._progress_dirty = False

            # Redraw: immediate for input line, throttled for full redraws.
            # draw_input() is the chat input renderer; settings text pages
            # (WiFi pass / node name / TCP host) redraw via their own draw().
            # The shell throttles harder: a repaint composites up to 2560
            # glyphs, and every arriving stdout chunk marks it dirty. At LoRa
            # data rates coalescing a burst into one repaint is invisible.
            _throttle = 120 if self.state == STATE_SHELL else 50
            if self._input_dirty and not self.dirty:
                spi_acquire_display()
                if self.state == STATE_CHAT:
                    self.draw_input()
                else:
                    self.draw()
                self._flush()
                spi_release_display()
                self._input_dirty = False
            elif self.dirty and time.ticks_diff(now, self._last_draw) > _throttle:
                spi_acquire_display()
                self.draw()
                self._flush()
                spi_release_display()
                self._last_draw = now

            await asyncio.sleep_ms(10 if self.dirty or self._input_dirty else 100)

    async def battery_loop(self, spi_acquire_display, spi_release_display):
        """Update battery reading every 10s, only redraw if changed."""
        _last_bl = -1
        while True:
            self.update_battery()
            bl = 3 if self.bat_v > 3.9 else (2 if self.bat_v > 3.6 else (1 if self.bat_v > 3.3 else 0))
            # Don't redraw mid-recording — the display SPI would steal GIL from
            # the mic thread (the level is re-checked after recording ends).
            if bl != _last_bl and self._screen_on and self.state != STATE_RECORDING:
                _last_bl = bl
                self._nav_bat_cache = ''  # only invalidate battery section
                self.dirty = True
            await asyncio.sleep(10)

    async def ticker_loop(self):
        """1s housekeeping tick: refresh the radio stats page while open,
        expire the transient ping status, and repaint the navbar clock on
        minute changes. Never draws — row caches skip unchanged text."""
        _last_min = -1
        while True:
            # Skip entirely during recording: no housekeeping redraw is worth
            # stealing GIL cycles from the mic capture thread.
            if self._screen_on and self.state != STATE_RECORDING:
                if self.state == STATE_SETTINGS and self._settings_page == _SET_RADIO:
                    self.dirty = True
                # Expire a finished ping result 8s after it resolved; keep
                # "ping..." on screen while the receipt is still outstanding.
                if (self.ping_status and not self.ping_pending
                        and time.ticks_diff(time.ticks_ms(), self._ping_status_ms) >= 8000):
                    self.ping_status = None
                    if self.state == STATE_NODES:
                        self.dirty = True
                # Drop the announce ">>>" flash once its 2s window has passed.
                if (self.announce_flash
                        and time.ticks_diff(time.ticks_ms(), self.announce_flash) >= 2000):
                    self.announce_flash = 0
                    self.dirty = True
                if _clock_valid():
                    m = time.localtime()[4]
                    if m != _last_min:
                        _last_min = m
                        self.dirty = True
            await asyncio.sleep(1)

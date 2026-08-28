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

# Node-screen tabs
TAB_MSG = 0
TAB_NET = 1
TAB_SSH = 2
N_TABS  = 3

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

# LoRa radio config: allowed values for the editor. BW is offered as the
# three practical LoRa bandwidths (the SX1262 also supports narrower ones,
# rarely used on a mesh). SF/CR/TX clamp at their bounds; freq is stepped
# or typed in kHz. These are the ranges the sx126x driver's configure()
# accepts (SF 6-12, CR 4/5-4/8), narrowed to sane mesh values.
_LORA_FIELDS = ("freq_khz", "bw", "sf", "coding_rate", "tx_power")
_LORA_BW_CHOICES = ("125", "250", "500")
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

# Scrollbar lane — kept 1px clear of the frame's right corner arm at x=319
SBAR_X = SCREEN_W - 3   # 317
SBAR_W = 2

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


# Keep displayable chars (ASCII + mapped Cyrillic), collapse whitespace —
# emoji/CJK removal leaves gaps
def _ascii(s):
    raw = ''.join(c for c in s if 32 <= ord(c) < 127 or ord(c) in _CYR)
    return ' '.join(raw.split())

# Pad string to exact width (no clearing needed)
def _pad(s, width=COLS):
    if len(s) >= width:
        return s[:width]
    return s + ' ' * (width - len(s))


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
        return str(d // 60) + "m"
    if d < 86400:
        return str(d // 3600) + "h"
    return str(d // 86400) + "d"


def _sw565(c):
    """Byte-swap an RGB565 colour for framebuf.

    framebuf stores 16-bit pixels in native (little-endian) order and
    blit_buffer() ships the bytes to the panel raw, but the ST7789 wants
    big-endian on the wire. tft.text() hides this by swapping internally
    (_swap_bytes in st7789.c); blit_buffer does not, so we pre-swap here."""
    return ((c << 8) | (c >> 8)) & 0xFFFF


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

        # Cyberpunk color palette (RGB565)
        self.YELLOW     = 0xFFE0
        self.BG_DARK    = 0x0821  # very dark blue-grey — main background
        self.NEON_CYAN  = 0x07FF  # primary text, borders
        self.NEON_GREEN = 0x07E0  # "me>" prefix, input prompt, active items
        self.NEON_MAG   = 0xF81F  # unread markers, accents
        self.DIM_CYAN   = 0x0514  # secondary/dimmed text
        self.HEADER_BG  = 0x0011  # very dark blue — navbar background
        self.SEL_BG     = 0x2966  # selection highlight — bright blue tint
        self.BODY_FG    = 0xC618  # light grey — message body text (matches micron)

        # State
        self.state = STATE_NODES
        self.dirty = True
        self._input_dirty = False
        self._prev_state = -1  # force full clear on first draw
        self._state_change_ms = 0  # debounce rapid state flips

        # Row cache: 15 slots (navbar + 12 body + sep + input)
        # Compared before drawing — skip SPI if row unchanged.
        self._cache = [''] * 15
        self._nav_bat_cache = ''
        self._nav_mid_cache = ''
        self._nav_right_cache = ''

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
        self._shell_manual = False   # manual hex-entry sub-mode on the SSH tab
        self._shell_hex = bytearray()

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
        self._irq_left = 0
        self._irq_right = 0
        # ISR debounce timestamps (ticks_ms is ISR-safe)
        self._irq_last_scroll = 0
        self._irq_last_click = 0
        self._irq_last_h = 0

        # Trackball pins, and the hardware interrupts that feed the counters
        # above. Boards without a trackball pass trackball=False and drive the
        # same counters through nav_event() instead -- on the T-Deck Pro these
        # GPIOs are LoRa CS, GPS PPS, the keyboard interrupt and the
        # vibration motor, so claiming them as pulled-up inputs would break
        # the radio.
        if trackball:
            self._tb_up    = Pin(3, Pin.IN, Pin.PULL_UP)
            self._tb_down  = Pin(15, Pin.IN, Pin.PULL_UP)
            self._tb_left  = Pin(1, Pin.IN, Pin.PULL_UP)
            self._tb_right = Pin(2, Pin.IN, Pin.PULL_UP)
            self._tb_click = Pin(0, Pin.IN, Pin.PULL_UP)
            self._tb_up.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_up)
            self._tb_down.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_down)
            self._tb_click.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_click)
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
        self._kbd_bl = False  # keyboard backlight state (restored from settings)
        self._auto_announce = False   # periodic re-announce toggle
        self._wifi_connecting = False  # True while an async WiFi connect is running
        self._wifi_err = ""            # last connect failure note (shown on scan page)
        self._tcp_connecting = False   # True while an async TCP connect is running
        self._screen_timeout_ms = _SCREEN_TIMEOUT_MS  # configurable inactivity sleep

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

        # Node identity/info (set by tdeck_node.py)
        self.my_address = None       # own LXMF address hex string
        self.my_identity_hash = None # own identity hash (for rnsh -a auth lists)
        self.get_radio_stats = None  # () -> [(label, value), ...] for radio page

        # Callbacks (set by tdeck_node.py)
        self.on_send = None       # on_send(dest_hash_bytes, text)
        self.on_announce = None   # on_announce()
        self.on_ping = None       # on_ping(dest_hash_bytes)
        self.on_wifi_scan = None      # () -> [(ssid, rssi), ...]
        self.on_wifi_connect = None   # (ssid, password) -> None — async; calls set_wifi_result
        self.on_tcp_toggle = None     # (enabled, host, port) -> bool — sync OFF path
        self.on_tcp_connect = None    # (host, port) -> None — async; calls set_tcp_result
        self.on_node_name = None      # (name) -> None
        self.on_lora_reset = None     # () -> bool
        self.on_lora_config = None    # (params) -> bool — live-apply + persist radio params
        self.on_volume = None         # (level) -> None
        self.on_kbd_backlight = None  # (enabled) -> bool
        self.on_audio_play = None     # (codec2_bytes, mode) -> None
        self.on_record_start = None   # () -> None
        self.on_record_stop = None    # (send: bool) -> None
        self.on_browse = None         # (dest_hash_bytes) -> None — open node index page
        self.on_browse_follow = None  # (url_str) -> None — follow a micron link
        self.on_browse_back = None    # () -> bool — went back (False: at stack bottom)
        self.on_browse_refresh = None # () -> None
        self.on_browser_exit = None   # () -> None — left the browser (free the link)
        self.on_net_seed = None       # () -> None — populate nomad_nodes from storage
        self.on_screen_timeout = None # (ms) -> None — persist inactivity timeout
        self.on_auto_announce = None  # (enabled) -> None — start/stop periodic announce
        self.on_delete_peer = None    # (dest_hash_bytes) -> None — forget a peer/node
        # rnsh shell callbacks (wired by tdeck_node.py)
        self.on_shell_connect = None    # (dest_hash, cols, rows) -> None
        self.on_shell_input = None      # (bytes) -> None — send stdin to remote
        self.on_shell_disconnect = None # () -> None — tear down the session
        self.on_shell_seed = None       # () -> None — populate shell_nodes from storage
        self.on_shell_resize = None     # (rows, cols) -> None

    # --- Screen power management ---

    def set_backlight(self, bl_pin):
        self._bl = bl_pin

    def wake_screen(self):
        if not self._screen_on:
            if self._bl:
                self._bl.value(1)
            self._screen_on = True
            self.dirty = True
            self._cache = [''] * 15
        self._last_activity = time.ticks_ms()

    def sleep_screen(self):
        if self._screen_on:
            if self._bl:
                self._bl.value(0)
            self._screen_on = False

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
        _top = BODY_Y - 3
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
        else:
            bat_v_str = "{:.1f}V".format(self.bat_v)
        name = self.node_name[:10]
        ann = ">>>" if (self.announce_flash and time.ticks_diff(time.ticks_ms(), self.announce_flash) < 2000) else ""

        # Center section: iface + optional SNR or transfer progress
        if self._audio_status:
            center = "[" + self._audio_status + "]"
        elif self.transfer_progress:
            rcv, tot = self.transfer_progress
            center = "RX " + str(rcv) + "/" + str(tot)
        elif self._tcp_enabled:
            center = "[TCP]"
        elif not self.lora_online:
            center = "[LoRa FAIL]"
        elif self.rssi is not None:
            center = "[LoRa] snr:" + str(self.snr or 0)
        else:
            center = "[LoRa]"

        # Mesh-synced wall clock (only meaningful after time sync)
        if _clock_valid():
            center = center + " " + _fmt_clock()

        # Right side: [ann][name] right-aligned
        right_str = (ann + " " if ann else "") + name
        left_w = 4 + len(bat_v_str)  # icon chars + voltage
        right_w = len(right_str)
        mid_w = COLS - left_w - right_w
        center_color = self.NEON_MAG if (not self._tcp_enabled and not self.lora_online) else self.DIM_CYAN

        hb = self.HEADER_BG

        # --- Section-based redraw: only repaint what changed ---

        # Build per-section cache keys
        bl = 3 if self.bat_v > 3.9 else (2 if self.bat_v > 3.6 else (1 if self.bat_v > 3.3 else 0))
        bat_key = bat_v_str + str(bl)
        mid_key = center
        right_key = right_str + ann

        # Full navbar invalidation: reset section caches + fill background
        if self._cache[0] == '':
            self._nav_bat_cache = ''
            self._nav_mid_cache = ''
            self._nav_right_cache = ''
            self.tft.fill_rect(0, 0, SCREEN_W, NAV_H, hb)

        # Battery section (icon + voltage text)
        if self._nav_bat_cache != bat_key:
            self._nav_bat_cache = bat_key
            # Battery icon (28x12 at top-left)
            gr = self.NEON_GREEN
            dm = self.DIM_CYAN
            self.tft.fill_rect(1, 4, 26, 12, gr)
            self.tft.fill_rect(2, 5, 24, 10, hb)
            self.tft.fill_rect(27, 7, 2, 6, gr)
            self.tft.fill_rect(3,  6, 7, 8, gr if bl >= 1 else dm)
            self.tft.fill_rect(11, 6, 7, 8, gr if bl >= 2 else dm)
            self.tft.fill_rect(19, 6, 7, 8, gr if bl >= 3 else dm)
            # Voltage text (padded to fixed width to clear old chars)
            self.tft.text(self.font, _pad(bat_v_str, 5), 4 * CHAR_W, NAV_TY, self.NEON_GREEN, hb)

        # Center section (interface status / progress)
        if self._nav_mid_cache != mid_key:
            self._nav_mid_cache = mid_key
            mid_str = center.center(mid_w) if mid_w > len(center) else center[:mid_w]
            center_x = left_w * CHAR_W
            # Pad to full mid_w to clear old text
            self.tft.text(self.font, self._tb(_pad(mid_str, mid_w)), center_x, NAV_TY, center_color, hb)

        # Right section (announce flash + node name)
        if self._nav_right_cache != right_key:
            self._nav_right_cache = right_key
            right_x = (COLS - right_w) * CHAR_W
            # Clear right area first (name length may change)
            right_max = COLS - left_w - mid_w
            self.tft.text(self.font, _pad(right_str, right_max), right_x, NAV_TY, self.NEON_CYAN, hb)
            if ann:
                self.tft.text(self.font, ann, right_x, NAV_TY, self.NEON_MAG, hb)

        # Update composite cache key
        self._cache[0] = bat_key + mid_key + right_key

    # --- Node list screen ---

    def _draw_tab_bar(self):
        """MSG/NET/SSH tab bar — first body row of the node screen."""
        un = 0
        for v in self.unread.values():
            un += v
        tabs = (
            " MSG(" + str(len(self._peer_keys)) + ("*" if un else "") + ") ",
            " NET(" + str(len(self._node_keys)) + ") ",
            " SSH(" + str(len(self._shell_keys)) + ") ",
        )
        cache_key = str(self.node_tab) + "".join(tabs)
        if self._cache[1] == cache_key:
            return
        self._cache[1] = cache_key
        y = BODY_Y
        self.tft.fill_rect(0, y, SCREEN_W, 16, self.BG_DARK)
        x = 0
        for i, label in enumerate(tabs):
            if i == self.node_tab:
                self.tft.text(self.font, label, x, y, self.NEON_GREEN, self.SEL_BG)
            else:
                self.tft.text(self.font, label, x, y, self.DIM_CYAN, self.BG_DARK)
            x += len(label) * CHAR_W
        self.tft.fill_rect(0, y + CHAR_H - 1, SCREEN_W, 1, self.DIM_CYAN)

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
        for i in range(_rows):
            y = BODY_Y + (i + 1) * CHAR_H
            ci = i + 2
            if i < len(visible):
                key = visible[i]
                entry = table[key]
                name = _ascii(entry.get("name") or "?")
                hash_tag = "[" + key.hex()[:8] + "]"
                uc = self.unread.get(key, 0) if show_unread else 0
                marker = str(min(uc, 9)) + "*" if uc > 1 else ("* " if uc == 1 else "  ")
                # Hash in brackets, 1 char right padding
                _rpad = 1
                max_name = COLS - len(marker) - len(hash_tag) - _rpad
                left = marker + name[:max_name]
                line = left + " " * (COLS - len(left) - len(hash_tag) - _rpad) + hash_tag
                hash_x = (COLS - len(hash_tag) - _rpad) * CHAR_W

                abs_idx = scroll + i
                if abs_idx == sel_idx:
                    cache_key = '\x01' + line
                    if self._cache[ci] != cache_key:
                        self._cache[ci] = cache_key
                        self.tft.text(self.font, self._tb(_pad(line)), 0, y, self.YELLOW, self.SEL_BG)
                        if uc:
                            self.tft.text(self.font, marker, 0, y, self.NEON_MAG, self.SEL_BG)
                        # Dim hash on right
                        self.tft.text(self.font, hash_tag, hash_x, y, self.DIM_CYAN, self.SEL_BG)
                        # Accent bar last, in the blank left margin (over marker cell)
                        self.tft.fill_rect(0, y, 3, CHAR_H, self.NEON_MAG)
                else:
                    self._draw_row_cached(ci, line, y, self.NEON_CYAN, self.BG_DARK)
                    if uc:
                        self.tft.text(self.font, marker, 0, y, self.NEON_MAG, self.BG_DARK)
                    # Dim hash on right
                    self.tft.text(self.font, hash_tag, hash_x, y, self.DIM_CYAN, self.BG_DARK)
            else:
                self._draw_row_cached(ci, "", y, self.NEON_CYAN)

    def draw_node_list(self):
        self._draw_tab_bar()
        _rows = BODY_ROWS - 1
        # SSH tab in manual-entry mode: a hex-address input instead of the list.
        if self.node_tab == TAB_SSH and self._shell_manual:
            self._draw_shell_manual()
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
        else:
            self._draw_list_rows(self._shell_keys, self.shell_nodes, self.ssh_scroll,
                                 self.ssh_idx, False,
                                 ("No rnsh nodes.", "(m) to enter a hash"))
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
        if self._cache[13] != _nf_key:
            self._cache[13] = _nf_key
            self.tft.text(self.font, _pad(""), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "(", 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "a", CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            self.tft.text(self.font, ")nnc", 2 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "(", 7 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "s", 8 * CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            self.tft.text(self.font, ")et", 9 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            if self.node_tab == TAB_MSG:
                self.tft.text(self.font, "(", 13 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
                self.tft.text(self.font, "p", 14 * CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
                self.tft.text(self.font, ")ing", 15 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
                self.tft.text(self.font, "(", 20 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
                self.tft.text(self.font, "d", 21 * CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
                self.tft.text(self.font, ")el", 22 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            elif self.node_tab == TAB_SSH:
                self.tft.text(self.font, "(", 13 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
                self.tft.text(self.font, "m", 14 * CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
                self.tft.text(self.font, ")hash  click=open", 15 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            else:
                self.tft.text(self.font, "click=open", 13 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self._route_cache = ''

        # Dynamic footer info, right-aligned in the last 14 cols (26-39),
        # leaving a clear gap after the "(p)ing" hotkey at col 18: transient
        # ping result, else the selected peer's hops + RSSI + last-seen,
        # e.g. "2h -87dB 5m". (Next-hop relay detail lives in the path
        # table; too wide for this line.)
        info = ""
        if self.node_tab == TAB_MSG:
            if self.ping_status:
                info = self.ping_status
            elif self._peer_keys and self.selected_idx < len(self._peer_keys):
                p = self.peers.get(self._peer_keys[self.selected_idx])
                if p:
                    bits = []
                    hops = p.get("hops")
                    if hops:
                        bits.append(str(hops) + "hp")
                    rs = p.get("rssi")
                    if rs is not None:
                        bits.append(str(rs) + "dB")
                    bits.append(_age(p.get("seen")))
                    info = " ".join(bits)
        elif self.node_tab == TAB_NET:
            if self._node_keys and self.net_idx < len(self._node_keys):
                n = self.nomad_nodes.get(self._node_keys[self.net_idx])
                if n:
                    bits = []
                    hops = n.get("hops")
                    if hops:
                        bits.append(str(hops) + "hp")
                    bits.append(_age(n.get("seen")))
                    info = " ".join(bits)
        else:  # TAB_SSH
            if self._shell_keys and self.ssh_idx < len(self._shell_keys):
                s = self.shell_nodes.get(self._shell_keys[self.ssh_idx])
                if s:
                    bits = []
                    hops = s.get("hops")
                    if hops:
                        bits.append(str(hops) + "hp")
                    bits.append(_age(s.get("seen")))
                    info = " ".join(bits)
        info = info[:14]
        if self._route_cache != info:
            self._route_cache = info
            # right-align by hand — MicroPython str has no rjust()
            info = " " * (14 - len(info)) + info
            self.tft.text(self.font, info, (COLS - 14) * CHAR_W, INPUT_Y,
                          self.DIM_CYAN, self.BG_DARK)

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
        if self._cache[13] != foot:
            self._cache[13] = foot
            self.tft.text(self.font, self._tb(_pad(foot)), 0, INPUT_Y, fcol, self.BG_DARK)

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
            if self._cache[13] != _sk:
                self._cache[13] = _sk
                self.tft.fill_rect(SBAR_X, _track_y, SBAR_W, _track_h, self.BG_DARK)
                self.tft.fill_rect(SBAR_X, _bar_y, SBAR_W, _bar_h, self.DIM_CYAN)

        # Input line drawn by draw() after draw_chat() returns

    def draw_input(self):
        inp = self.cmd_buf.decode()
        if inp:
            ik = "> " + inp
            if self._cache[14] == ik:
                return
            self._cache[14] = ik
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
            # Record hint only when not navigating messages (0 = mic key)
            _show_rec = self.chat_cursor < 0
            ik = ("IMG" if _on_image else "BACK") + _ts_txt + ("R" if _show_rec else "")
            if self._cache[14] == ik:
                return
            self._cache[14] = ik
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
            spi_release_display()
        self._image_drawn = True

    def _exit_image_view(self):
        """Return from image viewer to chat."""
        self._viewing_image = None
        self._image_drawn = False
        self.chat_cursor = -1
        self.state = STATE_CHAT
        self._prev_state = -1  # force full screen clear in draw()
        self._state_change_ms = time.ticks_ms()
        self._cache = [''] * 15
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

    # --- Input handling ---

    def handle_key(self, key):
        """Handle a keyboard key press. Returns True if UI needs redraw."""
        ch = key[0]

        if ch == 0:
            return False

        if self.state == STATE_IMAGE:
            self._exit_image_view()
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
            self.state = STATE_CHAT
            self._state_change_ms = time.ticks_ms()
            self.dirty = True

    def _handle_key_nodes(self, ch, key):
        # SSH manual hex-entry sub-mode captures all keys.
        if self.node_tab == TAB_SSH and self._shell_manual:
            return self._handle_shell_manual_key(ch, key)
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
        elif (key == b'm' or key == b'M') and self.node_tab == TAB_SSH:
            self._shell_manual = True
            self._shell_hex = bytearray()
            self._cache = [''] * 15
            self.dirty = True
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
            else:
                self._open_selected_shell()
            return True
        return False

    def _switch_tab(self, tab):
        """Switch MSG/NET/SSH tab on the node screen."""
        tab = tab % N_TABS
        if tab == self.node_tab:
            return
        self.node_tab = tab
        self._shell_manual = False
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
        self._cache = [''] * 15  # rows, tab bar and footer all change
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
        self._cache = [''] * 15
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
        self._cache = [''] * 15
        self.dirty = True
        if self.on_shell_connect:
            self.on_shell_connect(dest_hash, cols, rows)

    def _draw_shell_manual(self):
        """SSH tab manual hex-entry screen."""
        self._draw_row_cached(2, "rnsh listener hash:", BODY_Y + CHAR_H, self.NEON_CYAN)
        self._draw_row_cached(3, "(32 hex chars)", BODY_Y + 2 * CHAR_H, self.DIM_CYAN)
        for i in range(3, BODY_ROWS - 2):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)
        # Show our identity hash — a listener authorizes it via -a / allowed_identities.
        self._draw_row_cached(BODY_ROWS - 1, "your id (for listener -a):",
                              BODY_Y + (BODY_ROWS - 2) * CHAR_H, self.DIM_CYAN)
        self._draw_row_cached(BODY_ROWS, self.my_identity_hash or "?",
                              BODY_Y + (BODY_ROWS - 1) * CHAR_H, self.NEON_GREEN)
        self._draw_input_line(self._shell_hex.decode())
        foot = "Enter=connect  Esc=cancel"
        if self._cache[13] != foot:
            self._cache[13] = foot
            self.tft.text(self.font, self._tb(_pad(foot)), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)

    def _handle_shell_manual_key(self, ch, key):
        if ch == 0x1B:   # Esc — cancel
            self._shell_manual = False
            self._cache = [''] * 15
            self.dirty = True
            return True
        if ch == 0x08:   # Backspace
            if self._shell_hex:
                self._shell_hex = self._shell_hex[:-1]
                self._input_dirty = True
            return True
        if ch == 0x0D:   # Enter — parse + connect
            s = self._shell_hex.decode().strip().lower()
            try:
                from binascii import unhexlify
                dest = unhexlify(s)
                if len(dest) != 16:
                    raise ValueError
            except Exception:
                self._shell_status = "bad hash (need 32 hex)"
                self.dirty = True
                return True
            self._shell_manual = False
            self._start_shell(dest)
            return True
        if 0x20 <= ch < 0x7F and len(self._shell_hex) < 32:
            c = chr(ch).lower()
            if c in "0123456789abcdef":
                self._shell_hex += bytes([ord(c)])
                self._input_dirty = True
            return True
        return True

    def add_shell_node(self, dest_hash, name=None, hops=None, seen=None):
        """Add or update an rnsh listener (SSH tab, called by rnsh_client)."""
        prev = self.shell_nodes.get(dest_hash)
        if prev is None and len(self.shell_nodes) >= MAX_PEERS:
            oldest = min(self._shell_keys,
                         key=lambda k: self.shell_nodes[k].get("seen", 0))
            del self.shell_nodes[oldest]
            self._shell_keys.remove(oldest)
            if self.ssh_idx >= len(self._shell_keys):
                self.ssh_idx = max(0, len(self._shell_keys) - 1)
        if prev:
            if name is None:
                name = prev.get("name")
            if seen is not None and prev.get("seen", 0) > seen:
                seen = prev["seen"]
        # rnsh announces carry no name — show a short hash so the row isn't "?"
        self.shell_nodes[dest_hash] = {"name": name or dest_hash.hex()[:10],
                                       "hops": hops,
                                       "seen": time.time() if seen is None else seen}
        if dest_hash not in self._shell_keys:
            self._shell_keys.append(dest_hash)
        if self.state == STATE_NODES and self.node_tab == TAB_SSH:
            self.dirty = True

    def clear_shell_nodes(self):
        """Clear the SSH tab (interface switch)."""
        self.shell_nodes.clear()
        self._shell_keys.clear()
        self.ssh_idx = 0
        self.ssh_scroll = 0
        self._cache = [''] * 15
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

    # --- Browser page view ---

    def show_page(self, title, path, lines, links, can_back=False, keep_pos=False):
        """Display a rendered micron page (called by nomad_browser). keep_pos
        preserves the scroll/cursor across a reload of the same page."""
        self.browser_title = title
        self.browser_path = path
        self.browser_lines = lines
        self.browser_links = links
        self._browser_can_back = can_back
        if keep_pos:
            # clamp the preserved scroll to the (possibly changed) content
            _rows = BODY_ROWS - 1
            self.browser_scroll = max(0, min(self.browser_scroll, max(0, len(lines) - _rows)))
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
            if self._cache[13] != fkey:
                self._cache[13] = fkey
                self.tft.text(self.font, self._tb(_pad(foot)), 0, INPUT_Y, self.NEON_MAG, self.BG_DARK)
        elif self._shell_line_mode and self._shell_input:
            self._cache[13] = ''           # input redraws live; repaint on next status change
            self._draw_input_line(self._safe_decode(self._shell_input))
        else:
            if self._shell_line_mode:
                foot = "Bksp=quit click=keys trkbl=scrl"
            else:
                foot = "click=keys menu  trkbl=scroll"
            if self._shell_view:
                foot = "[+" + str(self._shell_view) + "] " + foot
            foot = foot[:COLS]
            if self._cache[13] != foot:
                self._cache[13] = foot
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
        self._cache = [''] * 15
        # The menu paints over the body with the 8x16 font, so every composited
        # terminal row underneath it is stale once the menu closes.
        self._shell_cache = [''] * len(self._shell_cache)
        self.dirty = True

    def _shell_menu_move(self, delta):
        self._shell_menu_idx = (self._shell_menu_idx + delta) % len(_SHELL_CTRL_ITEMS)
        self.dirty = True

    def _shell_menu_close(self):
        self._shell_menu = False
        self._cache = [''] * 15
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
        self._cache = [''] * 15
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
        if self._cache[13] != foot:
            self._cache[13] = foot
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
        self._cache = [''] * 15
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
                self._cache = [''] * 15
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
                self._cache = [''] * 15
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
        self._rec_seconds = 0
        self._rec_level = 0
        self._rec_warming = False
        self.state = STATE_RECORDING
        self._prev_state = -1
        self._state_change_ms = time.ticks_ms()
        self._cache = [''] * 15
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
        self._cache = [''] * 15
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
                if self._settings_idx == 0:  # WiFi
                    self._settings_page = _SET_WIFI_SCAN
                    self._settings_idx = 0
                    self._settings_scroll = 0
                    self._wifi_networks = []
                    self._wifi_scanning = True
                    self._cache = [''] * 15
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
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 2:  # Node name
                    self._settings_page = _SET_NODE_NAME
                    self.cmd_buf = bytearray(self.node_name.encode())
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 3:  # LoRa reset
                    if self.on_lora_reset:
                        self.on_lora_reset()
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 4:  # Volume
                    self._volume = (self._volume + 1) % 11  # cycle 0-10
                    if self.on_volume:
                        self.on_volume(self._volume)
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 5:  # Keyboard backlight toggle
                    if self.on_kbd_backlight:
                        if self.on_kbd_backlight(not self._kbd_bl):
                            self._kbd_bl = not self._kbd_bl
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 6:  # Auto-announce toggle
                    self._auto_announce = not self._auto_announce
                    if self.on_auto_announce:
                        try:
                            self.on_auto_announce(self._auto_announce)
                        except Exception:
                            pass
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 7:  # Sleep timeout cycle
                    self._cycle_timeout(1)
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 8:  # Radio stats page
                    self._settings_page = _SET_RADIO
                    self._settings_scroll = 0
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                elif self._settings_idx == 9:  # LoRa radio config page
                    self._settings_page = _SET_LORA
                    self._lora_edit = dict(self._lora_cfg)
                    self._lora_field = 0
                    self._lora_applying = ""
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                # idx 10 (address) is informational — Enter does nothing
        elif self._settings_page == _SET_RADIO:
            if ch == 0x1B or ch == 0x08:
                self._settings_page = _SET_MAIN
                self._settings_idx = 8
                self._cache = [''] * 15
                self.dirty = True
                return True
        elif self._settings_page == _SET_LORA:
            if ch == 0x1B or ch == 0x08:   # back — discard unapplied edits
                self._lora_edit = None
                self._lora_applying = ""
                self._settings_page = _SET_MAIN
                self._settings_idx = 9
                self._cache = [''] * 15
                self.dirty = True
                return True
            elif ch == 0x0D:   # Enter / trackball click
                if self._lora_field == 0:          # Freq -> numeric entry page
                    self._settings_page = _SET_LORA_FREQ
                    self.cmd_buf = bytearray()     # type a fresh value; Enter empty keeps current
                    self._cache = [''] * 15
                    self.dirty = True
                elif self._lora_field == 5:        # Apply & Save
                    self._lora_apply()
                else:                              # BW/SF/CR/TX -> cycle forward
                    self._lora_cycle(_LORA_FIELDS[self._lora_field], 1)
                    self._lora_applying = ""
                    self._cache = [''] * 15
                    self.dirty = True
                return True
        elif self._settings_page == _SET_LORA_FREQ:
            if ch == 0x1B:   # cancel, keep old freq
                self._settings_page = _SET_LORA
                self._cache = [''] * 15
                self.dirty = True
                return True
            elif ch == 0x08:   # Backspace (empty -> back)
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_LORA
                    self._cache = [''] * 15
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
                self._cache = [''] * 15
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
                self._cache = [''] * 15
                self.dirty = True
                return True
            elif ch == 0x0D and self._wifi_networks:
                idx = self._settings_idx
                if 0 <= idx < len(self._wifi_networks):
                    self._wifi_ssid = self._wifi_networks[idx][0]
                    self._wifi_err = ""
                    self._settings_page = _SET_WIFI_PASS
                    self.cmd_buf = bytearray()
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
            elif key == b'r' or key == b'R':  # rescan
                self._settings_idx = 0
                self._settings_scroll = 0
                self._wifi_err = ""
                self._wifi_networks = []
                self._wifi_scanning = True
                self._cache = [''] * 15
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
                self._cache = [''] * 15
                self.dirty = True
                return True
            elif ch == 0x08:  # Backspace
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_WIFI_SCAN
                    self._settings_idx = 0
                    self._cache = [''] * 15
                    self.dirty = True
                return True
            elif ch == 0x0D:  # Enter — connect (async; UI shows "connecting")
                password = self.cmd_buf.decode()
                self.cmd_buf = bytearray()
                self._cache = [''] * 15
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
                self._cache = [''] * 15
                self.dirty = True
                return True
            elif ch == 0x08:  # Backspace
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_MAIN
                    self._settings_idx = 1
                    self._cache = [''] * 15
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
                    self._cache = [''] * 15
                    self.dirty = True
                    self.on_tcp_connect(host, port)
                    return True
                self._settings_page = _SET_MAIN
                self._settings_idx = 1
                self._cache = [''] * 15
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
                self._cache = [''] * 15
                self.dirty = True
                return True
            elif ch == 0x08:  # Backspace
                if len(self.cmd_buf) > 0:
                    self.cmd_buf = self.cmd_buf[:-1]
                    self._input_dirty = True
                else:
                    self._settings_page = _SET_MAIN
                    self._settings_idx = 2
                    self._cache = [''] * 15
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
                self._cache = [''] * 15
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

    def _draw_settings_main(self):
        self._draw_row_cached(1, "Settings", BODY_Y, self.NEON_CYAN)

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
        radio_line = "Radio stats"
        c = self._lora_cfg
        loracfg_line = ("LoRa cfg: %dk SF%d BW%s"
                        % (c["freq_khz"], c["sf"], c["bw"]))
        addr_line = "Addr: " + (self.my_address or "?")
        items = [wifi_line, tcp_line, name_line, lora_line, vol_line,
                 kbbl_line, anc_line, sleep_line, radio_line, loracfg_line,
                 addr_line]
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
        self._cache = [''] * 15
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
        self._cache = [''] * 15
        self.dirty = True

    def set_tcp_result(self, ok, addr):
        """Async TCP connect finished — back to the settings menu either way."""
        self._tcp_connecting = False
        if ok:
            self._tcp_enabled = True
            self._tcp_target = addr
        self._settings_page = _SET_MAIN
        self._settings_idx = 1
        self._cache = [''] * 15
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
        t = time.ticks_ms()
        if time.ticks_diff(t, self._irq_last_click) >= 200:
            self._irq_last_click = t
            self._irq_click += 1

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
        up = self._irq_up;  self._irq_up = 0
        down = self._irq_down;  self._irq_down = 0
        click = self._irq_click;  self._irq_click = 0
        left = self._irq_left;  self._irq_left = 0
        right = self._irq_right;  self._irq_right = 0

        if not (up or down or click or left or right):
            return False

        # If screen is off, consume the event and just wake
        if not self._screen_on:
            self.wake_screen()
            return True

        self.wake_screen()

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
                if self.node_tab == TAB_MSG:
                    self._enter_chat()
                elif self.node_tab == TAB_NET:
                    self._open_selected_node()
                elif self.node_tab == TAB_SSH and not self._shell_manual:
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
            if self.node_tab == TAB_MSG:
                if self.selected_idx > 0:
                    self.selected_idx -= 1
                    if self.selected_idx < self.node_scroll:
                        self.node_scroll = self.selected_idx
            elif self.node_tab == TAB_NET:
                if self.net_idx > 0:
                    self.net_idx -= 1
                    if self.net_idx < self.net_scroll:
                        self.net_scroll = self.net_idx
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
        else:
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

    def _scroll_down(self):
        if self.state == STATE_IMAGE:
            return
        elif self.state == STATE_NODES:
            _rows = BODY_ROWS - 1  # tab bar takes the first body row
            if self.node_tab == TAB_MSG:
                if self.selected_idx < len(self._peer_keys) - 1:
                    self.selected_idx += 1
                    if self.selected_idx >= self.node_scroll + _rows:
                        self.node_scroll = self.selected_idx - _rows + 1
            elif self.node_tab == TAB_NET:
                if self.net_idx < len(self._node_keys) - 1:
                    self.net_idx += 1
                    if self.net_idx >= self.net_scroll + _rows:
                        self.net_scroll = self.net_idx - _rows + 1
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
        else:
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
            # 11 items: WiFi TCP Name LoRa Vol KbBL Announce Sleep Radio
            #           LoRaCfg Addr
            if self._settings_idx < 10:
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
        self._cache = [''] * 15
        self.dirty = True

    def _settings_adjust(self, delta):
        """Trackball left/right on an adjustable settings row."""
        if self._settings_page == _SET_LORA:
            if 0 <= self._lora_field <= 4:
                self._lora_cycle(_LORA_FIELDS[self._lora_field], delta)
                self._lora_applying = ""
                self._cache = [''] * 15
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
        else:
            return
        self._cache = [''] * 15
        self.dirty = True

    # --- Data management ---

    def clear_peers(self):
        """Clear node list, chat history, and related state."""
        self.peers.clear()
        self._peer_keys.clear()
        self.chat_history.clear()
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
        self._cache = [''] * 15
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

    def add_peer(self, dest_hash, name, rssi=None, hops=None, via=None):
        """Add or update a peer from an announce."""
        if dest_hash not in self.peers and len(self.peers) >= MAX_PEERS:
            # Evict the least-recently-seen peer — never index 0, which
            # message-bubbling makes the most active chat.
            sel_key = (self._peer_keys[self.selected_idx]
                       if self.selected_idx < len(self._peer_keys) else None)
            oldest = min(self._peer_keys, key=lambda k: self.peers[k].get("seen", 0))
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

        self.peers[dest_hash] = {"name": name or "?", "rssi": rssi,
                                 "hops": hops, "via": via, "seen": time.time()}
        if dest_hash not in self._peer_keys:
            self._peer_keys.append(dest_hash)
        self._route_cache = ''  # selected-peer footer may show new route info
        self.dirty = True

    def delete_selected(self):
        """Forget the selected peer (MSG) / node (NET) / listener (SSH) and its
        local state. The entry re-appears on the next announce; this just clears
        clutter."""
        if self.node_tab == TAB_MSG:
            if not (0 <= self.selected_idx < len(self._peer_keys)):
                return
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
            key = self._node_keys.pop(self.net_idx)
            self.nomad_nodes.pop(key, None)
            if self.net_idx >= len(self._node_keys):
                self.net_idx = max(0, len(self._node_keys) - 1)
            if self.net_scroll > max(0, len(self._node_keys) - (BODY_ROWS - 1)):
                self.net_scroll = max(0, len(self._node_keys) - (BODY_ROWS - 1))
        else:  # TAB_SSH
            if not (0 <= self.ssh_idx < len(self._shell_keys)):
                return
            key = self._shell_keys.pop(self.ssh_idx)
            self.shell_nodes.pop(key, None)
            if self.ssh_idx >= len(self._shell_keys):
                self.ssh_idx = max(0, len(self._shell_keys) - 1)
            if self.ssh_scroll > max(0, len(self._shell_keys) - (BODY_ROWS - 1)):
                self.ssh_scroll = max(0, len(self._shell_keys) - (BODY_ROWS - 1))
        self._cache = [''] * 15
        self.dirty = True

    def add_nomad_node(self, dest_hash, name, hops=None, seen=None):
        """Add or update a NomadNet node (NET tab, called by nomad_browser)."""
        prev = self.nomad_nodes.get(dest_hash)
        if prev is None and len(self.nomad_nodes) >= MAX_PEERS:
            oldest = min(self._node_keys,
                         key=lambda k: self.nomad_nodes[k].get("seen", 0))
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
        self.nomad_nodes[dest_hash] = {"name": name or "?", "hops": hops,
                                       "seen": time.time() if seen is None else seen}
        if dest_hash not in self._node_keys:
            self._node_keys.append(dest_hash)
        self._route_cache = ''
        self.dirty = True

    def clear_nomad_nodes(self):
        """Clear the NET tab (interface switch)."""
        self.nomad_nodes.clear()
        self._node_keys.clear()
        self.net_idx = 0
        self.net_scroll = 0
        self._cache = [''] * 15
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
        if self.state == STATE_CHAT and self.selected_peer == dest_hash:
            self._invalidate_chat_lines()
            for j in range(1, BODY_ROWS + 1):
                self._cache[j] = ''
            self.dirty = True

    def update_battery(self):
        """Read battery voltage via adc_reader (None if no battery sense)."""
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
            self.tft.fill_rect(0, SEP_Y, SCREEN_W, 1, self.DIM_CYAN)
            self._cache = [''] * 15  # invalidate all rows
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
                spi_release_display()
                self._input_dirty = False
            elif self.dirty and time.ticks_diff(now, self._last_draw) > _throttle:
                spi_acquire_display()
                self.draw()
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

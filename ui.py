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

# Settings sub-pages
_SET_MAIN      = 0
_SET_WIFI_SCAN = 1
_SET_WIFI_PASS = 2
_SET_TCP_HOST  = 3
_SET_NODE_NAME = 4
_SET_RADIO     = 5

# Layout constants (320x240 landscape, 8x16 font)
SCREEN_W = 320
SCREEN_H = 240
CHAR_W = 8
CHAR_H = 16
COLS = 40          # 320 / 8
NAV_H = 20        # navbar height (1 row + 4px padding)
NAV_TY = 2        # navbar text y offset (2px top padding)
INPUT_Y = 224      # status bar y (bottom of screen, 224+16=240)
BODY_Y = 26       # main area start (6px gap below navbar for frame line)
BODY_ROWS = 12    # 12 * 16 = 192px, ends at y=218, frame bottom at 219
SEP_Y = 222       # separator line just above input bar

# Data limits
MAX_PEERS = 16
MAX_HISTORY = 30  # per peer
MAX_CACHED_IMAGES = 3  # max JPEG payloads kept in RAM

# Trackball debounce
_TB_DEBOUNCE_MS = 80

# Screen power-off timeout
_SCREEN_TIMEOUT_MS = 10000

# Strip non-ASCII bytes and collapse whitespace — emoji removal leaves gaps
def _ascii(s):
    raw = ''.join(c for c in s if 32 <= ord(c) < 127)
    return ' '.join(raw.split())

# Pad string to exact width (no clearing needed)
def _pad(s, width=COLS):
    if len(s) >= width:
        return s[:width]
    return s + ' ' * (width - len(s))


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


class UI:

    def __init__(self, tft, font, get_key_func, node_name="T-Deck"):
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
        self._rec_seconds = 0          # recording duration counter
        self._progress_dirty = False
        self.announce_flash = 0  # timestamp of last announce flash

        # Trackball pins
        self._tb_up    = Pin(3, Pin.IN, Pin.PULL_UP)
        self._tb_down  = Pin(15, Pin.IN, Pin.PULL_UP)
        self._tb_left  = Pin(1, Pin.IN, Pin.PULL_UP)
        self._tb_right = Pin(2, Pin.IN, Pin.PULL_UP)
        self._tb_click = Pin(0, Pin.IN, Pin.PULL_UP)
        # IRQ counters (written by ISR, drained by main loop)
        self._irq_up = 0
        self._irq_down = 0
        self._irq_click = 0
        # ISR debounce timestamps (ticks_ms is ISR-safe)
        self._irq_last_scroll = 0
        self._irq_last_click = 0

        # Register hardware interrupts on trackball pins
        self._tb_up.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_up)
        self._tb_down.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_down)
        self._tb_click.irq(trigger=Pin.IRQ_FALLING, handler=self._irq_handler_click)

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
        self._volume = 8  # 0-10, synced with sound.volume

        # Image viewer state
        self._image_cache = {}  # (peer_hash, msg_idx) -> jpeg/webp bytes
        self._audio_cache = {}  # (peer_hash, msg_idx) -> (codec2_bytes, mode)
        self._image_cache_order = []  # LRU order of (peer_hash, msg_idx) keys
        self._viewing_image = None  # jpeg_bytes currently displayed
        self._image_drawn = False  # True once JPEG has been blitted
        self._visible_image_lines = {}  # display_row -> msg_idx (populated by draw_chat)
        self._visible_msg_lines = {}    # display_row -> msg_idx for ALL messages

        # Chat lines cache (avoids rebuilding word-wrapped lines on every draw)
        self._chat_lines_cache = None
        self._chat_lines_peer = None

        # Screen power management
        self._last_activity = time.ticks_ms()
        self._screen_on = True
        self._bl = None  # backlight pin, set by set_backlight()

        # Node identity/info (set by tdeck_node.py)
        self.my_address = None       # own LXMF address hex string
        self.get_radio_stats = None  # () -> [(label, value), ...] for radio page

        # Callbacks (set by tdeck_node.py)
        self.on_send = None       # on_send(dest_hash_bytes, text)
        self.on_announce = None   # on_announce()
        self.on_ping = None       # on_ping(dest_hash_bytes)
        self.on_wifi_scan = None      # () -> [(ssid, rssi), ...]
        self.on_wifi_connect = None   # (ssid, password) -> bool
        self.on_tcp_toggle = None     # (enabled, host, port) -> bool
        self.on_node_name = None      # (name) -> None
        self.on_lora_reset = None     # () -> bool
        self.on_volume = None         # (level) -> None
        self.on_audio_play = None     # (codec2_bytes, mode) -> None
        self.on_record_start = None   # () -> None
        self.on_record_stop = None    # (send: bool) -> None

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
        """Convert text to bytes for C display driver (avoids UTF-8 mangling
        of chars > 0x7F like the \\xfb checkmark in vga2_8x16 font).
        MicroPython lacks latin-1 codec, so we use ord() per char."""
        return bytes([ord(c) for c in text]) if isinstance(text, str) else text

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
        self.tft.text(self.font, self._tb(_pad(text)), 0, y, fg, bg or self.BG_DARK)
        return True

    def _row(self, text, y, fg, bg=None):
        """Draw a full-width padded row — overwrites old content, no flicker."""
        self.tft.text(self.font, self._tb(_pad(text)), 0, y, fg, bg or self.BG_DARK)

    def _text(self, text, x, y, fg, bg=None):
        """Draw text at pixel position."""
        self.tft.text(self.font, self._tb(text), x, y, fg, bg or self.BG_DARK)

    # --- Navbar ---

    def draw_navbar(self):
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
            self.tft.text(self.font, _pad(mid_str, mid_w), center_x, NAV_TY, center_color, hb)

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

    def draw_node_list(self):
        if not self._peer_keys:
            _mid = BODY_ROWS // 2 - 1
            for i in range(BODY_ROWS):
                y = BODY_Y + i * CHAR_H
                if i == _mid:
                    self._draw_row_cached(i + 1, "No peers yet.".center(COLS), y, self.NEON_CYAN)
                elif i == _mid + 1:
                    self._draw_row_cached(i + 1, "Waiting for announces...".center(COLS), y, self.DIM_CYAN)
                else:
                    self._draw_row_cached(i + 1, "", y, self.NEON_CYAN)
        else:
            visible = self._peer_keys[self.node_scroll:self.node_scroll + BODY_ROWS]
            for i in range(BODY_ROWS):
                y = BODY_Y + i * CHAR_H
                if i < len(visible):
                    key = visible[i]
                    peer = self.peers[key]
                    name = _ascii(peer.get("name") or "?")
                    hash_tag = "[" + key.hex()[:8] + "]"
                    uc = self.unread.get(key, 0)
                    marker = str(min(uc, 9)) + "*" if uc > 1 else ("* " if uc == 1 else "  ")
                    # Hash in brackets, 1 char right padding
                    _rpad = 1
                    max_name = COLS - len(marker) - len(hash_tag) - _rpad
                    left = marker + name[:max_name]
                    line = left + " " * (COLS - len(left) - len(hash_tag) - _rpad) + hash_tag
                    hash_x = (COLS - len(hash_tag) - _rpad) * CHAR_W

                    abs_idx = self.node_scroll + i
                    if abs_idx == self.selected_idx:
                        cache_key = '\x01' + line
                        if self._cache[i + 1] != cache_key:
                            self._cache[i + 1] = cache_key
                            self.tft.text(self.font, _pad(line), 0, y, self.YELLOW, self.SEL_BG)
                            # Accent bar (clear of corner bracket)
                            self.tft.fill_rect(4, y, 3, CHAR_H, self.NEON_MAG)
                            if uc:
                                self.tft.text(self.font, marker, 0, y, self.NEON_MAG, self.SEL_BG)
                            # Dim hash on right
                            self.tft.text(self.font, hash_tag, hash_x, y, self.DIM_CYAN, self.SEL_BG)
                    else:
                        self._draw_row_cached(i + 1, line, y, self.NEON_CYAN, self.BG_DARK)
                        if uc:
                            self.tft.text(self.font, marker, 0, y, self.NEON_MAG, self.BG_DARK)
                        # Dim hash on right
                        self.tft.text(self.font, hash_tag, hash_x, y, self.DIM_CYAN, self.BG_DARK)
                else:
                    self._draw_row_cached(i + 1, "", y, self.NEON_CYAN)

        # Scroll indicator on right edge
        _total = len(self._peer_keys)
        if _total > BODY_ROWS:
            _track_h = BODY_ROWS * CHAR_H
            _bar_h = max(6, _track_h * BODY_ROWS // _total)
            _bar_y = BODY_Y + self.node_scroll * _track_h // _total
            self.tft.fill_rect(SCREEN_W - 2, BODY_Y, 2, _track_h, self.BG_DARK)
            self.tft.fill_rect(SCREEN_W - 2, _bar_y, 2, _bar_h, self.DIM_CYAN)

        # Neon frame + footer hints — drawn once on state change (cached via _prev_state)
        if self._cache[13] != "NF":
            self._cache[13] = "NF"
            _cx = self.NEON_CYAN
            _L = 12  # corner vertical arm length
            _top = BODY_Y - 3
            _bot = BODY_Y + BODY_ROWS * CHAR_H + 1
            self.tft.fill_rect(0, _top, SCREEN_W, 1, _cx)
            self.tft.fill_rect(0, _bot, SCREEN_W, 1, _cx)
            self.tft.fill_rect(0, _top, 1, _L, _cx)
            self.tft.fill_rect(SCREEN_W - 1, _top, 1, _L, _cx)
            self.tft.fill_rect(0, _bot - _L + 1, 1, _L, _cx)
            self.tft.fill_rect(SCREEN_W - 1, _bot - _L + 1, 1, _L, _cx)

            self.tft.text(self.font, _pad(""), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "(", 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "a", CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            self.tft.text(self.font, ")nnc", 2 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "(", 7 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "s", 8 * CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            self.tft.text(self.font, ")et", 9 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "(", 13 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self.tft.text(self.font, "p", 14 * CHAR_W, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            self.tft.text(self.font, ")ing", 15 * CHAR_W, INPUT_Y, self.DIM_CYAN, self.BG_DARK)
            self._route_cache = ''

        # Dynamic footer info, right-aligned in the last 14 cols (26-39),
        # leaving a clear gap after the "(p)ing" hotkey at col 18: transient
        # ping result, else the selected peer's hops + RSSI + last-seen,
        # e.g. "2h -87dB 5m". (Next-hop relay detail lives in the path
        # table; too wide for this line.)
        if self.ping_status and time.ticks_diff(time.ticks_ms(), self._ping_status_ms) < 8000:
            info = self.ping_status
        else:
            info = ""
            if self._peer_keys and self.selected_idx < len(self._peer_keys):
                p = self.peers.get(self._peer_keys[self.selected_idx])
                if p:
                    bits = []
                    hops = p.get("hops")
                    if hops:
                        bits.append(str(hops) + "h")
                    rs = p.get("rssi")
                    if rs is not None:
                        bits.append(str(rs) + "dB")
                    bits.append(_age(p.get("seen")))
                    info = " ".join(bits)
        info = info[:14]
        if self._route_cache != info:
            self._route_cache = info
            self.tft.text(self.font, info.rjust(14), (COLS - 14) * CHAR_W, INPUT_Y,
                          self.DIM_CYAN, self.BG_DARK)

    # --- Chat screen ---

    def _invalidate_chat_lines(self):
        """Invalidate cached chat lines — call when messages change."""
        self._chat_lines_cache = None

    def _build_chat_lines(self):
        """Build word-wrapped display lines for current chat.
        Returns list of (is_mine, text, is_first, suffix_len, status, msg_idx, has_image, has_audio)"""
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
        for mi, msg in enumerate(msgs):
            is_mine = msg[0]
            text = msg[1]
            status = msg[3] if len(msg) > 3 else 0
            has_image = msg[4] if len(msg) > 4 else False
            has_audio = msg[5] if len(msg) > 5 else False
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
                lines.append((is_mine, wl, j == 0, slen, status, mi, has_image, has_audio))
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
            self.tft.text(self.font, _pad(header), 0, BODY_Y, self.NEON_CYAN, self.BG_DARK)
            self.tft.text(self.font, "<", 0, BODY_Y, self.NEON_GREEN, self.BG_DARK)
            hx = (COLS - len(phash) - 1) * CHAR_W
            self.tft.text(self.font, phash, hx, BODY_Y, self.DIM_CYAN, self.BG_DARK)
        # Separator under header
        self.tft.fill_rect(0, BODY_Y + CHAR_H - 1, SCREEN_W, 1, self.DIM_CYAN)

        lines = self._build_chat_lines()
        _chat_rows = BODY_ROWS - 1  # 11 rows for messages

        # Clamp scroll
        total = len(lines)
        max_scroll = max(0, total - 1)
        if self.chat_scroll > max_scroll:
            self.chat_scroll = max_scroll

        # Apply scroll — show last N lines, scrollable up
        view_end = max(0, total - self.chat_scroll)
        view_start = max(0, view_end - _chat_rows)
        visible = lines[view_start:view_end]

        # Track which visible lines have images/audio
        self._visible_image_lines = {}  # display_row -> msg_idx
        self._visible_audio_lines = {}  # display_row -> msg_idx
        self._visible_msg_lines = {}    # display_row -> msg_idx (all messages)

        # Clamp chat_cursor to visible range
        if self.chat_cursor >= len(visible):
            self.chat_cursor = len(visible) - 1

        _status_color = {1: self.YELLOW, 2: self.NEON_GREEN, 3: self.NEON_MAG,
                         4: self.DIM_CYAN, 5: self.YELLOW}
        for i in range(_chat_rows):
            y = BODY_Y + (i + 1) * CHAR_H
            ci = i + 2  # cache index (1=header, 2..12=chat rows)
            if i < len(visible):
                is_mine, text, is_first, slen, status, msg_idx, has_image, has_audio = visible[i]

                # Track image/audio lines for click detection
                if has_image and is_first:
                    self._visible_image_lines[i] = msg_idx
                if has_audio and is_first:
                    self._visible_audio_lines[i] = msg_idx
                self._visible_msg_lines[i] = msg_idx

                is_highlighted = (i == self.chat_cursor)
                in_cache = (self.selected_peer, msg_idx) in self._image_cache if has_image and is_first else True

                # Cache check: skip row if text and highlight state unchanged
                ck = text + ("\x01" if is_highlighted else "\x00")
                if self._cache[ci] == ck:
                    continue
                self._cache[ci] = ck

                row_bg = self.SEL_BG if is_highlighted else self.BG_DARK

                padded = _pad(text)
                self.tft.text(self.font, self._tb(padded), 0, y, self.NEON_CYAN, row_bg)
                if is_first:
                    if is_mine:
                        self.tft.text(self.font, text[:4], 0, y, self.NEON_GREEN, row_bg)
                    else:
                        gt = text.find(">")
                        if gt >= 0:
                            self.tft.text(self.font, text[:gt + 1], 0, y, self.NEON_MAG, row_bg)
                    # Image rendering
                    if has_image:
                        img_pos = text.find("[image]")
                        if img_pos >= 0:
                            if not in_cache:
                                # Expired: dim + strikethrough
                                self.tft.text(self.font, "[image]", img_pos * CHAR_W, y,
                                              self.DIM_CYAN, row_bg)
                                self.tft.fill_rect(img_pos * CHAR_W, y + 7,
                                                   7 * CHAR_W, 1, self.DIM_CYAN)
                            elif is_highlighted:
                                # Highlighted: yellow + accent bar
                                self.tft.text(self.font, "[image]", img_pos * CHAR_W, y,
                                              self.YELLOW, row_bg)
                                self.tft.fill_rect(0, y, 3, CHAR_H, self.NEON_MAG)
                            else:
                                # Normal: magenta
                                self.tft.text(self.font, "[image]", img_pos * CHAR_W, y,
                                              self.NEON_MAG, row_bg)
                    # Voice rendering
                    if has_audio:
                        vpos = text.find("[voice]")
                        if vpos >= 0:
                            vc = self.YELLOW if is_highlighted else self.NEON_GREEN
                            self.tft.text(self.font, "[voice]", vpos * CHAR_W, y, vc, row_bg)
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
                self.tft.fill_rect(SCREEN_W - 2, _track_y, 2, _track_h, self.BG_DARK)
                self.tft.fill_rect(SCREEN_W - 2, _bar_y, 2, _bar_h, self.DIM_CYAN)

        # Input line drawn by draw() after draw_chat() returns

    def draw_input(self):
        prompt = "> "
        inp = self.cmd_buf.decode()
        if inp:
            ik = "> " + inp
            if self._cache[14] == ik:
                return
            self._cache[14] = ik
            text_part = inp[:COLS - 3] + "_"
            text_padded = _pad(text_part, COLS - 2)
            self.tft.text(self.font, prompt, 0, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.NEON_CYAN, self.BG_DARK)
        else:
            _on_image = self.chat_cursor >= 0 and self.chat_cursor in self._visible_image_lines
            # Timestamp of the highlighted message (empty pre-time-sync)
            _ts_txt = ""
            if self.chat_cursor >= 0 and self.chat_cursor in self._visible_msg_lines:
                try:
                    _mi = self._visible_msg_lines[self.chat_cursor]
                    _ts_txt = _fmt_time(self.chat_history[self.selected_peer][_mi][2])
                except Exception:
                    _ts_txt = ""
            ik = ("IMG" if _on_image else "BACK") + _ts_txt
            if self._cache[14] == ik:
                return
            self._cache[14] = ik
            self.tft.text(self.font, _pad("> _"), 0, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
            if _ts_txt:
                self.tft.text(self.font, _pad(_ts_txt, 16), 6 * CHAR_W, INPUT_Y,
                              self.DIM_CYAN, self.BG_DARK)
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

    def _enter_image_view(self, msg_idx):
        """Enter full-screen JPEG view for a message with an image."""
        cache_key = (self.selected_peer, msg_idx)
        jpeg_data = self._image_cache.get(cache_key)
        if jpeg_data is None:
            return  # image expired from cache
        self._viewing_image = jpeg_data
        self._image_drawn = False
        self._prev_image_state = self.state
        self.state = STATE_IMAGE
        self._state_change_ms = time.ticks_ms()
        self.dirty = True

    def draw_image(self, spi_acquire_display, spi_release_display):
        """Render full-screen JPEG: decode+scale to 320x240 in C, blit."""
        if self._image_drawn or self._viewing_image is None:
            return
        gc.collect()
        spi_acquire_display()
        try:
            data = self._viewing_image
            # Detect format: WebP starts with RIFF....WEBP
            if len(data) > 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                import webp_fast_xtensawin as _dec
            else:
                import tjpgd_fast_xtensawin as _dec
            # Decode and scale to screen size in native C
            w, h, rgb565 = _dec.decode(data, SCREEN_W, SCREEN_H)
            self.tft.fill(0x0000)
            self.tft.blit_buffer(rgb565, 0, 0, w, h)
            del rgb565
            gc.collect()
            # Hint bar at bottom
            self.tft.fill_rect(0, SCREEN_H - 18, SCREEN_W, 18, 0x0000)
            self.tft.text(self.font, "any key = back", 96, SCREEN_H - 17, self.DIM_CYAN, 0x0000)
        except ImportError:
            self.tft.fill(0x0000)
            self.tft.text(self.font, "No JPEG decoder", 56, 104, self.NEON_MAG, 0x0000)
            self.tft.text(self.font, "Upload tjpgd_fast .mpy", 32, 128, self.DIM_CYAN, 0x0000)
        except Exception:
            self.tft.fill(0x0000)
            self.tft.text(self.font, "Image decode error", 40, 112, self.NEON_MAG, 0x0000)
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
        elif key == b'p' or key == b'P':
            if self.on_ping and self._peer_keys and self.selected_idx < len(self._peer_keys):
                self.ping_status = "ping..."
                self._ping_status_ms = time.ticks_ms()
                self.dirty = True
                self.on_ping(self._peer_keys[self.selected_idx])
            return True
        return False

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
                msg_idx = self.add_chat_message(self.selected_peer, True, text, status=1)
                if self.on_send:
                    self.on_send(self.selected_peer, text, msg_idx)
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
            # 'r' or '0' (Sym+0 mic key) with empty input = start voice recording
            if key in (b'r', b'R', b'0') and len(self.cmd_buf) == 0 and self.selected_peer:
                self._enter_recording()
                return True
            self.cmd_buf += key
            self._input_dirty = True
            return True
        return False

    def _draw_recording(self):
        """Draw recording screen."""
        mid = BODY_ROWS // 2
        for i in range(BODY_ROWS):
            y = BODY_Y + i * CHAR_H
            if i == mid - 1:
                secs = str(int(self._rec_seconds))
                self._draw_row_cached(i + 1, ("Recording... " + secs + "s").center(COLS),
                                      y, self.NEON_MAG)
            elif i == mid + 1:
                self._draw_row_cached(i + 1, "Enter=send  Esc=cancel".center(COLS),
                                      y, self.DIM_CYAN)
            else:
                self._draw_row_cached(i + 1, "", y, self.NEON_CYAN)
        # Input area
        self.tft.text(self.font, _pad(""), 0, INPUT_Y, self.DIM_CYAN, self.BG_DARK)

    def _enter_recording(self):
        """Switch to recording state."""
        self._rec_seconds = 0
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
                elif self._settings_idx == 5:  # Radio stats page
                    self._settings_page = _SET_RADIO
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
                # idx 6 (address) is informational — Enter does nothing
        elif self._settings_page == _SET_RADIO:
            if ch == 0x1B or ch == 0x08:
                self._settings_page = _SET_MAIN
                self._settings_idx = 5
                self._cache = [''] * 15
                self.dirty = True
                return True
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
                    self._settings_page = _SET_WIFI_PASS
                    self.cmd_buf = bytearray()
                    self._cache = [''] * 15
                    self.dirty = True
                    return True
        elif self._settings_page == _SET_WIFI_PASS:
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
            elif ch == 0x0D:  # Enter — connect
                password = self.cmd_buf.decode()
                self.cmd_buf = bytearray()
                # Show connecting status
                self._cache = [''] * 15
                self.dirty = True
                if self.on_wifi_connect:
                    ip = self.on_wifi_connect(self._wifi_ssid, password)
                    if ip:
                        self._wifi_connected = True
                        self._wifi_ssid_current = self._wifi_ssid
                        self._wifi_ip = ip
                        # Auto-jump to TCP host entry
                        self._settings_page = _SET_TCP_HOST
                        self.cmd_buf = bytearray(self._tcp_target.encode()) if self._tcp_target else bytearray(self._tcp_default.encode())
                        self._cache = [''] * 15
                        self.dirty = True
                        return True
                self._settings_page = _SET_MAIN
                self._settings_idx = 0
                self._cache = [''] * 15
                self.dirty = True
                return True
            elif 0x20 <= ch < 0x7F:  # Printable
                self.cmd_buf += key
                self._input_dirty = True
                return True
        elif self._settings_page == _SET_TCP_HOST:
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
            elif ch == 0x0D:  # Enter — parse host:port and connect
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
                if host and port:
                    if self.on_tcp_toggle and self.on_tcp_toggle(True, host, port):
                        self._tcp_enabled = True
                        self._tcp_target = addr
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
        # Separator
        self.tft.fill_rect(0, SEP_Y, SCREEN_W, 2, self.DIM_CYAN)

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
        radio_line = "Radio stats"
        addr_line = "Addr: " + (self.my_address or "?")
        items = [wifi_line, tcp_line, name_line, lora_line, vol_line,
                 radio_line, addr_line]
        for i in range(BODY_ROWS - 1):
            y = BODY_Y + (i + 1) * CHAR_H
            if i < len(items):
                line = "  " + items[i]
                if i == self._settings_idx:
                    cache_key = '\x01' + line
                    if self._cache[i + 2] != cache_key:
                        self._cache[i + 2] = cache_key
                        self.tft.text(self.font, _pad(line), 0, y, self.YELLOW, self.SEL_BG)
                        self.tft.fill_rect(4, y, 3, CHAR_H, self.NEON_MAG)
                else:
                    self._draw_row_cached(i + 2, line, y, self.NEON_CYAN)
            else:
                self._draw_row_cached(i + 2, "", y, self.NEON_CYAN)

        self._draw_settings_bottom_bar()

    def _draw_settings_radio(self):
        self._draw_row_cached(1, "< Radio / Mesh", BODY_Y, self.NEON_CYAN)
        stats = []
        if self.get_radio_stats:
            try:
                stats = self.get_radio_stats()
            except Exception:
                stats = [("error", "")]
        for i in range(BODY_ROWS - 1):
            y = BODY_Y + (i + 1) * CHAR_H
            if i < len(stats):
                label, value = stats[i]
                line = "  " + _pad(str(label), 14) + str(value)
                self._draw_row_cached(i + 2, line, y, self.NEON_CYAN)
            else:
                self._draw_row_cached(i + 2, "", y, self.NEON_CYAN)
        self._draw_settings_bottom_bar()

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
        self._draw_row_cached(1, "WiFi Networks", BODY_Y, self.NEON_CYAN)

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
                            self.tft.text(self.font, _pad(line), 0, y, self.YELLOW, self.SEL_BG)
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

        # Password input line
        prompt = "> "
        inp = self.cmd_buf.decode()
        text_part = inp[:COLS - 3] + "_"
        text_padded = _pad(text_part, COLS - 2)
        self.tft.text(self.font, prompt, 0, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
        self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.NEON_CYAN, self.BG_DARK)

    async def _do_wifi_scan(self):
        """Run WiFi scan in async task so UI renders 'Scanning...' first."""
        await asyncio.sleep_ms(100)  # yield to let UI draw "Scanning..."
        try:
            results = self.on_wifi_scan()
            self._wifi_networks = results or []
        except Exception as e:
            self._wifi_networks = []
        self._wifi_scanning = False
        self._cache = [''] * 15
        self.dirty = True

    def _draw_node_name(self):
        self._draw_row_cached(1, "Node Name", BODY_Y, self.NEON_CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)

        # Name input line
        prompt = "> "
        inp = self.cmd_buf.decode()
        text_part = inp[:COLS - 3] + "_"
        text_padded = _pad(text_part, COLS - 2)
        self.tft.text(self.font, prompt, 0, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
        self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.NEON_CYAN, self.BG_DARK)

    def _draw_tcp_host(self):
        self._draw_row_cached(1, "TCP Server Address", BODY_Y, self.NEON_CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.NEON_CYAN)

        # Address input line
        prompt = "> "
        inp = self.cmd_buf.decode()
        text_part = inp[:COLS - 3] + "_"
        text_padded = _pad(text_part, COLS - 2)
        self.tft.text(self.font, prompt, 0, INPUT_Y, self.NEON_GREEN, self.BG_DARK)
        self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.NEON_CYAN, self.BG_DARK)

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

    def handle_trackball(self):
        """Drain IRQ-captured trackball events."""
        # Read and reset counters — no disable_irq needed; worst case
        # an ISR fires between read and reset, losing one tick (harmless).
        up = self._irq_up;  self._irq_up = 0
        down = self._irq_down;  self._irq_down = 0
        click = self._irq_click;  self._irq_click = 0

        if not (up or down or click):
            return False

        # If screen is off, consume the event and just wake
        if not self._screen_on:
            self.wake_screen()
            return True

        self.wake_screen()

        for _ in range(up):
            self._scroll_up()
        for _ in range(down):
            self._scroll_down()
        if click:
            if self.state == STATE_IMAGE:
                self._exit_image_view()
            elif self.state == STATE_NODES:
                self._enter_chat()
            elif self.state == STATE_CHAT:
                # If cursor is on an image line, open it
                if self.chat_cursor >= 0 and self.chat_cursor in self._visible_image_lines:
                    msg_idx = self._visible_image_lines[self.chat_cursor]
                    cache_key = (self.selected_peer, msg_idx)
                    if cache_key in self._image_cache:
                        self._enter_image_view(msg_idx)
                # If cursor is on a voice line, play it
                elif self.chat_cursor >= 0 and self.chat_cursor in self._visible_audio_lines:
                    msg_idx = self._visible_audio_lines[self.chat_cursor]
                    cache_key = (self.selected_peer, msg_idx)
                    if cache_key in self._audio_cache and self.on_audio_play:
                        audio_data, audio_mode = self._audio_cache[cache_key]
                        self.on_audio_play(audio_data, audio_mode)
            elif self.state == STATE_SETTINGS:
                self.handle_key(b'\x0D')

        self.dirty = True
        return True

    def _scroll_up(self):
        if self.state == STATE_IMAGE:
            return
        elif self.state == STATE_NODES:
            if self.selected_idx > 0:
                self.selected_idx -= 1
                if self.selected_idx < self.node_scroll:
                    self.node_scroll = self.selected_idx
        elif self.state == STATE_SETTINGS:
            self._settings_scroll_up()
        else:
            # Move cursor up; scroll viewport when cursor reaches top
            _chat_rows = BODY_ROWS - 1
            if self.chat_cursor < 0:
                self.chat_cursor = _chat_rows - 1  # activate at bottom
            elif self.chat_cursor > 0:
                self.chat_cursor -= 1
            else:
                # Cursor at top — scroll viewport up
                if self.chat_scroll < MAX_HISTORY * 3:
                    self.chat_scroll += 1

    def _scroll_down(self):
        if self.state == STATE_IMAGE:
            return
        elif self.state == STATE_NODES:
            if self.selected_idx < len(self._peer_keys) - 1:
                self.selected_idx += 1
                if self.selected_idx >= self.node_scroll + BODY_ROWS:
                    self.node_scroll = self.selected_idx - BODY_ROWS + 1
        elif self.state == STATE_SETTINGS:
            self._settings_scroll_down()
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

    def _settings_scroll_down(self):
        if self._settings_page == _SET_MAIN:
            if self._settings_idx < 6:  # 7 items: WiFi..Volume, Radio, Addr
                self._settings_idx += 1
        elif self._settings_page == _SET_WIFI_SCAN:
            if self._settings_idx < len(self._wifi_networks) - 1:
                self._settings_idx += 1
                max_visible = BODY_ROWS - 2  # header row + 0-indexed
                if self._settings_idx >= self._settings_scroll + max_visible:
                    self._settings_scroll = self._settings_idx - max_visible + 1

    # --- Data management ---

    def clear_peers(self):
        """Clear node list, chat history, and related state."""
        self.peers.clear()
        self._peer_keys.clear()
        self.chat_history.clear()
        self.unread = {}
        self.selected_peer = None
        self.selected_idx = 0
        self.node_scroll = 0
        self.chat_scroll = 0
        self._cache = [''] * 15
        self.dirty = True

    def add_peer(self, dest_hash, name, rssi=None, hops=None, via=None):
        """Add or update a peer from an announce."""
        if dest_hash not in self.peers:
            if len(self.peers) >= MAX_PEERS:
                oldest = self._peer_keys[0]
                del self.peers[oldest]
                self._peer_keys.pop(0)

        self.peers[dest_hash] = {"name": name or "?", "rssi": rssi,
                                 "hops": hops, "via": via, "seen": time.time()}
        if dest_hash not in self._peer_keys:
            self._peer_keys.append(dest_hash)
        self._route_cache = ''  # selected-peer footer may show new route info
        self.dirty = True

    def add_chat_message(self, dest_hash, is_mine, text, status=0, image=None, audio=None, audio_mode=None):
        """Add a message to chat history. Returns index of the added message."""
        if dest_hash not in self.chat_history:
            self.chat_history[dest_hash] = []
        hist = self.chat_history[dest_hash]
        has_image = image is not None
        has_audio = audio is not None
        hist.append((is_mine, text, time.time(), status, has_image, has_audio))
        if len(hist) > MAX_HISTORY:
            hist.pop(0)
        msg_idx = len(hist) - 1

        # Cache audio data for replay
        if audio is not None:
            self._audio_cache[(dest_hash, msg_idx)] = (audio, audio_mode)

        # Cache image data (LRU eviction)
        if image is not None:
            cache_key = (dest_hash, msg_idx)
            self._image_cache[cache_key] = image
            self._image_cache_order.append(cache_key)
            while len(self._image_cache_order) > MAX_CACHED_IMAGES:
                old_key = self._image_cache_order.pop(0)
                self._image_cache.pop(old_key, None)

        # Track unread: incoming message when not viewing that chat
        if not is_mine:
            if self.state == STATE_NODES or self.selected_peer != dest_hash:
                self.unread[dest_hash] = self.unread.get(dest_hash, 0) + 1
            # Bubble peer to top of node list
            if dest_hash in self._peer_keys:
                self._peer_keys.remove(dest_hash)
                self._peer_keys.insert(0, dest_hash)
                # Keep selection on the same peer or reset to top
                if self.state == STATE_NODES:
                    self.selected_idx = 0
                    self.node_scroll = 0

        if self.state == STATE_CHAT:
            if self.selected_peer == dest_hash:
                self.chat_scroll = 0
                self._invalidate_chat_lines()
            # Invalidate body row cache — lines shift when new message arrives
            for i in range(1, BODY_ROWS + 1):
                self._cache[i] = ''
            self.dirty = True
        else:
            # On node list — mark dirty so unread indicator shows
            self.dirty = True
        return msg_idx

    def update_message_status(self, dest_hash, index, status):
        """Update delivery status of a specific message."""
        hist = self.chat_history.get(dest_hash)
        if hist and 0 <= index < len(hist):
            old = hist[index]
            has_image = old[4] if len(old) > 4 else False
            has_audio = old[5] if len(old) > 5 else False
            hist[index] = (old[0], old[1], old[2], status, has_image, has_audio)
            if self.state == STATE_CHAT and self.selected_peer == dest_hash:
                self._invalidate_chat_lines()
                for i in range(1, BODY_ROWS + 1):
                    self._cache[i] = ''
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
        elif self.state == STATE_RECORDING:
            self._draw_recording()
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

            # Screen timeout: turn off after inactivity
            if self._screen_on and time.ticks_diff(now, self._last_activity) > _SCREEN_TIMEOUT_MS:
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

            # Redraw: immediate for input line, throttled for full redraws
            if self._input_dirty and not self.dirty:
                spi_acquire_display()
                self.draw_input()
                spi_release_display()
                self._input_dirty = False
            elif self.dirty and time.ticks_diff(now, self._last_draw) > 50:
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
            if bl != _last_bl and self._screen_on:
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
            if self._screen_on:
                if self.state == STATE_SETTINGS and self._settings_page == _SET_RADIO:
                    self.dirty = True
                if self.ping_status and time.ticks_diff(
                        time.ticks_ms(), self._ping_status_ms) >= 8000:
                    self.ping_status = None
                    if self.state == STATE_NODES:
                        self.dirty = True
                if _clock_valid():
                    m = time.localtime()[4]
                    if m != _last_min:
                        _last_min = m
                        self.dirty = True
            await asyncio.sleep(1)

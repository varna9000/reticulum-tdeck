# T-Deck GUI Module
# Async state machine: node list + chat screens
# Diff-based drawing: only redraws changed rows. Async yields between rows.

import time
import gc
import uasyncio as asyncio
from machine import Pin, ADC

# Screen states
STATE_NODES    = 0
STATE_CHAT     = 1
STATE_SETTINGS = 2

# Settings sub-pages
_SET_MAIN      = 0
_SET_WIFI_SCAN = 1
_SET_WIFI_PASS = 2
_SET_TCP_HOST  = 3
_SET_NODE_NAME = 4

# Layout constants (320x240 landscape, 8x16 font)
SCREEN_W = 320
SCREEN_H = 240
CHAR_W = 8
CHAR_H = 16
COLS = 40          # 320 / 8
NAV_H = 16        # navbar height (1 row)
SEP_Y = 208       # separator y
INPUT_Y = 212      # input line y (below separator)
BODY_Y = NAV_H + CHAR_H  # main area starts one row below navbar (padding)
BODY_ROWS = 11    # (208 - 32) / 16 = 11 rows for content

# Data limits
MAX_PEERS = 16
MAX_HISTORY = 30  # per peer

# Trackball debounce
_TB_DEBOUNCE_MS = 80

# Pad string to exact width (no clearing needed)
def _pad(s, width=COLS):
    if len(s) >= width:
        return s[:width]
    return s + ' ' * (width - len(s))


class UI:

    def __init__(self, tft, font, get_key_func, node_name="T-Deck"):
        self.tft = tft
        self.font = font
        self.get_key = get_key_func
        self.node_name = node_name

        # Import colors from st7789py
        import st7789py as st
        self.BLACK   = st.BLACK
        self.WHITE   = st.WHITE
        self.RED     = st.RED
        self.GREEN   = st.GREEN
        self.BLUE    = st.BLUE
        self.CYAN    = st.CYAN
        self.YELLOW  = st.YELLOW
        self.MAGENTA = st.MAGENTA
        # Selection highlight: dark grey (RGB565)
        self.SEL_BG  = 0x2945

        # State
        self.state = STATE_NODES
        self.dirty = True
        self._input_dirty = False
        self._prev_state = -1  # force full clear on first draw
        self._state_change_ms = 0  # debounce rapid state flips

        # Row cache: 15 slots (navbar + 12 body + sep + input)
        # Compared before drawing — skip SPI if row unchanged.
        self._cache = [''] * 15

        # Peers: dest_hash_bytes -> {"name": str, "rssi": int}
        self.peers = {}
        self._peer_keys = []  # ordered list of dest_hash_bytes
        self.selected_idx = 0
        self.node_scroll = 0

        # Chat: dest_hash_bytes -> [(is_mine, text, timestamp, status), ...]
        # status: 0=none, 1=pending, 2=delivered, 3=failed
        self.chat_history = {}
        self.chat_scroll = 0
        self.selected_peer = None  # dest_hash_bytes of current chat peer

        # Input
        self.cmd_buf = bytearray()

        # Navbar state
        self.bat_v = 0.0
        self.rssi = None
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

        # Battery ADC
        self._bat_adc = ADC(Pin(4))
        self._bat_adc.atten(ADC.ATTN_11DB)

        # Unread message tracking
        self.unread = set()

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

        # Callbacks (set by tdeck_node.py)
        self.on_send = None       # on_send(dest_hash_bytes, text)
        self.on_announce = None   # on_announce()
        self.on_wifi_scan = None      # () -> [(ssid, rssi), ...]
        self.on_wifi_connect = None   # (ssid, password) -> bool
        self.on_tcp_toggle = None     # (enabled, host, port) -> bool
        self.on_node_name = None      # (name) -> None

    # --- Drawing helpers ---

    def _draw_row_cached(self, idx, text, y, fg, bg=None):
        """Draw row only if content changed. Returns True if drawn."""
        if self._cache[idx] == text:
            return False
        self._cache[idx] = text
        self.tft.text(self.font, _pad(text), 0, y, fg, bg or self.BLACK)
        return True

    def _row(self, text, y, fg, bg=None):
        """Draw a full-width padded row — overwrites old content, no flicker."""
        self.tft.text(self.font, _pad(text), 0, y, fg, bg or self.BLACK)

    def _text(self, text, x, y, fg, bg=None):
        """Draw text at pixel position."""
        self.tft.text(self.font, text, x, y, fg, bg or self.BLACK)

    # --- Navbar ---

    def draw_navbar(self):
        # Build full navbar string, padded to COLS
        bat_str = "bat:{:.1f}V".format(self.bat_v)
        iface_str = "[TCP]" if self._tcp_enabled else "[LoRa]"
        rssi_str = "rssi:" + str(self.rssi) if self.rssi is not None else ""
        name = self.node_name[:10]
        ann = "[A]" if (self.announce_flash and time.time() - self.announce_flash < 2) else "   "

        # Fixed-position layout: bat(10) iface(7) rssi(10) name(10) ann(3) = 40
        nav = "{:<10}{:<7}{:<10}{:>10}{}".format(bat_str, iface_str, rssi_str, name, ann)
        self._draw_row_cached(0, nav, 0, self.WHITE, self.BLUE)

    # --- Node list screen ---

    def draw_node_list(self):
        if not self._peer_keys:
            self._draw_row_cached(1, "No peers yet.", BODY_Y, self.WHITE)
            self._draw_row_cached(2, "Waiting for announces...", BODY_Y + CHAR_H, self.WHITE)
            for i in range(2, BODY_ROWS):
                self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.WHITE)
        else:
            visible = self._peer_keys[self.node_scroll:self.node_scroll + BODY_ROWS]
            for i in range(BODY_ROWS):
                y = BODY_Y + i * CHAR_H
                if i < len(visible):
                    key = visible[i]
                    peer = self.peers[key]
                    name = peer.get("name") or "?"
                    hash_str = key.hex()[:8]
                    marker = "* " if key in self.unread else "  "
                    right = "[" + hash_str + "]"
                    max_name = COLS - len(marker) - len(right) - 1
                    left = marker + name[:max_name]
                    line = left + " " * (COLS - len(left) - len(right)) + right

                    abs_idx = self.node_scroll + i
                    if abs_idx == self.selected_idx:
                        cache_key = '\x01' + line  # \x01 = selected marker
                        if self._cache[i + 1] != cache_key:
                            self._cache[i + 1] = cache_key
                            self.tft.text(self.font, _pad(line), 0, y, self.YELLOW, self.SEL_BG)
                            if key in self.unread:
                                self.tft.text(self.font, "* ", 0, y, self.MAGENTA, self.SEL_BG)
                    else:
                        self._draw_row_cached(i + 1, line, y, self.WHITE, self.BLACK)
                        if key in self.unread:
                            self.tft.text(self.font, "* ", 0, y, self.MAGENTA, self.BLACK)
                else:
                    self._draw_row_cached(i + 1, "", y, self.WHITE)

        # Separator
        self.tft.fill_rect(0, SEP_Y, SCREEN_W, 2, self.BLUE)

        # Status bar
        self._draw_row_cached(14, "Click=chat a=announce s=settings", INPUT_Y, self.CYAN)

    # --- Chat screen ---

    def _build_chat_lines(self):
        """Build word-wrapped display lines for current chat."""
        if self.selected_peer is None:
            return []

        _suffix_map = {1: " ..", 2: " \xfb", 3: " !"}
        msgs = self.chat_history.get(self.selected_peer, [])
        lines = []
        for msg in msgs:
            is_mine = msg[0]
            text = msg[1]
            status = msg[3] if len(msg) > 3 else 0
            if is_mine:
                prefix = "me> "
            else:
                peer = self.peers.get(self.selected_peer)
                pname = (peer.get("name") or "?")[:8]
                prefix = pname + "> "
            # Reserve space for suffix on last wrapped line
            suffix = _suffix_map.get(status, "") if is_mine else ""
            wrapped = self._wrap_text(prefix + text, COLS - len(suffix) if suffix else COLS)
            if suffix:
                wrapped[-1] = wrapped[-1] + suffix
            for j, wl in enumerate(wrapped):
                # status_suffix_len: how many chars of suffix on this line
                slen = len(suffix) if (suffix and j == len(wrapped) - 1) else 0
                lines.append((is_mine, wl, j == 0, slen, status))
        return lines

    def draw_chat(self):
        lines = self._build_chat_lines()

        # Clamp scroll — can't scroll past all content
        total = len(lines)
        max_scroll = max(0, total - 1)
        if self.chat_scroll > max_scroll:
            self.chat_scroll = max_scroll

        # Apply scroll — show last N lines, scrollable up
        view_end = max(0, total - self.chat_scroll)
        view_start = max(0, view_end - BODY_ROWS)
        visible = lines[view_start:view_end]

        _status_color = {1: self.YELLOW, 2: self.GREEN, 3: self.RED}
        for i in range(BODY_ROWS):
            y = BODY_Y + i * CHAR_H
            if i < len(visible):
                is_mine, text, is_first, slen, status = visible[i]
                cached = self._cache[i + 1] == text
                if cached:
                    continue  # skip unchanged row
                self._cache[i + 1] = text

                padded = _pad(text)
                if is_first:
                    # Optimized: draw full line WHITE, overdraw short prefix in color
                    self.tft.text(self.font, padded, 0, y, self.WHITE, self.BLACK)
                    if is_mine:
                        self.tft.text(self.font, text[:4], 0, y, self.GREEN, self.BLACK)
                    else:
                        gt = text.find(">")
                        if gt >= 0:
                            self.tft.text(self.font, text[:gt + 1], 0, y, self.RED, self.BLACK)
                else:
                    self.tft.text(self.font, padded, 0, y, self.WHITE, self.BLACK)
                # Overdraw status suffix in color
                if slen > 0 and status in _status_color:
                    sx = (len(text) - slen) * CHAR_W
                    self.tft.text(self.font, text[-slen:], sx, y, _status_color[status], self.BLACK)
            else:
                self._draw_row_cached(i + 1, "", y, self.WHITE)

        # Separator
        self.tft.fill_rect(0, SEP_Y, SCREEN_W, 2, self.BLUE)

        # Input line
        self.draw_input()

    def draw_input(self):
        prompt = "> "
        inp = self.cmd_buf.decode()
        text_part = inp[:COLS - 3] + "_"
        text_padded = _pad(text_part, COLS - 2)
        # Draw prompt in green, text+cursor in white — single pass, no flicker
        self.tft.text(self.font, prompt, 0, INPUT_Y, self.GREEN, self.BLACK)
        self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.WHITE, self.BLACK)

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

        if self.state == STATE_NODES:
            return self._handle_key_nodes(ch, key)
        elif self.state == STATE_SETTINGS:
            return self._handle_key_settings(ch, key)
        else:
            return self._handle_key_chat(ch, key)

    def _enter_chat(self):
        """Enter chat for the currently selected peer (trackball click only)."""
        if self._peer_keys and 0 <= self.selected_idx < len(self._peer_keys):
            self.selected_peer = self._peer_keys[self.selected_idx]
            self.unread.discard(self.selected_peer)
            self.chat_scroll = 0
            self.cmd_buf = bytearray()
            self.state = STATE_CHAT
            self._state_change_ms = time.ticks_ms()
            self.dirty = True

    def _handle_key_nodes(self, ch, key):
        if key == b'a' or key == b'A':
            if self.on_announce:
                self.on_announce()
            self.announce_flash = time.time()
            self.dirty = True
            return True
        elif key == b's' or key == b'S':
            self._settings_page = _SET_MAIN
            self._settings_idx = 0
            self.state = STATE_SETTINGS
            self._state_change_ms = time.ticks_ms()
            self.dirty = True
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
            self.cmd_buf += key
            self._input_dirty = True
            return True
        return False

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
        # Separator
        self.tft.fill_rect(0, SEP_Y, SCREEN_W, 2, self.BLUE)

    def _draw_settings_main(self):
        self._draw_row_cached(1, "Settings", BODY_Y, self.CYAN)

        if self._wifi_connected:
            wifi_status = self._wifi_ssid_current
            if self._wifi_ip:
                wifi_status += " [" + self._wifi_ip + "]"
        else:
            wifi_status = "not connected"
        wifi_line = "WiFi: " + wifi_status
        tcp_line = "TCP:  " + (self._tcp_target if self._tcp_enabled else "OFF")
        name_line = "Name: " + self.node_name

        items = [wifi_line, tcp_line, name_line]
        for i in range(BODY_ROWS - 1):
            y = BODY_Y + (i + 1) * CHAR_H
            if i < len(items):
                line = "  " + items[i]
                if i == self._settings_idx:
                    cache_key = '\x01' + line
                    if self._cache[i + 2] != cache_key:
                        self._cache[i + 2] = cache_key
                        self.tft.text(self.font, _pad(line), 0, y, self.YELLOW, self.SEL_BG)
                else:
                    self._draw_row_cached(i + 2, line, y, self.WHITE)
            else:
                self._draw_row_cached(i + 2, "", y, self.WHITE)

        self._draw_row_cached(14, "Click=select  Bksp=back", INPUT_Y, self.CYAN)

    def _draw_wifi_scan(self):
        self._draw_row_cached(1, "WiFi Networks", BODY_Y, self.CYAN)

        if not self._wifi_networks:
            msg = "  Scanning..." if self._wifi_scanning else "  No networks found"
            self._draw_row_cached(2, msg, BODY_Y + CHAR_H, self.WHITE)
            for i in range(2, BODY_ROWS):
                self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.WHITE)
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
                    else:
                        self._draw_row_cached(i + 2, line, y, self.WHITE)
                else:
                    self._draw_row_cached(i + 2, "", y, self.WHITE)

        self._draw_row_cached(14, "Click=select  Bksp=back", INPUT_Y, self.CYAN)

    def _draw_wifi_pass(self):
        self._draw_row_cached(1, "Connect to: " + self._wifi_ssid[:26], BODY_Y, self.CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.WHITE)

        # Password input line
        prompt = "> "
        inp = self.cmd_buf.decode()
        text_part = inp[:COLS - 3] + "_"
        text_padded = _pad(text_part, COLS - 2)
        self.tft.text(self.font, prompt, 0, INPUT_Y, self.GREEN, self.BLACK)
        self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.WHITE, self.BLACK)

    async def _do_wifi_scan(self):
        """Run WiFi scan in async task so UI renders 'Scanning...' first."""
        await asyncio.sleep(0)  # yield to let UI draw
        try:
            results = self.on_wifi_scan()
            self._wifi_networks = results or []
        except Exception:
            self._wifi_networks = []
        self._wifi_scanning = False
        self._cache = [''] * 15
        self.dirty = True

    def _draw_node_name(self):
        self._draw_row_cached(1, "Node Name", BODY_Y, self.CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.WHITE)

        # Name input line
        prompt = "> "
        inp = self.cmd_buf.decode()
        text_part = inp[:COLS - 3] + "_"
        text_padded = _pad(text_part, COLS - 2)
        self.tft.text(self.font, prompt, 0, INPUT_Y, self.GREEN, self.BLACK)
        self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.WHITE, self.BLACK)

    def _draw_tcp_host(self):
        self._draw_row_cached(1, "TCP Server Address", BODY_Y, self.CYAN)

        for i in range(1, BODY_ROWS):
            self._draw_row_cached(i + 1, "", BODY_Y + i * CHAR_H, self.WHITE)

        # Address input line
        prompt = "> "
        inp = self.cmd_buf.decode()
        text_part = inp[:COLS - 3] + "_"
        text_padded = _pad(text_part, COLS - 2)
        self.tft.text(self.font, prompt, 0, INPUT_Y, self.GREEN, self.BLACK)
        self.tft.text(self.font, text_padded, 2 * CHAR_W, INPUT_Y, self.WHITE, self.BLACK)

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

        for _ in range(up):
            self._scroll_up()
        for _ in range(down):
            self._scroll_down()
        if click:
            if self.state == STATE_NODES:
                self._enter_chat()
            elif self.state == STATE_SETTINGS:
                self.handle_key(b'\x0D')

        self.dirty = True
        return True

    def _scroll_up(self):
        if self.state == STATE_NODES:
            if self.selected_idx > 0:
                self.selected_idx -= 1
                if self.selected_idx < self.node_scroll:
                    self.node_scroll = self.selected_idx
        elif self.state == STATE_SETTINGS:
            self._settings_scroll_up()
        else:
            # Capped by draw_chat to actual content length
            if self.chat_scroll < MAX_HISTORY * 3:
                self.chat_scroll += 1

    def _scroll_down(self):
        if self.state == STATE_NODES:
            if self.selected_idx < len(self._peer_keys) - 1:
                self.selected_idx += 1
                if self.selected_idx >= self.node_scroll + BODY_ROWS:
                    self.node_scroll = self.selected_idx - BODY_ROWS + 1
        elif self.state == STATE_SETTINGS:
            self._settings_scroll_down()
        else:
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
            if self._settings_idx < 2:  # 3 items: WiFi, TCP, Name
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
        self.unread.clear()
        self.selected_peer = None
        self.selected_idx = 0
        self.node_scroll = 0
        self.chat_scroll = 0
        self._cache = [''] * 15
        self.dirty = True

    def add_peer(self, dest_hash, name, rssi=None):
        """Add or update a peer from an announce."""
        if dest_hash not in self.peers:
            if len(self.peers) >= MAX_PEERS:
                oldest = self._peer_keys[0]
                del self.peers[oldest]
                self._peer_keys.pop(0)

        self.peers[dest_hash] = {"name": name or "?", "rssi": rssi}
        if dest_hash not in self._peer_keys:
            self._peer_keys.append(dest_hash)
        self.dirty = True

    def add_chat_message(self, dest_hash, is_mine, text, status=0):
        """Add a message to chat history. Returns index of the added message."""
        if dest_hash not in self.chat_history:
            self.chat_history[dest_hash] = []
        hist = self.chat_history[dest_hash]
        hist.append((is_mine, text, time.time(), status))
        if len(hist) > MAX_HISTORY:
            hist.pop(0)
        msg_idx = len(hist) - 1

        # Track unread: incoming message when not viewing that chat
        if not is_mine:
            if self.state == STATE_NODES or self.selected_peer != dest_hash:
                self.unread.add(dest_hash)
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
            hist[index] = (old[0], old[1], old[2], status)
            if self.state == STATE_CHAT and self.selected_peer == dest_hash:
                for i in range(1, BODY_ROWS + 1):
                    self._cache[i] = ''
                self.dirty = True

    def update_battery(self):
        """Read battery voltage from ADC. T-Deck has voltage divider (x2)."""
        raw = self._bat_adc.read()
        self.bat_v = raw * 3.3 * 2 / 4095

    # --- Main draw ---

    def draw(self):
        """Cached screen redraw — skips unchanged rows."""
        # Clear body + invalidate cache on screen state change
        if self.state != self._prev_state:
            self.tft.fill_rect(0, NAV_H, SCREEN_W, SCREEN_H - NAV_H, self.BLACK)
            self._cache = [''] * 15  # invalidate all rows
            self._prev_state = self.state

        self.draw_navbar()
        if self.state == STATE_NODES:
            self.draw_node_list()
        elif self.state == STATE_SETTINGS:
            self.draw_settings()
        else:
            self.draw_chat()
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
                self.handle_key(key)
            self.handle_trackball()
            await asyncio.sleep_ms(30)

    async def gui_loop(self, spi_acquire_display, spi_release_display):
        """Drawing + input loop. kbd_loop handles fast polling separately."""
        self._last_draw = 0

        # Initial draw
        spi_acquire_display()
        self.draw()
        spi_release_display()

        while True:
            # Redraw: immediate for input line, throttled for full redraws
            now = time.ticks_ms()
            if self._input_dirty and not self.dirty:
                spi_acquire_display()
                self.draw_input()
                spi_release_display()
                self._input_dirty = False
            elif self.dirty and time.ticks_diff(now, self._last_draw) > 80:
                spi_acquire_display()
                self.draw()
                spi_release_display()
                self._last_draw = now

            await asyncio.sleep_ms(20 if self.dirty or self._input_dirty else 200)

    async def battery_loop(self, spi_acquire_display, spi_release_display):
        """Update battery reading every 10s."""
        while True:
            self.update_battery()
            spi_acquire_display()
            self.draw_navbar()
            spi_release_display()
            await asyncio.sleep(10)

# Scrolling text-log terminal for the T-Deck rnsh client.
#
# Feeds raw bytes from the remote shell's stdout/stderr and maintains a bounded
# scrollback of Unicode lines for the UI to render (via ui._tb glyph mapping).
# This is a text-log MVP, not a full VT100 emulator: it honours CR / LF / BS /
# TAB / BEL and clear-screen / erase-line, and strips all other ANSI escape
# sequences (colours, cursor addressing). Full-screen TUIs (vim/htop) won't
# render correctly — line-oriented commands, logs, git, etc. do.
#
# UTF-8 is decoded incrementally so multibyte characters split across chunks
# render correctly; undecodable bytes become '?'.


class Terminal:
    def __init__(self, cols=40, max_lines=200):
        self.cols = cols
        self.max_lines = max_lines
        self.lines = [""]          # scrollback; last entry is the current line
        self.cur_col = 0
        self.dirty = True
        self._utf8 = bytearray()   # partial UTF-8 bytes awaiting completion
        self._esc = 0              # 0 normal, 1 saw ESC, 2 in CSI, 3 in OSC
        self._esc_buf = bytearray()

    def feed(self, data):
        for b in data:
            self._byte(b)
        self.dirty = True

    def clear(self):
        self.lines = [""]
        self.cur_col = 0
        self._utf8 = bytearray()
        self._esc = 0
        self._esc_buf = bytearray()
        self.dirty = True

    # --- byte processing ----------------------------------------------------

    def _byte(self, b):
        if self._esc == 1:                 # just saw ESC
            if b == 0x5b:                  # '[' -> CSI
                self._esc = 2
                self._esc_buf = bytearray()
            elif b == 0x5d:                # ']' -> OSC
                self._esc = 3
            else:                          # ESC x : consume x, done
                self._esc = 0
            return
        if self._esc == 2:                 # CSI params until a final byte
            if 0x40 <= b <= 0x7e:
                self._csi(bytes(self._esc_buf), b)
                self._esc = 0
            elif len(self._esc_buf) < 32:
                self._esc_buf.append(b)
            return
        if self._esc == 3:                 # OSC until BEL (or ESC \)
            if b == 0x07:
                self._esc = 0
            elif b == 0x1b:
                self._esc = 1
            return

        if b == 0x1b:                      # ESC
            self._esc = 1
            return
        if b == 0x0d:                      # CR
            self.cur_col = 0
            return
        if b == 0x0a:                      # LF
            self._newline()
            return
        if b == 0x08:                      # BS
            if self.cur_col > 0:
                self.cur_col -= 1
            return
        if b == 0x09:                      # TAB -> next 8-col stop
            nxt = (self.cur_col // 8 + 1) * 8
            self._putstr(" " * (nxt - self.cur_col))
            return
        if b < 0x20 or b == 0x7f:          # other control / DEL -> ignore
            return

        # printable byte: accumulate + incrementally UTF-8 decode
        self._utf8.append(b)
        ch = self._decode()
        if ch is not None:
            self._putstr(ch)

    def _decode(self):
        try:
            s = bytes(self._utf8).decode("utf-8")
            self._utf8 = bytearray()
            return s
        except Exception:
            if len(self._utf8) >= 4:       # never a valid UTF-8 run this long
                self._utf8 = bytearray()
                return "?"
            return None                    # incomplete — wait for more bytes

    # --- line buffer --------------------------------------------------------

    def _putstr(self, s):
        line = self.lines[-1]
        if self.cur_col > len(line):
            line = line + " " * (self.cur_col - len(line))
        line = line[:self.cur_col] + s + line[self.cur_col + len(s):]
        self.cur_col += len(s)
        # wrap anything past the terminal width onto new lines
        while len(line) > self.cols:
            self.lines[-1] = line[:self.cols]
            self._push("")
            line = line[self.cols:]
            self.cur_col -= self.cols
        self.lines[-1] = line

    def _newline(self):
        self._push("")
        self.cur_col = 0

    def _push(self, s):
        self.lines.append(s)
        while len(self.lines) > self.max_lines:
            self.lines.pop(0)

    def _csi(self, params, final):
        f = final
        if f == 0x4a:          # 'J' erase in display
            if params in (b"", b"0"):
                # Erase BELOW the cursor only. The cursor always sits on the
                # last line of this scrollback model, so there is nothing
                # after it to drop — just truncate the tail of the current
                # line. zsh's ZLE emits ESC[J on every prompt redraw; mapping
                # it to a full clear wipes the whole scrollback after each
                # command (bash's readline uses ESC[K instead, which is why
                # bash listeners never triggered this).
                self.lines[-1] = self.lines[-1][:self.cur_col]
            elif params == b"1":
                # Erase above: blank the current line up to the cursor, keep
                # the scrollback (text-log approximation).
                line = self.lines[-1]
                n = min(self.cur_col, len(line))
                self.lines[-1] = " " * n + line[n:]
            elif params in (b"2", b"3"):
                self.lines = [""]
                self.cur_col = 0
        elif f == 0x4b:        # 'K' erase in line (cursor -> end)
            self.lines[-1] = self.lines[-1][:self.cur_col]
        # everything else (SGR 'm' colours, cursor moves 'H'/'A'..'D', etc.)
        # is intentionally stripped for the text-log MVP.

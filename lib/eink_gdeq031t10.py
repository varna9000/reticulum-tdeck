# GDEQ031T10 e-paper driver (UC8253 controller), 240x320 1-bit.
# For the LilyGO T-Deck Pro.
#
# Ported from GxEPD2 1.6.9 (src/gdeq/GxEPD2_310_GDEQ031T10.cpp, Jean-Marc Zingg,
# GPL-3.0), which is the C++ implementation Meshtastic builds against for this
# panel. Controller datasheet: UC8253.
#
# Panel timings taken from the reference header, and they are the whole story
# for how this device has to be driven:
#
#     power on          50 ms
#     full refresh    1100 ms
#     partial refresh  700 ms
#
# Nothing here is fast. A caller that flushes per draw call will spend seconds
# redrawing one screen, so the framebuffer is deliberately kept local and the
# panel is only touched on an explicit flush. See eink_shim.py for the batching
# policy layered on top.

import framebuf
import time
from micropython import const

# UC8253 commands
_PANEL_SETTING   = const(0x00)
_POWER_OFF       = const(0x02)
_POWER_ON        = const(0x04)
_WRITE_PREV      = const(0x10)  # previous frame, panel diffs against this
_DISPLAY_REFRESH = const(0x12)
_WRITE_CURR      = const(0x13)  # current frame
_VCOM_INTERVAL   = const(0x50)
_PARTIAL_WINDOW  = const(0x90)
_PARTIAL_IN      = const(0x91)
_PARTIAL_OUT     = const(0x92)
_CASCADE_SET     = const(0xE0)
_FORCE_TEMP      = const(0xE5)

_POWER_ON_MS     = const(50)
_FULL_MS         = const(1100)
_PARTIAL_MS      = const(700)


class GDEQ031T10:
    """240x320 mono e-paper.

    Owns a framebuf.FrameBuffer in MONO_HLSB. Drawing goes to that buffer with
    no panel traffic; flush() and flush_rect() are the only methods that talk
    to the display.

    In the local framebuffer a set bit means BLACK ink, which is the natural
    convention for drawing on white paper. The panel's own buffer uses 1 for
    white, so bytes are inverted on the way out. If the first bench render
    comes back as a photographic negative, flip `invert`.
    """

    WIDTH = 240
    HEIGHT = 320

    def __init__(self, spi, cs, dc, busy, rst=None,
                 acquire=None, release=None, invert=True):
        self._spi = spi
        self._cs = cs
        self._dc = dc
        self._busy = busy
        self._rst = rst
        # Bus arbitration hooks. The panel shares SCK with the SX1262 on this
        # board, so the caller supplies acquire/release to retune the bus.
        self._acquire = acquire
        self._release = release
        self._invert = invert

        self._power_on = False
        self._init_done = False
        self._hibernating = False
        self._initial_refresh = True

        stride = self.WIDTH // 8
        self._stride = stride
        self.buf = bytearray(stride * self.HEIGHT)
        self.fb = framebuf.FrameBuffer(
            self.buf, self.WIDTH, self.HEIGHT, framebuf.MONO_HLSB)
        # Scratch row used to invert one line at a time on transfer, so a flush
        # does not need a second full-size buffer.
        self._row = bytearray(stride)

        self._cs.value(1)

    # --- low level ---------------------------------------------------------

    def _cmd(self, c, data=None):
        self._dc.value(0)
        self._cs.value(0)
        self._spi.write(bytes([c]))
        if data is not None:
            self._dc.value(1)
            self._spi.write(bytes(data) if not isinstance(data, int) else bytes([data]))
        self._cs.value(1)

    def _data(self, buf):
        self._dc.value(1)
        self._cs.value(0)
        self._spi.write(buf)
        self._cs.value(1)

    def _wait_busy(self, timeout_ms):
        # BUSY is active low on this panel (GxEPD2 constructs it with LOW).
        # Always bound the wait: a panel that never releases BUSY must not
        # wedge the event loop, since this runs alongside the LoRa poll task.
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms + 500)
        while self._busy.value() == 0:
            if time.ticks_diff(deadline, time.ticks_ms()) < 0:
                return False
            time.sleep_ms(5)
        return True

    def _reset(self):
        if self._rst is not None:
            self._rst.value(0)
            time.sleep_ms(10)
            self._rst.value(1)
            time.sleep_ms(10)
        self._hibernating = False

    # --- panel sequences ---------------------------------------------------

    def _init_display(self):
        if self._hibernating:
            self._reset()
        else:
            # Soft reset via panel setting, per the reference driver.
            self._cmd(_PANEL_SETTING, [0x1E, 0x0D])
            time.sleep_ms(1)
        self._power_on = False
        self._cmd(_PANEL_SETTING, [0x1F, 0x0D])
        self._init_done = True

    def _do_power_on(self):
        if not self._power_on:
            self._cmd(_POWER_ON)
            self._wait_busy(_POWER_ON_MS)
        self._power_on = True

    def _power_off(self):
        if self._power_on:
            self._cmd(_POWER_OFF)
            self._wait_busy(_POWER_ON_MS)
        self._power_on = False

    def _set_window(self, x, y, w, h):
        xe = (x + w - 1) | 0x07      # inclusive, snapped up to a byte boundary
        x &= 0xF8                     # snapped down to a byte boundary
        ye = y + h - 1
        self._cmd(_PARTIAL_WINDOW,
                  [x, xe, y >> 8, y & 0xFF, ye >> 8, ye & 0xFF, 0x01])

    def _update_full(self):
        # Fast full update: fix the temperature the waveform is chosen for,
        # rather than letting the panel read its sensor and pick a slower LUT.
        self._cmd(_CASCADE_SET, [0x02])
        self._cmd(_FORCE_TEMP, [0x5A])
        self._cmd(_VCOM_INTERVAL, [0x97])
        self._do_power_on()
        self._cmd(_DISPLAY_REFRESH)
        self._wait_busy(_FULL_MS)
        self._init_done = False   # reference driver does this too

    def _update_part(self):
        self._cmd(_CASCADE_SET, [0x02])
        self._cmd(_FORCE_TEMP, [0x79])
        self._cmd(_VCOM_INTERVAL, [0xD7])
        self._do_power_on()
        self._cmd(_DISPLAY_REFRESH)
        self._wait_busy(_PARTIAL_MS)
        self._init_done = False

    # --- buffer transfer ---------------------------------------------------

    def _write_region(self, command, x, y, w, h):
        """Send a rectangle of the local framebuffer to one of the panel buffers.

        x and w are snapped to byte boundaries because the panel addresses
        source data in whole bytes.
        """
        x0 = x & 0xF8
        x1 = (x + w + 7) & 0xF8
        if x1 > self.WIDTH:
            x1 = self.WIDTH
        wb = (x1 - x0) // 8
        if wb <= 0 or h <= 0:
            return

        self._set_window(x0, y, x1 - x0, h)
        self._cmd(command)
        self._dc.value(1)
        self._cs.value(0)
        row = self._row
        mv = memoryview(row)[:wb]
        stride = self._stride
        buf = self.buf
        for r in range(y, y + h):
            base = r * stride + x0 // 8
            if self._invert:
                for i in range(wb):
                    row[i] = buf[base + i] ^ 0xFF
            else:
                for i in range(wb):
                    row[i] = buf[base + i]
            self._spi.write(mv)
        self._cs.value(1)

    # --- public API --------------------------------------------------------

    def init(self):
        if self._acquire:
            self._acquire()
        try:
            self._reset()
            self._init_display()
        finally:
            if self._release:
                self._release()

    def flush(self):
        """Full refresh of the whole panel. About 1.1 s. Clears ghosting."""
        if self._acquire:
            self._acquire()
        try:
            if not self._init_done:
                self._init_display()
            # Write both buffers so the panel's next diff has a correct base.
            self._write_region(_WRITE_PREV, 0, 0, self.WIDTH, self.HEIGHT)
            self._write_region(_WRITE_CURR, 0, 0, self.WIDTH, self.HEIGHT)
            self._update_full()
            self._initial_refresh = False
        finally:
            if self._release:
                self._release()

    def flush_rect(self, x, y, w, h):
        """Partial refresh of one rectangle. About 0.7 s.

        The very first update after power-up has to be a full one, or the
        panel has no valid previous frame to diff against.
        """
        if self._initial_refresh:
            return self.flush()

        # Clip to the panel.
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        if x + w > self.WIDTH:
            w = self.WIDTH - x
        if y + h > self.HEIGHT:
            h = self.HEIGHT - y
        if w <= 0 or h <= 0:
            return

        if self._acquire:
            self._acquire()
        try:
            if not self._init_done:
                self._init_display()
            self._cmd(_PARTIAL_IN)
            self._write_region(_WRITE_CURR, x, y, w, h)
            self._update_part()
            self._cmd(_PARTIAL_OUT)
            # Keep the previous-frame buffer in step, otherwise the next
            # partial update diffs against a stale frame and leaves artefacts.
            self._cmd(_PARTIAL_IN)
            self._write_region(_WRITE_PREV, x, y, w, h)
            self._cmd(_PARTIAL_OUT)
        finally:
            if self._release:
                self._release()

    def power_off(self):
        if self._acquire:
            self._acquire()
        try:
            self._power_off()
        finally:
            if self._release:
                self._release()

    def hibernate(self):
        self.power_off()
        self._hibernating = True
        self._init_done = False

# CST328 capacitive touch controller, as fitted to the LilyGO T-Deck Pro.
#
# Hands the board layer a stream of single-finger events -- DOWN, MOVE, UP
# with framebuffer coordinates -- and nothing else. Gestures and hit testing
# live above this, in the board module; the UI never sees this class.
#
# Everything here was checked on a V1.1 board with
# tools/tdeck_pro_touch_probe.py (2026-10-10):
#
#   - coordinates arrive in the panel's own portrait orientation, 0..239 by
#     0..319, matching the framebuffer with no swap or flip; jitter is 1 px;
#   - status nibble 6 means in contact. After lift-off the chip keeps
#     reporting count 1, status 0xB, with the LAST position for about a
#     second -- so the count byte alone never means "touching", and a tap
#     shorter than one poll still leaves its position readable;
#   - INT gives one falling edge per contact report (~65/s), nothing for the
#     lifted reports, nothing at rest. DOWN carries the tick of the first
#     edge and a touch read only after it ended carries the last one as its
#     UP, so event times are when things happened, not when they were read;
#   - the chip's own sleep command takes it off the bus and silences INT, so
#     "off" is simply the reset line held low.
#
# The register map is the Waveshare / SensorLib one, except that the chip
# type 0xCACA sits at 0xD1FE on this board rather than 0xD204.

import time
from micropython import const
from machine import Pin

CST328_ADDR = const(0x1A)

_REG_POINTS = const(0xD000)      # 27 bytes: point 0, count, point 1.., 5 bytes each
_REG_COUNT = const(0xD005)       # low nibble = fingers
_CLEAR = b"\xD0\x00\xAB"         # 0xD000 <- 0xAB: acknowledge the report
_POINTS_LEN = const(27)

_ST_CONTACT = const(6)
_JITTER_PX = const(1)            # moves of this much or less are dropped
_RESET_LOW_MS = const(20)
_WAKE_RETRY_MS = const(50)       # between "does it answer yet" reads after a pulse
_WAKE_TRIES = const(40)          # ~2 s, then give up until the screen next wakes

MAX_X = const(239)
MAX_Y = const(319)

DOWN = const(1)
MOVE = const(2)
UP = const(3)


class CST328:
    """Call poll() on every keyboard pass; it is free unless a finger is
    down or INT fired since the last call."""

    def __init__(self, i2c, int_gpio, rst_gpio, addr=CST328_ADDR):
        self._i2c = i2c
        self._addr = addr
        self._rst = Pin(rst_gpio, Pin.OUT, value=1)
        self._int = Pin(int_gpio, Pin.IN, Pin.PULL_UP)
        self._latched = 0            # set by the IRQ, cleared by poll()
        self._edge0 = 0              # tick of the first INT edge since the last poll
        self._edge1 = 0              # tick of the latest one
        self._down = False           # a finger is known to be in contact
        self._x = 0
        self._y = 0
        self.asleep = False          # reset held low
        self.ready = False           # answered on I2C since the last wake()
        self.gave_up = False         # never answered after a pulse; retried at the next wake
        self._tries = 0
        self._tried_at = 0
        self._int.irq(trigger=Pin.IRQ_FALLING, handler=self._on_int)

    def _on_int(self, pin):
        # Small-int stores only, nothing allocates. The ticks matter: the key
        # loop does not run while the e-ink panel refreshes (~700 ms), and a
        # touch made in that window is read long after it happened. INT
        # fires once per contact report, so the first edge is the finger
        # going down and the latest one is, to ~15 ms, the finger leaving.
        t = time.ticks_ms()
        if not self._latched:
            self._edge0 = t
        self._edge1 = t
        self._latched = 1

    def touching(self):
        return self._down

    def sleep(self):
        """Hold the chip in reset: no draw, no reports, no INT. The board
        does this whenever the screen is dark, which also covers light
        sleep (touch does not wake the deck; the keyboard does)."""
        self._rst.value(0)
        self.asleep = True
        self.ready = False
        self.gave_up = False
        self._tries = 0
        self._down = False
        self._latched = 0

    def service(self, screen_on):
        """The one call the board makes on each keyboard pass: keep the chip's
        reset in step with the screen, then return poll()'s events.

        Whether the chip exists is decided here, by wake() retries, and not
        by a scan at boot: the reset pad can still be low then (it stays
        driven across a soft reset, and V1.0 wires it to a strapping pin
        with a pull-down), which would hide a chip that is perfectly fine.
        A chip that never answers costs ~40 reads and is tried again the
        next time the screen comes on."""
        if not screen_on:
            if self.asleep:
                return []
            evs = []
            if self._down:
                # The screen went dark under a held finger: close the stroke
                # so nothing above is left holding a DOWN.
                evs.append((UP, self._x, self._y, time.ticks_ms()))
            self.sleep()
            return evs
        if not self.ready:
            if self.gave_up:
                return []
            now = time.ticks_ms()
            if self._tries and time.ticks_diff(now, self._tried_at) < _WAKE_RETRY_MS:
                return []
            self._tried_at = now
            if not self.wake():
                self._tries += 1
                if self._tries >= _WAKE_TRIES:
                    self.gave_up = True
                return []
            self._tries = 0
        return self.poll()

    def wake(self):
        """Release reset (once) and report whether the chip answers yet.

        The chip needs ~200 ms after the pulse. This never waits for it:
        the first call pulses, every call checks, and the caller keeps
        calling on its own cadence until True. Returns True once ready."""
        if self.asleep:
            self._rst.value(0)
            time.sleep_ms(_RESET_LOW_MS)
            self._rst.value(1)
            self.asleep = False
            self._latched = 0
        if self.ready:
            return True
        try:
            self._i2c.readfrom_mem(self._addr, _REG_COUNT, 1, addrsize=16)
        except OSError:
            return False
        self.ready = True
        self._latched = 0
        self._down = False
        return True

    def _read(self):
        """(count, report) or raises OSError when the chip is not answering."""
        n = self._i2c.readfrom_mem(self._addr, _REG_COUNT, 1, addrsize=16)[0] & 0x0F
        buf = None
        if n:
            buf = self._i2c.readfrom_mem(self._addr, _REG_POINTS, _POINTS_LEN, addrsize=16)
        self._i2c.writeto(self._addr, _CLEAR)
        return n, buf

    def poll(self):
        """Events since the last call: [(kind, x, y, t_ms), ...].

        Reads the chip only when INT fired since the last call or a finger is
        known to be down, so an idle deck puts nothing on the bus. The latch
        is cleared BEFORE the read: an edge that lands during the read sets
        it again and the next pass reads once more.

        Status 6 is contact. Anything else (0xB after lift-off, 0 rarely) is
        not, but the report still carries the last contact position, which
        is how a tap shorter than one poll still gets a DOWN and an UP."""
        if not self.ready or not (self._latched or self._down):
            return []
        t_down = self._edge0         # only used when a stroke starts here,
        t_last = self._edge1         # which always means the latch was set
        self._latched = 0
        try:
            n, buf = self._read()
        except OSError:
            # The chip stopped answering (the bus is shared and has no
            # lock). A stroke in progress is closed rather than abandoned:
            # every DOWN gets its UP.
            if self._down:
                self._down = False
                return [(UP, self._x, self._y, time.ticks_ms())]
            return []
        t = time.ticks_ms()
        contact = False
        have_point = False
        x = y = 0
        if n and buf is not None:
            have_point = True
            contact = (buf[0] & 0x0F) == _ST_CONTACT
            x = (buf[1] << 4) | (buf[3] >> 4)
            y = (buf[2] << 4) | (buf[3] & 0x0F)
            if x > MAX_X:
                x = MAX_X
            if y > MAX_Y:
                y = MAX_Y
        evs = []
        if contact:
            if not self._down:
                self._down = True
                self._x, self._y = x, y
                evs.append((DOWN, x, y, t_down))
            elif abs(x - self._x) > _JITTER_PX or abs(y - self._y) > _JITTER_PX:
                self._x, self._y = x, y
                evs.append((MOVE, x, y, t))
        elif self._down:
            # Lift-off (or the chip dropped the finger): UP where we last
            # saw it in contact. The lifted report's own coordinates are a
            # pixel or two off, so they are not used.
            self._down = False
            evs.append((UP, self._x, self._y, t))
        elif have_point:
            # INT fired but the contact report was already replaced by the
            # lifted one: a touch that began and ended between two polls.
            # Its last position survives in the report and its times in the
            # edge ticks; the path in between is gone, so a swipe made
            # during a panel refresh arrives as a press at its end point,
            # timestamped before the refresh finished. (More than ~1 s
            # late the report is gone too and the touch is dropped.)
            evs.append((DOWN, x, y, t_down))
            evs.append((UP, x, y, t_last))
        return evs

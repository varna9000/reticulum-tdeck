# Capacitive touch for the LilyGO T-Deck Pro: the CST328, and the CST3530
# that later V1.1 batches carry at the same address and pins (issue #19).
#
# Hands the board layer a stream of single-finger events -- DOWN, MOVE, UP
# with framebuffer coordinates -- and nothing else. Gestures and hit testing
# live above this, in the board module; the UI never sees this class.
#
# The two chips share nothing but the address. The CST328 is a register
# chip (16-bit register addresses); the CST3530 speaks Hynitron's "CST66xx"
# protocol, where every transaction is a 4-byte command word followed by a
# plain read. Which one is fitted is decided every time the chip comes out
# of reset, by the info probe below, and only the read and its decoding
# differ after that: the INT latch, the reset-as-sleep model and the event
# contract are the same for both.
#
# CST328, checked on a V1.1 board with tools/tdeck_pro_touch_probe.py
# (2026-10-10):
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
#   - the register map is the Waveshare / SensorLib one, except that the chip
#     type 0xCACA sits at 0xD1FE on this board rather than 0xD204.
#
# CST3530, from LilyGO's touch_hyn_core example, Hynitron's hyn_driver,
# SensorLib's TouchDrvCST3530 and Meshtastic, which agree byte for byte
# (NOT yet checked on a CST3530 board; see tools/tdeck_pro_touch_probe.py):
#
#   - D0 03 00 00 then a plain read gives the info block: 0xCA 0xCA at
#     [2..3] is the family mark, resolution at [28..31], firmware at [32..35].
#     LilyGO's own driver probes for this chip FIRST on every board, and so
#     does this one: a CST3530 ACKs CST328-style register reads, so the other
#     order would take it for a CST328 and misparse everything -- which is
#     exactly what the issue reports;
#   - the report is D0 07 00 00 then 9 bytes: checksum (LE, 0x55 + the
#     record bytes), type 0xFF, key count | finger count nibbles, then one
#     5-byte record per key then finger; records past the first are fetched
#     with a bare read. Every read ends with D0 00 02 AB. The record's event
#     nibble is 1 press, 2 hold, 0 lifted: the lifted report has count 1 and
#     event 0, the mirror of the CST328's status 0xB;
#   - the chip drops its I2C after a while idle ("automatic sleep": LilyGO's
#     reason for saying poll it on INT only). The first write only wakes it
#     and may fail, so a failed read is followed by the vendor's wake
#     preamble (D0 00 04 00 twice) and the mode words, then read again. A
#     chip that still does not answer is put back in reset and found again
#     on the next pass;
#   - its sleep command is only left via the reset pin, so "off" is the
#     reset line held low here too.
#
# Both chips boot for the first tens of ms after reset (the CST328's
# bootloader even listens on 0x1A then), and every vendor sequence waits
# 50 ms before the first command. Nothing goes on the bus before that.

import time
from micropython import const
from machine import Pin

CST328_ADDR = const(0x1A)

CHIP_CST328 = "cst328"
CHIP_CST3530 = "cst3530"

# CST328
_REG_POINTS = const(0xD000)      # 27 bytes: point 0, count, point 1.., 5 bytes each
_REG_COUNT = const(0xD005)       # low nibble = fingers
_CLEAR = b"\xD0\x00\xAB"         # 0xD000 <- 0xAB: acknowledge the report
_POINTS_LEN = const(27)
_ST_CONTACT = const(6)

# CST3530
_PROBE = b"\xD0\x03\x00\x00"     # info block follows on a plain read
_INFO_LEN = const(36)            # through the firmware version; the vendor reads 50
_PREAMBLE = b"\xD0\x00\x04\x00"  # "disable LP scan": wakes a dozing chip; sent twice
_MODE_WORDS = (b"\xD0\x00\x00\x00",   # normal mode (not factory)
               b"\xD0\x00\x0C\x00",   # coordinate reports (not gestures)
               b"\xD0\x00\x01\x00")   # normal reports (not debug)
_REPORT = b"\xD0\x07\x00\x00"
_REPORT_LEN = const(9)           # header + first record
_END = b"\xD0\x00\x02\xAB"       # end of report: acknowledge, release INT
_REC_LEN = const(5)
_TYPE_POS = const(0xFF)

_JITTER_PX = const(1)            # moves of this much or less are dropped
_RESET_LOW_MS = const(20)
_SETTLE_MS = const(50)           # after the pulse, before the first command
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
        self.chip = None             # CHIP_CST328 / CHIP_CST3530 once it has answered
        self.info = None             # CST3530: (res_x, res_y, firmware) from the info block
        self._tries = 0
        self._tried_at = 0
        self._pulsed_at = None       # tick the reset line last went high
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

        The chip needs ~200 ms after the pulse and must not be spoken to in
        its first 50. This never waits for it: the first call pulses, every
        call after the settle time probes, and the caller keeps calling on
        its own cadence until True. The probe is also the detection: the
        first answer says which chip this is. Returns True once ready."""
        if self.asleep:
            self._rst.value(0)
            time.sleep_ms(_RESET_LOW_MS)
            self._rst.value(1)
            self._pulsed_at = time.ticks_ms()
            self.asleep = False
            self._latched = 0
        if self.ready:
            return True
        if self._pulsed_at is not None and \
                time.ticks_diff(time.ticks_ms(), self._pulsed_at) < _SETTLE_MS:
            return False
        try:
            self._probe()
        except OSError:
            return False
        self.ready = True
        self._latched = 0
        self._down = False
        return True

    def _probe(self):
        """Ask for the CST3530 info block; what comes back says which chip
        answered. Raises OSError while nothing does.

        On a CST328 the probe is one harmless register write and a plain
        read that never carries the 0xCA 0xCA mark, after which the count
        register is read as before."""
        i2c = self._i2c
        i2c.writeto(self._addr, _PROBE)
        info = i2c.readfrom(self._addr, _INFO_LEN)
        if info[2] == 0xCA and info[3] == 0xCA:
            self._enter_normal()
            self.info = (info[28] | (info[29] << 8),
                         info[30] | (info[31] << 8),
                         info[32] | (info[33] << 8) | (info[34] << 16) | (info[35] << 24))
            self.chip = CHIP_CST3530
            return
        i2c.readfrom_mem(self._addr, _REG_COUNT, 1, addrsize=16)
        self.info = None
        self.chip = CHIP_CST328

    def _enter_normal(self):
        """The vendor's CST3530 wake-and-configure sequence. The first write
        to a dozing chip only wakes it and may be refused; the rest must
        get through, so their OSError propagates."""
        i2c = self._i2c
        try:
            i2c.writeto(self._addr, _PREAMBLE)
        except OSError:
            pass
        time.sleep_ms(1)
        i2c.writeto(self._addr, _PREAMBLE)
        for w in _MODE_WORDS:
            i2c.writeto(self._addr, w)

    def _read_328(self):
        """(count, first record) or raises OSError when the chip is not
        answering. The record is None when the count is 0."""
        i2c = self._i2c
        n = i2c.readfrom_mem(self._addr, _REG_COUNT, 1, addrsize=16)[0] & 0x0F
        rec = None
        if n:
            rec = i2c.readfrom_mem(self._addr, _REG_POINTS, _POINTS_LEN, addrsize=16)
        i2c.writeto(self._addr, _CLEAR)
        return n, rec

    def _read_3530(self):
        """(fingers, first finger record), (0, None) for no finger, or
        (-1, None) for a frame that failed its checks. The report is
        acknowledged whatever it said; an unacknowledged one holds INT."""
        i2c = self._i2c
        i2c.writeto(self._addr, _REPORT)
        buf = i2c.readfrom(self._addr, _REPORT_LEN)
        keys = buf[3] >> 4
        fingers = buf[3] & 0x0F
        n = keys + fingers
        if n > 1:
            buf += i2c.readfrom(self._addr, (n - 1) * _REC_LEN)
        i2c.writeto(self._addr, _END)
        if buf[2] != _TYPE_POS:
            return -1, None
        cs = 0x55
        for i in range(4, 4 + n * _REC_LEN):
            cs += buf[i]
        if (cs & 0xFFFF) != (buf[0] | (buf[1] << 8)):
            return -1, None
        if not fingers:
            return 0, None
        o = 4 + keys * _REC_LEN          # key records come first
        return fingers, buf[o:o + _REC_LEN]

    def _read(self):
        """One report, with the CST3530's doze recovery: a refused read is
        followed by the wake preamble and tried once more."""
        if self.chip != CHIP_CST3530:
            return self._read_328()
        try:
            return self._read_3530()
        except OSError:
            self._enter_normal()
            return self._read_3530()

    def poll(self):
        """Events since the last call: [(kind, x, y, t_ms), ...].

        Reads the chip only when INT fired since the last call or a finger is
        known to be down, so an idle deck puts nothing on the bus. The latch
        is cleared BEFORE the read: an edge that lands during the read sets
        it again and the next pass reads once more.

        In contact is CST328 status 6 or a CST3530 event nibble other than
        0. Anything else (0xB after lift-off; event 0; 0 rarely) is not, but
        the report still carries the last contact position, which is how a
        tap shorter than one poll still gets a DOWN and an UP."""
        if not self.ready or not (self._latched or self._down):
            return []
        t_down = self._edge0         # only used when a stroke starts here,
        t_last = self._edge1         # which always means the latch was set
        self._latched = 0
        try:
            n, rec = self._read()
        except OSError:
            # The chip stopped answering (the bus is shared and has no
            # lock). A stroke in progress is closed rather than abandoned:
            # every DOWN gets its UP. A CST3530 that refused even the wake
            # preamble is dozing or wedged: back into reset, and the next
            # service() pass pulses it and finds it again.
            evs = []
            if self._down:
                self._down = False
                evs.append((UP, self._x, self._y, time.ticks_ms()))
            if self.chip == CHIP_CST3530:
                self.sleep()
            return evs
        if n < 0:
            return []
        t = time.ticks_ms()
        contact = False
        have_point = False
        x = y = 0
        if n and rec is not None:
            have_point = True
            if self.chip == CHIP_CST3530:
                contact = (rec[4] >> 4) != 0
                x = rec[0] | ((rec[3] & 0x0F) << 8)
                y = rec[1] | ((rec[3] & 0xF0) << 4)
            else:
                contact = (rec[0] & 0x0F) == _ST_CONTACT
                x = (rec[1] << 4) | (rec[3] >> 4)
                y = (rec[2] << 4) | (rec[3] & 0x0F)
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

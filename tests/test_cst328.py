# Host-side tests for the T-Deck Pro touch driver (CST328). Run:
#   /opt/homebrew/bin/python3 tests/test_cst328.py
#
# Register sequences come from tools/tdeck_pro_touch_probe.py on a V1.1
# board (2026-10-10): status nibble 6 = contact, 0xB = lifted but still
# reported with the last position, INT = one falling edge per contact report.

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

_mp = types.ModuleType("micropython")
_mp.const = lambda x: x
sys.modules.setdefault("micropython", _mp)

import time as _time  # noqa: E402

_clock = [1000]
_time.ticks_ms = lambda: _clock[0]
_time.ticks_diff = lambda a, b: a - b


def _sleep_ms(ms):
    _clock[0] += ms


_time.sleep_ms = _sleep_ms


class FakePin:
    IN = 1
    OUT = 3
    PULL_UP = 2
    IRQ_FALLING = 2
    created = []

    def __init__(self, gpio, mode=None, pull=None, value=None):
        self.gpio = gpio
        self.mode = mode
        self.pull = pull
        self.v = 1 if value is None else value
        self.handler = None
        self.trigger = None
        self.history = []          # every value written, in order
        FakePin.created.append(self)

    def value(self, x=None):
        if x is None:
            return self.v
        self.v = x
        self.history.append(x)

    def irq(self, trigger=None, handler=None):
        self.trigger = trigger
        self.handler = handler

    def fire(self):
        """Simulate the INT falling edge."""
        self.handler(self)


_machine = types.ModuleType("machine")
_machine.Pin = FakePin
sys.modules["machine"] = _machine

import cst328  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeI2C:
    """The chip as a little state machine: a count, a 27-byte report, and a
    'dead' flag for the time it spends in reset. Every read and write is
    logged so a test can assert that nothing happened."""

    def __init__(self):
        self.count = 0
        self.report = bytearray(27)
        self.dead = False
        self.reads = []            # (reg, n)
        self.writes = []           # bytes

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        assert addr == 0x1A and addrsize == 16, (addr, addrsize)
        if self.dead:
            raise OSError(19)
        self.reads.append((reg, n))
        if reg == 0xD005:
            return bytes([self.count])
        if reg == 0xD000:
            return bytes(self.report[:n])
        return bytes(n)

    def writeto(self, addr, data):
        assert addr == 0x1A
        if self.dead:
            raise OSError(19)
        self.writes.append(bytes(data))

    # helpers that script the chip the way the probe saw it
    def _point(self, offset, x, y, status, pressure, fid):
        self.report[offset] = (fid << 4) | status
        self.report[offset + 1] = (x >> 4) & 0xFF
        self.report[offset + 2] = (y >> 4) & 0xFF
        self.report[offset + 3] = ((x & 0x0F) << 4) | (y & 0x0F)
        self.report[offset + 4] = pressure

    def contact(self, x, y, pressure=60):
        self.count = 1
        self._point(0, x, y, 6, pressure, 0)

    def contact2(self, x, y, x2, y2):
        self.count = 2
        self._point(0, x, y, 6, 60, 0)
        self._point(7, x2, y2, 6, 60, 1)

    def lifted(self, x, y):
        self.count = 1
        self._point(0, x, y, 0xB, 20, 0)

    def nothing(self):
        self.count = 0


def new():
    _clock[0] = 1000
    FakePin.created = []
    i2c = FakeI2C()
    t = cst328.CST328(i2c, 12, 38)
    pins = {p.gpio: p for p in FakePin.created}
    return i2c, t, pins[12], pins[38]


def kinds(evs):
    return [e[0] for e in evs]


# --- construction and idle -------------------------------------------------

def test_pins_configured():
    print("construction")
    i2c, t, int_pin, rst = new()
    check("INT is an input with pull-up", int_pin.mode == FakePin.IN and int_pin.pull == FakePin.PULL_UP)
    check("INT irq is falling edge", int_pin.trigger == FakePin.IRQ_FALLING and int_pin.handler is not None)
    check("reset is an output left high", rst.mode == FakePin.OUT and rst.value() == 1)
    check("no I2C traffic at construction", i2c.reads == [] and i2c.writes == [])


def test_idle_poll_makes_no_traffic():
    print("idle")
    i2c, t, int_pin, rst = new()
    for _ in range(50):
        evs = t.poll()
        if evs != []:
            check("idle poll returns []", False, repr(evs))
            break
    else:
        check("idle poll returns [] (50 passes)", True)
    check("idle polls never read the bus", i2c.reads == [] and i2c.writes == [], repr(i2c.reads))
    check("not touching", t.touching() is False)


# --- contact ---------------------------------------------------------------

def test_first_contact_is_down():
    print("contact")
    i2c, t, int_pin, rst = new()
    t.ready = True                      # the board woke it; see the reset tests
    i2c.contact(126, 169)
    int_pin.fire()
    evs = t.poll()
    check("one DOWN at the reported point", evs == [(cst328.DOWN, 126, 169, 1000)], repr(evs))
    check("count then points were read", i2c.reads == [(0xD005, 1), (0xD000, 27)], repr(i2c.reads))
    check("report acknowledged with 0xAB", i2c.writes == [b"\xD0\x00\xAB"], repr(i2c.writes))
    check("touching", t.touching() is True)


def test_jitter_is_dropped_and_moves_pass():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(126, 169)
    int_pin.fire()
    t.poll()
    i2c.contact(127, 170)              # 1 px: jitter
    _clock[0] += 20
    evs = t.poll()
    check("1 px wobble is not a MOVE", evs == [], repr(evs))
    check("a known finger is still polled without a new edge", len(i2c.reads) == 4, repr(i2c.reads))
    i2c.contact(130, 169)              # 4 px
    _clock[0] += 20
    evs = t.poll()
    check("4 px is a MOVE with the new point and time", evs == [(cst328.MOVE, 130, 169, 1040)], repr(evs))
    i2c.contact(129, 171)              # 1 px from (130,169) in x, 2 px in y
    _clock[0] += 20
    evs = t.poll()
    check("2 px in one axis is a MOVE", evs == [(cst328.MOVE, 129, 171, 1060)], repr(evs))


def test_coordinates_clamped_to_panel():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(250, 330)
    int_pin.fire()
    evs = t.poll()
    check("x and y clamp to 239 x 319", evs == [(cst328.DOWN, 239, 319, 1000)], repr(evs))


def test_second_finger_ignored_but_report_cleared():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact2(116, 32, 111, 288)
    int_pin.fire()
    evs = t.poll()
    check("only the first finger is reported", evs == [(cst328.DOWN, 116, 32, 1000)], repr(evs))
    check("full 27-byte read and the clear still happen",
          (0xD000, 27) in i2c.reads and i2c.writes[-1] == b"\xD0\x00\xAB")


# --- lift-off --------------------------------------------------------------

def test_lifted_report_is_up_at_last_position():
    print("lift-off")
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(21, 190)
    int_pin.fire()
    t.poll()
    i2c.contact(21, 260)
    _clock[0] += 20
    t.poll()
    i2c.lifted(21, 262)                 # the chip's lifted report: last position, status 0xB
    _clock[0] += 20
    evs = t.poll()
    check("UP at the last CONTACT position, not the lifted one",
          evs == [(cst328.UP, 21, 260, 1040)], repr(evs))
    check("finger forgotten", t.touching() is False)
    _clock[0] += 20
    evs = t.poll()
    check("lingering lifted report is not read again", evs == [] and i2c.reads[-1] == (0xD000, 27)
          and len(i2c.reads) == 6, repr(i2c.reads))


def test_short_tap_seen_only_as_lifted_is_down_then_up():
    i2c, t, int_pin, rst = new()
    t.ready = True
    int_pin.fire()                      # the contact report came and went between polls
    i2c.lifted(100, 50)
    evs = t.poll()
    check("DOWN then UP at the lifted report's coordinates",
          evs == [(cst328.DOWN, 100, 50, 1000), (cst328.UP, 100, 50, 1000)], repr(evs))
    check("not touching afterwards", t.touching() is False)


def test_count_zero_while_down_is_up():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(60, 60)
    int_pin.fire()
    t.poll()
    i2c.nothing()
    _clock[0] += 20
    evs = t.poll()
    check("count 0 with a finger known is UP at the last position",
          evs == [(cst328.UP, 60, 60, 1020)], repr(evs))
    check("no points read when the count is 0", i2c.reads[-1] == (0xD005, 1))


def test_edge_during_read_is_not_lost():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(10, 10)
    int_pin.fire()
    t.poll()
    i2c.lifted(10, 10)
    _clock[0] += 20
    t.poll()                            # UP; finger forgotten, latch clear
    # A new tap's edge arrives while nothing is known.
    int_pin.fire()
    i2c.contact(200, 300)
    _clock[0] += 20
    evs = t.poll()
    check("a fresh edge after UP is read", evs == [(cst328.DOWN, 200, 300, 1040)], repr(evs))


def test_i2c_error_mid_finger_is_swallowed():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(5, 5)
    int_pin.fire()
    t.poll()
    i2c.dead = True
    _clock[0] += 20
    try:
        evs = t.poll()
        check("ENODEV returns [] and raises nothing", evs == [], repr(evs))
    except Exception as e:
        check("ENODEV returns [] and raises nothing", False, repr(e))
    check("finger forgotten after the error", t.touching() is False)


# --- reset line --------------------------------------------------------------

def test_sleep_holds_reset_low_and_poll_goes_quiet():
    print("sleep / wake")
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(50, 50)
    int_pin.fire()
    t.poll()
    t.sleep()
    check("reset driven low", rst.value() == 0)
    check("asleep and not ready", t.asleep is True and t.ready is False)
    reads = len(i2c.reads)
    int_pin.fire()
    evs = t.poll()
    check("poll while asleep: no events, no bus traffic", evs == [] and len(i2c.reads) == reads, repr(evs))


def test_sleep_forgets_finger():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.contact(50, 50)
    int_pin.fire()
    t.poll()
    t.sleep()
    check("finger forgotten by sleep", t.touching() is False)
    i2c.lifted(50, 50)
    t.ready = True                      # as if woken
    t.asleep = False
    _clock[0] += 20
    evs = t.poll()
    check("no stale UP after a sleep that interrupted a touch", evs == [], repr(evs))


def test_wake_pulses_once_then_retries():
    i2c, t, int_pin, rst = new()
    t.sleep()
    i2c.dead = True                     # settling
    rst.history = []
    ok = t.wake()
    check("wake pulses reset: low then high, 20 ms", rst.history == [0, 1], repr(rst.history))
    check("not ready while the chip does not answer", ok is False and t.ready is False)
    ok = t.wake()
    ok = t.wake()
    check("later calls do not pulse again", rst.history == [0, 1], repr(rst.history))
    i2c.dead = False
    ok = t.wake()
    check("ready once the chip answers", ok is True and t.ready is True and t.asleep is False)
    check("wake read the count register", i2c.reads[-1] == (0xD005, 1), repr(i2c.reads))
    reads = len(i2c.reads)
    evs = t.poll()
    check("no stale latch after wake", evs == [] and len(i2c.reads) == reads)


def test_wake_when_not_asleep_only_checks():
    i2c, t, int_pin, rst = new()
    rst.history = []
    ok = t.wake()
    check("fresh driver: no pulse, just a read, ready", ok is True and rst.history == [] and t.ready)


if __name__ == "__main__":
    test_pins_configured()
    test_idle_poll_makes_no_traffic()
    test_first_contact_is_down()
    test_jitter_is_dropped_and_moves_pass()
    test_coordinates_clamped_to_panel()
    test_second_finger_ignored_but_report_cleared()
    test_lifted_report_is_up_at_last_position()
    test_short_tap_seen_only_as_lifted_is_down_then_up()
    test_count_zero_while_down_is_up()
    test_edge_during_read_is_not_lost()
    test_i2c_error_mid_finger_is_swallowed()
    test_sleep_holds_reset_low_and_poll_goes_quiet()
    test_sleep_forgets_finger()
    test_wake_pulses_once_then_retries()
    test_wake_when_not_asleep_only_checks()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

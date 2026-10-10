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
        self.stamps = []           # the fake clock at each write
        FakePin.created.append(self)

    def value(self, x=None):
        if x is None:
            return self.v
        self.v = x
        self.history.append(x)
        self.stamps.append(_clock[0])

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
        self.attempts = 0          # every read tried, answered or not
        self.count_hi = 0          # junk for the count byte's high nibble
        self.on_read = None        # called once, in the middle of a read

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        assert addr == 0x1A and addrsize == 16, (addr, addrsize)
        self.attempts += 1
        if self.dead:
            raise OSError(19)
        self.reads.append((reg, n))
        if self.on_read is not None:
            hook, self.on_read = self.on_read, None
            hook()
        if reg == 0xD005:
            return bytes([self.count_hi | self.count])
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


def test_fresh_edge_after_up_is_read():
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
    check("a fresh edge after UP is read; DOWN carries the edge's time",
          evs == [(cst328.DOWN, 200, 300, 1020)], repr(evs))


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
        check("ENODEV mid-finger raises nothing and closes the stroke with UP",
              evs == [(cst328.UP, 5, 5, 1020)], repr(evs))
    except Exception as e:
        check("ENODEV mid-finger raises nothing and closes the stroke with UP", False, repr(e))
    check("finger forgotten after the error", t.touching() is False)
    evs = t.poll()
    check("no second UP, and an idle error stays silent", evs == [], repr(evs))


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



# --- review fixes: the event contract ----------------------------------------

def test_edge_during_read_is_not_lost():
    print("review fixes")
    i2c, t, int_pin, rst = new()
    t.ready = True
    int_pin.fire()
    i2c.lifted(10, 10)                  # a short tap, already lifted
    i2c.on_read = int_pin.fire          # ...and a NEW edge lands mid-read
    evs = t.poll()
    check("the tap is delivered", kinds(evs) == [cst328.DOWN, cst328.UP], repr(evs))
    reads = len(i2c.reads)
    i2c.contact(200, 300)
    _clock[0] += 20
    evs = t.poll()
    check("the edge that fired during the read makes the next poll read",
          len(i2c.reads) > reads and kinds(evs) == [cst328.DOWN], repr(evs))


def test_event_times_come_from_the_edges():
    # The key loop does not run while the e-ink panel refreshes (~700 ms).
    # A touch made in that window must still carry when it really happened,
    # so the layer above can tell a tap from a hold and drop a stale one.
    i2c, t, int_pin, rst = new()
    t.ready = True
    int_pin.fire()                      # finger down at 1000
    _clock[0] = 1300
    int_pin.fire()                      # last contact report at 1300
    _clock[0] = 1700                    # the loop comes back at 1700
    i2c.lifted(100, 50)
    evs = t.poll()
    check("DOWN at the first edge, UP at the last, not at poll time",
          evs == [(cst328.DOWN, 100, 50, 1000), (cst328.UP, 100, 50, 1300)], repr(evs))
    i2c2, t2, int2, rst2 = new()
    t2.ready = True
    int2.fire()                         # down at 1000
    _clock[0] = 1015
    i2c2.contact(40, 40)
    evs = t2.poll()
    check("an ordinary DOWN carries the edge's time too",
          evs == [(cst328.DOWN, 40, 40, 1000)], repr(evs))


def test_count_and_status_nibbles_are_masked():
    i2c, t, int_pin, rst = new()
    t.ready = True
    i2c.count_hi = 0x80
    i2c.count = 1
    i2c._point(0, 77, 88, 6, 60, 3)     # finger id 3 in the high nibble of byte 0
    int_pin.fire()
    evs = t.poll()
    check("junk in the count's high nibble and a non-zero finger id still decode",
          evs == [(cst328.DOWN, 77, 88, 1000)], repr(evs))
    i2c.nothing()                       # count 0, high nibble still junk
    _clock[0] += 20
    evs = t.poll()
    check("count 0 under a junk high nibble is still 'no finger': UP, no points read",
          evs == [(cst328.UP, 77, 88, 1020)] and i2c.reads[-1] == (0xD005, 1), repr(evs))


def test_reset_pulse_is_20ms_low():
    i2c, t, int_pin, rst = new()
    t.sleep()
    rst.history = []
    rst.stamps = []
    t.wake()
    check("reset low, then high 20 ms later",
          rst.history == [0, 1] and rst.stamps[1] - rst.stamps[0] == 20, repr(rst.stamps))


# --- review fixes: service(), the one call the board makes --------------------

def test_service_wakes_with_the_screen_and_sleeps_without_it():
    i2c, t, int_pin, rst = new()
    t.sleep()
    evs = t.service(False)
    check("screen off, already asleep: nothing, no traffic", evs == [] and i2c.attempts == 0)
    evs = t.service(True)
    check("screen on: woken and ready on the first pass", evs == [] and t.ready and rst.value() == 1)
    i2c.contact(30, 30)
    int_pin.fire()
    evs = t.service(True)
    check("then it polls", kinds(evs) == [cst328.DOWN], repr(evs))
    evs = t.service(False)
    check("screen goes dark with the finger held: the stroke is closed with UP",
          evs == [(cst328.UP, 30, 30, _clock[0])], repr(evs))
    check("and the chip is back in reset", t.asleep and rst.value() == 0)
    check("dark again: nothing more", t.service(False) == [])


def test_service_presence_needs_no_scan_and_survives_a_low_reset_line():
    # After a soft reset the pad is still driven low from the last sleep(),
    # so the chip is off the bus when the board module is imported. Presence
    # is therefore decided by wake() retries, never by a boot-time scan.
    i2c, t, int_pin, rst = new()
    t.sleep()
    i2c.dead = True                     # settling after the pulse
    check("settling: no events", t.service(True) == [])
    n = i2c.attempts
    for _ in range(10):
        t.service(True)                 # the key loop calls again at once
    check("retries are spaced, not one per call", i2c.attempts == n, "%d -> %d" % (n, i2c.attempts))
    check("one pulse only", rst.history.count(0) == 2, repr(rst.history))   # sleep() + the pulse
    _clock[0] += 60
    i2c.dead = False
    evs = t.service(True)
    check("answers on a later pass: ready", evs == [] and t.ready is True)


def test_service_gives_up_on_an_absent_chip_until_the_screen_next_wakes():
    i2c, t, int_pin, rst = new()
    t.sleep()
    i2c.dead = True
    for _ in range(200):
        t.service(True)
        _clock[0] += 60
    check("gave up", t.gave_up is True)
    n = i2c.attempts
    for _ in range(50):
        t.service(True)
        _clock[0] += 60
    check("no more bus traffic after giving up", i2c.attempts == n, "%d -> %d" % (n, i2c.attempts))
    check("it tried for about two seconds, not forever", 20 <= n <= 60, str(n))
    t.service(False)                    # screen dark
    i2c.dead = False
    t.service(True)                     # next wake: try again
    check("a chip that comes back is picked up on the next screen wake",
          t.ready is True and t.gave_up is False)


def test_edges_while_dark_leave_nothing_behind():
    i2c, t, int_pin, rst = new()
    t.sleep()
    int_pin.fire()                      # e.g. a pulse as reset is released
    i2c.lifted(9, 9)                    # a stale report still in the chip
    evs = t.service(True)
    evs += t.service(True)
    check("no phantom tap after wake", evs == [], repr(evs))


# --- review fixes: the image and the board glue --------------------------------

def _src(rel):
    return open(os.path.join(ROOT, rel)).read()


def test_driver_is_in_the_firmware_image_and_the_deploy_list():
    # board_tdeck_pro imports cst328 unconditionally: an image or a deploy
    # without it does not boot. (Device runs with a VFS copy hid this.)
    man = _src("tools/tdeck_manifest.py")
    check("frozen into the Pro image", 'freeze(_root + "/lib", "cst328.py")' in man)
    dep = _src("tools/deploy_pro.sh")
    loop = [l for l in dep.splitlines() if l.startswith("for f in eink_gdeq031t10.py")]
    check("copied by deploy_pro.sh", loop and "cst328.py" in loop[0], repr(loop))
    check("and in its stale-copy sweep", "'cst328.py'" in dep.split("stale = (")[1].split(")")[0])


def test_board_glue():
    src = _src("board_tdeck_pro.py")
    handler = src.split("def _touch_handler(ev):")[1].split("\ndef ")[0]
    check("a touch stamps activity the way a key does (wake_screen, not note_user_input)",
          '_gui.wake_screen(why="touch")' in handler and "note_user_input" not in handler)
    check("...on DOWN and MOVE only, never on the UP that closes a stroke as the screen darkens",
          "cst328.UP" in handler)
    service = src.split("def _service_touch():")[1].split("\ndef ")[0]
    check("the board makes one call per pass", "touch.service(_gui._screen_on)" in service)
    check("no boot-time scan decides whether touch exists",
          "TOUCH_ADDR in i2c.scan()" not in src)


# --- board wiring ------------------------------------------------------------

def test_touch_reset_pin_per_revision():
    print("board wiring")
    import tdeck_pro_rev
    check("V1.0 touch reset is GPIO 45 (38 is its 1.8 V gate)", tdeck_pro_rev.touch_reset("1.0") == 45)
    check("V1.1 touch reset is GPIO 38", tdeck_pro_rev.touch_reset("1.1") == 38)
    check("MAX touch reset is GPIO 38", tdeck_pro_rev.touch_reset("max") == 38)


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
    test_fresh_edge_after_up_is_read()
    test_i2c_error_mid_finger_is_swallowed()
    test_sleep_holds_reset_low_and_poll_goes_quiet()
    test_sleep_forgets_finger()
    test_wake_pulses_once_then_retries()
    test_wake_when_not_asleep_only_checks()
    test_touch_reset_pin_per_revision()
    test_edge_during_read_is_not_lost()
    test_event_times_come_from_the_edges()
    test_count_and_status_nibbles_are_masked()
    test_reset_pulse_is_20ms_low()
    test_service_wakes_with_the_screen_and_sleeps_without_it()
    test_service_presence_needs_no_scan_and_survives_a_low_reset_line()
    test_service_gives_up_on_an_absent_chip_until_the_screen_next_wakes()
    test_edges_while_dark_leave_nothing_behind()
    test_driver_is_in_the_firmware_image_and_the_deploy_list()
    test_board_glue()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

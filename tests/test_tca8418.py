# Host-side tests for the T-Deck Pro keyboard driver. Run:
#   python3 tests/test_tca8418.py
#
# The Pro has no arrow keys and no trackball, so the alt layer is the only way
# to navigate the UI. These tests pin the layer behaviour and the raw event
# decoding, since getting either wrong makes the device look dead rather than
# merely mistyped.

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
_time.sleep_ms = lambda ms: None

import tca8418  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeI2C:
    """Register file plus a key-event FIFO."""

    def __init__(self):
        self.regs = bytearray(0x40)
        self.fifo = []
        self._ptr = 0
        self.writes = []

    def writeto(self, addr, data):
        if len(data) == 1:
            self._ptr = data[0]
        else:
            self.regs[data[0]] = data[1]
            self.writes.append((data[0], data[1]))

    def readfrom(self, addr, n):
        reg = self._ptr
        if reg == 0x03:               # KEY_LCK_EC
            return bytes([len(self.fifo)])
        if reg == 0x04:               # KEY_EVENT_A
            return bytes([self.fifo.pop(0)]) if self.fifo else b'\x00'
        return bytes([self.regs[reg]])

    # helpers: key index -> raw event byte
    def press(self, idx):
        self.fifo.append(0x80 | (idx + 1))

    def release(self, idx):
        self.fifo.append(idx + 1)


class FakePin:
    def __init__(self):
        self.v = 0

    def value(self, x=None):
        if x is None:
            return self.v
        self.v = x


def new():
    i2c = FakeI2C()
    bl = FakePin()
    kb = tca8418.TCA8418(i2c, bl_pin=bl)
    i2c.writes = []
    return i2c, bl, kb


def tap(i2c, kb, *idxs):
    """Press each key in turn, draining one event per press.

    get_key() consumes exactly one FIFO event per call, so a modifier plus a
    key is two calls. Returns the last non-empty byte the driver emitted.
    """
    out = b''
    for i in idxs:
        i2c.press(i)
        r = kb.get_key()
        if r:
            out = r
    return out


# --- setup -----------------------------------------------------------------

def test_matrix_configured():
    print("setup")
    i2c = FakeI2C()
    tca8418.TCA8418(i2c)
    check("rows claimed on KP_GPIO_1", i2c.regs[0x1D] == 0x0F,
          "got %02x" % i2c.regs[0x1D])
    check("columns 0-7 claimed on KP_GPIO_2", i2c.regs[0x1E] == 0xFF,
          "got %02x" % i2c.regs[0x1E])
    check("columns 8-9 claimed on KP_GPIO_3", i2c.regs[0x1F] == 0x03,
          "got %02x" % i2c.regs[0x1F])
    check("key events enabled in CFG", i2c.regs[0x01] & 0x01 == 1)


# --- decoding --------------------------------------------------------------

def test_plain_letter():
    print("decoding")
    i2c, _, kb = new()
    i2c.press(0)                      # index 0 == 'p'
    check("index 0 decodes as 'p'", kb.get_key() == b'p')


def test_release_ignored():
    i2c, _, kb = new()
    i2c.release(0)
    check("key release produces nothing", kb.get_key() == b'')


def test_empty_fifo():
    _, _, kb = new()
    check("empty FIFO returns b''", kb.get_key() == b'')


def test_out_of_range():
    i2c, _, kb = new()
    i2c.fifo.append(0x80 | 60)        # code beyond the 35-key matrix
    check("out-of-range key is dropped", kb.get_key() == b'')


def test_enter_and_space():
    i2c, _, kb = new()
    i2c.press(20)
    check("index 20 is Enter", kb.get_key() == b'\x0d')
    i2c.press(32)
    check("index 32 is space", kb.get_key() == b' ')


# --- layers ----------------------------------------------------------------

def test_shift_layer():
    print("layers")
    i2c, _, kb = new()
    i2c.press(34)                     # left shift
    check("shift alone emits nothing", kb.get_key() == b'')
    i2c.press(0)
    check("shift then p gives 'P'", kb.get_key() == b'P')
    i2c.press(0)
    check("shift is sticky for one key only", kb.get_key() == b'p')


def test_sym_layer():
    i2c, _, kb = new()
    check("sym then p gives '@'", tap(i2c, kb, 31, 0) == b'@')


def test_alt_arrows():
    i2c, _, kb = new()
    for idx, want, name in ((7, 0xB5, "up"), (27, 0xB6, "down"),
                            (18, 0xB4, "left"), (16, 0xB7, "right")):
        got = tap(i2c, kb, 29, idx)
        check("alt layer gives %s" % name, got == bytes([want]),
              "got %r" % got)


def test_modifier_expiry():
    i2c, _, kb = new()
    i2c.press(34)                     # shift
    kb.get_key()
    _clock[0] += 2000                 # past the 1.5 s window
    i2c.press(0)
    check("a stale modifier lapses", kb.get_key() == b'p')
    _clock[0] = 1000


def test_layer_fallback():
    i2c, _, kb = new()
    # Enter has nothing on the sym layer, so it should still send Enter.
    check("a key with no mapping on the active layer falls back to base",
          tap(i2c, kb, 31, 20) == b'\x0d')


def test_modifier_toggle_off():
    i2c, _, kb = new()
    i2c.press(34)
    kb.get_key()
    i2c.press(34)                     # tapping shift again cancels it
    kb.get_key()
    i2c.press(0)
    check("tapping a modifier twice cancels it", kb.get_key() == b'p')


# --- backlight -------------------------------------------------------------

def test_backlight():
    """Alt+B is surfaced, not acted on here.

    The backlight has a persisted setting, and on a board with no display
    backlight a state that follows the screen's sleep. A driver that toggled
    the pin itself would desynchronise both -- the Settings screen would show
    the wrong value, the choice would not survive a reboot, and the next
    sleep/wake would fight it. board_tdeck_pro.get_key() routes the key to the
    UI instead.
    """
    print("backlight")
    i2c, bl, kb = new()
    check("backlight starts off", bl.v == 0)
    out = tap(i2c, kb, 29, 24)        # alt + b
    check("alt+b surfaces the key rather than consuming it",
          out == bytes([tca8418.KEY_BL_TOGGLE]), repr(out))
    check("and the driver does not drive the pin behind the UI's back",
          bl.v == 0)


# --- FIFO draining (regression: hardware 2026-08-28) -----------------------

def test_gpio_event_skipped():
    print("draining")
    i2c, _, kb = new()
    # Pressing 'a' on real hardware emitted the matrix event plus a GPIO event
    # (code 98). GPI_EM is now 0, but a stray non-matrix code must still be
    # stepped over rather than costing a keystroke.
    i2c.fifo.append(0x80 | 98)
    i2c.press(0)
    check("a stray GPIO event does not consume the keystroke",
          kb.get_key() == b'p')


def test_modifier_and_key_in_one_call():
    i2c, _, kb = new()
    i2c.press(34)                     # shift
    i2c.press(0)                      # p
    check("a modifier queued with its key resolves in one call",
          kb.get_key() == b'P')


def test_releases_skipped():
    i2c, _, kb = new()
    i2c.press(0)
    i2c.release(0)
    i2c.press(24)
    check("releases are stepped over", kb.get_key() == b'p')
    check("the next call returns the next real key", kb.get_key() == b'b')


def test_drain_is_bounded():
    i2c, _, kb = new()
    for _ in range(40):               # nothing but releases
        i2c.release(0)
    check("a FIFO full of noise still returns", kb.get_key() == b'')
    check("it did not drain the whole FIFO in one call", len(i2c.fifo) > 0,
          "fifo emptied, drain was unbounded")


if __name__ == "__main__":
    test_matrix_configured()
    test_plain_letter()
    test_release_ignored()
    test_empty_fifo()
    test_out_of_range()
    test_enter_and_space()
    test_shift_layer()
    test_sym_layer()
    test_alt_arrows()
    test_modifier_expiry()
    test_layer_fallback()
    test_modifier_toggle_off()
    test_backlight()
    test_gpio_event_skipped()
    test_modifier_and_key_in_one_call()
    test_releases_skipped()
    test_drain_is_bounded()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

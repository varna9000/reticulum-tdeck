# Host tests for the DRV2605 haptic click. Run:
#   /opt/homebrew/bin/python3 tests/test_drv2605.py

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

_mp = types.ModuleType("micropython")
_mp.const = lambda x: x
sys.modules.setdefault("micropython", _mp)


class FakePin:
    OUT = 1
    made = []

    def __init__(self, gpio, mode=None, value=None):
        self.gpio, self.v = gpio, value
        FakePin.made.append(self)

    def value(self, x=None):
        if x is None:
            return self.v
        self.v = x


_machine = types.ModuleType("machine")
_machine.Pin = FakePin
sys.modules["machine"] = _machine

import drv2605  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeI2C:
    def __init__(self, dead=False):
        self.dead = dead
        self.regs = {0x1A: 0x36}
        self.writes = []

    def writeto_mem(self, addr, reg, data):
        if self.dead:
            raise OSError(19)
        assert addr == 0x5A
        self.regs[reg] = data[0]
        self.writes.append((reg, data[0]))

    def readfrom_mem(self, addr, reg, n):
        if self.dead:
            raise OSError(19)
        return bytes([self.regs.get(reg, 0)])


def test_setup_and_click():
    print("drv2605")
    FakePin.made = []
    i2c = FakeI2C()
    d = drv2605.DRV2605(i2c, en_gpio=2)
    check("the enable pin is driven high", FakePin.made and FakePin.made[0].gpio == 2 and FakePin.made[0].v == 1)
    check("out of standby, internal trigger", i2c.regs[0x01] == 0x00)
    check("library and effect are the ones chosen on the deck",
          i2c.regs[0x03] == drv2605.LIBRARY and i2c.regs[0x04] == drv2605.EFFECT and i2c.regs[0x05] == 0)
    lra = i2c.regs[0x1A] & 0x80
    check("ERM/LRA bit matches the library", bool(lra) == (drv2605.LIBRARY == 6), hex(i2c.regs[0x1A]))
    check("other feedback bits are left alone", i2c.regs[0x1A] & 0x7F == 0x36)
    i2c.writes = []
    d.click()
    check("a click is one GO write", i2c.writes == [(0x0C, 1)], repr(i2c.writes))
    check("ok flag set", d.ok is True)


def test_never_raises():
    d = drv2605.DRV2605(FakeI2C(dead=True))
    check("a chip that does not answer leaves ok False", d.ok is False)
    try:
        d.click()
        check("click() on a dead chip raises nothing", True)
    except Exception as e:
        check("click() on a dead chip raises nothing", False, repr(e))
    i2c = FakeI2C()
    d = drv2605.DRV2605(i2c)
    i2c.dead = True
    try:
        d.click()
        check("a bus error during click raises nothing", True)
    except Exception as e:
        check("a bus error during click raises nothing", False, repr(e))



def test_enable_follows_the_caller():
    # GPIO 2 gates the motor output (probe, 2026-10-11: held low, no tick).
    # An enabled driver draws idle current, so the board turns it off with
    # the screen; a click while off must not touch the bus.
    FakePin.made = []
    i2c = FakeI2C()
    d = drv2605.DRV2605(i2c, en_gpio=2)
    pin = FakePin.made[0]
    d.enable(False)
    check("enable(False) drives the pin low", pin.v == 0 and d.enabled is False)
    i2c.writes = []
    d.click()
    check("no click, and no bus traffic, while disabled", i2c.writes == [], repr(i2c.writes))
    d.enable(True)
    d.click()
    check("enable(True) raises it and clicks work again", pin.v == 1 and i2c.writes == [(0x0C, 1)], repr(i2c.writes))
    d.enable(True)
    check("repeated enable(True) is harmless", pin.v == 1)
    d2 = drv2605.DRV2605(FakeI2C())          # no enable pin wired
    d2.enable(False)
    d2.enable(True)
    check("a driver without an enable pin accepts the calls", d2.enabled is True)


if __name__ == "__main__":
    test_setup_and_click()
    test_never_raises()
    test_enable_follows_the_caller()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

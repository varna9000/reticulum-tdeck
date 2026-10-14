# The page a deck leaves on its e-ink when it cuts its own battery
# (power.LowBattery -> board.power_off): the panel keeps it for free, so it
# must say what happened and what to do, and it must be a whole-panel
# refresh (the live frame under it is gone for good). Run:
#   /opt/homebrew/bin/python3 tests/test_low_battery_screen.py

import os
import sys
import types
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

_clock = [100000]
_time.ticks_ms = lambda: _clock[0]
_time.ticks_diff = lambda a, b: a - b
_time.ticks_add = lambda a, d: a + d
_time.sleep_ms = lambda ms: None
sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))


class _Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    IRQ_FALLING = 4

    def __init__(self, *a, **k):
        pass

    def irq(self, *a, **k):
        pass

    def value(self, *a):
        return 1


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
sys.modules["machine"] = _machine

_geo = types.ModuleType("board_geometry")
_geo.SCREEN_W = 240
_geo.SCREEN_H = 320
sys.modules["board_geometry"] = _geo

import ui  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class RecTFT:
    mono = True

    def __init__(self):
        self.calls = []

    def text(self, font, s, x, y, fg=None, bg=None):
        if isinstance(s, (bytes, bytearray)):
            s = bytes(s).decode("latin-1")
        self.calls.append(("text", s, x, y, fg, bg))

    def fill(self, c):
        self.calls.append(("fill", c))

    def __getattr__(self, name):
        return lambda *a, **k: None


t = RecTFT()
g = ui.UI(t, object(), lambda: b"\x00", node_name="t", trackball=False)
g._screen_on = True
flushes = []
g._flush = lambda: flushes.append(g._force_full_flush)
spi = []
g._spi_acquire = lambda: spi.append("acq")
g._spi_release = lambda: spi.append("rel")
g.bat_v = 3.17

g.power_off_screen()
texts = [c[1] for c in t.calls if c[0] == "text"]
check("panel cleared first", t.calls and t.calls[0][0] == "fill", t.calls[:2])
check("says the battery is empty", any("empty" in s.lower() for s in texts), texts)
check("says how to bring it back", any("charge" in s.lower() or "plug" in s.lower() for s in texts), texts)
check("shows the voltage it cut at", any("3.17" in s for s in texts), texts)
check("one whole-panel flush", flushes == [True], flushes)
check("SPI handed over around the draw", spi == ["acq", "rel"], spi)

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")

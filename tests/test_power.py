# The ESP32 sleeps only when nothing needs it: screen off, UI idle, LoRa-only
# mode, on battery (or told to ignore USB), radio quiet, no recent failure.
# These tests pin the decision table, the trackball polarity helper and the
# wake classifier, because a wrong gate here either never sleeps (no saving)
# or sleeps at the wrong moment (a transfer stalls, the USB console vanishes).
#
# Run:  python3 tests/test_power.py

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))

import power  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


def _ok_state(**over):
    """A state that allows sleep; override one field to test one gate."""
    s = power.State(screen_on=False, sleep_blocked=False, wifi_on=False,
                    lora_online=True, usb=False, sleep_on_usb=False,
                    quiet_ms=power.QUIET_MS, backoff_ms=0)
    for k, v in over.items():
        setattr(s, k, v)
    return s


print("may_sleep")
check("all clear allows", power.may_sleep(_ok_state()) is None)
check("default State refuses", power.may_sleep(power.State()) is not None)
check("screen on blocks", power.may_sleep(_ok_state(screen_on=True)) == "screen on")
check("ui busy blocks", power.may_sleep(_ok_state(sleep_blocked=True)) == "ui busy")
check("wifi blocks", power.may_sleep(_ok_state(wifi_on=True)) == "wifi")
check("lora offline blocks", power.may_sleep(_ok_state(lora_online=False)) == "lora offline")
check("usb blocks", power.may_sleep(_ok_state(usb=True)) == "usb")
check("usb override allows", power.may_sleep(_ok_state(usb=True, sleep_on_usb=True)) is None)
check("traffic blocks", power.may_sleep(_ok_state(quiet_ms=power.QUIET_MS - 1)) == "traffic")
check("quiet exactly at threshold allows", power.may_sleep(_ok_state(quiet_ms=power.QUIET_MS)) is None)
check("backoff blocks", power.may_sleep(_ok_state(backoff_ms=1)) == "backoff")
check("first reason wins", power.may_sleep(_ok_state(screen_on=True, usb=True)) == "screen on")

print("opposite_levels")
levels = {3: 1, 15: 0, 1: 1, 2: 1}
t = power.opposite_levels((3, 15, 1, 2), lambda g: levels[g], 2)
check("four entries", len(t) == 4, repr(t))
check("high line armed low", t[0] == (3, 0, 2, "input"), repr(t[0]))
check("low line armed high", t[1] == (15, 1, 2, "input"), repr(t[1]))
check("restore carried", all(e[2] == 2 for e in t))
check("kind is input", all(e[3] == "input" for e in t))
check("empty input", power.opposite_levels((), lambda g: 0, 2) == ())

print("board wake tables (source scan)")
import re


def _wake_table_src(fname):
    src = open(os.path.join(ROOT, fname)).read()
    m = re.search(r"def sleep_wake_pins\(\):(.*?)\n\n\ndef ", src, re.S)
    return m.group(1) if m else ""


pro = _wake_table_src("board_tdeck_pro.py")
v1 = _wake_table_src("board_tdeck_v1.py")
check("pro table present", pro != "")
check("pro: DIO1 restores IRQ_RISING", "(config.LORA_DIO1, 1, Pin.IRQ_RISING, \"lora\")" in pro, pro)
check("pro: KBD_INT wakes low, no restore", "(config.KBD_INT, 0, 0, \"input\")" in pro, pro)
check("v1 table present", v1 != "")
check("v1: DIO1 45 restores IRQ_RISING", "(45, 1, Pin.IRQ_RISING, \"lora\")" in v1, v1)
check("v1: click restores both edges", "Pin.IRQ_FALLING | Pin.IRQ_RISING, \"input\")" in v1, v1)
check("v1: movement lines via opposite_levels", "opposite_levels(_TB_MOVE, _tb_level, Pin.IRQ_FALLING)" in v1, v1)

print("classify")
PINS = ((5, 1, 1, "lora"), (15, 0, 0, "input"))


def _rd(levels):
    return lambda g: levels[g]


check("timer", power.classify(0, PINS, _rd({5: 0, 15: 1}), 0, 1) == "timer")
check("gpio + DIO1 high -> lora", power.classify(1, PINS, _rd({5: 1, 15: 1}), 0, 1) == "lora")
check("gpio + INT low -> input", power.classify(1, PINS, _rd({5: 0, 15: 0}), 0, 1) == "input")
check("input beats lora when both", power.classify(1, PINS, _rd({5: 1, 15: 0}), 0, 1) == "input")
check("gpio + nothing active -> other", power.classify(1, PINS, _rd({5: 0, 15: 1}), 0, 1) == "other")
check("unknown cause -> other", power.classify(2, PINS, _rd({5: 1, 15: 0}), 0, 1) == "other")

print("Power.step")


class FakeLowpower:
    WAKE_TIMER, WAKE_GPIO, WAKE_OTHER = 0, 1, 2

    def __init__(self):
        self.calls = []
        self.result = 0
        self.raise_once = None
        self.usb = False
        self.levels = {5: 0, 15: 1}

    def sleep(self, ms, pins):
        self.calls.append((ms, pins))
        if self.raise_once:
            e, self.raise_once = self.raise_once, None
            raise e
        return self.result

    def usb_connected(self):
        return self.usb


class FakeBoard:
    def __init__(self, lp):
        self.lp = lp
        self.before = 0
        self.after = 0
        self.pins = ((5, 1, 1, "lora"), (15, 0, 0, "input"))

    def sleep_wake_pins(self):
        return self.pins

    def before_sleep(self):
        self.before += 1

    def after_wake(self):
        self.after += 1

    def read_pin(self, g):
        return self.lp.levels[g]


class FakeGui:
    def __init__(self):
        self._screen_on = False
        self.blocked = False
        self.woken = 0

    def _sleep_blocked(self):
        return self.blocked

    def wake_screen(self):
        self.woken += 1


class FakeIface:
    def __init__(self):
        self.online = True
        self.rxb = 0
        self.txb = 0


def _mk(**over):
    lp = FakeLowpower()
    bd = FakeBoard(lp)
    gui = FakeGui()
    iface = FakeIface()
    clock = [10000]
    logs = []
    p = power.Power(gui, bd, lp, lambda: iface, lambda: False, False,
                    lambda: clock[0], logs.append, read_pin=bd.read_pin)
    for k, v in over.items():
        setattr(p, k, v)
    return p, lp, bd, gui, iface, clock, logs


p, lp, bd, gui, iface, clock, logs = _mk()
check("first pass: not quiet yet", p.step() == "traffic" and lp.calls == [])
clock[0] += power.QUIET_MS
check("quiet after QUIET_MS: sleeps", p.step() == "timer" and len(lp.calls) == 1)
check("sleep capped", lp.calls[0][0] == power.SLEEP_CAP_MS)
check("pins passed through", lp.calls[0][1] == bd.pins)
check("hooks bracket the sleep", bd.before == 1 and bd.after == 1)
check("timer counted", p.counts["timer"] == 1)
check("screen stays off on timer", gui.woken == 0)

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
lp.result = 1
lp.levels = {5: 1, 15: 1}
check("DIO1 wake -> lora, no screen wake", p.step() == "lora" and gui.woken == 0)
check("lora counted", p.counts["lora"] == 1)

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
lp.result = 1
lp.levels = {5: 0, 15: 0}
check("INT wake -> input, screen wakes", p.step() == "input" and gui.woken == 1)

p, lp, bd, gui, iface, clock, logs = _mk()
p.step()                         # first reading is the baseline
clock[0] += power.QUIET_MS
iface.rxb = 100
check("counter moved: traffic", p.step() == "traffic" and lp.calls == [])
clock[0] += power.QUIET_MS - 1
check("still inside the window", p.step() == "traffic")
clock[0] += 1
check("quiet again", p.step() == "timer")

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
gui._screen_on = True
check("screen on", p.step() == "screen on")
gui._screen_on = False
gui.blocked = True
check("ui busy", p.step() == "ui busy")
gui.blocked = False
iface.online = False
check("lora offline", p.step() == "lora offline")
iface.online = True
lp.usb = True
check("usb", p.step() == "usb")
p.sleep_on_usb = True
check("usb override", p.step() == "timer")

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
lp.raise_once = OSError(-1)
check("raise -> backoff reported", p.step() == "backoff")
check("raise logged", any("lowpower" in m or "sleep" in m for m in logs), repr(logs))
check("hooks still bracketed", bd.before == 1 and bd.after == 1)
clock[0] += power.BACKOFF_MS - 1
check("backoff holds", p.step() == "backoff")
clock[0] += 1
check("backoff expires", p.step() == "timer")

p, lp, bd, gui, iface, clock, logs = _mk()
p.wifi_on = lambda: True
clock[0] += power.QUIET_MS
check("wifi on", p.step() == "wifi")

p, lp, bd, gui, iface, clock, logs = _mk()
p.lora_iface = lambda: None
clock[0] += power.QUIET_MS
check("no lora interface", p.step() == "lora offline")

p, lp, bd, gui, iface, clock, logs = _mk()
p.step()
check("summary before any sleep names the reason", p.summary() == "off: traffic", p.summary())
p.step()
check("reason logged once", logs.count("power: no sleep: traffic") == 1, repr(logs))
clock[0] += power.QUIET_MS
p.step()
check("summary counts", p.summary() == "1 (lora 0 key 0 t 1)", p.summary())

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")

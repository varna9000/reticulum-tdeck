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

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")

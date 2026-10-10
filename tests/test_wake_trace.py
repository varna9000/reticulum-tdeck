# Every path that lights a dark screen goes through UI.wake_screen(). When a
# deck wakes "for no reason" (v1, 2026-10-10, on battery after the sleep
# work) the question is which path did it, and with no console attached the
# only witness is the deck itself. So a dark-to-lit transition records who
# asked -- key, ball, msg, announce, input, shell, unlock -- in a short
# in-memory trace and, best effort, in /rns/wake.txt.
#
# Run:  python3 tests/test_wake_trace.py

import os
import sys
import types
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_time.ticks_ms = lambda: int(_time.time() * 1000)
_time.ticks_diff = lambda a, b: a - b
_time.sleep_ms = lambda ms: None
sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))


class _Pin:
    IN = OUT = PULL_UP = IRQ_FALLING = IRQ_RISING = 0

    def __init__(self, *a, **k):
        pass

    def irq(self, *a, **k):
        pass

    def value(self, *a):
        return 1


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
sys.modules["machine"] = _machine

import ui  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeTFT:
    def text(self, *a, **k):
        pass

    def fill_rect(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass


ui.WAKE_TRACE_PATH = os.path.join(os.path.dirname(__file__), "wake_trace_test.txt")
try:
    os.remove(ui.WAKE_TRACE_PATH)
except OSError:
    pass


def mkui():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    g._screen_on = False
    return g


print("a dark-to-lit wake records its reason")
g = mkui()
g.wake_screen(why="msg")
check("screen lit", g._screen_on)
check("trace holds the reason", [e[1] for e in g._wake_trace] == ["msg"], g._wake_trace)
g.wake_screen(why="key")
check("a wake with the screen already on is not a transition", [e[1] for e in g._wake_trace] == ["msg"], g._wake_trace)
g._screen_on = False
g.wake_screen()
check("no reason given is recorded as ?", [e[1] for e in g._wake_trace] == ["msg", "?"], g._wake_trace)

print("the trace is bounded")
g = mkui()
for i in range(40):
    g._screen_on = False
    g.wake_screen(why="k%d" % i)
check("at most WAKE_TRACE_MAX entries, newest last",
      len(g._wake_trace) == ui.WAKE_TRACE_MAX and g._wake_trace[-1][1] == "k39", len(g._wake_trace))

print("the file on the deck gets the same, best effort")
body = open(ui.WAKE_TRACE_PATH).read()
check("file written", "k39" in body and "msg" in body, body[-120:])
check("file bounded", os.path.getsize(ui.WAKE_TRACE_PATH) <= ui.WAKE_TRACE_FILE_MAX * 2, os.path.getsize(ui.WAKE_TRACE_PATH))
os.remove(ui.WAKE_TRACE_PATH)

print("the trackball handler names the event that woke the screen")
g = mkui()
g._irq_up = 1
g.handle_trackball()
check("ball wake recorded with the direction", g._wake_trace and g._wake_trace[-1][1] == "ball:up", g._wake_trace)
g = mkui()
g._irq_click = 1
g.handle_trackball()
check("click wake recorded", g._wake_trace and g._wake_trace[-1][1] == "ball:click", g._wake_trace)

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")

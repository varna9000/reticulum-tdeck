# The recording screen's "Warming mic..." title. After a light sleep the v1's
# ADC needs a few seconds (bench 2026-10-10: 0.4-7 s) to come back, and the
# old screen showed a static title the user could not tell apart from a hang.
# The title now carries the elapsed seconds the node feeds it, and the node
# cancels the recording when the warm-up gives up, so the screen never
# strands. This pins the title text; the node side is pinned by reading.
#
# Run:  python3 tests/test_recording_warmup_ui.py

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

import ui  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeTFT:
    def __init__(self):
        self.texts = []

    def text(self, font, s, x, y, fg, bg=None):
        # ui.py hands the driver raw bytes (see README: C driver UTF-8 path)
        self.texts.append(s.decode("latin-1") if isinstance(s, (bytes, bytearray)) else s)

    def fill_rect(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass


def mkui():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    g.on_record_start = lambda: None
    g._enter_recording()
    g._cache = [''] * ui.CACHE_ROWS
    g.tft.texts.clear()
    return g


def drawn(g):
    return " ".join(t.strip() for t in g.tft.texts if t.strip())


print("recording title")
g = mkui()
g._draw_recording()
check("fresh recording says Recording", "Recording" in drawn(g), drawn(g))

g = mkui()
g._rec_warming = True
g._rec_warm_secs = 0
g._draw_recording()
check("warming shows the title", "Warming mic" in drawn(g), drawn(g))

g = mkui()
g._rec_warming = True
g._rec_warm_secs = 7
g._draw_recording()
check("warming counts the seconds", "Warming mic... 7s" in drawn(g), drawn(g))

g = mkui()
g._rec_warming = True
g._rec_warm_secs = 7
g._draw_recording()
g._cache = [''] * ui.CACHE_ROWS
g.tft.texts.clear()
g._rec_warm_secs = 8
g._draw_recording()
check("a new second repaints the title", "8s" in drawn(g), drawn(g))

print("entering a recording resets the counter")
g = mkui()
g._rec_warm_secs = 5
g._enter_recording()
check("warm seconds back to 0", g._rec_warm_secs == 0, g._rec_warm_secs)

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")

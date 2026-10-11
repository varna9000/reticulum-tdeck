# The Pro's redesigned list must not exist on the T-Deck v1: with the v1's
# landscape geometry (no board_geometry module) ui.py keeps its old layout.
#   /opt/homebrew/bin/python3 tests/test_ui_pro_inactive.py

import os
import sys
import types
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

_time.ticks_ms = lambda: 100000
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
sys.modules["board_geometry"] = None        # the v1 ships without it

import ui  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("  %s %s %s" % ("ok  " if cond else "FAIL", name, "" if cond else detail))
    if not cond:
        fails.append(name)


check("v1 geometry", ui.SCREEN_W == 320 and ui.SCREEN_H == 240 and ui.COLS == 40 and ui.BODY_ROWS == 12)
check("the Pro layout is off", ui._pro is None)
check("the list keeps its 11 rows", ui.LIST_ROWS == ui.BODY_ROWS - 1 == 11)


class T:
    def __getattr__(self, n):
        return lambda *a, **k: None


g = ui.UI(T(), object(), lambda: b"\x00", node_name="t", trackball=False)
for i in range(14):
    g.add_peer(bytes([i + 1]) * 16, "p%d" % i)
g.selected_idx = 0
g.node_scroll = 0
for _ in range(12):
    g._scroll_down()
check("v1 list window is still 11 rows", g.selected_idx == 12 and g.node_scroll == 2, "%d %d" % (g.selected_idx, g.node_scroll))
g.draw()
check("the old draw path runs without the Pro module", True)
P = b"\x07" * 16
g.add_peer(P, "Alex")
g.selected_peer = P
g.add_chat_message(P, False, "hello there")
rows = g._build_chat_lines()
check("v1 chat lines are the old 8-tuples with the name prefix",
      len(rows) == 1 and len(rows[0]) == 8 and rows[0][1].startswith("Alex> "), repr(rows))
g.state = ui.STATE_CHAT
g._cache = [''] * ui.CACHE_ROWS
g.draw()
g.state = ui.STATE_RECORDING
g._rec_warming = False
g._cache = [''] * ui.CACHE_ROWS
g.draw()
check("v1 chat and recording screens draw the old way", True)
print()
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("all passed")

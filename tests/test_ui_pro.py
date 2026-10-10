# Host tests for the T-Deck Pro's redesigned node list (ui_pro.py), against
# the real UI class with the Pro's geometry and a display that records every
# draw call. Run:
#   /opt/homebrew/bin/python3 tests/test_ui_pro.py

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
import ui_pro  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class RecTFT:
    """Records what is drawn and where."""
    mono = True

    def __init__(self):
        self.calls = []

    def text(self, font, s, x, y, fg=None, bg=None):
        if isinstance(s, (bytes, bytearray)):
            s = bytes(s).decode("latin-1")
        self.calls.append(("text", s, x, y, fg, bg))

    def fill_rect(self, x, y, w, h, c):
        self.calls.append(("rect", x, y, w, h, c))

    def fill(self, c):
        self.calls.append(("fill", c))

    def blit_buffer(self, *a, **k):
        self.calls.append(("blit",))

    def __getattr__(self, name):
        return lambda *a, **k: None


def mkui():
    _clock[0] = 100000
    t = RecTFT()
    g = ui.UI(t, object(), lambda: b"\x00", node_name="t", trackball=False)
    g.state = ui.STATE_NODES
    g._screen_on = True
    g._state_change_ms = 0
    # Record small-font text and icons by content rather than as raw blits.
    g._sdraw = lambda s, x, y, fg, bg: t.calls.append(("small", s, x, y, fg, bg))
    g._bitmap = lambda icon, x, y, c: t.calls.append(("icon", icon, x, y, c))
    return g, t


def peers(g, n, tab=None):
    for i in range(n):
        g.add_peer(bytes([i + 1]) * 16, "peer%02d" % i, hops=2)
    g.node_tab = ui.TAB_MSG if tab is None else tab
    g.selected_idx = 0
    g.node_scroll = 0
    g._cache = [''] * ui.CACHE_ROWS


def texts(t, kind="text"):
    return [c for c in t.calls if c[0] == kind]


def find(t, kind, s):
    for c in t.calls:
        if c[0] == kind and c[1] == s:
            return c
    return None


# --- Task 1: activation -------------------------------------------------------

def test_pro_layout_is_active_on_the_pro():
    print("activation")
    check("ui holds the Pro module", ui._pro is ui_pro)
    check("the list shows 8 rows", ui.LIST_ROWS == 8 and ui_pro.LIST_ROWS == 8)
    check("the bands add up to the panel",
          ui_pro.LIST_Y + ui_pro.LIST_ROWS * ui_pro.ROW_H == ui_pro.TOOL_Y
          and ui_pro.TOOL_Y + ui_pro.TOOL_H == ui.SCREEN_H
          and ui_pro.TAB_Y + ui_pro.TAB_H == ui_pro.LIST_Y)
    g, t = mkui()
    check("a new UI has no sheet", g._sheet is None)


def test_list_window_follows_the_selection():
    g, t = mkui()
    peers(g, 14)
    for _ in range(9):
        g._scroll_down()
    check("9 steps down: selection 9", g.selected_idx == 9)
    check("the window is 8 rows: scroll is 2", g.node_scroll == 2, str(g.node_scroll))
    for _ in range(9):
        g._scroll_up()
    check("back at the top", g.selected_idx == 0 and g.node_scroll == 0)


def test_l_opens_find():
    g, t = mkui()
    peers(g, 2)
    g.handle_key(b'l')
    check("l opens Find, like m", g._find is True)


def test_image_lists():
    man = open(os.path.join(ROOT, "tools/tdeck_manifest.py")).read()
    pro = man.split('_board == "tdeck_pro"')[1].split('elif _board')[0]
    check("ui_pro.py frozen for the Pro only",
          'freeze(_root, "ui_pro.py")' in pro and man.count('"ui_pro.py"') == 1)
    check("ui_pro.py in deploy_pro.sh", "ui_pro.py" in open(os.path.join(ROOT, "tools/deploy_pro.sh")).read())


if __name__ == "__main__":
    test_pro_layout_is_active_on_the_pro()
    test_list_window_follows_the_selection()
    test_l_opens_find()
    test_image_lists()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

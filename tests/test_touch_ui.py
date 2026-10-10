# Host tests for the touch dispatcher, against the real UI class with the
# T-Deck Pro's geometry (240x320, 30 columns, 17 body rows). Run:
#   /opt/homebrew/bin/python3 tests/test_touch_ui.py

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

_geo = types.ModuleType("board_geometry")      # what deploy copies onto a Pro
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


class FakeTFT:
    mono = True

    def __getattr__(self, name):               # text, fill_rect, fill, blit_buffer, ...
        return lambda *a, **k: None


def mkui(state=None):
    _clock[0] = 100000
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    if state is not None:
        g.state = state
    g._screen_on = True
    g._state_change_ms = 0                     # the 500 ms back guards are long past
    return g


def keys_pressed(g):
    """Record what handle_key receives instead of acting on it."""
    seen = []
    g.handle_key = lambda k: seen.append(bytes(k)) or True
    return seen


# --- Task 3: hint zones ------------------------------------------------------

def test_geometry_is_the_pros():
    print("geometry")
    check("30 columns, 17 body rows", ui.COLS == 30 and ui.BODY_ROWS == 17, "%d %d" % (ui.COLS, ui.BODY_ROWS))


def test_hint_zones_follow_the_hints():
    print("hint zones")
    g = mkui(ui.STATE_NODES)
    g._draw_hints((("A", "nnc"), ("S", "etup"), ("F", "av"), ("M", "hash")))
    z = g._hint_zones
    check("one zone per hint, tagged with the state", len(z) == 4 and all(s == ui.STATE_NODES for s, _, _, _ in z), repr(z))
    check("keys are what handle_key takes", [k for _, _, _, k in z] == [b'a', b's', b'f', b'm'], repr(z))
    check("(A)nnc spans its 6 cells", z[0][1] == 0 and z[0][2] == 6 * ui.CHAR_W, repr(z[0]))
    check("zones do not overlap and run left to right",
          all(z[i][2] <= z[i + 1][1] for i in range(3)), repr(z))
    g._draw_hints((("BKSP", "exit"), ("ENT", "rooms")))
    z = g._hint_zones
    check("a redraw replaces the zones", len(z) == 2, repr(z))
    check("BKSP and ENT map to their bytes", [k for _, _, _, k in z] == [b'\x08', b'\r'], repr(z))


def test_hint_key_table():
    hk = ui.UI._hint_key
    check("CLICK and ENTER are Enter", hk("CLICK") == b'\r' and hk("ENTER") == b'\r')
    check("the mic icon is the 0 key", hk(ui._MIC) == b'0')
    check("a capital letter is its lower-case key", hk("R") == b'r')
    check("a label that names no single key has none", hk("U/D") is None)


def test_settings_bar_and_chat_footer_record_zones():
    g = mkui(ui.STATE_SETTINGS)
    g._draw_settings_bottom_bar()
    z = g._hint_zones
    check("settings bar: select on the left, back on the right",
          [k for _, _, _, k in z] == [b'\r', b'\x08'] and z[0][1] == 0 and z[1][2] <= ui.SCREEN_W, repr(z))
    g = mkui(ui.STATE_CHAT)
    g.selected_peer = b"\x01" * 16
    g.cmd_buf = bytearray()
    g.chat_cursor = -1
    g.on_record_start = lambda: None
    g.draw_input()
    z = g._hint_zones
    check("empty chat footer: mic and back", [k for _, _, _, k in z] == [b'0', b'\x08'], repr(z))
    g.cmd_buf = bytearray(b"hi")
    g._cache[ui.INPUT_SLOT] = ''
    g.draw_input()
    check("typing replaces the hints with the draft: no zones", g._hint_zones == [], repr(g._hint_zones))


if __name__ == "__main__":
    test_geometry_is_the_pros()
    test_hint_zones_follow_the_hints()
    test_hint_key_table()
    test_settings_bar_and_chat_footer_record_zones()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

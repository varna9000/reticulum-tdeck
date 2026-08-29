# The layout constants in ui.py are derived from screen size so that one UI
# serves both the T-Deck v1 (320x240 landscape) and the T-Deck Pro (240x320
# portrait). These tests pin both ends of that: the v1 numbers must come out
# byte-identical to the hardcoded ones they replaced, and the Pro must reflow
# rather than draw off the edge of its panel. Run:
#   python3 tests/test_board_geometry.py

import importlib
import os
import sys
import types
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


def load_ui(geometry=None):
    """Import ui.py fresh, optionally with a board_geometry module present."""
    for mod in ("ui", "board_geometry"):
        sys.modules.pop(mod, None)
    if geometry is None:
        # Make sure a stale file on disk cannot leak into the v1 case.
        sys.modules["board_geometry"] = None
    else:
        m = types.ModuleType("board_geometry")
        m.SCREEN_W, m.SCREEN_H = geometry
        sys.modules["board_geometry"] = m
    return importlib.import_module("ui")


def test_v1_unchanged():
    print("T-Deck v1 (no board_geometry)")
    ui = load_ui(None)
    for name, want in (("SCREEN_W", 320), ("SCREEN_H", 240), ("COLS", 40),
                       ("INPUT_Y", 224), ("SEP_Y", 222), ("BODY_ROWS", 12),
                       ("CHAR_W", 8), ("CHAR_H", 16), ("BODY_Y", 26),
                       ("CACHE_ROWS", 15), ("FOOT_SLOT", 13),
                       ("INPUT_SLOT", 14)):
        got = getattr(ui, name)
        check("%s == %d" % (name, want), got == want, "got %r" % got)


def test_pro_portrait():
    print("T-Deck Pro (240x320 portrait)")
    ui = load_ui((240, 320))
    for name, want in (("SCREEN_W", 240), ("SCREEN_H", 320), ("COLS", 30),
                       ("INPUT_Y", 304), ("SEP_Y", 302), ("BODY_ROWS", 17),
                       ("CACHE_ROWS", 20), ("FOOT_SLOT", 18),
                       ("INPUT_SLOT", 19)):
        got = getattr(ui, name)
        check("%s == %d" % (name, want), got == want, "got %r" % got)


def test_layout_fits_panel():
    print("layout stays on the panel")
    for label, geom in (("v1", None), ("pro", (240, 320))):
        ui = load_ui(geom)
        w, h = ui.SCREEN_W, ui.SCREEN_H
        check("%s: text columns fit the width" % label,
              ui.COLS * ui.CHAR_W <= w,
              "%d*%d > %d" % (ui.COLS, ui.CHAR_W, w))
        check("%s: input bar sits inside the panel" % label,
              ui.INPUT_Y + ui.CHAR_H <= h,
              "%d+%d > %d" % (ui.INPUT_Y, ui.CHAR_H, h))
        check("%s: body rows stop above the separator" % label,
              ui.BODY_Y + ui.BODY_ROWS * ui.CHAR_H <= ui.SEP_Y,
              "%d+%d*%d > %d" % (ui.BODY_Y, ui.BODY_ROWS, ui.CHAR_H, ui.SEP_Y))
        check("%s: scrollbar lane is inside the width" % label,
              ui.SBAR_X + ui.SBAR_W <= w,
              "%d+%d > %d" % (ui.SBAR_X, ui.SBAR_W, w))
        check("%s: at least 10 body rows" % label, ui.BODY_ROWS >= 10,
              "only %d" % ui.BODY_ROWS)
        # The row cache is indexed by slot, and the highest body slot a list
        # page writes is BODY_ROWS + 1 (`ci = i + 2` over BODY_ROWS - 1 rows,
        # plus the identity row _draw_shell_manual puts at BODY_ROWS). Sizing
        # it by hand is how a taller panel walks off the end of the list.
        check("%s: footer slot clears the body rows" % label,
              ui.FOOT_SLOT > ui.BODY_ROWS,
              "foot %d vs %d body rows" % (ui.FOOT_SLOT, ui.BODY_ROWS))
        check("%s: input slot is distinct from the footer" % label,
              ui.INPUT_SLOT != ui.FOOT_SLOT)
        check("%s: cache holds every slot" % label,
              ui.CACHE_ROWS > max(ui.FOOT_SLOT, ui.INPUT_SLOT),
              "%d slots, highest index %d"
              % (ui.CACHE_ROWS, max(ui.FOOT_SLOT, ui.INPUT_SLOT)))


def test_pro_geometry_file_matches():
    print("shipped board_geometry_tdeck_pro.py")
    ns = {}
    with open(os.path.join(ROOT, "board_geometry_tdeck_pro.py")) as f:
        exec(f.read(), ns)
    check("declares the Pro panel as 240x320",
          (ns.get("SCREEN_W"), ns.get("SCREEN_H")) == (240, 320),
          "got %r x %r" % (ns.get("SCREEN_W"), ns.get("SCREEN_H")))


if __name__ == "__main__":
    test_v1_unchanged()
    test_pro_portrait()
    test_layout_fits_panel()
    test_pro_geometry_file_matches()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

# tdeck_node._show_audio_status: set the navbar's audio status and wait until the
# panel shows it before the caller starts work that starves the GUI task (the
# playback thread, the codec2 decode). Lifted out of tdeck_node.py (it starts
# hardware at import) and run against a fake gui.
#
# Run:  python3 tests/test_audio_status.py

import ast
import asyncio
import os
import sys
import time as _time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_time.ticks_ms = lambda: int(_time.monotonic() * 1000)
_time.ticks_diff = lambda a, b: a - b
_uasyncio = types.ModuleType("uasyncio")
_uasyncio.sleep_ms = lambda ms: asyncio.sleep(ms / 1000)
sys.modules["uasyncio"] = _uasyncio


def _lifted():
    with open(os.path.join(ROOT, "tdeck_node.py")) as f:
        tree = ast.parse(f.read())
    nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_show_audio_status"]
    assert len(nodes) == 1
    return compile(ast.Module(body=nodes, type_ignores=[]), "tdeck_node.py", "exec")


class Gui:
    def __init__(self, screen_on=True, paint_after=None):
        self._screen_on = screen_on
        self._audio_status = None
        self._nav_mid_cache = "x"
        self.dirty = False
        self.user = 0
        self.painted = None
        self.paint_after = paint_after          # polls until the "gui_loop" paints

    def note_user_input(self):
        self.user += 1

    def audio_status_shown(self):
        if self.paint_after is not None:
            self.paint_after -= 1
            if self.paint_after <= 0:
                self.painted = self._audio_status
        return self.painted == self._audio_status


def _run(gui, text, timeout_ms=3000):
    g = {"gui": gui, "time": _time}
    exec(_lifted(), g)
    t0 = _time.monotonic()
    asyncio.run(g["_show_audio_status"](text, timeout_ms))
    return _time.monotonic() - t0


def test_waits_until_the_status_is_painted():
    gui = Gui(paint_after=5)
    dt = _run(gui, "playing")
    assert gui._audio_status == "playing" and gui.painted == "playing"
    assert gui.dirty and gui._nav_mid_cache == "" and gui.user == 1
    assert dt < 1.0, dt


def test_screen_off_does_not_wait():
    gui = Gui(screen_on=False)
    dt = _run(gui, "playing")
    assert gui._audio_status == "playing" and dt < 0.05, dt


def test_gives_up_after_the_timeout():
    gui = Gui(paint_after=10 ** 9)
    dt = _run(gui, "playing", timeout_ms=200)
    assert 0.15 < dt < 1.0, dt


if __name__ == "__main__":
    failed = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            try:
                v()
                print("ok  ", k)
            except Exception as e:
                failed += 1
                print("FAIL", k, type(e).__name__, e)
    print("\n%s" % ("all ok" if not failed else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)

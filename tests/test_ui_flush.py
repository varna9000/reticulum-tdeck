# The T-Deck Pro's e-ink panel is never touched while ui.py draws: every call
# lands in a framebuffer, and one flush() at the end pushes the frame. That is
# the whole reason the port is usable -- a flush per draw call would be 86
# refreshes at ~700 ms, ten minutes a screen.
#
# So the invariant these tests hold is: exactly one flush per rendered screen,
# and always inside the acquire/release window, since the flush IS the SPI
# transfer. The T-Deck v1 has no flush() on its display object at all and must
# keep working untouched.
#
# Run:  python3 tests/test_ui_flush.py

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

import ui

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeTFT:
    """A TFT: written directly, so it has no flush() at all."""

    def __init__(self):
        self.log = []

    def text(self, font, s, x, y, fg, bg=None):
        self.log.append("text")

    def fill_rect(self, x, y, w, h, c):
        self.log.append("fill_rect")

    def fill(self, c):
        self.log.append("fill")


class FakePanel(FakeTFT):
    """An e-ink surface: drawing is free, flush() is the transfer."""

    def __init__(self):
        FakeTFT.__init__(self)
        self.flushes = 0
        self.acquired = None   # set by the harness, read at flush time

    def flush(self):
        self.flushes += 1
        self.log.append("flush@%s" % self.acquired)


def _mkui(tft):
    g = ui.UI(tft, object(), lambda: b"\x00", node_name="t", trackball=False)
    g._screen_on = True
    return g


def test_no_flush_while_drawing():
    print("drawing never touches the panel")
    tft = FakePanel()
    g = _mkui(tft)
    g.draw()
    check("draw() alone flushes nothing", tft.flushes == 0,
          "flushed %d times" % tft.flushes)
    check("draw() did paint", "text" in tft.log or "fill_rect" in tft.log)


def test_flush_pushes_once():
    print("one flush per rendered screen")
    tft = FakePanel()
    g = _mkui(tft)
    g.draw()
    g._flush()
    check("_flush() pushes exactly once", tft.flushes == 1,
          "flushed %d times" % tft.flushes)
    g._flush()
    check("a second screen is a second flush", tft.flushes == 2)


def test_tft_without_flush():
    print("a display with no flush()")
    tft = FakeTFT()
    g = _mkui(tft)
    g.draw()
    g._flush()  # must be a no-op, not an AttributeError
    check("_flush() is inert on a plain TFT", True)
    check("the flush hook resolved to None", g._panel_flush is None)


def test_flush_is_inside_the_spi_window():
    """Every redraw in gui_loop sits between spi_acquire_display() and
    spi_release_display(). The flush is the transfer, so it has to be in
    there too -- outside it, the bus is back at the radio's clock rate."""
    print("flush lands inside the acquire/release window")
    import asyncio

    tft = FakePanel()
    g = _mkui(tft)

    events = []

    def acquire():
        tft.acquired = "held"
        events.append("acquire")

    def release():
        tft.acquired = "released"
        events.append("release")

    class _Stop(Exception):
        pass

    # gui_loop draws once, then loops on sleeps. Let it through the initial
    # draw and one more pass, then break out.
    ticks = [0]

    async def _sleep_ms(ms):
        ticks[0] += 1
        if ticks[0] > 3:
            raise _Stop

    # ui.py imports uasyncio; on the host it is the stub module created above,
    # so sleep_ms is ours to define.
    ui.asyncio.sleep_ms = _sleep_ms
    try:
        asyncio.run(g.gui_loop(acquire, release))
    except _Stop:
        pass

    check("the initial draw was flushed", tft.flushes >= 1,
          "flushed %d times" % tft.flushes)
    check("no flush happened with the bus released",
          "flush@released" not in tft.log,
          str([e for e in tft.log if e.startswith("flush@")][:5]))
    check("every flush happened with the bus held",
          all(e == "flush@held" for e in tft.log if e.startswith("flush@")))
    # A screen that has not changed must not pay for a refresh.
    _flushes_after_first = tft.flushes
    check("an idle loop does not keep flushing", _flushes_after_first <= 2,
          "flushed %d times over one idle pass" % _flushes_after_first)


if __name__ == "__main__":
    test_no_flush_while_drawing()
    test_flush_pushes_once()
    test_tft_without_flush()
    test_flush_is_inside_the_spi_window()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

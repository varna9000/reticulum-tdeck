# The UI must not offer hardware the board does not have.
#
# ui.py is shared between the T-Deck v1 and the T-Deck Pro, and the Pro is
# missing things the v1 has -- chiefly the ES7210 microphone, so there is no
# voice recording. tdeck_node.py leaves on_record_start unset on such a board,
# and that absence is the signal the UI keys off. These tests hold both ends of
# it: nothing offers recording without a microphone, and everything still does
# with one.
#
# Run:  python3 tests/test_ui_capabilities.py

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
    def __init__(self):
        self.texts = []

    def text(self, font, s, x, y, fg, bg=None):
        self.texts.append(s if isinstance(s, str) else bytes(s).decode("latin-1"))

    def fill_rect(self, x, y, w, h, c):
        pass

    def fill(self, c):
        pass


def _mkui(with_mic):
    tft = FakeTFT()
    g = ui.UI(tft, object(), lambda: b"\x00", node_name="t", trackball=False)
    g._screen_on = True
    g.state = ui.STATE_CHAT
    g.selected_peer = b"\x01" * 8
    g.chat_cursor = -1          # composing, not navigating history
    g.cmd_buf = bytearray()
    if with_mic:
        g.on_record_start = lambda: None
        g.on_record_stop = lambda send: None
    return g, tft


def _drew_rec_hint(tft):
    return any("rec" in t for t in tft.texts)


def test_hint_hidden_without_mic():
    print("the record hint follows the hardware")
    g, tft = _mkui(with_mic=False)
    g.draw_input()
    check("no mic: the chat footer does not offer [0=rec]",
          not _drew_rec_hint(tft), str(tft.texts))

    g, tft = _mkui(with_mic=True)
    g.draw_input()
    check("mic present: the chat footer still offers [0=rec]",
          _drew_rec_hint(tft), str(tft.texts))


def test_hint_hidden_while_navigating():
    """The original condition, which the new one must not have broken: the
    hint is for composing, and disappears while stepping back through
    messages."""
    print("the record hint still hides while navigating history")
    g, tft = _mkui(with_mic=True)
    g.chat_cursor = 0
    g.draw_input()
    check("navigating history hides the hint even with a mic",
          not _drew_rec_hint(tft), str(tft.texts))


def test_recording_state_refuses_without_mic():
    """Belt and braces: even if something reached _enter_recording, a board
    with no microphone must not land on a recording screen that never
    records."""
    print("recording state refuses to open without a mic")
    g, _ = _mkui(with_mic=False)
    g._enter_recording()
    check("no mic: state does not become STATE_RECORDING",
          g.state != ui.STATE_RECORDING, "state=%r" % g.state)

    g, _ = _mkui(with_mic=True)
    g._enter_recording()
    check("mic present: state becomes STATE_RECORDING",
          g.state == ui.STATE_RECORDING, "state=%r" % g.state)


def test_battery_reads_through_the_board():
    """The two boards sense the battery in completely different ways, so the
    UI takes a reader from the board rather than importing one."""
    print("battery comes from the board")
    g, _ = _mkui(with_mic=False)
    check("starts unknown", g.bat_v == 0.0, "got %r" % g.bat_v)

    g.on_battery = lambda: 4.229          # what the Pro's BQ27220 reports
    g.update_battery()
    check("a board reading is adopted", abs(g.bat_v - 4.229) < 1e-6,
          "got %r" % g.bat_v)

    # A gauge that does not answer must not wipe the last good reading -- an
    # unreadable pack is not a flat pack.
    g.on_battery = lambda: None
    g.update_battery()
    check("a failed read keeps the last value", abs(g.bat_v - 4.229) < 1e-6,
          "got %r" % g.bat_v)

    g.on_battery = lambda: 1 / 0          # a driver that raises
    g.update_battery()
    check("a raising reader is survivable", abs(g.bat_v - 4.229) < 1e-6,
          "got %r" % g.bat_v)


def test_unlit_battery_segments_are_visible_on_mono():
    """On a 1-bit panel DIM_CYAN and NEON_GREEN both cross the shim's ink
    threshold, so unlit segments drawn dim come out as black as lit ones and
    the pack always reads full. Unlit must be background there."""
    print("battery icon is legible on a 1-bit panel")

    class MonoTFT(FakeTFT):
        mono = True

        def __init__(self):
            FakeTFT.__init__(self)
            self.rects = []

        def fill_rect(self, x, y, w, h, c):
            self.rects.append(c)

    def segments(bat_v):
        """The three bar fills are the last three rects draw_navbar paints."""
        g = ui.UI(MonoTFT(), object(), lambda: b"\x00", node_name="t",
                  trackball=False)
        g._screen_on = True
        g.bat_v = bat_v
        g.draw_navbar()
        return g, g.tft.rects[-3:]

    g, flat = segments(3.0)             # below every threshold: all unlit
    check("mono panel is detected", g._mono is True)
    check("a flat pack paints no segment in the lit colour",
          g.NEON_GREEN not in flat, str(flat))
    check("a flat pack paints unlit segments as background",
          flat == [g.BG_DARK] * 3, str(flat))

    g, full = segments(4.1)             # above every threshold: all lit
    check("a full pack still paints every segment lit",
          full == [g.NEON_GREEN] * 3, str(full))

    # The whole point: the two states have to look different.
    check("flat and full render differently", flat != full,
          "%s vs %s" % (flat, full))

    colour = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t",
                   trackball=False)
    check("a colour panel is not treated as mono", colour._mono is False)


if __name__ == "__main__":
    test_hint_hidden_without_mic()
    test_hint_hidden_while_navigating()
    test_recording_state_refuses_without_mic()
    test_battery_reads_through_the_board()
    test_unlit_battery_segments_are_visible_on_mono()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

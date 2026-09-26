# Host tests: an open RRC panel (members / rooms) must not repaint whole on
# every draw -- that blanking was the flicker. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_panel_flicker.py

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from test_ui_shell import _mkui

import ui as U
import rrc_ui


def _ui_with_panel(kind):
    g = _mkui()
    calls = g.tft.calls
    orig_fill = g.tft.fill_rect

    def fill_rect(x, y, w, h, c):
        orig_fill(x, y, w, h, c)
        calls.append(("fill", (w, h), x, y))   # x/y where text calls have them
    g.tft.fill_rect = fill_rect
    g._rrc_room = "#varna"
    g._rrc_hub_name = "Varna Hub"
    g._rrc_roster = [(bytes([i]) * 16, "nick%d" % i) for i in range(10)]
    g._rrc_rooms = [("room%d" % i, "topic") for i in range(10)]
    g._rrc_list_seen = True
    g.state = U.STATE_RRC_CHAT if kind == "members" else U.STATE_RRC_ROOMS
    g.draw()
    rrc_ui.panel_toggle(g, kind)
    g.draw()                              # first paint of the open panel
    return g


def _in_panel(c):
    """Draws inside the panel box (the body frame's rails at x=0/W-1 run
    through the same rows but are outside it)."""
    x, y = c[2], c[3]
    return (rrc_ui.PANEL_X <= x < rrc_ui.PANEL_X + rrc_ui.PANEL_W
            and rrc_ui.PANEL_Y <= y < rrc_ui.PANEL_Y + rrc_ui.PANEL_H)


def _panel_calls(g):
    return [c for c in g.tft.calls if _in_panel(c)]


def test_an_idle_redraw_leaves_the_panel_alone():
    for kind in ("members", "rooms"):
        g = _ui_with_panel(kind)
        g.tft.calls.clear()
        g.draw()
        assert not _panel_calls(g), (kind, _panel_calls(g))
    print("ok test_an_idle_redraw_leaves_the_panel_alone")


def test_a_trackball_tick_repaints_only_the_two_rows_it_moves():
    for kind in ("members", "rooms"):
        g = _ui_with_panel(kind)
        g.tft.calls.clear()
        rrc_ui.panel_scroll(g, 1)
        g.draw()
        full = [c for c in _panel_calls(g) if c[0] == "fill"
                and c[1][0] >= rrc_ui.PANEL_W and c[1][1] >= rrc_ui.PANEL_H]
        assert not full, (kind, full)                 # no whole-box wipe
        ys = {c[3] for c in _panel_calls(g) if c[0] == "text"}
        assert len(ys) == 2, (kind, sorted(ys))
    print("ok test_a_trackball_tick_repaints_only_the_two_rows_it_moves")


def test_a_new_line_does_not_paint_under_the_open_panel():
    g = _ui_with_panel("members")
    g.tft.calls.clear()
    for n in range(3):
        g.rrc_line("msg", "sam", "hello %d" % n)
    g.draw()
    assert not _panel_calls(g), _panel_calls(g)
    print("ok test_a_new_line_does_not_paint_under_the_open_panel")


def test_closing_the_panel_repaints_the_scrollback():
    g = _ui_with_panel("members")
    g.rrc_line("msg", "sam", "hello under the panel")
    g.draw()
    rrc_ui.panel_close(g)
    g.tft.calls.clear()
    g.draw()
    painted = " ".join(c[1].decode("latin-1") if isinstance(c[1], (bytes, bytearray))
                       else c[1] for c in g.tft.calls if c[0] == "text")
    assert "hello under the panel" in painted, painted
    print("ok test_closing_the_panel_repaints_the_scrollback")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc panel flicker tests passed")

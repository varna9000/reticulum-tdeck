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
    g.SW, g.SH = 6, 12        # the device's small font (spleen 6x12); the host has no font module
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


# --- Task 2: rows ---------------------------------------------------------------

def row_y(i):
    return ui_pro.LIST_Y + i * ui_pro.ROW_H


def test_rows_are_two_lines_at_32px():
    print("rows")
    g, t = mkui()
    peers(g, 3)
    g.selected_idx = 1
    ui_pro.draw_rows(g)
    for i in range(3):
        c = find(t, "text", "peer%02d" % i)
        check("row %d name at x 16, y %d" % (i, row_y(i)), c is not None and c[2] == 16 and c[3] == row_y(i), repr(c))
        h = find(t, "small", (bytes([i + 1]) * 16).hex()[:8])
        check("row %d hash on the second line" % i, h is not None and h[2] == 16 and h[3] == row_y(i) + 17, repr(h))
    info = [c for c in texts(t, "small") if "hop" in c[1]]
    check("hops / last-seen right-aligned, ending at x 212",
          len(info) == 3 and all(c[2] + len(c[1]) * g.SW == ui_pro.INFO_R for c in info), repr(info[:1]))
    dots = [c for c in t.calls if c[0] == "rect" and c[3] == 3 and c[4] == 3]
    check("three dots per row in the ... column", len(dots) == 9 and all(c[1] >= ui_pro.DOTS_X for c in dots), str(len(dots)))


def test_selected_row_is_inverted():
    g, t = mkui()
    peers(g, 3)
    g.selected_idx = 1
    ui_pro.draw_rows(g)
    sel = find(t, "text", "peer01")
    oth = find(t, "text", "peer00")
    check("selected: paper text on an ink block", sel[4] == g.BG_DARK and sel[5] == g.SEL_BG, repr(sel))
    check("others: ink text on paper", oth[4] != g.BG_DARK and oth[5] == g.BG_DARK, repr(oth))
    blk = [c for c in t.calls if c[0] == "rect" and c[2] == row_y(1) and c[5] == g.SEL_BG and c[4] >= 31]
    check("the ink block covers the row", len(blk) == 1, repr(blk))


def test_margin_pill_and_star():
    g, t = mkui()
    peers(g, 4)
    k = g._peer_keys
    g.unread[k[0]] = 3
    g.unread[k[1]] = 12
    g.peers[k[1]]["fav"] = True
    g.peers[k[2]]["fav"] = True
    g.selected_idx = 3
    ui_pro.draw_rows(g)
    p3 = find(t, "small", "3")
    p12 = find(t, "small", "12")
    check("one-digit pill in the margin on the name line", p3 is not None and p3[2] < 16 and p3[3] == row_y(0) + 2, repr(p3))
    check("two-digit pill is exactly the margin wide", p12 is not None and p12[2] == 2 and p12[2] + 2 * g.SW + 2 == 16, repr(p12))
    check("the pill is inverse to its row", p3[4] == g.BG_DARK)
    stars = [c for c in t.calls if c[0] == "icon" and c[1] is ui._STAR]
    check("stars on the second line of rows 1 and 2",
          sorted(c[3] for c in stars) == [row_y(1) + 19, row_y(2) + 19] and all(c[2] < 16 for c in stars), repr(stars))
    check("a row can carry both", find(t, "small", "12")[3] == row_y(1) + 2)
    g.unread[k[0]] = 250
    g._cache = [''] * ui.CACHE_ROWS
    del t.calls[:]
    ui_pro.draw_rows(g)
    check("the count caps at 99", find(t, "small", "99") is not None and find(t, "small", "250") is None)


def test_rows_are_cached():
    g, t = mkui()
    peers(g, 3)
    ui_pro.draw_rows(g)
    del t.calls[:]
    ui_pro.draw_rows(g)
    check("nothing changed: nothing drawn", t.calls == [], repr(t.calls[:3]))
    g.selected_idx = 2
    ui_pro.draw_rows(g)
    drawn = sorted(set(c[3] for c in texts(t)))
    check("moving the selection redraws exactly the two rows involved",
          drawn == [row_y(0), row_y(2)], repr(drawn))


def test_short_list_blanks_the_rest():
    g, t = mkui()
    peers(g, 8)
    ui_pro.draw_rows(g)
    for k in list(g._peer_keys)[3:]:
        del g.peers[k]
    g._peer_keys = g._peer_keys[:3]
    del t.calls[:]
    ui_pro.draw_rows(g)
    blank = sorted(c[2] for c in t.calls if c[0] == "rect" and c[5] == g.BG_DARK and c[4] == ui_pro.ROW_H)
    check("rows 3..7 are blanked once the list is shorter", blank == [row_y(i) for i in range(3, 8)], repr(blank))
    check("no stale names are redrawn there", all(c[3] < row_y(3) for c in texts(t)))


def test_ping_status_on_the_selected_row():
    g, t = mkui()
    peers(g, 2)
    g.ping_status = "ping 1.2s"
    ui_pro.draw_rows(g)
    c = find(t, "small", "ping 1.2s")
    check("ping result replaces the selected row's right-hand text", c is not None and c[3] == row_y(0) + 17, repr(c))
    check("the other row still shows its hops", any("hop" in x[1] and x[3] == row_y(1) + 17 for x in texts(t, "small")))


def test_empty_list_and_other_tabs():
    g, t = mkui()
    g.node_tab = ui.TAB_NET
    ui_pro.draw_rows(g)
    check("empty NET tab shows its two lines",
          any("No nodes yet." in c[1] for c in texts(t)) and any("Waiting" in c[1] for c in texts(t)))
    g, t = mkui()
    g.add_shell_node(b"\x07" * 16, name="box", hops=1)
    g.node_tab = ui.TAB_SSH
    g.ssh_idx = 0
    g._cache = [''] * ui.CACHE_ROWS
    ui_pro.draw_rows(g)
    check("the SSH tab draws its own list", find(t, "text", "box") is not None)


def test_draw_node_list_uses_the_pro_rows():
    g, t = mkui()
    peers(g, 2)
    g.draw_node_list()
    check("ui.draw_node_list hands over", find(t, "text", "peer00") is not None and find(t, "text", "peer00")[3] == row_y(0))
    sb = [c for c in t.calls if c[0] == "rect" and c[1] == ui.SBAR_X]
    check("a scroll lane is drawn beside the list", sb and sb[0][2] == ui_pro.LIST_Y and sb[0][4] == 8 * 32, repr(sb[:1]))


if __name__ == "__main__":
    test_pro_layout_is_active_on_the_pro()
    test_list_window_follows_the_selection()
    test_l_opens_find()
    test_image_lists()
    test_rows_are_two_lines_at_32px()
    test_selected_row_is_inverted()
    test_margin_pill_and_star()
    test_rows_are_cached()
    test_short_list_blanks_the_rest()
    test_ping_status_on_the_selected_row()
    test_empty_list_and_other_tabs()
    test_draw_node_list_uses_the_pro_rows()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

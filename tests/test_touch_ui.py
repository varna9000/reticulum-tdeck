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
    g.dirty = False                            # ...and the screen has been drawn
    return g


def drawn(g):
    """The redraw a change asked for has reached the panel."""
    g.dirty = False


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
    check("ENT and ENTER are Enter", hk("ENT") == b'\r' and hk("ENTER") == b'\r')
    check("CLICK is the click gesture, which is not Enter on every screen", hk("CLICK") == "click")
    check("the mic icon is the 0 key", hk(ui._MIC) == b'0')
    check("a capital letter is its lower-case key", hk("R") == b'r')
    check("a label that names no single key has none", hk("U/D") is None)


def test_settings_bar_and_chat_footer_record_zones():
    g = mkui(ui.STATE_SETTINGS)
    g._draw_settings_bottom_bar()
    z = g._hint_zones
    check("settings bar: select (Enter: no trackball here) on the left, back on the right",
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


import touch_ui  # noqa: E402
import touch_gestures as tg  # noqa: E402
from touch_gestures import TAP, LONG, DRAG, FLICK, SWIPE, BACK, DOWN, MOVE, UP  # noqa: E402

ROW0 = ui.BODY_Y + ui.CHAR_H          # y of the first list row on a list screen


def row_y(i):
    return ROW0 + i * ui.CHAR_H + ui.CHAR_H // 2


def with_peers(n):
    g = mkui(ui.STATE_NODES)
    g.node_tab = ui.TAB_MSG
    for i in range(n):
        g.add_peer(bytes([i + 1]) * 16, "peer%02d" % i)
    g.selected_idx = 0
    g.node_scroll = 0
    g._enter_chat = lambda: None      # one tap opens now; keep that inert by default
    g.dirty = False                   # add_peer asked for a redraw; it has happened
    return g


def tap(g, x, y, t=200000, changed=0):
    return touch_ui.handle(g, (TAP, x, y, 0, t), changed)


def settle(g):
    """Run the UI's own navigation, as kbd_loop does after get_key()."""
    g.handle_trackball()


# --- Task 4: core ------------------------------------------------------------

def test_tabs_and_swipe():
    print("tabs")
    g = with_peers(2)
    tab_y = ui.BODY_Y + 4
    check("tap the 3rd tab column", tap(g, ui.SCREEN_W * 5 // 8, tab_y) is True and g.node_tab == 2, str(g.node_tab))
    check("tap the navbar above a tab also counts (bigger target)", tap(g, 10, 5) is True and g.node_tab == 0, str(g.node_tab))
    check("tapping the tab that is already active does nothing", tap(g, 10, tab_y) is False)
    # Chosen on the device: the finger moves the way the arrow keys do. A
    # phone-style "drag the page left to bring the next one in" felt
    # backwards on a panel that shows no sliding animation.
    check("finger moving RIGHT goes to the next tab",
          touch_ui.handle(g, (SWIPE, 60, 150, 1, 200000), 0) is True)
    settle(g)
    check("...tab 1", g.node_tab == 1, str(g.node_tab))
    touch_ui.handle(g, (SWIPE, 200, 150, -1, 200000), 0)
    settle(g)
    check("finger moving left goes back", g.node_tab == 0, str(g.node_tab))
    touch_ui.handle(g, (SWIPE, 200, 150, -1, 200000), 0)
    settle(g)
    check("...and wraps from the first tab to the last, as the arrow keys do",
          g.node_tab == ui.N_TABS - 1, str(g.node_tab))
    g.node_tab = 0
    touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0)
    settle(g)
    check("an edge swipe on the node list is a rightward swipe (next tab)",
          g.node_tab == 1, str(g.node_tab))


def test_footer_taps_press_the_hinted_key():
    print("footer")
    g = with_peers(2)
    g._draw_hints((("A", "nnc"), ("S", "etup"), ("F", "av"), ("M", "hash")))
    seen = keys_pressed(g)
    fy = ui.INPUT_Y + 8
    check("tap inside (S)etup", tap(g, g._hint_zones[1][1] + 4, fy) is True and seen == [b's'], repr(seen))
    del seen[:]
    check("on the Pro list the zone is the toolbar band, from its top line", tap(g, 4, ui._pro.TOOL_Y) is True and seen == [b'a'], repr(seen))
    del seen[:]
    gap = g._hint_zones[0][2] + 2                 # in the space between two hints
    check("a tap in a gap goes to the nearest hint within 8 px", tap(g, gap, fy) is True and len(seen) == 1, repr(seen))
    del seen[:]
    check("far from any hint: nothing", tap(g, ui.SCREEN_W - 2, fy) is False and seen == [], repr(seen))


def test_footer_zones_from_another_state_are_ignored():
    g = with_peers(2)
    g._draw_hints((("A", "nnc"),))
    g.state = ui.STATE_BROWSER                   # the footer was drawn for the node list
    seen = keys_pressed(g)
    check("stale zones do not fire", tap(g, 4, ui.INPUT_Y + 8) is False and seen == [], repr(seen))


def test_navbar_clock_opens_setup():
    g = with_peers(2)
    seen = keys_pressed(g)
    check("tap the right third of the navbar", tap(g, ui.SCREEN_W - 10, 5) is True and seen == [b's'], repr(seen))


def test_stale_layout_guard():
    print("stale layout")
    g = with_peers(6)
    check("a tap whose DOWN predates the layout change is dropped",
          tap(g, 100, nrow_y(3), t=5000, changed=5001) is False)
    settle(g)
    check("...and selected nothing", g.selected_idx == 0)
    check("a tap after the change counts", tap(g, 100, nrow_y(3), t=5001, changed=5001) is True)
    check("a drag is exempt (it causes the changes)",
          touch_ui.handle(g, (DRAG, 100, 100, 1, 5000), 9000) is True)


def test_stale_long_press_is_dropped():
    g = mkui(ui.STATE_IMAGE)
    seen = keys_pressed(g)
    check("a long press from before the screen changed does nothing",
          touch_ui.handle(g, (LONG, 100, 100, 0, 5000), 5001) is False and seen == [], repr(seen))


def test_locked_and_dark():
    g = with_peers(3)
    g.locked = True
    check("locked: nothing", tap(g, 100, nrow_y(1)) is False)
    g.locked = False
    g._screen_on = False
    check("dark: nothing", tap(g, 100, nrow_y(1)) is False)


def test_layout_key_changes_with_what_is_on_screen():
    print("layout key")
    g = with_peers(40)
    k0 = touch_ui.layout_key(g)
    g.selected_idx = 2
    check("moving the selection changes it (a tap must not act on a selection not yet shown)",
          touch_ui.layout_key(g) != k0)
    k0 = touch_ui.layout_key(g)
    g.node_scroll = 5
    k1 = touch_ui.layout_key(g)
    check("scrolling does", k1 != k0)
    g.node_tab = ui.TAB_NET
    k2 = touch_ui.layout_key(g)
    check("a tab switch does", k2 != k1)
    g.state = ui.STATE_SETTINGS
    check("a state change does", touch_ui.layout_key(g) != k2)


def test_controller_flow():
    print("controller")
    g = with_peers(6)
    clicks = []
    c = touch_ui.Controller(g, feedback=lambda: clicks.append(1))
    c.service(1000)
    c.event((DOWN, 100, nrow_y(2), 1100))
    c.event((UP, 100, nrow_y(2), 1200))
    settle(g)
    check("a tap through the controller selects and clicks once", g.selected_idx == 2 and clicks == [1], "%d %r" % (g.selected_idx, clicks))
    drawn(g)
    c.service(1250)
    c.event((DOWN, 100, nrow_y(9), 1300))          # beyond the list end
    c.event((UP, 100, nrow_y(9), 1400))
    check("a rejected tap gives no feedback", clicks == [1], repr(clicks))
    c.key(2000)
    c.event((DOWN, 100, nrow_y(4), 2100))
    c.event((UP, 100, nrow_y(4), 2200))
    settle(g)
    check("a touch right after a key is ignored", g.selected_idx == 2 and clicks == [1])
    del clicks[:]
    c.event((DOWN, 100, 100, 3000))
    for i in range(1, 6):
        c.event((MOVE, 100, 100 + 20 * i, 3000 + 200 * i))
    c.event((UP, 100, 200, 5000))
    check("a drag gives no feedback: it is for presses only", clicks == [], repr(clicks))
    g = with_peers(6)
    c = touch_ui.Controller(g, feedback=lambda: clicks.append(1))
    c.service(6100)
    c.event((DOWN, 60, 150, 6200))
    c.event((MOVE, 110, 152, 6260))
    c.event((MOVE, 170, 155, 6320))
    c.event((UP, 170, 155, 6340))
    check("nor does a swipe, though it acted", g._irq_right == 1 and clicks == [], "%d %r" % (g._irq_right, clicks))


def test_controller_stamps_layout_changes():
    g = with_peers(40)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, nrow_y(3), 1500))          # the finger went down at 1500...
    g.node_scroll = 4                             # ...then the list scrolled under it
    g.dirty = True
    c.service(1600)                               # the controller notices at 1600
    c.event((UP, 100, nrow_y(3), 1700))
    settle(g)
    check("the tap is dropped: the row under it changed", g.selected_idx == 0, str(g.selected_idx))
    c.event((DOWN, 100, nrow_y(3), 1800))          # the old frame is STILL on the panel
    c.event((UP, 100, nrow_y(3), 1900))
    settle(g)
    check("a tap made before the new frame is shown is dropped too", g.selected_idx == 0, str(g.selected_idx))
    drawn(g)                                      # the ~700 ms refresh ends
    c.service(2500)
    c.event((DOWN, 100, nrow_y(3), 2400))          # this finger went down DURING the refresh
    c.event((UP, 100, nrow_y(3), 2550))
    settle(g)
    check("...and so is one that went down during the refresh", g.selected_idx == 0, str(g.selected_idx))
    drawn(g)
    c.service(2600)
    c.event((DOWN, 100, nrow_y(3), 2700))
    c.event((UP, 100, nrow_y(3), 2800))
    settle(g)
    check("a tap on the frame the user can see counts", g.selected_idx == 4 + 3, str(g.selected_idx))


def test_controller_long_press_and_dark_screen():
    g = mkui(ui.STATE_IMAGE)
    g._image_drawn = True                         # the picture is on the panel
    seen = keys_pressed(g)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, 100, 1100))
    c.service(1799)
    check("no long press yet", seen == [])
    c.service(1800)
    check("the tick fires the long press: rotate", seen == [b'r'], repr(seen))
    g = with_peers(4)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, nrow_y(2), 1100))
    g._screen_on = False                          # the screen timed out under the finger
    c.service(1200)
    c.event((UP, 100, nrow_y(2), 1210))            # the driver closes the stroke
    settle(g)
    check("that UP is not a tap", g.selected_idx == 0, str(g.selected_idx))


def test_controller_swallows_ui_errors():
    g = with_peers(4)
    reports = []

    def boom(name):
        raise ValueError("ui blew up")

    g.nav_event = boom
    c = touch_ui.Controller(g, report=lambda name, e: reports.append((name, str(e))))
    c.service(1000)
    try:
        c.event((DOWN, 100, nrow_y(2), 1100))
        c.event((UP, 100, nrow_y(2), 1200))
        check("an exception in the UI does not escape", True)
    except Exception as e:
        check("an exception in the UI does not escape", False, repr(e))
    check("it is reported", reports == [("touch", "ui blew up")], repr(reports))
    check("and the stroke is cancelled", c.rec._active is False)
    g._find = True                                # a list whose row count the drag must ask for
    g._find_res = [("n", b"x" * 16)]
    g._find_rows = lambda: boom("drag")
    c.event((DOWN, 100, 100, 3000))               # now fail in the MIDDLE of a stroke
    c.event((MOVE, 100, 140, 3100))               # a drag step -> the list's state -> boom
    check("an error mid-drag cancels the stroke while the finger is still down",
          c.rec._active is False and len(reports) == 2, "%r %r" % (c.rec._active, reports))


# --- Pro layout: node list rows, the ... column, the sheet --------------------

def nrow_y(i):
    """Centre of node-list row i on the Pro layout (32 px rows from y 42)."""
    return 42 + i * 32 + 16


def test_one_tap_opens_a_row():
    print("node list rows (Pro layout)")
    g = with_peers(6)
    opened = []
    g._enter_chat = lambda: opened.append(g.selected_idx)
    check("tap on row 3 is accepted", tap(g, 100, nrow_y(3)) is True)
    check("the selection moved at once", g.selected_idx == 3, str(g.selected_idx))
    settle(g)
    check("and it opened, with one tap", opened == [3], repr(opened))
    tap(g, 100, nrow_y(3))
    settle(g)
    check("tapping the selected row opens it too", opened == [3, 3])


def test_tap_beyond_list_end_is_ignored():
    g = with_peers(3)
    check("a tap on row 5 of a 3-row list does nothing", tap(g, 100, nrow_y(5)) is False)
    settle(g)
    check("selection unchanged", g.selected_idx == 0)
    g = with_peers(0)
    check("a tap on an empty list does nothing", tap(g, 100, nrow_y(0)) is False)


def test_dots_column_opens_the_sheet():
    g = with_peers(6)
    opened = []
    g._enter_chat = lambda: opened.append(1)
    check("tap the ... of row 2", tap(g, 225, nrow_y(2)) is True)
    settle(g)
    check("it selects the row and opens the sheet, not the chat",
          g.selected_idx == 2 and g._sheet == ui.TAB_MSG and opened == [], "%d %r %r" % (g.selected_idx, g._sheet, opened))
    import ui_pro
    ui_pro.sheet_close(g)
    check("the ... of the already-selected row opens the sheet too",
          tap(g, 225, nrow_y(2)) is True and g._sheet == ui.TAB_MSG and opened == [])


def test_long_press_opens_the_sheet():
    g = with_peers(6)
    check("long press on row 4", touch_ui.handle(g, (LONG, 100, nrow_y(4), 0, 200000), 0) is True)
    check("selects it and opens the sheet", g.selected_idx == 4 and g._sheet == ui.TAB_MSG)
    g = with_peers(2)
    check("a long press below the last row does nothing",
          touch_ui.handle(g, (LONG, 100, nrow_y(6), 0, 200000), 0) is False and g._sheet is None)


def test_sheet_taps():
    import ui_pro
    g = with_peers(6)
    g.selected_idx = 1
    seen = keys_pressed(g)
    ui_pro.sheet_open(g)
    y0, n = ui_pro.sheet_rect(g)
    check("tap the Favorite item", tap(g, 100, y0 + 32 + 10) is True and seen == [b'f'] and g._sheet is None, repr(seen))
    ui_pro.sheet_open(g)
    check("tap a row above the sheet: it only closes", tap(g, 100, nrow_y(0)) is True and g._sheet is None and g.selected_idx == 1)
    ui_pro.sheet_open(g)
    check("an edge swipe closes it", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and g._sheet is None)
    ui_pro.sheet_open(g)
    check("a drag does nothing while it is up", touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0) is False and g._sheet is not None)
    check("nor a swipe", touch_ui.handle(g, (SWIPE, 60, 150, 1, 200000), 0) is False and g.node_tab == ui.TAB_MSG)
    k0 = touch_ui.layout_key(g)
    ui_pro.sheet_close(g)
    check("the sheet is part of the layout fingerprint", touch_ui.layout_key(g) != k0)


def test_drag_and_flick_scroll_the_list():
    print("drag (lists scroll like a page: finger up shows what is below)")
    g = with_peers(16)
    check("finger down at the top: nothing earlier to show, nothing moves",
          touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0) is True and g.node_scroll == 0 and g.selected_idx == 0)
    touch_ui.handle(g, (DRAG, 100, 100, -2, 200000), 0)
    settle(g)
    check("finger up two 16 px steps: the window moves one row, the highlight keeps its screen row",
          g.node_scroll == 1 and g.selected_idx == 1, "%d %d" % (g.node_scroll, g.selected_idx))
    touch_ui.handle(g, (DRAG, 100, 100, -1, 200000), 0)
    settle(g)
    check("a single further step is not yet a row", g.node_scroll == 1, str(g.node_scroll))
    touch_ui.handle(g, (DRAG, 100, 100, -1, 200000), 0)
    settle(g)
    check("the second one is", g.node_scroll == 2 and g.selected_idx == 2, "%d %d" % (g.node_scroll, g.selected_idx))
    touch_ui.handle(g, (DRAG, 100, 100, 4, 200000), 0)
    settle(g)
    check("finger down two rows: back to the top", g.node_scroll == 0 and g.selected_idx == 0)
    touch_ui.handle(g, (FLICK, 100, 100, -1, 200000), 0)
    settle(g)
    check("a flick up moves a screen of rows less one", g.node_scroll == ui.LIST_ROWS - 1 and g.selected_idx == ui.LIST_ROWS - 1,
          "%d %d" % (g.node_scroll, g.selected_idx))
    touch_ui.handle(g, (FLICK, 100, 100, 1, 200000), 0)
    settle(g)
    check("a flick down brings it back", g.node_scroll == 0 and g.selected_idx == 0)
    g.selected_idx = 3
    touch_ui.handle(g, (DRAG, 100, 100, -2, 200000), 0)
    settle(g)
    check("a highlight mid-page keeps its screen row too", g.node_scroll == 1 and g.selected_idx == 4, "%d %d" % (g.node_scroll, g.selected_idx))


# --- Task 5: chat, recording, browser, viewer --------------------------------

def mkchat(lines=40):
    g = mkui(ui.STATE_CHAT)
    g.selected_peer = b"\x01" * 16
    g.cmd_buf = bytearray()
    g.chat_cursor = -1
    g.chat_scroll = 0
    rows = [(2, False, "x", i, 0, "", 0) for i in range(lines)]     # ui_pro text rows, one block
    g._build_chat_lines = lambda: rows
    return g


def test_chat():
    print("chat")
    g = mkchat()
    opened = []
    g._chat_open_cursor_media = lambda: opened.append(g.chat_cursor)
    g._visible_image_lines = {4: 77, 5: 77}
    g._visible_audio_lines = {8: 78, 9: 78}
    g._image_cache = {(g.selected_peer, 77): b"jpg"}
    # Row tops as ui_pro.draw_chat leaves them: 6 px gaps above rows 4 and 8.
    g._chat_ys = [42 + 16 * i + (6 if i >= 4 else 0) + (6 if i >= 8 else 0) for i in range(11)]
    ys = g._chat_ys
    check("tap on plain text does nothing", tap(g, 100, ys[2] + 8) is False and g.chat_cursor == -1 and g.dirty is False)
    check("tap on an image tile's top row opens it", tap(g, 100, ys[4] + 8) is True and opened == [4] and g.chat_cursor == 4, repr(opened))
    settle(g)
    check("...and on its bottom row", tap(g, 100, ys[5] + 15) is True and opened == [4, 5], repr(opened))
    settle(g)
    check("tap on a voice tile plays it", tap(g, 100, ys[9] + 8) is True and opened == [4, 5, 9], repr(opened))
    check("...without parking the cursor on it (the tile would stay inverted and the mic hint vanish)",
          g.chat_cursor == 5, str(g.chat_cursor))
    settle(g)
    check("rows are found by where they were drawn: the top pixel line of row 4", tap(g, 100, ys[4]) is True and opened[-1] == 4, repr(opened))
    settle(g)
    n = len(opened)
    check("a tap in the gap above a block hits nothing", tap(g, 100, ys[4] - 3) is False and len(opened) == n)
    check("a tap below the last drawn row hits nothing", tap(g, 100, ys[10] + 20) is False and len(opened) == n)
    g._image_cache = {}
    g.chat_cursor = -1
    check("an expired image tile does nothing", tap(g, 100, ys[4] + 8) is False and len(opened) == n and g.chat_cursor == -1)
    g = mkchat(40)
    check("finger down 3 steps: older messages come into view",
          touch_ui.handle(g, (DRAG, 100, 100, 3, 200000), 0) is True and g.chat_scroll == 3 and g.chat_cursor == -1,
          "%d %d" % (g.chat_scroll, g.chat_cursor))
    touch_ui.handle(g, (DRAG, 100, 100, -5, 200000), 0)
    check("finger up: back toward the newest, clamped at 0", g.chat_scroll == 0, str(g.chat_scroll))
    touch_ui.handle(g, (DRAG, 100, 100, 999, 200000), 0)
    check("clamped at ui's own scroll limit", g.chat_scroll == g._chat_max_scroll() and g.chat_scroll > 0, str(g.chat_scroll))
    g = mkchat()
    seen = keys_pressed(g)
    check("header tap goes back", tap(g, 30, ui.BODY_Y + 4) is True and seen == [b'\x1b'], repr(seen))
    del seen[:]
    check("edge swipe goes back", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and seen == [b'\x1b'], repr(seen))
    del seen[:]
    g.cmd_buf = bytearray(b"draft")
    check("with a draft, back does nothing", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is False and seen == [], repr(seen))
    check("...from the header too", tap(g, 30, ui.BODY_Y + 4) is False and seen == [])


def test_recording():
    print("recording")
    g = mkui(ui.STATE_RECORDING)
    seen = keys_pressed(g)
    check("a tap above the drawn divider sends", tap(g, 120, ui._pro.REC_MID - 2) is True and seen == [b'\r'], repr(seen))
    del seen[:]
    settle(g)
    check("a tap below it cancels", tap(g, 120, ui._pro.REC_MID + 2) is True and seen == [b'\x08'], repr(seen))
    del seen[:]
    check("an edge swipe cancels", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and seen == [b'\x08'], repr(seen))
    check("a drag does nothing here", touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0) is False)


def mkbrowser(lines=60):
    g = mkui(ui.STATE_BROWSER)
    g.browser_lines = [("line %d" % i,) for i in range(lines)]
    g.browser_path = "/page/index.mu"
    g.browser_cursor = -1
    g.browser_scroll = 0
    return g


def test_browser():
    print("browser")
    g = mkbrowser()
    followed = []
    g._browser_follow_cursor = lambda: followed.append(g.browser_cursor)
    tap(g, 100, row_y(5))
    settle(g)
    check("tap a line: cursor to it", g.browser_cursor == 5, str(g.browser_cursor))
    tap(g, 100, row_y(5))
    settle(g)
    check("tap it again: follow", followed == [5], repr(followed))
    touch_ui.handle(g, (DRAG, 100, 200, -4, 200000), 0)
    check("finger up 4 steps: 4 lines further down the page", g.browser_scroll == 4, str(g.browser_scroll))
    touch_ui.handle(g, (FLICK, 100, 200, -1, 200000), 0)
    check("flick up: a page forward", g.browser_scroll == 4 + (ui.BODY_ROWS - 2), str(g.browser_scroll))
    touch_ui.handle(g, (FLICK, 100, 100, 1, 200000), 0)
    check("flick down: a page back", g.browser_scroll == 4, str(g.browser_scroll))
    g = mkbrowser(3)
    check("a tap below the last line of a short page does nothing", tap(g, 100, row_y(8)) is False)
    g = mkbrowser()
    calls = []
    g._browser_back = lambda: calls.append("back")
    g._browser_exit = lambda: calls.append("exit")
    check("edge swipe: one page back", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and calls == ["back"], repr(calls))
    check("header tap: straight out", tap(g, 30, ui.BODY_Y + 4) is True and calls == ["back", "exit"], repr(calls))


def test_image_viewer():
    print("viewer")
    g = mkui(ui.STATE_IMAGE)
    closed = []
    g._exit_image_view = lambda: closed.append(1)
    check("a tap anywhere closes", tap(g, 120, 160) is True and closed == [1])
    check("so does a tap on the hint bar", tap(g, 120, ui.INPUT_Y + 8) is True and closed == [1, 1])
    check("and an edge swipe", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and len(closed) == 3)
    check("a drag does nothing", touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0) is False)


# --- Task 6: setup, find, shell, rrc -----------------------------------------

def srow(i):
    return 42 + 32 * i + 16


def mksetup(idx=0, scroll=0):
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_MAIN
    g._settings_idx = idx
    g._settings_scroll = scroll
    return g


def test_setup():
    print("setup (Pro layout)")
    g = mksetup()
    seen = keys_pressed(g)
    check("the Pro's rows are in the agreed order",
          ui._pro.SETUP_IDX == (0, 1, 3, 10, 9, 4, 5, ui._SET_HAPTICS, 6, 7, 8, 11, 2, 12, 14), repr(ui._pro.SETUP_IDX))
    check("tap row 5 is accepted", tap(g, 100, srow(5)) is True)
    check("it selects at once (Volume, idx 4)", g._settings_idx == 4, str(g._settings_idx))
    g2 = mksetup()
    tap(g2, 100, srow(3))
    check("row 3 is LoRa cfg (idx 10)", g2._settings_idx == 10, str(g2._settings_idx))
    tap(g2, 100, srow(2))
    check("row 2 is LoRa (idx 3)", g2._settings_idx == 3, str(g2._settings_idx))
    settle(g)
    check("and acts, with one tap (Enter)", seen == [b'\r'], repr(seen))
    g = mksetup(idx=12, scroll=7)
    seen = keys_pressed(g)
    tap(g, 100, srow(7))
    settle(g)
    check("rows map through the shown idx list: the last row is Id (idx 14)", g._settings_idx == 14 and seen == [b'\r'],
          "%d %r" % (g._settings_idx, seen))
    g = mksetup(idx=6, scroll=7)
    tap(g, 100, 42 + 32 * 7 + 31)
    check("the last pixel line of the last row is still that row, not the footer", g._settings_idx == 14, str(g._settings_idx))
    g = mksetup(idx=3)
    g._wifi_connected = True
    seen = keys_pressed(g)
    check("WiFi while connected: the first tap is accepted", tap(g, 100, srow(0)) is True and g._settings_idx == 0)
    settle(g)
    check("...but only selects (a click there disconnects)", seen == [], repr(seen))
    tap(g, 100, srow(0), t=300000)
    settle(g)
    check("a second tap on it acts", seen == [b'\r'], repr(seen))
    g = mksetup(idx=3)
    seen = keys_pressed(g)
    tap(g, 100, srow(0))
    settle(g)
    check("WiFi while offline acts on one tap (it opens the scan)", seen == [b'\r'], repr(seen))
    g = mksetup(idx=4)
    adj = []
    g._settings_adjust = lambda d: adj.append(d)
    check("swipe right on the selected row steps it up",
          touch_ui.handle(g, (SWIPE, 60, srow(5), 1, 200000), 0) is True and adj == [1], repr(adj))
    check("a swipe on another row does nothing",
          touch_ui.handle(g, (SWIPE, 60, srow(6), 1, 200000), 0) is False and adj == [1])
    g = mksetup(idx=3)
    touch_ui.handle(g, (DRAG, 100, 100, 2, 300000), 0)
    settle(g)
    check("finger down at the top of Setup: nothing moves", g._settings_idx == 3 and g._settings_scroll == 0)
    touch_ui.handle(g, (DRAG, 100, 100, -1, 300000), 0)
    settle(g)
    check("a drag of one 16 px step is half a row: nothing yet", g._settings_idx == 3, str(g._settings_idx))
    touch_ui.handle(g, (DRAG, 100, 100, -1, 300000), 0)
    settle(g)
    check("the second step scrolls one row, the highlight keeps its screen row",
          g._settings_scroll == 1 and g._settings_idx == ui._pro.SETUP_IDX[3], "%d %d" % (g._settings_scroll, g._settings_idx))
    touch_ui.handle(g, (FLICK, 100, 100, -1, 400000), 0)
    settle(g)
    _max = len(ui._pro.SETUP_IDX) - ui._pro.SETUP_ROWS
    check("a flick up pages seven rows (clamped to the end)",
          g._settings_scroll == min(_max, 1 + 7) and g._settings_idx == ui._pro.SETUP_IDX[3 + g._settings_scroll - 1],
          "%d %d" % (g._settings_scroll, g._settings_idx))
    seen = keys_pressed(g)
    check("header tap goes back", tap(g, 30, 30) is True and seen == [b'\x1b'], repr(seen))
    del seen[:]
    check("edge swipe goes back", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and seen == [b'\x1b'], repr(seen))
    del seen[:]
    g._settings_page = g._TEXT_ENTRY_PAGES[0]
    check("on a text-entry page an edge swipe does nothing (it would discard the entry by accident)",
          touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is False and seen == [], repr(seen))
    check("...but a tap on its '< title' header is a deliberate cancel (the Pro has no Esc key)",
          tap(g, 30, 30) is True and seen == [b'\x1b'], repr(seen))


def test_find():
    print("find")
    g = with_peers(3)
    g._find = True
    g._find_res = [("n%d" % i, bytes([i]) * 16) for i in range(5)]
    g._find_sel = 0
    g._find_scroll = 0
    picked = []
    g._find_pick = lambda: picked.append(g._find_sel)
    f0 = 58                                              # two-line result rows on the Pro
    check("one tap on result 2 selects it", tap(g, 100, f0 + 2 * 32 + 16) is True and g._find_sel == 2, str(g._find_sel))
    settle(g)
    check("...and picks it", picked == [2], repr(picked))
    n = len(picked)
    check("a tap past the last result does nothing", tap(g, 100, f0 + 5 * 32 + 16, t=300000) is False and len(picked) == n)
    g._find_scroll = 2
    tap(g, 100, f0 + 1 * 32 + 16, t=400000)
    settle(g)
    check("rows follow the list's scroll", g._find_sel == 3 and picked[-1] == 3, "%d %r" % (g._find_sel, picked))
    g._find_res = [("n%d" % i, bytes([i]) * 16) for i in range(10)]
    g._find_scroll = 0
    g._find_sel = 0
    touch_ui.handle(g, (DRAG, 100, 150, 2, 500000), 0)
    settle(g)
    check("finger down at the top of Find: nothing moves", g._find_scroll == 0 and g._find_sel == 0)
    touch_ui.handle(g, (DRAG, 100, 150, -1, 500000), 0)
    settle(g)
    touch_ui.handle(g, (DRAG, 100, 150, -1, 500000), 0)
    settle(g)
    check("finger up 32 px scrolls one row", g._find_scroll == 1 and g._find_sel == 1, "%d %d" % (g._find_scroll, g._find_sel))
    check("the tab bar is inert while Find is open", tap(g, 200, ui.BODY_Y + 4) is False and g.node_tab == ui.TAB_MSG)
    check("edge swipe closes Find", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and g._find is False)


def test_shell():
    print("shell")
    g = mkui(ui.STATE_SHELL)
    g._shell_menu = False
    scrolled = []
    g._shell_scroll = lambda d: scrolled.append(d)
    check("finger down scrolls back toward older output",
          touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0) is True and scrolled == [2], repr(scrolled))
    opened = []
    g._shell_menu_open = lambda: opened.append(1)
    check("a long press opens the control menu", touch_ui.handle(g, (LONG, 100, 100, 0, 200000), 0) is True)
    settle(g)
    check("...through the click path", opened == [1], repr(opened))
    check("a tap on the terminal does nothing", tap(g, 100, 150) is False)
    check("back does not leave a live shell", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is False)
    g._shell_menu = True
    g._shell_menu_idx = 0
    sent = []
    g._shell_menu_exec = lambda: sent.append(g._shell_menu_idx)
    m0 = 42                                              # two-line menu rows on the Pro
    check("menu: one tap on item 2 selects and sends it", tap(g, 100, m0 + 2 * 32 + 16) is True and g._shell_menu_idx == 2 and sent == [2],
          "%d %r" % (g._shell_menu_idx, sent))
    g._shell_menu_idx = 8                                # past the window: it scrolls to keep the cursor in
    top = ui._pro.shell_menu_top(g)
    tap(g, 100, m0 + 0 * 32 + 16, t=300000)
    check("menu: rows follow the window", top == 1 and sent[-1] == 1, "%d %r" % (top, sent))
    quit_i = [k for k, it in enumerate(ui._SHELL_CTRL_ITEMS) if it[1] == "quit"][0]
    g._shell_menu_idx = 10                               # window 3..10: Quit shell is row quit_i - 3
    n = len(sent)
    check("menu: Quit shell only selects on the first tap", tap(g, 100, m0 + (quit_i - 3) * 32 + 16, t=400000) is True
          and g._shell_menu_idx == quit_i and len(sent) == n, repr(sent))
    tap(g, 100, m0 + (quit_i - ui._pro.shell_menu_top(g)) * 32 + 16, t=500000)
    check("menu: the second tap on it quits", sent[-1] == quit_i and len(sent) == n + 1, repr(sent))
    g._shell_menu_idx = 0
    g._menu_top = 0
    touch_ui.handle(g, (DRAG, 100, 150, 2, 600000), 0)
    settle(g)
    check("menu: finger down at the top: nothing moves", g._menu_top == 0 and g._shell_menu_idx == 0)
    touch_ui.handle(g, (DRAG, 100, 150, -1, 600000), 0)
    settle(g)
    touch_ui.handle(g, (DRAG, 100, 150, -1, 600000), 0)
    settle(g)
    check("menu: finger up 32 px scrolls one row", g._menu_top == 1 and g._shell_menu_idx == 1, "%d %d" % (g._menu_top, g._shell_menu_idx))
    touch_ui.handle(g, (FLICK, 100, 150, -1, 600000), 0)
    settle(g)
    _max = len(ui._SHELL_CTRL_ITEMS) - ui._pro.MENU_ROWS
    check("menu: a flick up stops at the end, no wrap", g._menu_top == _max and g._shell_menu_idx == _max, "%d %d" % (g._menu_top, g._shell_menu_idx))
    check("menu: a tap on its '< Control keys' band closes it", tap(g, 60, 30, t=700000) is True and g._shell_menu is False)
    g._shell_menu = True
    check("menu: back closes it", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and g._shell_menu is False)
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_networks = [("net%d" % k, -40 - k) for k in range(12)]
    g._settings_idx = 0
    g._settings_scroll = 2
    seen = keys_pressed(g)
    check("WiFi scan: one tap on row 1 selects network 3", tap(g, 100, 42 + 32 + 16) is True and g._settings_idx == 3, str(g._settings_idx))
    settle(g)
    check("...and opens it (Enter)", seen == [b'\r'], repr(seen))
    k0 = touch_ui.layout_key(g)
    g._wifi_networks = g._wifi_networks[:5]
    check("WiFi scan: a rescan that changes the list is a new layout", touch_ui.layout_key(g) != k0)
    g._settings_scroll = 0
    check("WiFi scan: a tap past the last network does nothing", tap(g, 100, 42 + 6 * 32 + 16, t=300000) is False)


def test_rrc():
    print("rrc")
    import rrc_ui
    g = mkui(ui.STATE_RRC_ROOMS)
    g._rrc_panel = False
    g._rrc_prompt = False
    toggled = []
    orig = rrc_ui.panel_toggle
    rrc_ui.panel_toggle = lambda u, kind: toggled.append(kind)
    try:
        check("header tap toggles the room picker", tap(g, 150, ui.BODY_Y + 4) is True)
        settle(g)
        check("...through the click path", toggled == ["rooms"], repr(toggled))
    finally:
        rrc_ui.panel_toggle = orig
    g._rrc_panel = True
    g._rrc_panel_kind = "rooms"
    g._rrc_rooms = [("mesh", "chatter"), ("ops", "operators"), ("bg", "bg")]
    g._rrc_list_seen = True
    g._rrc_panel_idx = 0
    g._rrc_panel_scroll = 0
    clicked = []
    orig_click = rrc_ui.panel_click
    rrc_ui.panel_click = lambda u: clicked.append(u._rrc_panel_idx) or True
    try:
        px = rrc_ui.PANEL_X + 40
        r0 = rrc_ui.PANEL_Y + 22                       # 32 px rows on the Pro
        check("panel: one tap on row 2 selects it and acts", tap(g, px, r0 + 2 * 32 + 16) is True
              and g._rrc_panel_idx == 2 and clicked == [2], "%d %r" % (g._rrc_panel_idx, clicked))
        check("panel: the last pixel line of a row is that row", tap(g, px, r0 + 31, t=300000) is True and clicked == [2, 0], repr(clicked))
        n = len(clicked)
        check("panel: a tap on the title band does nothing", tap(g, px, rrc_ui.PANEL_Y + 8, t=400000) is False
              and g._rrc_panel is True and len(clicked) == n)
        check("panel: a tap on the foot band does nothing", tap(g, px, rrc_ui.PANEL_Y + rrc_ui.PANEL_H - 10, t=500000) is False
              and g._rrc_panel is True and len(clicked) == n)
        check("panel: a tap on an empty slot does nothing (4 rows, 5 slots)", tap(g, px, r0 + 4 * 32 + 16, t=600000) is False
              and len(clicked) == n)
    finally:
        rrc_ui.panel_click = orig_click
    g._rrc_rooms = [("r%d" % k, "") for k in range(12)]
    g._rrc_panel_idx = 0
    g._rrc_panel_scroll = 0
    touch_ui.handle(g, (DRAG, px, 150, 2, 700000), 0)
    settle(g)
    check("panel: finger down at the top: nothing moves", g._rrc_panel_scroll == 0 and g._rrc_panel_idx == 0)
    touch_ui.handle(g, (DRAG, px, 150, -1, 700000), 0)
    settle(g)
    check("panel: a 16 px drag is half a row", g._rrc_panel_scroll == 0, str(g._rrc_panel_scroll))
    touch_ui.handle(g, (DRAG, px, 150, -1, 700000), 0)
    settle(g)
    check("panel: the second step scrolls one row", g._rrc_panel_scroll == 1 and g._rrc_panel_idx == 1, "%d %d" % (g._rrc_panel_scroll, g._rrc_panel_idx))
    g._rrc_panel_idx = 0
    g._rrc_panel_scroll = 0
    touch_ui.handle(g, (FLICK, px, 150, -1, 800000), 0)
    settle(g)
    _pg = rrc_ui.PANEL_ROWS - 1
    check("panel: a flick up pages a screen less one", g._rrc_panel_scroll == _pg and g._rrc_panel_idx == _pg,
          "%d %d" % (g._rrc_panel_scroll, g._rrc_panel_idx))
    k0 = touch_ui.layout_key(g)
    g._rrc_rooms = [("x%d" % k, "") for k in range(12)]
    check("panel: the list changing under the same cursor is a new layout", touch_ui.layout_key(g) != k0)
    k1 = touch_ui.layout_key(g)
    g._rrc_rooms = g._rrc_rooms + [("late", "")]
    check("...and so is it growing", touch_ui.layout_key(g) != k1)
    g._rrc_rooms = [("mesh", "chatter"), ("ops", "operators"), ("bg", "bg")]
    g._rrc_panel_idx = 0
    g._rrc_panel_scroll = 0
    check("panel: a tap outside the box closes it",
          tap(g, 120, rrc_ui.PANEL_Y + rrc_ui.PANEL_H + 20) is True and g._rrc_panel is False)
    g._rrc_panel = True
    check("panel: back closes it", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and g._rrc_panel is False)
    g = mkui(ui.STATE_RRC_CHAT)
    g._rrc_panel = False
    g._rrc_prompt = False
    g._rrc_input = "half a line"
    check("room with a draft: back does nothing", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is False)
    g._rrc_input = ""
    keys = []
    orig_hk = rrc_ui.handle_key
    rrc_ui.handle_key = lambda u, ch, key: keys.append(ch) or True
    try:
        check("room with an empty composer: back is Backspace",
              touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and keys == [8], repr(keys))
    finally:
        rrc_ui.handle_key = orig_hk
    g._rrc_prompt = True
    check("while the join prompt is up, back does nothing",
          touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is False)


# --- review fixes --------------------------------------------------------------

def stale_zones(g):
    g._hint_zones = [(g.state, 0, 80, b'\x08'), (g.state, 96, 200, b'\r'), (g.state, 208, 236, "click")]


def test_footer_is_dead_while_a_text_field_owns_it():
    print("review: footer vs typing")
    fy = ui.INPUT_Y + 8
    g = mkui(ui.STATE_CHAT)
    g.selected_peer = b"\x01" * 16
    g.cmd_buf = bytearray(b"half a message")
    stale_zones(g)
    seen = keys_pressed(g)
    check("chat with a draft: a footer tap presses nothing", tap(g, 120, fy) is False and seen == [], repr(seen))
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = g._TEXT_ENTRY_PAGES[0]
    stale_zones(g)
    seen = keys_pressed(g)
    check("a text-entry settings page: nothing (it would submit or delete)",
          tap(g, 120, fy) is False and tap(g, 20, fy) is False and seen == [], repr(seen))
    g = with_peers(2)
    g._find = True
    g._find_res = []
    g._find_sel = g._find_scroll = 0
    stale_zones(g)
    seen = keys_pressed(g)
    check("Find open: nothing (it would type into the query)", tap(g, 20, fy) is False and seen == [], repr(seen))
    g = mkui(ui.STATE_SHELL)
    g._shell_menu = False
    g._shell_input = bytearray(b"rm x")
    stale_zones(g)
    seen = keys_pressed(g)
    check("shell with a typed command: nothing (Enter would run it)", tap(g, 120, fy) is False and seen == [], repr(seen))
    g = mkui(ui.STATE_RRC_ROOMS)
    g._rrc_panel = False
    g._rrc_prompt = True
    stale_zones(g)
    seen = keys_pressed(g)
    check("the room-name prompt: nothing", tap(g, 120, fy) is False and seen == [], repr(seen))
    g = mkui(ui.STATE_RRC_CHAT)
    g._rrc_panel = False
    g._rrc_prompt = False
    stale_zones(g)
    seen = keys_pressed(g)
    check("a room's composer line: nothing", tap(g, 120, fy) is False and seen == [], repr(seen))


def test_text_lines_clear_the_zones_when_painted():
    g = mkui(ui.STATE_CHAT)
    stale_zones(g)
    g._draw_input_line("typing")
    check("_draw_input_line leaves no zones behind", g._hint_zones == [], repr(g._hint_zones))
    g = mkui(ui.STATE_RECORDING)
    stale_zones(g)
    g._rec_warming = False
    g._draw_recording()
    check("the recording screen leaves no zones behind", g._hint_zones == [], repr(g._hint_zones))
    import rrc_ui
    g = mkui(ui.STATE_RRC_ROOMS)
    g._rrc_prompt = True
    g._rrc_input = "mes"
    stale_zones(g)
    rrc_ui._draw_footer(g)
    check("the room prompt leaves no zones behind", g._hint_zones == [], repr(g._hint_zones))


def test_click_hints_click_and_shell_quit_is_inert():
    print("review: CLICK hints")
    fy = ui.INPUT_Y + 8
    g = mkui(ui.STATE_SHELL)
    g._shell_menu = False
    g._shell_input = bytearray()
    g._shell_line_mode = True
    g._hint_zones = [(g.state, 0, 80, b'\x08'), (g.state, 88, 180, "click")]
    opened = []
    g._shell_menu_open = lambda: opened.append(1)
    sent = []
    g._shell_send = lambda d: sent.append(bytes(d))
    check("tap (CLICK)keys is accepted", tap(g, 120, fy) is True)
    settle(g)
    check("...it opens the control menu and sends nothing to the remote", opened == [1] and sent == [], "%r %r" % (opened, sent))
    left = []
    g._leave_shell = lambda *a: left.append(1)
    check("tap (BKSP)quit does nothing: one tap must not drop a live session",
          tap(g, 20, fy) is False and left == [], repr(left))
    g = mkui(ui.STATE_NODES)
    g._hint_zones = [(g.state, 0, 80, None)]
    seen = keys_pressed(g)
    check("a hint that names no key is not tappable", tap(g, 20, fy) is False and seen == [], repr(seen))


def test_tcp_form_drag_and_swipe():
    print("review: the TCP form (issue #20)")
    # The form is cursor-driven like the LoRa editor: a drag moves the row
    # cursor, a swipe steps the row under it (the IFAC size), the footer's
    # (ENT) selects. Its text rows are not text pages: the form takes no letters.
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_TCP_HOST
    g._tcp_edit = g._tcp_seed()
    g._tcp_field = 0
    check("TCP form: a drag is accepted", touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0) is True)
    settle(g)
    check("...and moves the field", g._tcp_field == 2, str(g._tcp_field))
    adj = []
    g._settings_adjust = lambda d: adj.append(d)
    check("TCP form: a swipe adjusts the field", touch_ui.handle(g, (SWIPE, 100, 100, 1, 200000), 0) is True and adj == [1], repr(adj))
    check("TCP form: a tap on an empty row lands nowhere", tap(g, 100, frow(0)) is False)
    k0 = touch_ui.layout_key(g)
    g._tcp_field = 4
    check("TCP form: moving the cursor changes the layout key", touch_ui.layout_key(g) != k0)
    g._settings_page = ui._SET_TEXT
    g._text_entry = ("Host", g._tcp_edit, "host", ui._SET_TCP_HOST, True)
    k1 = touch_ui.layout_key(g)
    g._text_entry = ("Passphrase", g._tcp_edit, "passphrase", ui._SET_TCP_HOST, False)
    check("text page: a different field changes the layout key", touch_ui.layout_key(g) != k1)


def frow(i):
    """y inside form row i: the forms put their rows at explicit y, a full
    row per gap on the Pro (BODY_ROWS >= 14)."""
    return ROW0 + i * ui.CHAR_H + ui.CHAR_H // 2


def test_tcp_form_taps():
    print("review: the TCP form takes taps (Pro)")
    # TCP rows: blank, Host, blank, legend, Net, Pass, IFAC, blank, Connect.
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_TCP_HOST
    g._tcp_edit = g._tcp_seed()
    g._tcp_field = 0
    g.draw()
    drawn(g)
    hy = lambda f: dict((fl, y) for y, fl in g._form_hits)[f] + ui.CHAR_H // 2
    check("a tap on Pass is accepted", tap(g, 100, hy(2)) is True)
    check("it selects the row", g._tcp_field == 2, str(g._tcp_field))
    settle(g)
    check("and only selects: the first tap moves the bar",
          g._settings_page == ui._SET_TCP_HOST and g._text_entry is None, repr(g._settings_page))
    g.draw()
    drawn(g)
    check("a tap on the selected row is accepted", tap(g, 100, hy(2)) is True)
    settle(g)
    check("and opens its text page",
          g._settings_page == ui._SET_TEXT and g._text_entry[2] == "passphrase",
          "%r %r" % (g._settings_page, g._text_entry))
    g.handle_key(b"\x1b")
    g.draw()
    drawn(g)
    size = g._tcp_edit["ifac_size"]
    check("a tap on IFAC selects it", tap(g, 100, hy(3)) is True and g._tcp_field == 3)
    settle(g)
    check("without stepping it", g._tcp_edit["ifac_size"] == size, repr(g._tcp_edit))
    g.draw()
    drawn(g)
    check("the second tap steps it", tap(g, 100, hy(3)) is True)
    settle(g)
    check("...the size changed", g._tcp_edit["ifac_size"] != size and g._settings_page == ui._SET_TCP_HOST,
          repr(g._tcp_edit))
    g.draw()
    drawn(g)
    check("the legend row is not a target", tap(g, 100, frow(3)) is False and g._tcp_field == 3)
    check("a blank row is not a target", tap(g, 100, hy(3) + ui.CHAR_H) is False)
    got = []
    g.on_tcp_connect = lambda h, p, i: got.append((h, p, i))
    g._tcp_edit["host"] = "rns.example.org:4242"
    g.draw()
    drawn(g)
    check("a tap on Connect selects it", tap(g, 100, hy(4)) is True and g._tcp_field == 4)
    settle(g)
    check("the first tap does not connect", got == [], repr(got))
    g.draw()
    drawn(g)
    check("the second tap is accepted", tap(g, 100, hy(4)) is True)
    settle(g)
    check("and connects", len(got) == 1 and g._tcp_connecting, repr(got))
    check("while connecting a tap lands nowhere", tap(g, 100, hy(4)) is False)


def test_lora_editor_taps():
    print("review: the LoRa editor takes taps (Pro)")
    # LoRa rows: gap, Freq, BW, SF, CR, TX, gap, legend, Net, Pass, gap, Apply.
    g = mkui(ui.STATE_SETTINGS)
    g.set_lora_config({"freq_khz": 868800, "bw": 125, "sf": 8, "coding_rate": 5, "tx_power": 22})
    g._settings_idx = 10
    g.handle_key(b"\r")                        # the LoRa cfg row opens the editor
    g.draw()
    drawn(g)
    sf = g._lora_edit["sf"]
    check("a tap on SF is accepted", tap(g, 100, frow(3)) is True)
    check("it selects the row", g._lora_field == 2, str(g._lora_field))
    settle(g)
    check("without stepping it", g._lora_edit["sf"] == sf, repr(g._lora_edit))
    g.draw()
    drawn(g)
    check("a second tap on SF is accepted", tap(g, 100, frow(3)) is True)
    settle(g)
    check("and steps it", g._lora_edit["sf"] != sf, repr(g._lora_edit))
    g.draw()
    drawn(g)
    hy = lambda f: dict((fl, y) for y, fl in g._form_hits)[f] + ui.CHAR_H // 2
    check("a tap on Net selects it", tap(g, 100, hy(5)) is True and g._lora_field == 5)
    settle(g)
    check("the first tap opens nothing", g._settings_page == ui._SET_LORA, repr(g._settings_page))
    g.draw()
    drawn(g)
    check("the second tap is accepted", tap(g, 100, hy(5)) is True)
    settle(g)
    check("and opens its text page", g._settings_page == ui._SET_TEXT and g._text_entry[2] == "networkname",
          "%r %r" % (g._settings_page, g._text_entry))
    g.handle_key(b"\x1b")
    g.draw()
    drawn(g)
    check("a gap is not a target", tap(g, 100, frow(6)) is False and g._lora_field == 5)
    applied = []
    g.on_lora_config = lambda p: applied.append(p) or True
    check("a tap on Apply selects it", tap(g, 100, hy(ui._LORA_APPLY)) is True and g._lora_field == ui._LORA_APPLY)
    settle(g)
    check("the first tap does not apply", applied == [], repr(applied))
    g.draw()
    drawn(g)
    check("the second tap is accepted", tap(g, 100, hy(ui._LORA_APPLY)) is True)
    settle(g)
    check("and applies", len(applied) == 1, repr(applied))


class _RectTFT(FakeTFT):
    """Records fill_rect so the rail can be checked."""
    def __init__(self):
        self.rects = []

    def fill_rect(self, x, y, w, h, c):
        self.rects.append((x, y, w, h, c))


def test_form_group_has_air_on_the_pro():
    print("review: air inside the access-code group (Pro)")
    # The Pro has rows to spare: half a row between legend and Net, Net and
    # Pass, Pass and IFAC. The rail runs through the air, and the foot
    # still sits under the last row.
    g = mkui(ui.STATE_SETTINGS)
    g.tft = _RectTFT()
    g._settings_page = ui._SET_TCP_HOST
    g._tcp_edit = g._tcp_seed()
    g._tcp_field = 0
    g.draw()
    ys = dict((f, y) for y, f in g._form_hits)
    half = ui.CHAR_H // 2
    check("Net..Pass..IFAC are a row and a half apart",
          ys[2] - ys[1] == ui.CHAR_H + half and ys[3] - ys[2] == ui.CHAR_H + half, repr(ys))
    legend_y = ys[1] - half - ui.CHAR_H
    rail = [(y, h) for x, y, w, h, c in g.tft.rects if x == ui._RAIL_X and w == 2]
    covered = set()
    for y, h in rail:
        covered.update(range(y, y + h))
    want = range(legend_y + ui.CHAR_H // 2, ys[3] + ui.CHAR_H)
    check("the rail is unbroken from the legend to the last row", all(y in covered for y in want),
          repr(sorted(set(want) - covered)[:5]))
    foot = [(x, y) for x, y, w, h, c in g.tft.rects if x == ui._RAIL_X and w > 2 and h == 2]
    check("one foot, half a row under IFAC: the rows sit centred in the bracket",
          foot == [(ui._RAIL_X, ys[3] + ui.CHAR_H + half - 2)], repr(foot))
    want = range(legend_y + ui.CHAR_H // 2, ys[3] + ui.CHAR_H + half)
    check("the rail reaches the foot", all(y in covered for y in want), repr(sorted(set(want) - covered)[:5]))
    g = mkui(ui.STATE_SETTINGS)
    g.set_lora_config({"freq_khz": 868800, "bw": 125, "sf": 8, "coding_rate": 5, "tx_power": 22})
    g._settings_idx = 10
    g.handle_key(b"\r")
    g.draw()
    ys = dict((f, y) for y, f in g._form_hits)
    check("LoRa editor: Net..Pass a row and a half apart", ys[6] - ys[5] == ui.CHAR_H + half, repr(ys))
    check("LoRa editor: Apply still on the page", ys[ui._LORA_APPLY] + ui.CHAR_H <= ui.BODY_Y + ui.BODY_ROWS * ui.CHAR_H, repr(ys))


def test_lora_and_radio_pages_drag():
    print("review: setup pages")
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_LORA
    g._lora_field = 0
    check("LoRa page: a drag is accepted", touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0) is True)
    settle(g)
    check("...and moves the field", g._lora_field == 2, str(g._lora_field))
    adj = []
    g._settings_adjust = lambda d: adj.append(d)
    check("LoRa page: a swipe adjusts the field", touch_ui.handle(g, (SWIPE, 100, 100, -1, 200000), 0) is True and adj == [-1], repr(adj))
    seen = keys_pressed(g)
    check("LoRa page: an edge swipe adjusts too, it must not drop the pending edits",
          touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and adj == [-1, 1] and seen == [], "%r %r" % (adj, seen))
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_RADIO
    g._settings_scroll = 0
    g._radio_rows = 40
    check("Radio stats: a drag scrolls", touch_ui.handle(g, (DRAG, 100, 100, 3, 200000), 0) is True)
    settle(g)
    check("...by 3", g._settings_scroll == 3, str(g._settings_scroll))
    check("Radio stats: a swipe has nothing to adjust", touch_ui.handle(g, (SWIPE, 100, 100, 1, 200000), 0) is False)
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_MAIN
    g._settings_idx = 4
    adj = []
    g._settings_adjust = lambda d: adj.append(d)
    seen = keys_pressed(g)
    check("Setup: an edge swipe that starts on the selected row steps its value",
          touch_ui.handle(g, (BACK, 5, srow(5), 0, 200000), 0) is True and adj == [1] and seen == [], "%r %r" % (adj, seen))
    check("Setup: an edge swipe elsewhere is back",
          touch_ui.handle(g, (BACK, 5, srow(6), 0, 200000), 0) is True and seen == [b'\x1b'], repr(seen))


def test_find_back_keeps_a_typed_query():
    g = with_peers(2)
    g._find = True
    g._find_res = []
    g._find_sel = g._find_scroll = 0
    g._find_q = "6335c5b9"
    check("Find with a typed query: back does nothing", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is False and g._find is True)
    g._find_q = ""
    check("Find with an empty query: back closes it", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and g._find is False)


def test_untested_lists_follow_their_scroll():
    print("review: scrolled lists")
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_networks = [("net%d" % i, -60) for i in range(30)]
    g._settings_scroll = 5
    g._settings_idx = 5
    tap(g, 100, 42 + 3 * 32 + 16)
    settle(g)
    check("WiFi list: row 3 of a list scrolled by 5 is network 8", g._settings_idx == 8, str(g._settings_idx))
    g = with_peers(2)
    g._find = True
    g._find_q = ""
    g._find_res = [("n%d" % i, bytes([i]) * 16) for i in range(30)]
    g._find_scroll = 4
    g._find_sel = 4
    fy = ui.BODY_Y + 2 * ui.CHAR_H + ui.CHAR_H // 2
    tap(g, 100, 58 + 2 * 32 + 16)
    settle(g)
    check("Find: row 2 of results scrolled by 4 is result 6", g._find_sel == 6, str(g._find_sel))
    import rrc_ui
    g = mkui(ui.STATE_RRC_ROOMS)
    g._rrc_panel = True
    g._rrc_panel_kind = "rooms"
    g._rrc_prompt = False
    g._rrc_rooms = [("room%d" % i, "") for i in range(20)]
    g._rrc_list_seen = True
    g._rrc_panel_scroll = 3
    g._rrc_panel_idx = 3
    tap(g, rrc_ui.PANEL_X + 40, rrc_ui.PANEL_Y + 22 + 2 * 32 + 8)
    settle(g)
    check("panel: row 2 of a panel scrolled by 3 is row 5", g._rrc_panel_idx == 5, str(g._rrc_panel_idx))
    g = mkui(ui.STATE_RRC_CHAT)
    g._rrc_panel = False
    g._rrc_prompt = False
    ups = []
    real = g.nav_event
    g.nav_event = lambda name: ups.append(name)
    touch_ui.handle(g, (DRAG, 100, 100, 2, 200000), 0)
    check("room scrollback: finger down brings older lines in (the scrollback's 'up')", ups == ["up", "up"], repr(ups))
    g = mkui(ui.STATE_SHELL)
    g._shell_menu = True
    g._shell_menu_idx = 0
    check("shell menu open: a long press does nothing", touch_ui.handle(g, (LONG, 100, 100, 0, 200000), 0) is False)
    g = mkui(ui.STATE_RRC_ROOMS)
    g._rrc_panel = False
    g._rrc_prompt = True
    clicks = []
    g.nav_event = lambda name: clicks.append(name)
    check("hub header tap while the join prompt is up does nothing",
          tap(g, 30, ui.BODY_Y + 4) is False and clicks == [], repr(clicks))
    g = mkui(ui.STATE_CHAT)
    seen = keys_pressed(g)
    check("the navbar's Setup shortcut exists only on the node list",
          tap(g, ui.SCREEN_W - 10, 5) is True and seen == [b'\x1b'], repr(seen))


def test_controller_guard_cannot_wedge():
    print("review: guard robustness")
    g = with_peers(6)
    c = touch_ui.Controller(g)
    c.service(1000)
    g.node_tab = ui.TAB_NET
    g.dirty = True                                 # ...and suppose nothing ever clears it
    c.service(1100)
    c.event((DOWN, 30, ui.BODY_Y + 4, 1200))
    c.event((UP, 30, ui.BODY_Y + 4, 1300))
    check("while the redraw is pending the tap is dropped", g.node_tab == ui.TAB_NET)
    c.service(1100 + touch_ui.PENDING_MAX_MS + 1)
    c.event((DOWN, 30, ui.BODY_Y + 4, 1100 + touch_ui.PENDING_MAX_MS + 50))
    c.event((UP, 30, ui.BODY_Y + 4, 1100 + touch_ui.PENDING_MAX_MS + 150))
    check("a redraw flag that never clears cannot disable touch for good", g.node_tab == ui.TAB_MSG, str(g.node_tab))


def test_controller_survives_a_week_idle():
    # ticks_ms wraps at 2**30 and ticks_diff is only valid within 2**29.
    real_diff, real_ms, real_add = _time.ticks_diff, _time.ticks_ms, _time.ticks_add
    period = 1 << 30
    _time.ticks_diff = lambda a, b: ((a - b + period // 2) & (period - 1)) - period // 2
    _time.ticks_add = lambda a, d: (a + d) & (period - 1)
    try:
        g = with_peers(6)
        c = touch_ui.Controller(g)
        c.service(1000)
        late = (1000 + (1 << 29) + 5000) & (period - 1)      # ~6.2 days later, nothing changed
        step = 1 << 27
        t = 1000
        while t < 1000 + (1 << 29) + 5000:                  # the loop keeps servicing meanwhile
            t += step
            c.service(t & (period - 1))
        c.event((DOWN, 100, nrow_y(2), late))
        c.event((UP, 100, nrow_y(2), (late + 100) & (period - 1)))
        settle(g)
        check("taps still work after 6 days on one screen", g.selected_idx == 2, str(g.selected_idx))
    finally:
        _time.ticks_diff, _time.ticks_ms, _time.ticks_add = real_diff, real_ms, real_add


def test_controller_feedback_and_dark():
    g = with_peers(6)

    def bad_feedback():
        raise OSError("motor fell off the bus")

    reports = []
    c = touch_ui.Controller(g, feedback=bad_feedback, report=lambda n, e: reports.append(n))
    c.service(1000)
    try:
        c.event((DOWN, 100, nrow_y(2), 1100))
        c.event((UP, 100, nrow_y(2), 1200))
        check("a raising feedback does not escape into the key loop", True)
    except Exception as e:
        check("a raising feedback does not escape into the key loop", False, repr(e))
    check("...and is reported", reports == ["touch"], repr(reports))
    g = with_peers(6)
    marks = []
    g.note_user_input = lambda: marks.append(1)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, nrow_y(2), 1100))
    c.event((UP, 100, nrow_y(2), 1200))
    check("an accepted gesture marks the redraw as the user's (the e-ink shim pays its ghosting debt on those)",
          marks == [1], repr(marks))
    g = with_peers(6)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, nrow_y(2), 1100))
    g._screen_on = False
    c.service(1200)
    check("the screen going dark cancels the stroke in progress", c.rec._active is False)


# --- Task 8: glue and image --------------------------------------------------

def _src(rel):
    return open(os.path.join(ROOT, rel)).read()


def test_image_carries_the_touch_modules():
    print("image")
    man = _src("tools/tdeck_manifest.py")
    pro = man.split('_board == "tdeck_pro"')[1].split('elif _board')[0]
    for f, where in (("touch_gestures.py", "_root"), ("touch_ui.py", "_root"), ("drv2605.py", '_root + "/lib"')):
        check("%s frozen for the Pro only" % f,
              'freeze(%s, "%s")' % (where, f) in pro and man.count('"%s"' % f) == 1, f)
    dep = _src("tools/deploy_pro.sh")
    for f in ("touch_gestures.py", "touch_ui.py", "drv2605.py"):
        check("%s in deploy_pro.sh" % f, f in dep)


def test_board_glue_wiring():
    src = _src("board_tdeck_pro.py")
    handler = src.split("def _touch_handler(ev):")[1].split("\ndef ")[0]
    check("events reach the controller", "_touch_ctl.event(ev)" in handler)
    check("activity is still stamped on DOWN and MOVE", '_gui.wake_screen(why="touch")' in handler and "cst328.UP" in handler)
    service = src.split("def _service_touch():")[1].split("\ndef ")[0]
    check("the controller is serviced before this pass's events",
          service.index("_touch_ctl.service(") < service.index("touch.service("))
    getkey = src.split("def _get_key():")[1].split("\ndef ")[0]
    check("keys are reported to the recogniser", "_touch_ctl.key(" in getkey)
    check("the motor driver is enabled only while the screen is lit",
          "haptic.enable(_gui._screen_on)" in service or "haptic.enable(" in service)
    check("the driver gets its enable pin (GPIO 2, probed)", "en_gpio=config.PIN_VIBRATION" in src)
    check("without the motor chip GPIO 2 is left alone (its role on a V1.0 is unknown)",
          "Pin(config.PIN_VIBRATION, Pin.OUT, value=0)" not in src)
    check("feedback never touches the saved backlight preference",
          "on_kbd_backlight" not in src.split("def _touch_feedback():")[1].split("\ndef ")[0])



def test_layout_key_sees_a_reorder():
    print("review: reorder / row edges (Pro layout)")
    g = with_peers(10)
    g.node_scroll, g.selected_idx = 2, 9
    k0 = touch_ui.layout_key(g)
    ks = g._peer_keys
    ks[3], ks[4] = ks[4], ks[3]            # a message bubbled one visible peer over another
    check("two visible rows swapping changes the layout", touch_ui.layout_key(g) != k0)
    k1 = touch_ui.layout_key(g)
    ks[0], ks[1] = ks[1], ks[0]
    check("rows off screen do not", touch_ui.layout_key(g) == k1)


def test_row_edges():
    g = with_peers(10)
    opened = []
    g._enter_chat = lambda: opened.append(g.selected_idx)
    pressed = keys_pressed(g)
    check("the last pixel line of row 7 is still row 7", tap(g, 100, 297) is True and g.selected_idx == 7, str(g.selected_idx))
    settle(g)
    check("...and not the toolbar", opened == [7] and pressed == [], repr((opened, pressed)))
    g = with_peers(4)
    opened = []
    g._enter_chat = lambda: opened.append(g.selected_idx)
    tap(g, 215, nrow_y(1))
    settle(g)
    check("x 215 opens the row", opened == [1] and g._sheet is None, repr(opened))
    g = with_peers(4)
    tap(g, 216, nrow_y(1))
    check("x 216 opens the sheet", g._sheet is not None)


def test_chat_layout_key_at_the_history_cap():
    print("review: chat layout key")
    g = mkchat(5)
    P = g.selected_peer
    g.chat_history = {P: [(False, "a", 0, 0, False, False, 1), (False, "b", 0, 0, False, False, 2)]}
    k0 = touch_ui.layout_key(g)
    g.chat_history[P] = [(False, "b", 0, 0, False, False, 2), (False, "c", 0, 0, False, False, 3)]
    check("a full history rolling by one message is a new layout (same length, new rows)", touch_ui.layout_key(g) != k0)
    k1 = touch_ui.layout_key(g)
    g._build_chat_lines = lambda: [None] * 6
    check("the same messages taking another number of rows is a new layout too", touch_ui.layout_key(g) != k1)


def test_setup_guards():
    print("review: Setup one-tap guards")
    # Disconnecting WiFi or TCP tears the session down and clears every chat:
    # it always takes a tap that selects and a second that confirms, however
    # the row came to be selected (Setup opens with WiFi selected).
    g = mksetup(idx=0)
    g._wifi_connected = True
    seen = keys_pressed(g)
    check("Setup just opened, WiFi connected: one tap on WiFi does not disconnect", tap(g, 100, srow(0)) is True)
    settle(g)
    check("...no click was sent", seen == [], repr(seen))
    tap(g, 100, srow(0), t=300000)
    settle(g)
    check("the confirming tap acts", seen == [b'\r'], repr(seen))
    g = mksetup(idx=0)
    g._wifi_connected = True
    seen = keys_pressed(g)
    tap(g, 100, srow(0))
    settle(g)
    tap(g, 100, srow(5), t=300000)
    settle(g)
    del seen[:]
    tap(g, 100, srow(0), t=400000)
    settle(g)
    check("a tap elsewhere in between disarms it", seen == [], repr(seen))
    g = mksetup(idx=5)
    g._wifi_connected = True
    g._tcp_enabled = True
    seen = keys_pressed(g)
    tap(g, 100, srow(1))
    settle(g)
    check("TCP while on is the same teardown: first tap only selects", g._settings_idx == 1 and seen == [], repr(seen))
    tap(g, 100, srow(1), t=300000)
    settle(g)
    check("...second tap acts", seen == [b'\r'], repr(seen))
    g = mksetup(idx=5)
    g._wifi_connected = True
    seen = keys_pressed(g)
    tap(g, 100, srow(1))
    settle(g)
    check("TCP while off opens its host page on one tap", seen == [b'\r'], repr(seen))
    g = mksetup(idx=5)
    g.lora_online = True
    seen = keys_pressed(g)
    tap(g, 100, srow(2))
    settle(g)
    tap(g, 100, srow(2), t=300000)
    settle(g)
    check("LoRa while online: a tap selects and never resets a healthy radio", g._settings_idx == 3 and seen == [], repr(seen))
    g = mksetup(idx=5)
    g.lora_online = False
    seen = keys_pressed(g)
    tap(g, 100, srow(2))
    settle(g)
    check("LoRa while offline: one tap resets", seen == [b'\r'], repr(seen))
    g = mksetup(idx=5)
    k0 = touch_ui.layout_key(g)
    tap(g, 100, srow(5))
    check("an acting tap is a layout change, so a tap during its refresh is dropped", touch_ui.layout_key(g) != k0)
    k1 = touch_ui.layout_key(g)
    g._wifi_connected = True
    check("WiFi coming up behind the screen is one too", touch_ui.layout_key(g) != k1)
    g = mksetup(idx=0)
    g._wifi_connected = True
    g._draw_settings_bottom_bar()
    seen = keys_pressed(g)
    check("the footer's (CLICK) hint is dead on this page: it would act on a row the finger is not on",
          tap(g, 50, 300) is False and tap(g, 50, 312, t=300000) is False)
    settle(g)
    check("...nothing clicked", seen == [], repr(seen))
    check("(BKSP)back still works", tap(g, 220, 312, t=400000) is True and seen == [b'\x08'], repr(seen))
    g = mksetup(idx=0)
    adj = []
    g._settings_adjust = lambda d: adj.append(d)
    seen = keys_pressed(g)
    check("a swipe on a selected row with nothing to step is not swallowed",
          touch_ui.handle(g, (SWIPE, 60, srow(0), 1, 200000), 0) is False and adj == [])
    check("...so an edge swipe starting on it still goes back",
          touch_ui.handle(g, (BACK, 5, srow(0), 0, 200000), 0) is True and seen == [b'\x1b'] and adj == [], repr(seen))
    g = mksetup(idx=11, scroll=7)
    adj = []
    g._settings_adjust = lambda d: adj.append(d)
    check("a swipe finds the selected row in a scrolled window (Time zone at row 4)",
          touch_ui.handle(g, (SWIPE, 60, srow(4), -1, 200000), 0) is True and adj == [-1], repr(adj))


def test_rrc_review():
    print("review: rrc")
    import rrc_ui
    g = mkui(ui.STATE_RRC_ROOMS)
    g._rrc_prompt = False
    g._rrc_rooms = [("mesh", "chatter"), ("ops", "")]
    g._rrc_list_seen = True
    g._rrc_panel = True
    g._rrc_panel_kind = "rooms"
    g._rrc_panel_idx = 1
    g._rrc_panel_scroll = 0
    g._draw_hints((("BKSP", "exit"), ("ENT", "rooms")))
    joined = []
    g.on_rrc_join = lambda room: joined.append(room)
    seen = keys_pressed(g)
    check("picker open: a tap on the footer's (ENT)rooms hint closes it", tap(g, 150, 310) is True and g._rrc_panel is False)
    settle(g)
    check("...and joins nothing", joined == [] and seen == [], repr((joined, seen)))
    g = mkui(ui.STATE_RRC_CHAT)
    g._rrc_prompt = False
    g._rrc_input = ""
    g._rrc_roster = [(b"\x01" * 6, "alex")]
    g._rrc_panel = True
    g._rrc_panel_kind = "members"
    check("member panel open: a tap on the composer line closes it too", tap(g, 150, 310) is True and g._rrc_panel is False)
    g._rrc_panel = True
    g._rrc_panel_idx = 0
    k0 = touch_ui.layout_key(g)
    g._rrc_roster = [(b"\x02" * 6, "late")] + g._rrc_roster
    check("a member joining under the open panel is a new layout", touch_ui.layout_key(g) != k0)
    g._rrc_roster = [(bytes([i]) * 6, "n%d" % i) for i in range(40)]
    k1 = touch_ui.layout_key(g)
    g._rrc_roster = g._rrc_roster[:30] + [(b"\xff" * 6, "x")] + g._rrc_roster[31:]
    check("a change far off screen (same length) is not", touch_ui.layout_key(g) == k1)
    # The "<" at the left of the band is back, as on the chat and Setup; the
    # rest of the band opens the panel.
    g = mkui(ui.STATE_RRC_CHAT)
    g._rrc_panel = False
    g._rrc_prompt = False
    g._rrc_input = ""
    keys = []
    toggled = []
    orig_hk, orig_tg = rrc_ui.handle_key, rrc_ui.panel_toggle
    rrc_ui.handle_key = lambda u, ch, key: keys.append(ch) or True
    rrc_ui.panel_toggle = lambda u, kind: toggled.append(kind)
    try:
        check("room: a tap on the '<' leaves (Backspace)", tap(g, 12, 30) is True and keys == [8] and toggled == [], repr((keys, toggled)))
        tap(g, 150, 30, t=300000)
        settle(g)
        check("room: a tap on the rest of the band opens the member panel", toggled == ["members"] and keys == [8], repr((keys, toggled)))
        g._rrc_input = "draft"
        check("room with a draft: the '<' does nothing", tap(g, 12, 30, t=400000) is False and keys == [8])
    finally:
        rrc_ui.handle_key, rrc_ui.panel_toggle = orig_hk, orig_tg


def test_shell_menu_review():
    print("review: shell menu")
    P = ui._pro
    n = len(ui._SHELL_CTRL_ITEMS)
    quit_i = [k for k, it in enumerate(ui._SHELL_CTRL_ITEMS) if it[1] == "quit"][0]
    font_i = [k for k, it in enumerate(ui._SHELL_CTRL_ITEMS) if it[1] == "font"][0]

    def menu(idx=0):
        g = mkui(ui.STATE_SHELL)
        g._shell_menu_open()
        g._shell_menu_idx = idx
        g.sent = []
        g._shell_menu_exec = lambda: g.sent.append(g._shell_menu_idx)
        return g

    def row_of(g, idx):
        return 42 + (idx - P.shell_menu_top(g)) * 32 + 16

    g = menu(n - 1)
    tap(g, 100, row_of(g, quit_i))
    check("Quit: the first tap arms", g.sent == [])
    g._shell_menu_close()
    g._shell_menu_open()
    g._shell_menu_exec = lambda: g.sent.append(g._shell_menu_idx)
    g._shell_menu_idx = n - 1
    tap(g, 100, row_of(g, quit_i), t=300000)
    check("...closing and reopening the menu disarms it: one tap does not quit", g.sent == [], repr(g.sent))
    g = menu(n - 1)
    tap(g, 100, row_of(g, quit_i))
    g._shell_menu_move(-1)
    tap(g, 100, row_of(g, quit_i), t=300000)
    check("...moving the cursor with the keys disarms it too", g.sent == [], repr(g.sent))
    tap(g, 100, row_of(g, quit_i), t=400000)
    check("...and two taps in a row still quit", g.sent == [quit_i], repr(g.sent))
    g = menu(font_i)
    P.shell_menu_top(g)                  # Font scrolled into view...
    g._shell_menu_idx = 5                # ...and the cursor moved back up inside the window
    y = row_of(g, font_i)
    tap(g, 100, y)
    tap(g, 100, y, t=300000)
    tap(g, 100, y, t=400000)
    check("three taps at one spot on Font are three Font taps: the rows do not move under the finger",
          g.sent == [font_i] * 3, repr(g.sent))
    g = menu(n - 1)
    P.shell_menu_top(g)
    g._shell_menu_idx = 4
    y = row_of(g, font_i)
    tap(g, 100, y)
    tap(g, 100, y, t=300000)
    check("...from any cursor position", g.sent == [font_i] * 2, repr(g.sent))
    k0 = touch_ui.layout_key(g)
    tap(g, 100, y, t=500000)
    check("an acting menu tap is a layout change (no double step inside one refresh)", touch_ui.layout_key(g) != k0)
    g = menu(quit_i)
    g._draw_hints((("ENT", "send"), ("BKSP", "close")))
    check("the footer's (ENT)send is dead in touch: it would act on the cursor row, not the one under the finger",
          tap(g, 20, 310) is False and g.sent == [] and g._shell_menu is True, repr(g.sent))
    g = menu(0)
    g._menu_top = 0
    touch_ui.handle(g, (DRAG, 100, 150, 2, 600000), 0)
    settle(g)
    check("a drag down at the top stops there (the keys wrap; a finger must not jump to the end)",
          g._shell_menu_idx == 0 and g._menu_top == 0, "%d %d" % (g._shell_menu_idx, g._menu_top))
    touch_ui.handle(g, (FLICK, 100, 150, -1, 700000), 0)
    settle(g)
    touch_ui.handle(g, (FLICK, 100, 150, -1, 800000), 0)
    settle(g)
    _end = n - ui._pro.MENU_ROWS
    check("flicks up stop with the last item in view, the highlight on its screen row",
          g._menu_top == _end and g._shell_menu_idx == _end, "%d %d" % (g._menu_top, g._shell_menu_idx))
    g = mkui(ui.STATE_NODES)
    for i in range(3):
        g.add_peer(bytes([i + 1]) * 16, "p%d" % i)
    g._find = True
    g._find_res = [(bytes([i]) * 16, "n%d" % i, 0) for i in range(5)]
    g._find_sel = 0
    g._find_scroll = 0
    g._find_listen = ""
    picked = []
    g._find_pick = lambda: picked.append(g._find_sel)
    tap(g, 100, 58 + 2 * 32 + 16)
    check("a Lookup tap picks at once (nothing typed in the same pass can move the selection first)", picked == [2], repr(picked))

def test_drag_holds_the_redraw_until_the_finger_lifts():
    print("redraw hold")
    g = with_peers(16)
    check("nothing is held by default", g._redraw_hold is None and g._redraw_held(5000) is False)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, 100, 1100))
    c.event((MOVE, 100, 120, 1130))
    c.event((MOVE, 100, 140, 1160))                # 40 px: two steps, one row
    check("a drag step early in a stroke holds the redraw",
          g._redraw_hold == 1100 + touch_ui.DRAG_HOLD_MS, repr(g._redraw_hold))
    check("...so a flick is one refresh, not two", g._redraw_held(1170) is True)
    c.event((UP, 100, 140, 1180))
    check("lifting releases it at once", g._redraw_hold is None and g._redraw_held(1181) is False)
    c.event((DOWN, 100, 100, 2000))
    c.event((MOVE, 100, 140, 2100))
    check("a finger that stays down gets its frame when the hold runs out",
          g._redraw_held(2000 + touch_ui.DRAG_HOLD_MS - 1) is True
          and g._redraw_held(2000 + touch_ui.DRAG_HOLD_MS) is False and g._redraw_hold is None)
    c.event((MOVE, 100, 180, 2000 + touch_ui.DRAG_HOLD_MS + 500))
    check("a slow drag is not held again: the frames follow the finger",
          g._redraw_held(2000 + touch_ui.DRAG_HOLD_MS + 501) is False)
    c.event((MOVE, 100, 100, 2000 + touch_ui.DRAG_HOLD_MS + 900))
    g._redraw_hold = 1 << 20
    g._screen_on = False
    c.service(9000)
    check("the screen going dark drops the hold", g._redraw_hold is None)
    loop = _src("ui.py").split("async def gui_loop(")[1].split("\n    async def ")[0]
    check("gui_loop asks before the throttled redraw", "not self._redraw_held(now)" in loop)


def test_touch_is_sampled_during_a_refresh():
    src = _src("board_tdeck_pro.py")
    check("the panel's busy wait calls the sampler", "panel.idle = _touch_sample" in src)
    sample = src.split("def _touch_sample():")[1].split("\ndef ")[0]
    check("it only reads a chip that is up, with the screen on",
          "touch.ready" in sample and "_screen_on" in sample and "touch.poll()" in sample)
    check("it never acts on the UI mid-flush: events are only queued",
          "_touch_handler" not in sample and "_touch_ctl" not in sample)
    service = src.split("def _service_touch():")[1].split("\ndef ")[0]
    check("the backlog is replayed before this pass's own events",
          "_touch_backlog" in service and service.index("_touch_backlog") < service.index("touch.service("))


def test_a_rightward_swipe_is_back_where_it_means_nothing_else():
    # Captured on the deck 2026-10-11: strokes starting at x=28 were SWIPEs,
    # 4 px outside the 24 px edge band, and did nothing in a chat. "Back"
    # worked only from the bezel.
    print("swipe right = back")
    g = mkchat()
    seen = keys_pressed(g)
    check("chat: a rightward swipe from mid-screen goes back",
          touch_ui.handle(g, (SWIPE, 100, 150, 1, 200000), 0) is True and seen == [b'\x1b'], repr(seen))
    del seen[:]
    check("chat: a leftward one does not", touch_ui.handle(g, (SWIPE, 150, 150, -1, 200000), 0) is False and seen == [])
    g.cmd_buf = bytearray(b"draft")
    check("chat with a draft: still nothing is thrown away",
          touch_ui.handle(g, (SWIPE, 100, 150, 1, 200000), 0) is False and seen == [])
    g = mkbrowser()
    calls = []
    g._browser_back = lambda: calls.append("back")
    check("browser: one page back", touch_ui.handle(g, (SWIPE, 100, 150, 1, 200000), 0) is True and calls == ["back"], repr(calls))
    g = mkui(ui.STATE_RECORDING)
    seen = keys_pressed(g)
    check("recording: only the edge swipe cancels a clip",
          touch_ui.handle(g, (SWIPE, 100, 150, 1, 200000), 0) is False and seen == [], repr(seen))


def test_touch_feedback_is_a_setting():
    src = _src("board_tdeck_pro.py")
    fb = src.split("def _touch_feedback():")[1].split("\ndef ")[0]
    check("no click and no blink unless the user turned feedback on", "_gui._haptics" in fb
          and fb.index("_gui._haptics") < fb.index("haptic.click()"))
    service = src.split("def _service_touch():")[1].split("\ndef ")[0]
    check("the motor driver stays disabled while feedback is off (it idles at a current)",
          "_gui._screen_on and _gui._haptics" in service)
    node = _src("tdeck_node.py")
    check("the choice is saved", 'settings["haptics"]' in node and "gui.on_haptics = " in node)
    check("...and restored at boot, off when never set", 'settings.get("haptics", False)' in node)
    g = mksetup()
    seen = []
    g.on_haptics = lambda on: seen.append(on)
    k = ui._pro.SETUP_IDX.index(ui._SET_HAPTICS)
    check("one tap on its Setup row toggles it", tap(g, 100, srow(k)) is True)
    settle(g)
    check("...through the click path", g._haptics is True and seen == [True], repr(seen))


def test_tcp_row_switches_tcp_off_through_the_toggle():
    print("review: the TCP row's confirming tap reaches on_tcp_toggle(False) (2026-11-01: a night on TCP)")
    g = mksetup(idx=5)
    g._wifi_connected = True
    g._tcp_enabled = True
    g._tcp_target = "h:1"
    calls = []
    g.on_tcp_toggle = lambda en, h, p: calls.append((en, h, p)) or True
    g.on_wifi_reconnect = lambda: calls.append("reconnect") or True
    tap(g, 100, srow(1))
    settle(g)
    tap(g, 100, srow(1), t=300000)
    settle(g)
    check("the toggle is called with False, not the WiFi reconnect", calls == [(False, None, None)], repr(calls))
    check("the row shows OFF", g._tcp_enabled is False and g._tcp_target == "", repr((g._tcp_enabled, g._tcp_target)))


if __name__ == "__main__":
    test_touch_feedback_is_a_setting()
    test_a_rightward_swipe_is_back_where_it_means_nothing_else()
    test_drag_holds_the_redraw_until_the_finger_lifts()
    test_touch_is_sampled_during_a_refresh()
    test_geometry_is_the_pros()
    test_hint_zones_follow_the_hints()
    test_hint_key_table()
    test_settings_bar_and_chat_footer_record_zones()
    test_tap_beyond_list_end_is_ignored()
    test_tabs_and_swipe()
    test_footer_taps_press_the_hinted_key()
    test_footer_zones_from_another_state_are_ignored()
    test_navbar_clock_opens_setup()
    test_drag_and_flick_scroll_the_list()
    test_stale_layout_guard()
    test_stale_long_press_is_dropped()
    test_locked_and_dark()
    test_layout_key_changes_with_what_is_on_screen()
    test_controller_flow()
    test_controller_stamps_layout_changes()
    test_controller_long_press_and_dark_screen()
    test_controller_swallows_ui_errors()
    test_chat()
    test_recording()
    test_browser()
    test_image_viewer()
    test_setup()
    test_find()
    test_shell()
    test_rrc()
    test_image_carries_the_touch_modules()
    test_board_glue_wiring()
    test_footer_is_dead_while_a_text_field_owns_it()
    test_text_lines_clear_the_zones_when_painted()
    test_click_hints_click_and_shell_quit_is_inert()
    test_lora_and_radio_pages_drag()
    test_tcp_form_drag_and_swipe()
    test_tcp_form_taps()
    test_lora_editor_taps()
    test_form_group_has_air_on_the_pro()
    test_find_back_keeps_a_typed_query()
    test_untested_lists_follow_their_scroll()
    test_controller_guard_cannot_wedge()
    test_controller_survives_a_week_idle()
    test_controller_feedback_and_dark()
    test_one_tap_opens_a_row()
    test_dots_column_opens_the_sheet()
    test_long_press_opens_the_sheet()
    test_sheet_taps()
    test_layout_key_sees_a_reorder()
    test_row_edges()
    test_chat_layout_key_at_the_history_cap()
    test_setup_guards()
    test_tcp_row_switches_tcp_off_through_the_toggle()
    test_rrc_review()
    test_shell_menu_review()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

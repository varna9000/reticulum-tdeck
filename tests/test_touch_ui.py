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
    check("settings bar: select on the left, back on the right",
          [k for _, _, _, k in z] == ["click", b'\x08'] and z[0][1] == 0 and z[1][2] <= ui.SCREEN_W, repr(z))
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
    g.dirty = False                   # add_peer asked for a redraw; it has happened
    return g


def tap(g, x, y, t=200000, changed=0):
    return touch_ui.handle(g, (TAP, x, y, 0, t), changed)


def settle(g):
    """Run the UI's own navigation, as kbd_loop does after get_key()."""
    g.handle_trackball()


# --- Task 4: core ------------------------------------------------------------

def test_tap_selects_then_opens():
    print("node list rows")
    g = with_peers(6)
    opened = []
    g._enter_chat = lambda: opened.append(g.selected_idx)
    check("tap on row 3 is accepted", tap(g, 100, row_y(3)) is True)
    settle(g)
    check("...and selects it without opening", g.selected_idx == 3 and opened == [], "%d %r" % (g.selected_idx, opened))
    check("tap on the selected row is accepted", tap(g, 100, row_y(3)) is True)
    settle(g)
    check("...and opens it", opened == [3], repr(opened))
    tap(g, 100, row_y(1))
    settle(g)
    check("tapping above moves the selection up", g.selected_idx == 1, str(g.selected_idx))


def test_tap_beyond_list_end_is_ignored():
    g = with_peers(3)
    check("a tap on row 5 of a 3-row list does nothing", tap(g, 100, row_y(5)) is False)
    settle(g)
    check("selection unchanged", g.selected_idx == 0)
    g = with_peers(0)
    check("a tap on an empty list does nothing", tap(g, 100, row_y(0)) is False)


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
    check("the zone reaches 8 px above the hint row", tap(g, 4, ui.SEP_Y - 6) is True and seen == [b'a'], repr(seen))
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


def test_drag_and_flick_move_the_selection():
    print("drag")
    g = with_peers(40)
    check("drag 3 steps down", touch_ui.handle(g, (DRAG, 100, 100, 3, 200000), 0) is True)
    settle(g)
    check("selection follows the finger down", g.selected_idx == 3, str(g.selected_idx))
    touch_ui.handle(g, (DRAG, 100, 100, -2, 200000), 0)
    settle(g)
    check("and back up", g.selected_idx == 1, str(g.selected_idx))
    g.selected_idx = 0                # the peer list holds at most MAX_PEERS (16)
    touch_ui.handle(g, (FLICK, 100, 100, 1, 200000), 0)
    settle(g)
    check("a flick moves a page", g.selected_idx == ui.BODY_ROWS - 2, str(g.selected_idx))


def test_stale_layout_guard():
    print("stale layout")
    g = with_peers(6)
    check("a tap whose DOWN predates the layout change is dropped",
          tap(g, 100, row_y(3), t=5000, changed=5001) is False)
    settle(g)
    check("...and selected nothing", g.selected_idx == 0)
    check("a tap after the change counts", tap(g, 100, row_y(3), t=5001, changed=5001) is True)
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
    check("locked: nothing", tap(g, 100, row_y(1)) is False)
    g.locked = False
    g._screen_on = False
    check("dark: nothing", tap(g, 100, row_y(1)) is False)


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
    c.event((DOWN, 100, row_y(2), 1100))
    c.event((UP, 100, row_y(2), 1200))
    settle(g)
    check("a tap through the controller selects and clicks once", g.selected_idx == 2 and clicks == [1], "%d %r" % (g.selected_idx, clicks))
    drawn(g)
    c.service(1250)
    c.event((DOWN, 100, row_y(9), 1300))          # beyond the list end
    c.event((UP, 100, row_y(9), 1400))
    check("a rejected tap gives no feedback", clicks == [1], repr(clicks))
    c.key(2000)
    c.event((DOWN, 100, row_y(4), 2100))
    c.event((UP, 100, row_y(4), 2200))
    settle(g)
    check("a touch right after a key is ignored", g.selected_idx == 2 and clicks == [1])
    del clicks[:]
    c.event((DOWN, 100, 100, 3000))
    for i in range(1, 6):
        c.event((MOVE, 100, 100 + 20 * i, 3000 + 200 * i))
    c.event((UP, 100, 200, 5000))
    check("a drag of several steps clicks once, at its start", clicks == [1], repr(clicks))


def test_controller_stamps_layout_changes():
    g = with_peers(40)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, row_y(3), 1500))          # the finger went down at 1500...
    g.node_scroll = 4                             # ...then the list scrolled under it
    g.dirty = True
    c.service(1600)                               # the controller notices at 1600
    c.event((UP, 100, row_y(3), 1700))
    settle(g)
    check("the tap is dropped: the row under it changed", g.selected_idx == 0, str(g.selected_idx))
    c.event((DOWN, 100, row_y(3), 1800))          # the old frame is STILL on the panel
    c.event((UP, 100, row_y(3), 1900))
    settle(g)
    check("a tap made before the new frame is shown is dropped too", g.selected_idx == 0, str(g.selected_idx))
    drawn(g)                                      # the ~700 ms refresh ends
    c.service(2500)
    c.event((DOWN, 100, row_y(3), 2400))          # this finger went down DURING the refresh
    c.event((UP, 100, row_y(3), 2550))
    settle(g)
    check("...and so is one that went down during the refresh", g.selected_idx == 0, str(g.selected_idx))
    drawn(g)
    c.service(2600)
    c.event((DOWN, 100, row_y(3), 2700))
    c.event((UP, 100, row_y(3), 2800))
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
    c.event((DOWN, 100, row_y(2), 1100))
    g._screen_on = False                          # the screen timed out under the finger
    c.service(1200)
    c.event((UP, 100, row_y(2), 1210))            # the driver closes the stroke
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
        c.event((DOWN, 100, row_y(2), 1100))
        c.event((UP, 100, row_y(2), 1200))
        check("an exception in the UI does not escape", True)
    except Exception as e:
        check("an exception in the UI does not escape", False, repr(e))
    check("it is reported", reports == [("touch", "ui blew up")], repr(reports))
    check("and the stroke is cancelled", c.rec._active is False)
    c.event((DOWN, 100, 100, 3000))               # now fail in the MIDDLE of a stroke
    c.event((MOVE, 100, 140, 3100))               # a drag step -> nav_event -> boom
    check("an error mid-drag cancels the stroke while the finger is still down",
          c.rec._active is False and len(reports) == 2, "%r %r" % (c.rec._active, reports))


# --- Task 5: chat, recording, browser, viewer --------------------------------

def mkchat(lines=40):
    g = mkui(ui.STATE_CHAT)
    g.selected_peer = b"\x01" * 16
    g.cmd_buf = bytearray()
    g.chat_cursor = -1
    g.chat_scroll = 0
    g._build_chat_lines = lambda: [None] * lines
    return g


def test_chat():
    print("chat")
    g = mkchat()
    opened = []
    g._chat_open_cursor_media = lambda: opened.append(g.chat_cursor)
    check("tap line 4", tap(g, 100, row_y(4)) is True)
    settle(g)
    check("the cursor lands on it", g.chat_cursor == 4, str(g.chat_cursor))
    tap(g, 100, row_y(2))
    settle(g)
    check("tap a line above: cursor moves up", g.chat_cursor == 2, str(g.chat_cursor))
    tap(g, 100, row_y(2))
    settle(g)
    check("tap the cursor line: open its media", opened == [2], repr(opened))
    g = mkchat(40)
    check("finger down 3 steps: older messages come into view",
          touch_ui.handle(g, (DRAG, 100, 100, 3, 200000), 0) is True and g.chat_scroll == 3 and g.chat_cursor == -1,
          "%d %d" % (g.chat_scroll, g.chat_cursor))
    touch_ui.handle(g, (DRAG, 100, 100, -5, 200000), 0)
    check("finger up: back toward the newest, clamped at 0", g.chat_scroll == 0, str(g.chat_scroll))
    touch_ui.handle(g, (DRAG, 100, 100, 999, 200000), 0)
    check("clamped at the oldest full window", g.chat_scroll == 40 - (ui.BODY_ROWS - 1), str(g.chat_scroll))
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
    mid = ui.BODY_Y + (ui.BODY_ROWS // 2) * ui.CHAR_H
    check("upper half sends", tap(g, 120, mid - 40) is True and seen == [b'\r'], repr(seen))
    del seen[:]
    check("lower half cancels", tap(g, 120, mid + 40) is True and seen == [b'\x08'], repr(seen))
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

def test_setup():
    print("setup")
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_MAIN
    g._settings_idx = 0
    seen = keys_pressed(g)
    tap(g, 100, row_y(4))
    settle(g)
    check("tap row 4 selects it", g._settings_idx == 4 and seen == [], "%d %r" % (g._settings_idx, seen))
    tap(g, 100, row_y(4))
    settle(g)
    check("tap it again activates it (Enter)", seen == [b'\r'], repr(seen))
    adj = []
    g._settings_adjust = lambda d: adj.append(d)
    check("swipe right on the selected row steps it up",
          touch_ui.handle(g, (SWIPE, 60, row_y(4), 1, 200000), 0) is True)
    settle(g)
    check("...by +1", adj == [1], repr(adj))
    check("a swipe on another row does nothing",
          touch_ui.handle(g, (SWIPE, 60, row_y(9), 1, 200000), 0) is False)
    del seen[:]
    check("edge swipe goes back", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and seen == [b'\x1b'], repr(seen))
    del seen[:]
    g._settings_page = g._TEXT_ENTRY_PAGES[0]
    check("on a text-entry page back does nothing (it would discard the entry)",
          touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is False and seen == [], repr(seen))
    g = mkui(ui.STATE_SETTINGS)
    g._settings_page = ui._SET_MAIN
    g._settings_idx = ui._SET_MAIN_LAST
    top = max(0, g._settings_idx - (ui.BODY_ROWS - 1) + 1)
    tap(g, 100, row_y(0))
    settle(g)
    check("the main list is windowed from the selection, as it is drawn",
          g._settings_idx == top, "%d vs %d" % (g._settings_idx, top))


def test_find():
    print("find")
    g = with_peers(3)
    g._find = True
    g._find_res = [("n%d" % i, bytes([i]) * 16) for i in range(5)]
    g._find_sel = 0
    g._find_scroll = 0
    picked = []
    g._find_pick = lambda: picked.append(g._find_sel)
    fy = ui.BODY_Y + 2 * ui.CHAR_H + ui.CHAR_H // 2          # the first result row
    tap(g, 100, fy + 2 * ui.CHAR_H)
    settle(g)
    check("tap result 2 selects it", g._find_sel == 2 and picked == [], "%d %r" % (g._find_sel, picked))
    tap(g, 100, fy + 2 * ui.CHAR_H)
    settle(g)
    check("tap again picks it", picked == [2], repr(picked))
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
    tap(g, 100, row_y(2))
    settle(g)
    check("menu: tap item 2 selects it", g._shell_menu_idx == 2 and sent == [], "%d %r" % (g._shell_menu_idx, sent))
    tap(g, 100, row_y(2))
    settle(g)
    check("menu: tap again sends it", sent == [2], repr(sent))
    check("menu: back closes it", touch_ui.handle(g, (BACK, 5, 150, 0, 200000), 0) is True and g._shell_menu is False)
    g._shell_menu = True
    last = len(ui._SHELL_CTRL_ITEMS)
    if last < ui.BODY_ROWS - 1:
        check("menu: a tap below the last item closes it", tap(g, 100, row_y(last)) is True and g._shell_menu is False)


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
        check("header tap toggles the room picker", tap(g, 30, ui.BODY_Y + 4) is True)
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
    rrc_ui.panel_click = lambda u: clicked.append(u._rrc_panel_idx)
    try:
        py = rrc_ui.PANEL_Y + 22 + 2 * ui.CHAR_H + ui.CHAR_H // 2
        px = rrc_ui.PANEL_X + 40
        tap(g, px, py)
        settle(g)
        check("panel: tap row 2 selects it", g._rrc_panel_idx == 2 and clicked == [], "%d %r" % (g._rrc_panel_idx, clicked))
        tap(g, px, py)
        settle(g)
        check("panel: tap again acts on it", clicked == [2], repr(clicked))
    finally:
        rrc_ui.panel_click = orig_click
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
          touch_ui.handle(g, (BACK, 5, row_y(4), 0, 200000), 0) is True and adj == [1] and seen == [], "%r %r" % (adj, seen))
    check("Setup: an edge swipe elsewhere is back",
          touch_ui.handle(g, (BACK, 5, row_y(9), 0, 200000), 0) is True and seen == [b'\x1b'], repr(seen))


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
    tap(g, 100, row_y(3))
    settle(g)
    check("WiFi list: row 3 of a list scrolled by 5 is network 8", g._settings_idx == 8, str(g._settings_idx))
    g = with_peers(2)
    g._find = True
    g._find_q = ""
    g._find_res = [("n%d" % i, bytes([i]) * 16) for i in range(30)]
    g._find_scroll = 4
    g._find_sel = 4
    fy = ui.BODY_Y + 2 * ui.CHAR_H + ui.CHAR_H // 2
    tap(g, 100, fy + 2 * ui.CHAR_H)
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
    tap(g, rrc_ui.PANEL_X + 40, rrc_ui.PANEL_Y + 22 + 2 * ui.CHAR_H + 8)
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
        c.event((DOWN, 100, row_y(2), late))
        c.event((UP, 100, row_y(2), (late + 100) & (period - 1)))
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
        c.event((DOWN, 100, row_y(2), 1100))
        c.event((UP, 100, row_y(2), 1200))
        check("a raising feedback does not escape into the key loop", True)
    except Exception as e:
        check("a raising feedback does not escape into the key loop", False, repr(e))
    check("...and is reported", reports == ["touch"], repr(reports))
    g = with_peers(6)
    marks = []
    g.note_user_input = lambda: marks.append(1)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, row_y(2), 1100))
    c.event((UP, 100, row_y(2), 1200))
    check("an accepted gesture marks the redraw as the user's (the e-ink shim pays its ghosting debt on those)",
          marks == [1], repr(marks))
    g = with_peers(6)
    c = touch_ui.Controller(g)
    c.service(1000)
    c.event((DOWN, 100, row_y(2), 1100))
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


if __name__ == "__main__":
    test_geometry_is_the_pros()
    test_hint_zones_follow_the_hints()
    test_hint_key_table()
    test_settings_bar_and_chat_footer_record_zones()
    test_tap_selects_then_opens()
    test_tap_beyond_list_end_is_ignored()
    test_tabs_and_swipe()
    test_footer_taps_press_the_hinted_key()
    test_footer_zones_from_another_state_are_ignored()
    test_navbar_clock_opens_setup()
    test_drag_and_flick_move_the_selection()
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
    test_find_back_keeps_a_typed_query()
    test_untested_lists_follow_their_scroll()
    test_controller_guard_cannot_wedge()
    test_controller_survives_a_week_idle()
    test_controller_feedback_and_dark()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

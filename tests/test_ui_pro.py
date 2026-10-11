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


class _FakeSmall:
    """Stands in for ui._SmallText(spleen_6x12): records instead of blitting."""
    w = 6
    h = 12

    def draw(self, tft, slots, x, y, fg, bg):
        tft.calls.append(("small", bytes(slots).decode("latin-1"), x, y, fg, bg))


def mkui():
    _clock[0] = 100000
    t = RecTFT()
    g = ui.UI(t, object(), lambda: b"\x00", node_name="t", trackball=False)
    g.state = ui.STATE_NODES
    g._screen_on = True
    g._state_change_ms = 0
    # On the Pro ui.py has NO small font: the shim's text() is 8 px only, so
    # ui.SW/SH are 8/16 and ui._sdraw draws the main font. ui_pro brings its
    # own 6x12 through a blit; record that by content. A stray ui._sdraw from
    # ui_pro is recorded as "big" so a test can see it.
    g._sdraw = lambda s, x, y, fg, bg: t.calls.append(("big", s, x, y, fg, bg))
    ui_pro._small = _FakeSmall()
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
          len(info) == 3 and all(c[2] + len(c[1]) * ui_pro.SW == ui_pro.INFO_R for c in info), repr(info[:1]))
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
    check("a two-digit pill leaves 2 px of paper before the name",
          p12 is not None and p12[2] == 1 and p12[2] + 2 * ui_pro.SW + 1 == ui_pro.MARGIN - 2, repr(p12))
    blk = [c for c in t.calls if c[0] == "rect" and c[2] == row_y(1) + 2 and c[4] == ui_pro.SH]
    check("...and its block is 14 px from the panel edge", len(blk) == 1 and blk[0][1] == 0 and blk[0][3] == 14, repr(blk))
    check("the pill is inverse to its row", p3[4] == g.BG_DARK)
    stars = [c for c in t.calls if c[0] == "icon" and c[1] is ui_pro._STAR]
    check("stars on the second line of rows 1 and 2, clear of the row's rule",
          sorted(c[3] for c in stars) == [row_y(1) + 18, row_y(2) + 18]
          and all(c[2] >= 0 and c[2] + 12 <= 16 for c in stars), repr(stars))
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


# --- Task 3: tab bar and toolbar ------------------------------------------------

def test_tab_bar():
    print("tab bar / toolbar")
    g, t = mkui()
    peers(g, 3)
    g.unread[g._peer_keys[0]] = 1
    g.node_tab = ui.TAB_NET
    g._cache = [''] * ui.CACHE_ROWS
    ui_pro.draw_tab_bar(g)
    counts = [c for c in texts(t) if c[3] == ui_pro.TAB_Y + 3]
    check("four counts, MSG marked unread", [c[1] for c in counts] == ["3*", "0", "0", "0"], repr([c[1] for c in counts]))
    check("one icon per tab", len([c for c in t.calls if c[0] == "icon"]) == 4)
    check("each count sits in its own 60 px cell", [c[2] // 60 for c in counts] == [0, 1, 2, 3])
    act = [c for c in counts if c[5] == g.SEL_BG]
    check("the active tab (NET) is the inverted one", len(act) == 1 and act[0][2] // 60 == 1 and act[0][4] == g.BG_DARK, repr(act))
    del t.calls[:]
    ui_pro.draw_tab_bar(g)
    check("cached: an unchanged bar draws nothing", t.calls == [])
    g._draw_tab_bar()
    check("ui._draw_tab_bar hands over (still cached)", t.calls == [])


def test_toolbar():
    g, t = mkui()
    peers(g, 2)
    ui_pro.draw_toolbar(g)
    z = g._hint_zones
    check("four zones with the keys a, s, f, l", [k for _, _, _, k in z] == [b'a', b's', b'f', b'l'], repr(z))
    check("each zone is a full 60 px cell", [(a, b) for _, a, b, _ in z] == [(0, 60), (60, 120), (120, 180), (180, 240)])
    check("zones belong to the node list", all(s == ui.STATE_NODES for s, _, _, _ in z))
    labels = [c[1] for c in texts(t, "small")]
    check("labels", labels == ["Annc", "Setup", "Fav", "Lookup"], repr(labels))
    ul = [c for c in t.calls if c[0] == "rect" and c[4] == 1 and c[2] > ui_pro.TOOL_Y + 10 and c[3] == ui_pro.SW - 1]
    check("the hotkey letter of each label is underlined", len(ul) == 4, repr(ul))
    check("everything is inside the toolbar band",
          all(ui_pro.TOOL_Y <= c[3] < 320 for c in texts(t, "small")))
    del t.calls[:]
    ui_pro.draw_toolbar(g)
    check("cached", t.calls == [])


def test_no_body_frame_on_the_pro_list():
    g, t = mkui()
    peers(g, 2)
    g._draw_frame()
    check("the old body frame is not drawn over the tab bar and toolbar", t.calls == [], repr(t.calls[:2]))
    g.state = ui.STATE_SETTINGS
    g._draw_frame()
    check("other screens keep it", len(t.calls) > 0)


# --- Task 4: top bar --------------------------------------------------------------

def nav(g, t, clock="09:41", valid=True):
    ui._clock_valid = lambda: valid
    ui._fmt_clock = lambda: clock
    g._cache[0] = ''
    del t.calls[:]
    ui_pro.draw_navbar(g)
    return [c for c in texts(t, "small")]


def test_navbar_clock_right_no_name():
    print("top bar")
    g, t = mkui()
    g.node_name = "MyDeck"
    g.bat_v = 3.9
    g.lora_online = True
    g.rssi = -80
    g.snr = 7
    s = nav(g, t)
    strs = [c[1] for c in s]
    check("voltage on the left", "3.9V" in strs)
    check("the node name is not drawn", not any("MyDeck" in x for x in strs), repr(strs))
    clk = [c for c in s if c[1] == "09:41"][0]
    check("the clock ends one character from the right edge",
          clk[2] + 5 * ui_pro.SW == ui.SCREEN_W - ui_pro.SW, repr(clk))
    snr = [c for c in s if c[1] == "snr 7"][0]
    check("radio state sits between the battery and the clock", 4 * ui.CHAR_W + 5 * ui_pro.SW < snr[2] < clk[2], repr(snr))
    s = nav(g, t, valid=False)
    check("no valid clock: nothing on the right", [c[1] for c in s if c[2] > 180] == [], repr([c[1] for c in s]))
    g.announce_flash = _clock[0]
    s = nav(g, t)
    fl = [c for c in s if ">>>" in c[1]]
    check("the announce flash sits just left of the clock", len(fl) == 1 and fl[0][1].endswith("09:41"), repr(fl))
    del t.calls[:]
    g.draw_navbar()
    check("ui.draw_navbar hands over and is cached", t.calls == [], repr(t.calls[:2]))


# --- Task 5: the sheet ------------------------------------------------------------

def test_sheet_items_per_tab():
    print("sheet")
    g, t = mkui()
    peers(g, 3)
    check("MSG: open, favorite, ping, delete",
          [i[0] for i in ui_pro.sheet_items(g)] == ["Open chat", "Favorite", "Ping", "Delete"])
    g.node_tab = ui.TAB_NET
    check("NET: open, favorite, delete",
          [i[0] for i in ui_pro.sheet_items(g)] == ["Open page", "Favorite", "Delete"])
    g.node_tab = ui.TAB_SSH
    check("SSH", ui_pro.sheet_items(g)[0][0] == "Open shell")
    g.node_tab = ui.TAB_RRC
    check("RRC", ui_pro.sheet_items(g)[0][0] == "Open hub")


def test_sheet_open_draw_close():
    g, t = mkui()
    peers(g, 3)
    g.selected_idx = 1
    g.draw_node_list()
    check("opens on a list with rows", ui_pro.sheet_open(g) is True and g._sheet == ui.TAB_MSG and g.dirty)
    y0, n = ui_pro.sheet_rect(g)
    check("four 32 px items, bottom-aligned above the toolbar", n == 4 and y0 == ui_pro.TOOL_Y - 4 * 32, "%d %d" % (y0, n))
    del t.calls[:]
    g.draw_node_list()
    labels = [c for c in texts(t) if c[1] in ("Open chat", "Favorite", "Ping", "Delete")]
    check("items drawn top to bottom at 32 px", [c[3] - (c[3] - y0) % 32 for c in labels] == [y0, y0 + 32, y0 + 64, y0 + 96], repr([c[3] for c in labels]))
    keys = [c[1] for c in texts(t, "small")]
    check("each item names its key", all(k in keys for k in ("Enter", "F", "P", "D")), repr(keys))
    check("the toolbar line explains how to close", any("close" in k for k in keys))
    z = [k for _, _, _, k in g._hint_zones]
    check("the only footer zone left is close", z == [b'\x08'], repr(z))
    del t.calls[:]
    g.draw_node_list()
    check("cached while nothing changes", t.calls == [], repr(t.calls[:2]))
    check("close", ui_pro.sheet_close(g) is True and g._sheet is None)
    del t.calls[:]
    g.draw_node_list()
    check("the rows it covered and the toolbar come back",
          find(t, "small", "Annc") is not None and len([c for c in t.calls if c[0] == "rect" and c[5] == g.BG_DARK and c[4] == 32]) >= 1)
    g2, t2 = mkui()
    check("refuses on an empty list", ui_pro.sheet_open(g2) is False and g2._sheet is None)
    g2._find = True
    check("refuses while Find is open", ui_pro.sheet_open(g2) is False)


def test_sheet_keeps_the_row_visible():
    g, t = mkui()
    peers(g, 14)
    g.selected_idx = 6                      # the 7th visible row: under a 4-item sheet
    g.node_scroll = 0
    ui_pro.sheet_open(g)
    y0, n = ui_pro.sheet_rect(g)
    vis = g.selected_idx - g.node_scroll
    check("the list scrolls so the selected row is the last one above the sheet",
          ui_pro.LIST_Y + (vis + 1) * 32 == y0, "vis %d scroll %d" % (vis, g.node_scroll))
    ui_pro.sheet_close(g)
    check("closing puts the scroll back", g.node_scroll == 0, str(g.node_scroll))
    g.selected_idx = 1
    ui_pro.sheet_open(g)
    check("a row already above the sheet does not scroll", g.node_scroll == 0)


def test_sheet_actions():
    g, t = mkui()
    peers(g, 3)
    g.selected_idx = 2
    seen = []
    clicks = []
    g.handle_key = lambda k: seen.append(bytes(k)) or True
    g.nav_event = lambda n: clicks.append(n)
    ui_pro.sheet_open(g)
    ui_pro.sheet_act(g, 0)
    check("Open is the click, with the sheet already closed", clicks == ["click"] and g._sheet is None, repr(clicks))
    for i, k in ((1, b'f'), (2, b'p'), (3, b'd')):
        ui_pro.sheet_open(g)
        ui_pro.sheet_act(g, i)
        check("item %d presses %r" % (i, k), seen[-1] == k and g._sheet is None, repr(seen))
    ui_pro.sheet_open(g)
    ui_pro.sheet_act(g, 9)
    check("an item index past the end just closes", g._sheet is None and len(seen) == 3)


def test_sheet_keys():
    g, t = mkui()
    peers(g, 3)
    g.selected_idx = 1
    fav = []
    g._favorite = lambda kind=None: fav.append(kind)
    ui_pro.sheet_open(g)
    g.handle_key(b'\x08')
    check("Backspace closes the sheet and does nothing else", g._sheet is None and g.state == ui.STATE_NODES)
    ui_pro.sheet_open(g)
    g.handle_key(b'f')
    check("F closes the sheet and still favourites the row", g._sheet is None and fav == ["peer"], repr(fav))
    ui_pro.sheet_open(g)
    g.nav_event("down")
    g.handle_trackball()
    check("an arrow closes the sheet and moves the selection", g._sheet is None and g.selected_idx == 2, str(g.selected_idx))
    ui_pro.sheet_open(g)
    g._switch_tab(ui.TAB_NET)
    g.draw_node_list()
    check("a tab switch drops the sheet", g._sheet is None)
    g._switch_tab(ui.TAB_MSG)
    ui_pro.sheet_open(g)
    g.state = ui.STATE_SETTINGS
    g.draw()
    check("leaving the list drops the sheet", g._sheet is None)



# --- review fixes -------------------------------------------------------------

def test_small_text_is_ui_pros_own_6x12():
    print("review: the Pro's small font")
    g, t = mkui()
    check("ui.py's own small font on the Pro is the 8x16 main font", g.SW == 8 and g.SH == 16, "%d %d" % (g.SW, g.SH))
    check("ui_pro measures with 6x12", ui_pro.SW == 6 and ui_pro.SH == 12)
    peers(g, 3)
    k = g._peer_keys
    g.peers[k[0]]["hops"] = 12
    g.peers[k[0]]["rssi"] = -102
    g.peers[k[0]]["seen"] = _time.time() - 50 * 60
    g.unread[k[1]] = 12
    g.draw_navbar()
    ui_pro.draw_node_list(g)
    g.selected_idx = 0
    ui_pro.sheet_open(g)
    ui_pro.draw_node_list(g)
    check("nothing in ui_pro goes through ui._sdraw", texts(t, "big") == [], repr(texts(t, "big")[:2]))
    sm = texts(t, "small")
    hsh = find(t, "small", k[0].hex()[:8])
    info = [c for c in sm if "hops" in c[1] and c[3] == row_y(0) + 17]
    check("the longest info line starts right of the hash",
          hsh is not None and len(info) >= 1 and info[0][2] > hsh[2] + 8 * ui_pro.SW, repr(info))
    line2 = [c for c in sm if ui_pro.LIST_Y <= c[3] < ui_pro.TOOL_Y and (c[3] - ui_pro.LIST_Y) % 32 == 17]
    check("the second line ends above the row's rule",
          len(line2) > 0 and all((c[3] - ui_pro.LIST_Y) % 32 + ui_pro.SH <= 31 for c in line2))
    p12 = find(t, "small", "12")
    check("a two-digit pill stays inside the 16 px margin",
          p12 is not None and p12[2] - 1 >= 0 and p12[2] + 2 * ui_pro.SW + 1 <= ui_pro.MARGIN, repr(p12))
    a = find(t, "small", "tap outside to close")
    b = find(t, "small", "(BKSP)close")
    check("the sheet's footer texts do not overlap",
          a is not None and b is not None and a[2] + len(a[1]) * ui_pro.SW < b[2], repr((a, b)))


def _visible(g):
    ia, sa, keys = ui_pro.cur(g)[:3]
    sel, sc = getattr(g, ia), getattr(g, sa)
    return sc <= sel < sc + ui_pro.LIST_ROWS and 0 <= sc <= max(0, len(keys) - ui_pro.LIST_ROWS)


def test_selection_stays_in_the_window():
    print("review: the window follows the selection whoever moved it")
    g, t = mkui()
    peers(g, 12)
    g.node_scroll, g.selected_idx = 4, 11
    g.handle_key(b'd')
    ui_pro.draw_node_list(g)
    check("after a delete at the bottom", _visible(g), "%d %d" % (g.selected_idx, g.node_scroll))
    g, t = mkui()
    peers(g, 12)
    g.node_scroll, g.selected_idx = 0, 10          # what a Find pick or a bubble leaves behind
    ui_pro.draw_node_list(g)
    check("a selection below the window is scrolled to", _visible(g) and g.node_scroll == 3, str(g.node_scroll))
    g.node_scroll, g.selected_idx = 8, 2
    ui_pro.draw_node_list(g)
    check("a selection above the window is scrolled to", _visible(g) and g.node_scroll == 2, str(g.node_scroll))
    g.node_scroll, g.selected_idx = 9, 11
    ui_pro.draw_node_list(g)
    check("the window never runs past the end", g.node_scroll == 4, str(g.node_scroll))
    g.node_scroll, g.selected_idx = 4, 6
    ui_pro.draw_node_list(g)
    check("a visible selection is left where it is", g.node_scroll == 4)
    g.selected_idx = 7
    ui_pro.sheet_open(g)
    sc = g.node_scroll
    ui_pro.draw_node_list(g)
    check("the sheet's own scroll is not fought", g.node_scroll == sc and g._sheet is not None)


def test_find_clears_the_toolbar_band():
    print("review: Find over the Pro list")
    g, t = mkui()
    peers(g, 3)
    ui_pro.draw_node_list(g)
    g.handle_key(b'l')
    del t.calls[:]
    ui_pro.draw_node_list(g)
    band = [c for c in t.calls if c[0] == "rect" and c[1] == 0 and c[2] <= ui_pro.TOOL_Y
            and c[2] + c[4] >= ui.INPUT_Y and c[3] == ui.SCREEN_W and c[5] == g.BG_DARK]
    check("opening Find clears the toolbar's rule and icon tops", len(band) >= 1, repr(t.calls[:6]))
    del t.calls[:]
    ui_pro.draw_node_list(g)
    band = [c for c in t.calls if c[0] == "rect" and c[2] == ui_pro.TOOL_Y]
    check("...once, not on every draw", band == [], repr(band))


def test_lane_repaint_reaches_the_panel_edge():
    g, t = mkui()
    peers(g, 12)
    ui_pro.draw_node_list(g)
    lane = [c for c in t.calls if c[0] == "rect" and c[1] == ui.SBAR_X and c[5] == g.BG_DARK]
    check("the lane's clear runs to x 239 (Find inks its selected row that far)",
          len(lane) == 1 and lane[0][1] + lane[0][3] == ui.SCREEN_W, repr(lane))


def test_icons_have_the_designs_proportions():
    print("review: icons")
    # The canvas draws tab icons in a 14 px square and toolbar / sheet icons
    # in an 11..13 px square. Hand-drawn 7 px tall ones looked squashed.
    for ic in ui_pro._TAB_ICONS:
        check("tab icon %dx%d fills its square" % ic[:2], 13 <= ic[0] <= 14 and 11 <= ic[1] <= 14)
    for ic, label, key in ui_pro._TOOLS:
        check("%s icon %dx%d is square" % ((label,) + ic[:2]), ic[0] == 11 and 10 <= ic[1] <= 11)
    check("the row's star is 12x12", ui_pro._STAR[:2] == (12, 12))
    g, t = mkui()
    for tab in (0, 1, 2, 3):
        g.node_tab = tab
        for label, kl, key, ic in ui_pro.sheet_items(g):
            check("sheet icon for %s (%dx%d)" % ((label,) + ic[:2]), 11 <= ic[0] <= 14 and 9 <= ic[1] <= 14)
    g, t = mkui()
    peers(g, 2)
    ui_pro.draw_tab_bar(g)
    ui_pro.draw_toolbar(g)
    ic = [c for c in t.calls if c[0] == "icon"]
    check("tab icons stay inside the tab bar",
          all(ui_pro.TAB_Y < c[3] and c[3] + c[1][1] < ui_pro.LIST_Y for c in ic if c[3] < ui_pro.LIST_Y))
    check("toolbar icons stay inside the toolbar",
          all(ui_pro.TOOL_Y < c[3] and c[3] + c[1][1] <= ui.SCREEN_H for c in ic if c[3] >= ui_pro.TOOL_Y))

# --- piece B: chat ------------------------------------------------------------

PEER = b"\x07" * 16


def mkchat(name="Alex"):
    g, t = mkui()
    g.add_peer(PEER, name, hops=1)
    g.selected_peer = PEER
    g.state = ui.STATE_CHAT
    g.chat_scroll = 0
    g.chat_cursor = -1
    g._cache = [''] * ui.CACHE_ROWS
    ui._fmt_time = lambda ts: "09:%02d" % (int(ts) // 60 % 60)
    return g, t


def say(g, mine, text, at=0, status=0, image=None, audio=None):
    """Add a message stamped `at` seconds into the fake hour."""
    mid = g.add_chat_message(PEER, mine, text, status=status, image=image, audio=audio)
    h = g.chat_history[PEER]
    m = h[-1]
    h[-1] = (m[0], m[1], at, m[3], m[4], m[5], m[6])
    g._invalidate_chat_lines()
    return mid


def kinds(rows):
    return [r[0] for r in rows]


def test_chat_blocks():
    print("chat: blocks")
    P = ui_pro
    g, t = mkchat()
    say(g, False, "got it, 73", at=60)
    r = g._build_chat_lines()
    check("one short message is one inline row", kinds(r) == [P.K_INLINE], repr(r))
    check("inline: 'Name: text', the time on the side, last of its block",
          r[0][2] == "Alex: got it, 73" and r[0][5] == "09:01" and r[0][4] & P.F_LAST, repr(r[0]))
    g, t = mkchat()
    say(g, False, "hey, are you on the mesh tonight? asking for a friend", at=0)
    r = g._build_chat_lines()
    check("a wrapping message: header + text rows", kinds(r) == [P.K_HDR, P.K_TEXT, P.K_TEXT], repr(kinds(r)))
    check("header is 'Name . time'", r[0][2] == "Alex \xfa 09:00", repr(r[0][2]))
    check("text is full width, no name prefix", r[1][2].startswith("hey, are") and len(r[1][2]) <= P.PEER_COLS)
    check("only the last row closes the block", [x[4] & P.F_LAST for x in r] == [0, 0, 1])
    g, t = mkchat()
    say(g, False, "one", at=0)
    say(g, False, "two", at=30)
    r = g._build_chat_lines()
    check("two one-liners from one sender: one header block",
          kinds(r) == [P.K_HDR, P.K_TEXT, P.K_TEXT] and r[1][2] == "one" and r[2][2] == "two", repr(r))
    say(g, True, "yes", at=40)
    say(g, False, "ok", at=50)
    r = g._build_chat_lines()
    check("a change of sender starts a new block",
          kinds(r) == [P.K_HDR, P.K_TEXT, P.K_TEXT, P.K_INLINE, P.K_INLINE] and r[3][2] == "me: yes" and r[3][1] is True, repr(r))
    check("the first row of each block is flagged (it carries the gap above it)",
          [bool(x[4] & P.F_FIRST) for x in r] == [True, False, False, True, True], repr([x[4] for x in r]))
    g, t = mkchat()
    say(g, False, "one", at=0)
    say(g, False, "two", at=0 + P.BLOCK_GAP + 1)
    check("ten minutes of silence starts a new block too", kinds(g._build_chat_lines()) == [P.K_INLINE, P.K_INLINE])
    g, t = mkchat()
    say(g, False, "a\nb", at=0)
    r = g._build_chat_lines()
    check("typed line breaks are kept", kinds(r) == [P.K_HDR, P.K_TEXT, P.K_TEXT] and [x[2] for x in r[1:]] == ["a", "b"])
    g, t = mkchat("A very long peer name")
    say(g, False, "hi", at=0)
    check("names are cut to 8", g._build_chat_lines()[0][2] == "A very l: hi", repr(g._build_chat_lines()[0][2]))
    g, t = mkchat()
    ui._fmt_time = lambda ts: ""
    say(g, False, "hi", at=0)
    say(g, False, "there", at=1)
    r = g._build_chat_lines()
    check("no time before the clock is set", r[0][2] == "Alex" and kinds(r) == [P.K_HDR, P.K_TEXT, P.K_TEXT], repr(r[0]))


def test_chat_blocks_media_and_status():
    P = ui_pro
    g, t = mkchat()
    say(g, False, "[image 12k] nice shot", at=0, image=b"jpg")
    r = g._build_chat_lines()
    check("image with text: header, tile (2 rows), text",
          kinds(r) == [P.K_HDR, P.K_TOP, P.K_BOT, P.K_TEXT] and r[3][2] == "nice shot", repr(r))
    check("tile rows: title, caption from the marker, image flag",
          r[1][2] == "image" and r[1][5] == "12k" and r[2][5] == "12k" and r[1][4] & P.F_IMG and r[2][4] & P.F_IMG, repr(r[1:3]))
    g, t = mkchat()
    mid = say(g, True, "[voice 3s]", at=0, status=2, audio=b"c2")
    r = g._build_chat_lines()
    check("voice only, mine: header + tile, no text row", kinds(r) == [P.K_HDR, P.K_TOP, P.K_BOT] and r[0][2].startswith("me"), repr(r))
    check("voice caption says how to play it", r[1][5] == "3s - tap to play" and r[1][4] & P.F_VOICE, repr(r[1]))
    check("a text-less message carries its mark on the tile's top row", r[1][6] == 2 and r[2][6] == 0, repr(r[1:]))
    check("rows know their message", all(x[3] == mid for x in r))
    g, t = mkchat()
    say(g, True, "this is my rather long message that will wrap for sure", at=0, status=1)
    r = g._build_chat_lines()
    check("my mark is on the message's last text row only",
          [x[6] for x in r][-1] == 1 and [x[6] for x in r].count(0) == len(r) - 1, repr(r))
    check("my text is narrower", all(len(x[2]) <= P.ME_COLS for x in r if x[0] == P.K_TEXT))
    g, t = mkchat()
    say(g, True, "ok", at=0, status=2)
    r = g._build_chat_lines()
    check("inline row of mine carries the mark", kinds(r) == [P.K_INLINE] and r[0][6] == 2 and r[0][2] == "me: ok", repr(r))
    g, t = mkchat()
    say(g, False, "[image 9k] [voice 2s]", at=0, image=b"j", audio=b"a")
    r = g._build_chat_lines()
    check("image and voice in one message: two tiles, nothing left over",
          kinds(r) == [P.K_HDR, P.K_TOP, P.K_BOT, P.K_TOP, P.K_BOT] and r[1][4] & P.F_IMG and r[3][4] & P.F_VOICE, repr(r))
    g, t = mkchat()
    say(g, False, "[image 12k", at=0, image=b"j")
    r = g._build_chat_lines()
    check("a marker with no closing bracket still makes a tile", kinds(r) == [P.K_HDR, P.K_TOP, P.K_BOT], repr(r))
    g, t = mkchat()
    say(g, False, "hello", at=0)
    a = g._build_chat_lines()
    check("the list is cached until invalidated", g._build_chat_lines() is a)


def crow_y(i):
    return ui_pro.CHAT_Y + 16 * i


def test_chat_header_and_rows():
    print("chat: drawing")
    P = ui_pro
    g, t = mkchat()
    say(g, False, "hey, are you on the mesh tonight? asking for a friend", at=0)   # rows 0..2
    say(g, True, "yes", at=30, status=2)                                           # row 3
    ui_pro.draw_chat(g)
    hd = find(t, "text", "< Alex")
    check("header: '< name' in the normal font (bold was tried on the panel and rejected), in the band under the top bar", hd is not None and 20 < hd[3] and hd[3] + 16 <= 41, repr(hd))
    hs = find(t, "small", "[" + PEER.hex()[:8] + "]")
    check("header: hash in the small font, right-aligned", hs is not None and hs[2] + 10 * P.SW == 232, repr(hs))
    check("header: a rule closes the band", any(c[0] == "rect" and c[2] == 41 and c[3] == 240 and c[4] == 1 for c in t.calls))
    check("a rule separates the rows from the input line", any(c[0] == "rect" and c[2] == 299 and c[3] == 240 for c in t.calls))
    h = find(t, "small", "Alex \xfa 09:00")
    check("block header in the small font at the peer's x", h is not None and h[2] == P.PEER_X and h[3] == crow_y(0) + 2, repr(h))
    tx = [c for c in texts(t) if c[3] == crow_y(1)]
    check("peer text at x 6", len(tx) == 1 and tx[0][2] == P.PEER_X, repr(tx))
    bars = [c for c in t.calls if c[0] == "rect" and c[3] == 2 and c[4] == 16]
    check("peer rows have their bar on the left edge", sorted(c[2] for c in bars if c[1] == 0) == [crow_y(0), crow_y(1), crow_y(2)], repr(bars))
    check("my row has its bar on the right; its block sits a gap lower", [c[2] for c in bars if c[1] == P.ME_BAR_X] == [crow_y(3) + P.GAP], repr(bars))
    check("the gap is 6 px", P.GAP == 6)
    check("the gap above a block is cleared with its first row",
          any(c[0] == "rect" and c[1] == 0 and c[2] == crow_y(3) and c[4] == P.GAP and c[5] == g.BG_DARK for c in t.calls))
    check("the window's first row has no gap above it", not any(c[0] == "rect" and c[2] == crow_y(0) - P.GAP for c in t.calls))
    mine = find(t, "text", "me: yes")
    check("my inline row is indented", mine is not None and mine[2] == P.ME_X and mine[3] == crow_y(3) + P.GAP, repr(mine))
    tm = [c for c in texts(t, "small") if c[1] == "09:00" and c[3] == crow_y(3) + P.GAP + 2]
    check("its time is on the right in the small font", len(tm) == 1 and tm[0][2] + 5 * P.SW == P.ME_X + P.ME_COLS * 8, repr(tm))
    mk = [c for c in texts(t) if c[1] == "\xfb" and c[3] == crow_y(3) + P.GAP]
    check("the delivery mark sits left of the time", len(mk) == 1 and mk[0][2] + 8 <= tm[0][2], repr(mk))
    rules = sorted(c[2] for c in t.calls if c[0] == "rect" and c[4] == 1 and c[1] == 0 and c[3] == ui.SBAR_X and crow_y(0) <= c[2] < 298)
    check("no rules between blocks (tried on the panel: cramped)", rules == [], repr(rules))
    y_end = crow_y(4) + P.GAP
    check("everything below the thread is blanked in one piece",
          any(c[0] == "rect" and c[1] == 0 and c[2] == y_end and c[2] + c[4] == 298 and c[5] == g.BG_DARK for c in t.calls))
    check("the row tops are kept for the touch layer", g._chat_ys == [crow_y(0), crow_y(1), crow_y(2), crow_y(3) + P.GAP], repr(g._chat_ys))
    check("nothing goes through ui._sdraw", texts(t, "big") == [])
    check("there is no bold text path any more", not hasattr(ui_pro, "_bold") and not hasattr(ui_pro, "_Bold"))
    check("every row maps to its message", sorted(g._visible_msg_lines) == [0, 1, 2, 3])
    del t.calls[:]
    ui_pro.draw_chat(g)
    check("a second draw of the same thread draws nothing", t.calls == [], repr(t.calls[:3]))


def test_chat_cursor_and_updates():
    P = ui_pro
    g, t = mkchat()
    mid = say(g, True, "first", at=0, status=1)
    say(g, False, "second", at=10)
    ui_pro.draw_chat(g)
    del t.calls[:]
    g.chat_cursor = 1
    ui_pro.draw_chat(g)
    rows = set((c[3] if c[0] in ("text", "small") else c[2]) for c in t.calls if c[0] in ("text", "small", "rect"))
    check("moving the cursor redraws only that row", all(crow_y(1) <= y < crow_y(2) + P.GAP for y in rows), repr(sorted(rows)))
    sel = find(t, "text", "Alex: second")
    check("the cursor row is inverted", sel is not None and sel[4] == g.BG_DARK and sel[5] == g.SEL_BG, repr(sel))
    del t.calls[:]
    g.chat_cursor = -1
    ui_pro.draw_chat(g)
    del t.calls[:]
    h = g.chat_history[PEER]
    i = g._msg_index(h, mid)
    m = h[i]
    h[i] = (m[0], m[1], m[2], 2, m[4], m[5], m[6])
    g._invalidate_chat_lines()
    ui_pro.draw_chat(g)
    ys = set(c[3] for c in texts(t))
    check("a status change redraws only its own row", ys == {crow_y(0)}, repr(ys))
    check("...with the new mark", find(t, "text", "\xfb") is not None)
    g, t = mkchat()
    for k in range(30):
        say(g, bool(k % 2), "line %d" % k, at=k)
    ui_pro.draw_chat(g)
    shown = [c[1] for c in texts(t) if ": line" in c[1]]
    check("a long thread of one-line blocks shows the eleven that fit with their gaps",
          len(shown) == 11 and shown[-1].endswith("line 29") and g._chat_nvis == 11, "%d %r" % (len(shown), shown[-2:]))
    check("the scroll lane has a thumb", any(c[0] == "rect" and c[1] == ui.SBAR_X and c[5] == g.DIM_CYAN for c in t.calls))
    g.chat_scroll = 99
    ui_pro.draw_chat(g)
    check("the scroll is clamped where the first message reaches the top", g.chat_scroll == 30 - 11, str(g.chat_scroll))
    del t.calls[:]
    g._cache = [''] * ui.CACHE_ROWS
    ui_pro.draw_chat(g)
    shown = [c[1] for c in texts(t) if ": line" in c[1]]
    check("...and the oldest message is on screen there", len(shown) == 11 and shown[0].endswith("line 0"), repr(shown[:2]))
    g.chat_cursor = 10
    g._scroll_down()
    check("the cursor on the last visible row scrolls the view down, though fewer than 16 rows show", g.chat_scroll == 18 and g.chat_cursor == 10,
          "%d %d" % (g.chat_scroll, g.chat_cursor))
    g.chat_cursor = -1
    g._scroll_up()
    check("the cursor wakes on the last visible row", g.chat_cursor == 10, str(g.chat_cursor))
    g.chat_scroll = 0
    check("ui's own scroll limit agrees", g._chat_max_scroll() == 19, str(g._chat_max_scroll()))


def test_no_body_frame_on_the_pro_chat():
    g, t = mkchat()
    say(g, False, "hi", at=0)
    g._prev_state = g.state
    g.draw()
    arms = [c for c in t.calls if c[0] == "rect" and c[3] == 1 and c[4] == 12]
    check("the body frame's corner arms are not drawn on the Pro chat", arms == [], repr(arms))


def test_chat_tiles():
    print("chat: tiles")
    P = ui_pro
    g, t = mkchat()
    mid = say(g, False, "[image 12k] nice", at=0, image=b"jpg")      # rows: hdr, top, bot, text
    vid = say(g, True, "[voice 3s]", at=700, status=2, audio=b"c2")  # rows: hdr, top, bot
    # RecTFT records icons through ui._bitmap only; tiles draw icon runs as rects
    ui_pro.draw_chat(g)
    ti = find(t, "text", "image")
    check("image tile title on its top row, right of the icon", ti is not None and ti[3] == crow_y(1) and ti[2] == P.PEER_X + 32, repr(ti))
    cap = find(t, "small", "12k")
    check("caption in the small font on the bottom row", cap is not None and cap[3] == crow_y(2) and cap[2] == P.PEER_X + 32, repr(cap))
    frame = [c for c in t.calls if c[0] == "rect" and c[3] == P.TILE_W and c[4] == 1 and c[1] == P.PEER_X]
    check("frame: top line on the top row, bottom line inside the bottom row",
          sorted(c[2] for c in frame) == [crow_y(1), crow_y(2) + 13], repr(frame))
    sides = [c for c in t.calls if c[0] == "rect" and c[3] == 1 and c[1] in (P.PEER_X, P.PEER_X + P.TILE_W - 1) and crow_y(1) <= c[2] < crow_y(3)]
    check("frame: both sides on both rows", len(sides) == 4, repr(sides))
    icon = [c for c in t.calls if c[0] == "rect" and c[4] == 1 and P.PEER_X + 6 <= c[1] < P.PEER_X + 26 and c[3] <= 20
            and crow_y(1) < c[2] < crow_y(3) and c[2] not in (crow_y(1), crow_y(2) + 13)]
    check("the icon spans both rows", any(c[2] < crow_y(2) for c in icon) and any(c[2] >= crow_y(2) for c in icon), str(len(icon)))
    vt = find(t, "text", "voice")
    check("my voice tile is indented like my text", vt is not None and vt[2] == P.ME_X + 32 and vt[3] == crow_y(5) + P.GAP, repr(vt))
    check("its caption", find(t, "small", "3s - tap to play") is not None)
    mk = [c for c in texts(t) if c[1] == "\xfb"]
    check("a text-less message shows its mark on the tile's top row", len(mk) == 1 and mk[0][3] == crow_y(5) + P.GAP, repr(mk))
    check("tile rows are in the image map", g._visible_image_lines == {1: mid, 2: mid}, repr(g._visible_image_lines))
    check("...and the audio map", g._visible_audio_lines == {5: vid, 6: vid}, repr(g._visible_audio_lines))
    del t.calls[:]
    g.chat_cursor = 2
    ui_pro.draw_chat(g)
    inv = [c for c in t.calls if c[0] == "rect" and c[4] == 16 and c[5] == g.SEL_BG]
    check("the cursor on one half inverts both halves", sorted(c[2] for c in inv) == [crow_y(1), crow_y(2)], repr(inv))
    opened = []
    g._enter_image_view = lambda m: opened.append(m)
    g._chat_open_cursor_media()
    check("Enter on the bottom half opens the image", opened == [mid], repr(opened))
    g._image_cache.clear()
    del t.calls[:]
    ui_pro.draw_chat(g)
    check("an expired image says so", find(t, "small", "expired") is not None and find(t, "small", "12k") is None)
    check("...and its title is struck through",
          any(c[0] == "rect" and c[4] == 1 and c[1] == P.PEER_X + 32 and c[3] == 5 * 8 and c[2] == crow_y(1) + 8 for c in t.calls))


def test_chat_tile_cut_by_the_window():
    P = ui_pro
    g, t = mkchat()
    mid = say(g, False, "[image 12k]", at=0, image=b"jpg")           # hdr, top, bot
    say(g, True, "\n".join("l%d" % k for k in range(13)), at=1000)  # hdr + 13 text rows
    ui_pro.draw_chat(g)   # 17 rows; 16 + 6 + 14 * 16 = 246 px fit below the tile's top half
    check("only the tile's bottom half is in view", g._visible_image_lines == {0: mid}, repr(g._visible_image_lines))
    check("the half draws its caption and no title", find(t, "small", "12k") is not None and find(t, "text", "image") is None)
    g.chat_cursor = 0
    opened = []
    g._enter_image_view = lambda m: opened.append(m)
    g._chat_open_cursor_media()
    check("and still opens", opened == [mid])
    g.chat_scroll = 1
    del t.calls[:]
    ui_pro.draw_chat(g)
    check("scrolled one row up, both halves are drawn", find(t, "text", "image") is not None and g._visible_image_lines == {0: mid, 1: mid},
          repr(g._visible_image_lines))


def test_recording_screen():
    print("recording")
    P = ui_pro
    g, t = mkchat()
    g.state = ui.STATE_RECORDING
    g._rec_warming = False
    g._rec_warm_secs = 0
    g._cache = [''] * ui.CACHE_ROWS
    ui_pro.draw_recording(g)
    ti = find(t, "text", "* Recording *")
    check("title in the send half", ti is not None and 42 <= ti[3] < 70, repr(ti))
    s = find(t, "text", "tap to SEND")
    c = find(t, "text", "tap to CANCEL")
    check("send above the divider, cancel below", s is not None and c is not None and s[3] + 16 < P.REC_MID < c[3], repr((s, c)))
    check("the divider", any(x[0] == "rect" and x[2] == P.REC_MID and x[4] == 1 and x[3] > 200 for x in t.calls))
    disc = [x for x in t.calls if x[0] == "rect" and x[4] == 1 and x[2] < P.REC_MID and x[3] >= 50]
    check("a filled disc in the send half", len(disc) >= 8, str(len(disc)))
    f = [x for x in texts(t) if "BKSP=cancel" in x[1]]
    check("footer names the Pro's keys", len(f) == 1 and "Enter=send" in f[0][1] and "Esc" not in f[0][1] and f[0][3] == ui.INPUT_Y, repr(f))
    check("no stale footer zones", g._hint_zones == [])
    del t.calls[:]
    ui_pro.draw_recording(g)
    check("painted once: a second draw draws nothing", t.calls == [], repr(t.calls[:2]))
    g._rec_warming = True
    g._rec_warm_secs = 2
    ui_pro.draw_recording(g)
    check("warming: the title counts", find(t, "text", "Warming mic... 2s") is not None)
    check("warming: nothing to send yet", find(t, "text", "tap to SEND") is None and find(t, "text", "tap to CANCEL") is not None)
    del t.calls[:]
    g._rec_warm_secs = 3
    ui_pro.draw_recording(g)
    check("a new second redraws the title only", find(t, "text", "Warming mic... 3s") is not None and find(t, "text", "tap to CANCEL") is None)



def test_chat_status_does_not_reflow():
    print("review: chat reflow / widths")
    P = ui_pro
    for text in ("x" * n for n in range(8, 26)):
        shapes = []
        for st in (1, 2, 3, 4, 5, 0):
            g, t = mkchat()
            say(g, True, text, at=0, status=st)
            shapes.append(kinds(g._build_chat_lines()))
        if any(s != shapes[0] for s in shapes):
            check("a %d-char message keeps its rows whatever its status" % len(text), False, repr(shapes))
            break
    else:
        check("my message keeps its rows whatever its delivery status", True)
    g, t = mkchat()
    say(g, True, "w" * 60, at=0, status=2)
    r = [x for x in g._build_chat_lines() if x[0] == P.K_TEXT]
    check("my text wraps inside my column, two columns kept for the mark",
          max(len(x[2]) for x in r) == P.ME_COLS - 3, repr([len(x[2]) for x in r]))
    g, t = mkchat()
    say(g, False, "w" * 60, at=0)
    r = [x for x in g._build_chat_lines() if x[0] == P.K_TEXT]
    check("the peer's text uses its full column", max(len(x[2]) for x in r) == P.PEER_COLS, repr([len(x[2]) for x in r]))
    for n in range(10, 24):
        g, t = mkchat()
        say(g, True, "y" * n, at=0, status=2)
        r = g._build_chat_lines()
        if r[0][0] == P.K_INLINE:
            px = P.ME_X + len(r[0][2]) * 8
            limit = P.ME_X + P.ME_COLS * 8 - len(r[0][5]) * P.SW - 4 - 2 * 8 - 4
            if px > limit:
                check("an inline row leaves room for the mark and the time", False, "%d chars: %d > %d" % (n, px, limit))
                break
    else:
        check("an inline row leaves room for the mark and the time", True)

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
    test_tab_bar()
    test_toolbar()
    test_no_body_frame_on_the_pro_list()
    test_navbar_clock_right_no_name()
    test_sheet_items_per_tab()
    test_sheet_open_draw_close()
    test_sheet_keeps_the_row_visible()
    test_sheet_actions()
    test_sheet_keys()
    test_small_text_is_ui_pros_own_6x12()
    test_selection_stays_in_the_window()
    test_find_clears_the_toolbar_band()
    test_lane_repaint_reaches_the_panel_edge()
    test_icons_have_the_designs_proportions()
    test_chat_blocks()
    test_chat_blocks_media_and_status()
    test_chat_header_and_rows()
    test_chat_cursor_and_updates()
    test_no_body_frame_on_the_pro_chat()
    test_chat_tiles()
    test_chat_tile_cut_by_the_window()
    test_recording_screen()
    test_chat_status_does_not_reflow()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

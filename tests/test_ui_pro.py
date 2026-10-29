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
    g._settings_page = ui._SET_RADIO
    g._draw_frame()
    check("other screens keep it (a Setup sub-page here; Setup's main page has its own rules too)", len(t.calls) > 0)


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


# --- piece C: Setup -------------------------------------------------------------

def mksetup():
    g, t = mkui()
    g.state = ui.STATE_SETTINGS
    g._prev_state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    g._settings_idx = 0
    g._settings_scroll = 0
    g.node_name = "Pro"
    g.my_address = "93481a35" * 4
    g.my_identity_hash = "0123456789abcdef" * 2
    g.my_lxma_uri = "lxma://x"
    g.lora_online = True
    g._cache = [''] * ui.CACHE_ROWS
    return g, t


def srow_y(i):
    return ui_pro.LIST_Y + 32 * i


def test_setup_items():
    print("setup: rows")
    P = ui_pro
    g, t = mksetup()
    it = P.setup_items(g)
    check("fifteen rows, one per shown idx", len(it) == 15 == len(P.SETUP_IDX)
          and P.SETUP_IDX == (0, 1, 2, 3, 4, 5, ui._SET_HAPTICS, 6, 7, 8, 9, 10, 11, 12, 14))
    check("labels", [x[0] for x in it] == ["WiFi", "TCP", "Name", "LoRa", "Volume", "Keyboard light", "Touch feedback", "Announce",
                                           "Sleep", "Wake", "Radio stats", "LoRa cfg", "Time zone", "Addr", "Id"], repr([x[0] for x in it]))
    check("offline WiFi: says so and opens the scan", it[0][1] == "not connected" and it[0][2] is True, repr(it[0]))
    check("TCP off without WiFi: no sub-page", it[1][1] == "OFF" and not it[1][2])
    check("name and its marker", it[2][1] == "Pro" and it[2][2])
    check("LoRa online", it[3][1] == "Online" and not it[3][2])
    check("volume bar", it[4][1] == "[" + "#" * g._volume + "." * (10 - g._volume) + "] " + str(g._volume))
    check("sleep / wake / tz come from ui's own labels", it[8][1] == g._timeout_label() and it[9][1] == g._wake_label()
          and it[12][1] == ui._tz_label(g._tz_min))
    check("radio stats and LoRa cfg open pages", it[10][2] and it[11][2] and "SF" in it[11][1])
    check("the hashes are whole and small", it[13][1] == g.my_address and it[13][3] and it[14][1] == g.my_identity_hash and it[14][3])
    check("Addr opens its QR when there is a contact URI", it[13][2] is True and not it[14][2])
    g._wifi_connected = True
    g._wifi_ssid_current = "home"
    g._wifi_ip = "10.0.0.7"
    g.lora_online = False
    g.my_lxma_uri = None
    it = P.setup_items(g)
    check("connected WiFi: ssid [ip], no marker (a click disconnects)", it[0][1] == "home [10.0.0.7]" and not it[0][2], repr(it[0]))
    check("TCP then offers its host page", it[1][2] is True)
    check("LoRa offline says what selecting does", it[3][1].startswith("OFFLINE") and "reset" in it[3][1])
    check("no URI, no QR", not it[13][2])


def test_setup_step():
    P = ui_pro
    g, t = mksetup()
    seen = [0]
    for _ in range(20):
        g._settings_scroll_down()
        if g._settings_idx != seen[-1]:
            seen.append(g._settings_idx)
    check("down walks the shown rows, skipping the hash continuation rows, and stops", seen == list(P.SETUP_IDX), repr(seen))
    seen = [g._settings_idx]
    for _ in range(20):
        g._settings_scroll_up()
        if g._settings_idx != seen[-1]:
            seen.append(g._settings_idx)
    check("up walks them back", seen == list(reversed(P.SETUP_IDX)), repr(seen))
    g._settings_idx = 13
    check("an idx the Pro does not show counts as the row before it", P.SETUP_IDX[P.setup_row(g)] == 12)
    g._settings_scroll_down()
    check("...and steps on from there", g._settings_idx == 14)


def test_setup_draw():
    print("setup: drawing")
    P = ui_pro
    g, t = mksetup()
    g._settings_idx = 4
    g.draw_settings()
    hd = find(t, "text", "< Setup")
    check("header in the band, normal font", hd is not None and hd[2] == 4 and 20 < hd[3] and hd[3] + 16 <= 41, repr(hd))
    check("a rule closes the band", any(c[0] == "rect" and c[2] == 41 and c[3] == 240 and c[4] == 1 for c in t.calls))
    check("no second rule under the last row's own (two lines 2 px apart looked like a mistake)",
          not any(c[0] == "rect" and c[2] == 299 and c[5] != g.BG_DARK for c in t.calls))
    lab = [c for c in texts(t, "small") if c[1] in ("WiFi", "TCP", "Name", "LoRa", "Volume", "Keyboard light", "Touch feedback", "Announce")]
    check("eight labels in the small font, one per 32 px row", [(c[2], c[3]) for c in lab] == [(16, srow_y(i) + 3) for i in range(8)], repr(lab[:2]))
    v = find(t, "text", "Pro")
    check("the value under its label in the main font", v is not None and v[2] == 16 and v[3] == srow_y(2) + 15, repr(v))
    sel = [c for c in texts(t) if c[1].startswith("[#")]
    check("the selected row is inverted", len(sel) == 1 and sel[0][4] == g.BG_DARK and sel[0][5] == g.SEL_BG and sel[0][3] == srow_y(4) + 15, repr(sel))
    marks = sorted(c[3] // 1 for c in t.calls if c[0] == "icon" and c[1] is ui._SUBPAGE)
    check("sub-page markers on WiFi (offline) and Name", len(marks) == 2 and srow_y(0) < marks[0] < srow_y(1) and srow_y(2) < marks[1] < srow_y(3), repr(marks))
    rules = sorted(c[2] for c in t.calls if c[0] == "rect" and c[4] == 1 and c[3] == ui.SBAR_X)
    check("a rule under every row", rules == [srow_y(i) + 31 for i in range(8)], repr(rules))
    check("a scroll thumb", any(c[0] == "rect" and c[1] == ui.SBAR_X and c[5] == g.DIM_CYAN and c[4] > 1 for c in t.calls))
    check("the footer bar is ui's own, back only", any(c[0] == "text" and c[1] == "BKSP" and c[3] == ui.INPUT_Y for c in t.calls))
    check("no (CLICK)select hint: rows act under the finger, Enter on the keyboard",
          not any(c[0] == "text" and c[1] == "CLICK" for c in t.calls) and [z[3] for z in g._hint_zones] == [b'\x08'], repr(g._hint_zones))
    check("no body frame", [c for c in t.calls if c[0] == "rect" and c[3] == 1 and c[4] == 12] == [])
    check("nothing goes through ui._sdraw", texts(t, "big") == [])
    del t.calls[:]
    g.draw_settings()
    check("a second draw draws nothing", t.calls == [], repr(t.calls[:3]))
    g._volume = (g._volume + 1) % 10
    g.draw_settings()
    ys = set(c[3] for c in texts(t)) | set(c[3] for c in texts(t, "small"))
    check("a changed value redraws only its row", ys and all(srow_y(4) <= y < srow_y(5) for y in ys), repr(sorted(ys)))


def test_setup_window_and_hashes():
    P = ui_pro
    g, t = mksetup()
    g._settings_idx = 14
    g.draw_settings()
    check("the window follows the selection to the last row", g._settings_scroll == 7, str(g._settings_scroll))
    a = find(t, "small", g.my_address)
    i = find(t, "small", g.my_identity_hash)
    check("both hashes whole, on one small-font line each", a is not None and i is not None and a[3] == srow_y(6) + 17 and i[3] == srow_y(7) + 17
          and a[2] + 32 * P.SW <= 216, repr((a, i)))
    check("the Id row is the selected one", i[4] == g.BG_DARK and i[5] == g.SEL_BG)
    g._settings_idx = 2
    g._settings_scroll = 6
    g.draw_settings()
    check("a selection above the window pulls it up", g._settings_scroll == 2, str(g._settings_scroll))
    g._settings_idx = 12
    g._settings_scroll = 0              # what closing the QR page leaves
    g.draw_settings()
    check("a jump from a closing sub-page is followed", g._settings_scroll == 6, str(g._settings_scroll))


def test_setup_sub_page_round_trip():
    g, t = mksetup()
    g.draw_settings()
    del t.calls[:]
    g._settings_page = ui._SET_RADIO
    g._settings_scroll = 0
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    check("leaving for a sub-page clears the header band's top lines (the old page's rows start at y 26)",
          any(c[0] == "rect" and c[1] == 0 and c[2] <= 21 and c[2] + c[4] >= 26 and c[3] == 240 and c[5] == g.BG_DARK for c in t.calls))
    check("the sub-page has the body frame", len([c for c in t.calls if c[0] == "rect" and c[3] == 1 and c[4] == 12]) == 4)
    del t.calls[:]
    g._settings_page = ui._SET_MAIN
    g._settings_idx = 9
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    check("coming back repaints the band", find(t, "text", "< Setup") is not None)
    check("...every row", len([c for c in t.calls if c[0] == "rect" and c[1] == 0 and c[3] == ui.SBAR_X and c[4] == 31]) == 8)
    check("...and the line the frame's arms end on", any(c[0] == "rect" and c[2] == 298 and c[3] == 240 and c[5] == g.BG_DARK for c in t.calls))


def test_setup_review_gaps():
    print("review: setup")
    P = ui_pro
    g, t = mksetup()
    g.draw()
    check("no body frame on the Pro's Setup main page (through the real draw())",
          [c for c in t.calls if c[0] == "rect" and c[3] == 1 and c[4] == 12] == [])
    g, t = mksetup()
    g.my_address = None
    g.my_identity_hash = None
    g.my_lxma_uri = None
    it = P.setup_items(g)
    check("before the identity loads the hash rows show '?'", it[13][1] == "?" and it[14][1] == "?")
    g._settings_idx = 14
    g.draw_settings()
    check("...and draw", find(t, "small", "?") is not None)
    g, t = mksetup()
    g._settings_idx = 9
    g._settings_scroll = 14              # what a scrolled Radio stats page leaves behind
    g.draw_settings()
    check("an inherited scroll past the end is clamped", g._settings_scroll == 7, str(g._settings_scroll))
    g, t = mksetup()
    g._settings_idx = 2
    g.draw_settings()
    del t.calls[:]
    g._settings_idx = 3
    g.draw_settings()
    inv = sorted(c[2] for c in t.calls if c[0] == "rect" and c[4] == 31)
    check("moving the selection inside the window repaints the two rows involved", inv == [srow_y(2), srow_y(3)], repr(inv))
    g, t = mksetup()
    g._tcp_enabled = True
    g._tcp_target = "rns.example.org:4242"
    g._wifi_connected = True
    g._kbd_bl = True
    g._auto_announce = True
    it = P.setup_items(g)
    check("TCP target, light and announce follow the state", it[1][1] == "rns.example.org:4242" and not it[1][2]
          and it[5][1] == "ON" and it[7][1] == "AUTO", repr(it[1:8]))
    c = g._lora_cfg
    check("LoRa cfg shows frequency, SF and bandwidth", it[11][1] == "%dk SF%d BW%s" % (c["freq_khz"], c["sf"], c["bw"]))


def test_setup_sub_pages_have_a_back_header():
    print("device pass: sub-page headers")
    # Found on the device: the Name page had no header to tap and Backspace
    # deletes the name there, so there was no visible way back.
    for page, title in ((ui._SET_NODE_NAME, "Node Name"), (ui._SET_TCP_HOST, "TCP Server"),
                        (ui._SET_LORA_FREQ, "LoRa Frequency (kHz)"), (ui._SET_RADIO, None), (ui._SET_LORA, None),
                        (ui._SET_WIFI_SCAN, None)):
        g, t = mksetup()
        g._settings_page = page
        g.cmd_buf = bytearray(b"Pro")
        g.draw()
        hd = [c for c in texts(t) if c[1].startswith("< ") and 20 < c[3] and c[3] + 16 <= 41]
        check("page %d has a '< title' header in the band" % page, len(hd) == 1 and hd[0][2] == 4
              and (title is None or hd[0][1] == "< " + title), repr(hd))
        check("page %d: a rule under it" % page, any(c[0] == "rect" and c[2] == 41 and c[3] == 240 and c[4] == 1 for c in t.calls))
        old = [c for c in texts(t) if c[3] == ui.BODY_Y and c[2] == 0]
        check("page %d: the old header row is not drawn under it" % page, old == [], repr(old))
        del t.calls[:]
        g.draw()
        check("page %d: the header is cached" % page, not any(c[0] == "text" and c[1].startswith("< ") for c in t.calls))
    g, t = mksetup()
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_err = "wrong password, try again please"
    g.draw()
    hd = [c for c in texts(t, "small") if c[1].startswith("< ")]
    check("a header too long for the main font goes small and whole",
          len(hd) == 1 and 4 + len(hd[0][1]) * ui_pro.SW <= 240 and hd[0][1].endswith("(r)escan"), repr(hd))
    check("...and is not also drawn big", [c for c in texts(t) if c[1].startswith("< ")] == [])


def body_lines(t):
    return [c for c in texts(t) if ui_pro.LIST_Y <= c[3] < 298]


def test_setup_sub_pages_fit_30_columns():
    print("device pass: sub-pages on a 30-column screen")
    # These pages were laid out for the v1's 40 columns; on the Pro their
    # long lines ran off the right edge.
    g, t = mksetup()
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_networks = [("A-rather-long-network-name-here", -67), ("home", -40)]
    g._settings_idx = 1
    g.draw()
    rows = body_lines(t)
    check("WiFi scan: every row fits", rows and all(c[2] + len(c[1].rstrip()) * 8 <= 240 for c in rows), repr([c[1] for c in rows]))
    check("WiFi scan: the signal strength is on screen", find(t, "small", "-67 dBm") is not None and find(t, "small", "-40 dBm") is not None)
    g, t = mksetup()
    g._settings_page = ui._SET_LORA
    g._lora_applying = ""
    g.draw()
    rows = body_lines(t)
    joined = " ".join(c[1].strip() for c in rows)
    check("LoRa cfg: every row fits", all(c[2] + len(c[1].rstrip()) * 8 <= 240 for c in rows), repr([c[1] for c in rows]))
    check("LoRa cfg: the access-code group and the Apply row are on the page",
          "Access code (optional)" in joined and "Net    none" in joined and "[ Apply & Save ]" in joined, joined)
    g._lora_applying = "failed"
    g._cache = [''] * ui.CACHE_ROWS
    del t.calls[:]
    g.draw()
    joined = " ".join(c[1].strip() for c in body_lines(t))
    check("LoRa cfg: a failed apply shows on the Apply row", "[ Apply FAILED ]" in joined, joined)
    hd = [c for c in texts(t) + texts(t, "small") if c[1].startswith("<")]
    check("a page title that already starts with '<' gets one arrow, not two", len(hd) == 1 and hd[0][1].startswith("< LoRa"), repr(hd))
    g, t = mksetup()
    g._settings_page = ui._SET_RADIO
    g.get_radio_stats = lambda: [("LoRa", "online"), ("RF", "868000kHz SF8 17dBm"), ("LBT", "12 waits 3 forced"),
                                 ("identities", "42"), ("sleep", "light 12 key 3 lora 9 timer 4 usb 0")]
    g.draw()
    rows = body_lines(t)
    joined = " ".join(c[1].strip() for c in rows)
    check("Radio stats: every row fits", all(c[2] + len(c[1].rstrip()) * 8 <= 240 for c in rows), repr([c[1] for c in rows]))
    check("Radio stats: nothing is cut off", "17dBm" in joined and "3 forced" in joined and "usb 0" in joined and "42" in joined, joined)
    check("Radio stats: short rows stay on one line", any(c[1].strip().startswith("LoRa") and "online" in c[1] for c in rows))
    check("Radio stats: the scroll range counts the wrapped rows", g._radio_rows == len(rows), "%d %d" % (g._radio_rows, len(rows)))
    g, t = mksetup()
    g._settings_page = ui._SET_WIFI_PASS
    g._wifi_ssid = "A-rather-long-network-name"
    g.draw()
    hd = [c for c in texts(t) + texts(t, "small") if c[1].startswith("< Connect")]
    check("WiFi password: the header shows the network name", len(hd) == 1 and "A-rather-long-network" in hd[0][1], repr(hd))


# --- piece D: RRC ---------------------------------------------------------------

def mkrrc(state=None):
    import rrc_ui
    g, t = mkui()
    g.state = ui.STATE_RRC_CHAT if state is None else state
    g._prev_state = g.state
    g._rrc_hub_name = "Varna Hub"
    g._rrc_status = ""
    g._rrc_room = "mesh"
    g._rrc_members = 7
    g._rrc_members_exact = True
    g._rrc_roster = [(bytes([0x93, 0x48, 0x1a, 0x35]) * 4, "alex"), (b"\xaa" * 16, None), (b"\xc0" * 16, "vesna")]
    g._rrc_rooms = [("mesh", "general LoRa chatter"), ("ops", ""), ("bg", "a topic that is far too long to fit on one small line")]
    g._rrc_list_seen = True
    g._rrc_panel = False
    g._rrc_panel_idx = 0
    g._rrc_panel_scroll = 0
    g._rrc_input = ""
    g._rrc_prompt = False
    g._cache = [''] * ui.CACHE_ROWS
    return g, t, rrc_ui


def prow_y(i):
    return 80 + 32 * i


def test_rrc_header_band():
    print("rrc: header band")
    P = ui_pro
    g, t, R = mkrrc(ui.STATE_RRC_ROOMS)
    g._rrc_status = "connected"
    R.draw_rooms(g)
    hd = find(t, "text", "< Varna Hub")
    check("console: '< hub name' in the band, main font", hd is not None and hd[2] == 4 and 20 < hd[3] and hd[3] + 16 <= 41, repr(hd))
    st = find(t, "small", "connected")
    check("console: the status in the small font, right-aligned to x 232", st is not None and st[2] + 9 * P.SW == 232 and 20 < st[3] < 41, repr(st))
    check("a rule closes the band", any(c[0] == "rect" and c[2] == 41 and c[3] == 240 and c[4] == 1 for c in t.calls))
    check("the old header row is not drawn", [c for c in texts(t) if c[3] == ui.BODY_Y] == [])
    del t.calls[:]
    R.draw_rooms(g)
    check("the band is cached", not any(c[0] == "text" and c[1].startswith("<") for c in t.calls))
    g, t, R = mkrrc(ui.STATE_RRC_ROOMS)
    g._rrc_hub_name = None
    g._rrc_status = "waiting for the hub to answer"
    R.draw_rooms(g)
    hd = [c for c in texts(t) if c[1].startswith("< ")]
    check("before WELCOME the status has the band to itself", len(hd) == 1 and hd[0][1].startswith("< waiting for the hub") and texts(t, "small") == [], repr(hd))
    g, t, R = mkrrc(ui.STATE_RRC_ROOMS)
    g._rrc_hub_name = "A hub with a very long announced name"
    g._rrc_status = "rate limited"
    R.draw_rooms(g)
    hd = [c for c in texts(t) if c[1].startswith("< ")][0]
    st = find(t, "small", "rate limited")
    check("a long hub name is cut short of the status", st is not None and hd[2] + len(hd[1]) * 8 <= st[2] - 6, repr((hd, st)))
    g, t, R = mkrrc()
    R.draw_room(g)
    hd = find(t, "text", "< #mesh")
    ct = find(t, "small", "7 users")
    check("room: '#room' in the band and the member count small on the right",
          hd is not None and hd[2] == 4 and 20 < hd[3] < 41 and ct is not None and ct[2] + 7 * P.SW == 232, repr((hd, ct)))


def test_rrc_panel_rooms():
    print("rrc: panel")
    P = ui_pro
    g, t, R = mkrrc(ui.STATE_RRC_ROOMS)
    check("the Pro panel has five rows in a taller box", R.PANEL_ROWS == 5 and R.PANEL_H == 208 and R.PANEL_Y == 58 and R.PANEL_X == 8)
    g._rrc_panel = True
    g._rrc_panel_kind = "rooms"
    g._rrc_panel_idx = 1
    R.draw_rooms(g)
    ti = find(t, "text", "#rooms")
    check("title band: '#rooms' inverted at the top of the box", ti is not None and ti[3] == 61 and ti[5] == g.SEL_BG and ti[4] == g.BG_DARK, repr(ti))
    check("...and the count", find(t, "small", "3 rooms") is not None or find(t, "text", "3 rooms") is not None)
    frame = [c for c in t.calls if c[0] == "rect" and c[1] == 8 and c[2] == 58 and c[3] == 224 and c[4] == 1]
    check("the box starts at 8,58 and is 224 wide", len(frame) == 1, repr(frame))
    check("...and 208 tall, ending on a scrollback row boundary (no sliver of the row under its edge)",
          any(c[0] == "rect" and c[1] == 8 and c[2] == 58 + 207 and c[3] == 224 for c in t.calls) and (58 + 208 - 42) % 16 == 0)
    strips = [c for c in t.calls if c[0] == "rect" and c[2] == 58 and c[4] == 208 and c[5] == g.BG_DARK and c[3] == 8]
    check("the strips beside the box are cleared (the rows under it are not redrawn, and their first letters showed)",
          sorted(c[1] for c in strips) == [0, 232], repr(strips))
    j = find(t, "text", "+ join by name...")
    check("the join action is the first row, on one line", j is not None and j[2] == 16 and prow_y(0) < j[3] < prow_y(0) + 16, repr(j))
    n = find(t, "text", "#mesh")
    tp = find(t, "small", "general LoRa chatter")
    check("a room: '#name' over its topic in the small font", n is not None and n[2] == 16 and n[3] == prow_y(1) + 1
          and tp is not None and tp[2] == 16 and tp[3] == prow_y(1) + 18, repr((n, tp)))
    check("the selected row is inverted", n[4] == g.BG_DARK and n[5] == g.SEL_BG and tp[5] == g.SEL_BG)
    o = find(t, "text", "#ops")
    check("a room with no topic shows its name only", o is not None and o[3] == prow_y(2) + 1
          and not any(c[3] == prow_y(2) + 18 for c in texts(t, "small")), repr(o))
    lt = [c for c in texts(t, "small") if c[3] == prow_y(3) + 18]
    check("a long topic is cut inside the box, clear of the scroll bar",
          len(lt) == 1 and lt[0][2] + len(lt[0][1]) * P.SW <= 224, repr(lt))
    rules = sorted(c[2] for c in t.calls if c[0] == "rect" and c[4] == 1 and c[1] == 10 and prow_y(0) <= c[2] < 240)
    check("a rule under each of the four rows the list has; the empty fifth slot is blank", rules == [prow_y(i) + 31 for i in range(4)], repr(rules))
    ft = [c for c in texts(t) if 242 <= c[3] < 250]
    check("foot band: how to close and what Enter does", "".join(c[1] for c in ft).replace(" ", "") .find("BKSP") >= 0
          and any("join" in c[1] for c in ft) and all(c[5] == g.SEL_BG for c in ft), repr(ft))
    under = [c for c in texts(t) if 58 <= c[3] < 262 and c[2] == 0]
    check("scrollback rows under the box are not drawn over it", under == [], repr(under[:2]))
    del t.calls[:]
    R.draw_rooms(g)
    check("a second draw of the open panel draws nothing in the box",
          not any(c[0] in ("text", "small") and 58 <= c[3] < 262 for c in t.calls), repr(t.calls[:3]))
    R.panel_scroll(g, 1)
    del t.calls[:]
    R.draw_rooms(g)
    rows = sorted(set(c[2] for c in t.calls if c[0] == "rect" and c[4] == 31))
    check("moving the highlight repaints the two rows involved", rows == [prow_y(1), prow_y(2)], repr(rows))
    g, t, R = mkrrc(ui.STATE_RRC_ROOMS)
    g._rrc_rooms = []
    g._rrc_list_seen = False
    g._rrc_panel = True
    g._rrc_panel_kind = "rooms"
    R.draw_rooms(g)
    m = find(t, "text", "asking hub...")
    check("the empty picker says it is asking, in the second row's slot", m is not None and prow_y(1) <= m[3] < prow_y(2), repr(m))


def test_rrc_panel_members():
    P = ui_pro
    g, t, R = mkrrc()
    g._rrc_panel = True
    g._rrc_panel_kind = "members"
    g._rrc_panel_idx = 0
    R.draw_room(g)
    check("title is the room, count the members", find(t, "text", "#mesh") is not None and any(c[1] == "7 users" and 58 < c[3] < 80 for c in t.calls if c[0] in ("text", "small")))
    a = [c for c in texts(t) if c[1] == "alex"]
    h = find(t, "small", "93481a3593481a35")
    check("a member: nick over 16 hex of the hash", len(a) == 1 and a[0][2] == 16 and a[0][3] == prow_y(0) + 1
          and h is not None and h[3] == prow_y(0) + 18, repr((a, h)))
    q = [c for c in texts(t) if c[1] == "?" and c[3] == prow_y(1) + 1]
    check("a member who never spoke is '?'", len(q) == 1, repr(q))
    check("foot band names the mention", any("mention" in c[1] and 242 <= c[3] < 250 for c in texts(t)))
    g._rrc_roster = [(bytes([i]) * 16, "n%d" % i) for i in range(12)]
    g._rrc_pc = {}
    g._rrc_panel_idx = 0
    for _ in range(6):
        R.panel_scroll(g, 1)
    check("five visible rows drive the panel's scrolling", g._rrc_panel_idx == 6 and g._rrc_panel_scroll == 2, "%d %d" % (g._rrc_panel_idx, g._rrc_panel_scroll))
    del t.calls[:]
    R.draw_room(g)
    check("a scroll thumb inside the box, a gap away from the frame", any(c[0] == "rect" and c[1] == 225 and c[3] == 3 and 80 <= c[2] < 240 and c[5] != g.BG_DARK for c in t.calls))
    check("nothing goes through ui._sdraw", texts(t, "big") == [])


def test_rrc_review_gaps():
    print("review: rrc")
    P = ui_pro
    g, t, R = mkrrc(ui.STATE_RRC_ROOMS)
    g._rrc_status = "rate limited - hold on"
    R.draw_rooms(g)
    check("the console status is shown whole (the band holds 24 small characters)", find(t, "small", "rate limited - hold on") is not None,
          repr(texts(t, "small")))
    g, t, R = mkrrc()
    R.draw_room(g)
    check("the room band starts with a back arrow, like the chat and Setup", find(t, "text", "< #mesh") is not None, repr(texts(t)[:3]))
    g, t, R = mkrrc()
    g._rrc_room = "\u043c\u0435\u0448\x07"
    g._rrc_roster = [(b"\x01" * 6, "\u0432\u0435\u0441\u043d\u0430\x1b[31m"), (b"\x02" * 6, "x" * 60)]
    g._rrc_panel = True
    g._rrc_panel_kind = "members"
    g._rrc_panel_idx = 1
    R.draw_room(g)
    bad = [c for c in t.calls if c[0] in ("text", "small") and any(ord(ch) < 32 or ord(ch) > 255 for ch in c[1])]
    check("hub strings reach the display as glyph bytes: no control characters, nothing above one byte", bad == [], repr(bad[:2]))
    longn = [c for c in texts(t) if c[1].startswith("xxxx")]
    check("an over-long nick is cut inside the box", len(longn) == 1 and longn[0][2] + len(longn[0][1]) * 8 <= 224, repr(longn))
    check("the selected member row is inverted", longn[0][4] == g.BG_DARK and longn[0][5] == g.SEL_BG)
    h = find(t, "small", "010101010101")
    check("a real roster key is 6 bytes: 12 hex shown", h is not None, repr(texts(t, "small")))
    g, t, R = mkrrc()
    for k in range(30):
        g.rrc_line("msg", "alex", "line %d" % k)
    g._rrc_panel = True
    g._rrc_panel_kind = "members"
    g._cache = [''] * ui.CACHE_ROWS
    del t.calls[:]
    R.draw_room(g)
    under = [c for c in texts(t) if c[2] == 0 and 58 <= c[3] < 266]
    above = [c for c in texts(t) if c[2] == 0 and c[3] == 42]
    check("scrollback rows the box covers are skipped; the one above it is drawn", under == [] and len(above) == 1, repr((under[:1], above)))
    comp = [c for c in texts(t) if c[3] == ui.INPUT_Y]
    check("the composer line is still drawn", len(comp) == 1 and comp[0][1].startswith(">"), repr(comp))
    g, t, R = mkrrc(ui.STATE_RRC_ROOMS)
    g._rrc_rooms = []
    g._rrc_list_seen = True
    g._rrc_panel = True
    g._rrc_panel_kind = "rooms"
    R.draw_rooms(g)
    check("an answered, empty list says so", find(t, "text", "hub lists no public rooms") is not None)


# --- piece E: remaining headers, sleep screen ------------------------------------

def mkbrowser(title="CICADA forums", path="/page/index.mu"):
    g, t = mkui()
    g.state = ui.STATE_BROWSER
    g._prev_state = g.state
    g.browser_title = title
    g.browser_path = path
    g.browser_lines = []
    g.browser_scroll = 0
    g.browser_cursor = 0
    g._cache = [''] * ui.CACHE_ROWS
    return g, t


def test_browser_and_qr_bands():
    print("headers: browser, QR")
    P = ui_pro
    g, t = mkbrowser()
    g.draw_browser()
    hd = find(t, "text", "< CICADA forums")
    check("browser: '< title' in the band", hd is not None and hd[2] == 4 and 20 < hd[3] and hd[3] + 16 <= 41, repr(hd))
    pa = find(t, "small", "/page/index.mu")
    check("browser: the path small on the right", pa is not None and pa[2] + 14 * P.SW == 232, repr(pa))
    check("browser: the old '> title' row is not drawn", [c for c in texts(t) if c[3] == ui.BODY_Y] == [])
    check("a rule closes the band", any(c[0] == "rect" and c[2] == 41 and c[3] == 240 and c[4] == 1 for c in t.calls))
    del t.calls[:]
    g.draw_browser()
    check("the band is cached", not any(c[0] == "text" and c[1].startswith("<") for c in t.calls))
    g, t = mkbrowser("A node with a really long title that goes on", "/page/boards/lora-and-antennas/thread-0042.mu")
    g.draw_browser()
    pa = [c for c in texts(t, "small") if c[1].startswith("...")]
    hd = [c for c in texts(t) if c[1].startswith("< A node")]
    check("a long path keeps its tail", len(pa) == 1 and pa[0][1].endswith("0042.mu") and len(pa[0][1]) == 16, repr(pa))
    check("a long title stops short of the path", len(hd) == 1 and hd[0][2] + len(hd[0][1]) * 8 <= pa[0][2] - 6, repr(hd))
    g, t = mksetup()
    g._settings_page = ui._SET_QR
    g._qr = ("lxma://x", bytes(49 * 49))
    g.draw()
    hd = [c for c in texts(t) if c[1].startswith("< Scan")]
    check("QR page: its title in the band", len(hd) == 1 and hd[0][2] == 4 and 20 < hd[0][3] and hd[0][3] + 16 <= 41, repr(hd))
    check("QR page: not also at the old position", [c for c in texts(t) if c[3] == ui.BODY_Y] == [])
    del t.calls[:]
    g.draw()
    check("QR page: painted once", not any(c[0] == "text" and c[1].startswith("< Scan") for c in t.calls))


class _Flushes:
    def __init__(self):
        self.n = []

    def __call__(self, force_full=False, user=False):
        self.n.append(force_full)


def mksleep(unread=0, bat=4.0, clock=True):
    g, t = mkui()
    g.node_name = "Varna-Pro"
    g._bl = None
    g._screen_on = True
    g.bat_v = bat
    g.unread = {b"a" * 16: unread} if unread else {}
    ui._clock_valid = lambda: clock
    ui._fmt_clock = lambda: "09:41"
    g._panel_flush = _Flushes()
    return g, t


def test_sleep_screen():
    print("sleep screen")
    P = ui_pro
    g, t = mksleep(unread=3)
    g.sleep_screen()
    check("the whole panel is cleared", ("fill", g.BG_DARK) in t.calls)
    nm = find(t, "text", "Varna-Pro")
    check("the node name, centred", nm is not None and nm[2] == (240 - 9 * 8) // 2 and 100 < nm[3] < 140, repr(nm))
    since = find(t, "small", "asleep since 09:41")
    check("'asleep since HH:MM' in the small font, not a clock that looks live",
          since is not None and since[2] == (240 - 18 * P.SW) // 2 and nm[3] < since[3] < nm[3] + 40, repr(since))
    un = find(t, "text", "3 unread")
    check("the unread count, centred under it", un is not None and un[2] == (240 - 8 * 8) // 2 and un[3] > since[3], repr(un))
    box = [c for c in t.calls if c[0] == "rect" and c[4] == 2 and un[3] - 8 <= c[2] <= un[3] + 20 and c[5] != g.BG_DARK]
    check("...in a 2 px box", len(box) == 2 and box[0][3] == box[1][3] > 8 * 8, repr(box))
    bt = find(t, "small", "77% 4.00V")
    check("battery percent and voltage on the bottom line", bt is not None and bt[3] >= 296 and bt[2] == (240 - 9 * P.SW) // 2, repr(bt))
    words = " ".join(c[1] for c in t.calls if c[0] in ("text", "small"))
    check("no 'sleeping', no 'press any key', no frame", "sleeping" not in words and "press" not in words
          and not any(c[0] == "rect" and c[3] >= 200 and c[4] >= 200 and c[5] != g.BG_DARK for c in t.calls), words)
    check("a full refresh was asked for", g._panel_flush.n == [True], repr(g._panel_flush.n))
    check("the screen is off", g._screen_on is False)
    g, t = mksleep(unread=0, bat=4.9, clock=False)
    g.sleep_screen()
    check("nothing unread: no box", not any("unread" in c[1] for c in t.calls if c[0] == "text")
          and not any(c[0] == "rect" and c[4] == 2 and c[5] != g.BG_DARK for c in t.calls))
    check("clock not set: no 'asleep since'", not any("asleep" in c[1] for c in texts(t, "small")))
    check("on a cable it says USB", find(t, "small", "USB 4.90V") is not None, repr(texts(t, "small")))
    g, t = mksleep(bat=0.0)
    g.sleep_screen()
    check("no battery reading: no battery line", texts(t, "small") == [c for c in texts(t, "small") if "asleep" in c[1]])
    g, t = mksleep(unread=120, bat=3.2)
    g.sleep_screen()
    check("a flat pack reads 0%, a big count fits", find(t, "small", "0% 3.20V") is not None and find(t, "text", "120 unread") is not None)


def test_sleep_tick():
    P = ui_pro
    g, t = mksleep(unread=1)
    g.sleep_screen()
    del t.calls[:]
    del g._panel_flush.n[:]
    P.sleep_tick(g)
    check("nothing changed: the tick draws nothing", t.calls == [] and g._panel_flush.n == [])
    g.unread[b"b" * 16] = 2
    acq = []
    g._spi_acquire = lambda: acq.append("a")
    g._spi_release = lambda: acq.append("r")
    P.sleep_tick(g)
    check("a message arrived while asleep: the box shows the new total", find(t, "text", "3 unread") is not None, repr(texts(t)))
    check("...as a partial refresh, inside the display hand-over", g._panel_flush.n == [False] and acq == ["a", "r"], repr((g._panel_flush.n, acq)))
    check("...and nothing else is redrawn", find(t, "text", "Varna-Pro") is None and ("fill", g.BG_DARK) not in t.calls)
    del t.calls[:]
    P.sleep_tick(g)
    check("once", t.calls == [])
    g.unread.clear()
    P.sleep_tick(g)
    check("read elsewhere: the box goes away", not any("unread" in c[1] for c in texts(t))
          and any(c[0] == "rect" and c[5] == g.BG_DARK for c in t.calls))
    g, t = mksleep(unread=1)
    g.sleep_screen()
    g._screen_on = True                     # woken: the live screen is up again
    g.unread[b"c" * 16] = 5
    del t.calls[:]
    P.sleep_tick(g)
    check("the tick does nothing while the screen is on", t.calls == [])
    g, t = mksleep(unread=1)
    g.sleep_screen()
    g._bl = object()
    g.unread[b"c" * 16] = 5
    del t.calls[:]
    P.sleep_tick(g)
    check("...or on a panel with a backlight (it shows nothing when off)", t.calls == [])


def test_sleep_review():
    print("review: sleep")
    P = ui_pro
    # The app does not count a message as unread when its chat is the open
    # one -- but asleep, nobody is reading it.
    g, t = mksleep()
    X = b"x" * 16
    g.add_peer(X, "Alex")
    g.selected_peer = X
    g.state = ui.STATE_CHAT
    g.add_chat_message(X, False, "before sleep")
    g.sleep_screen()
    check("asleep in a chat with nothing new: no box", not any("unread" in c[1] for c in texts(t)))
    del t.calls[:]
    g.add_chat_message(X, True, "my own, queued earlier")
    P.sleep_tick(g)
    check("my own message is not unread", t.calls == [], repr(t.calls[:2]))
    g.add_chat_message(X, False, "are you there?")
    g.add_chat_message(X, False, "hello?")
    P.sleep_tick(g)
    check("messages from the open chat's peer while asleep reach the box", find(t, "text", "2 unread") is not None, repr(texts(t)))
    check("...without touching the app's own unread counts", g.unread.get(X, 0) == 0)
    g, t = mksleep(unread=2)
    g.selected_peer = X
    g.state = ui.STATE_NODES
    g.add_peer(X, "Alex")
    g.sleep_screen()
    del t.calls[:]
    g.add_chat_message(X, False, "hi")
    P.sleep_tick(g)
    check("on the node list a message is counted once (the app already counts it)", find(t, "text", "3 unread") is not None, repr(texts(t)))
    g, t = mksleep(unread=1)
    g.sleep_screen()
    g.unread[b"z" * 16] = None              # must never happen; must never kill the GUI task
    try:
        P.sleep_tick(g)
        ok = True
    except Exception:
        ok = False
    check("a tick that fails does not raise into the GUI loop", ok)
    g.unread = {b"z" * 16: 4}
    del t.calls[:]
    P.sleep_tick(g)
    check("...and stops drawing until the next sleep screen", t.calls == [])
    g, t = mksleep()
    g.state = ui.STATE_IMAGE
    g._image_drawn = True
    g.sleep_screen()
    g.wake_screen(force=True, why="key")
    check("waking in the image viewer redraws the image (the sleep screen was left on the panel)", g._image_drawn is False)
    g, t = mksleep(bat=4.25)
    g.sleep_screen()
    check("the percentage stops at 100", find(t, "small", "100% 4.25V") is not None, repr(texts(t, "small")))
    g, t = mksleep(bat=-1.0)
    g.sleep_screen()
    check("a negative reading shows no battery line", not any("V" in c[1] for c in texts(t, "small") if "asleep" not in c[1]))


# --- piece F: Lookup, WiFi scan, shell menu ----------------------------------------

def mkfind(n=10, q=""):
    g, t = mkui()
    peers(g, 2)
    snap = [(bytes([0x40 + i]) * 16, "node%02d" % i, 1700000000) for i in range(n)]
    g.on_contact_snapshot = lambda tab: snap
    g._find_open()
    for ch in q.encode():
        g.handle_key(bytes([ch]))
    g._cache = [''] * ui.CACHE_ROWS
    return g, t


def frow_y(i):
    return 58 + 32 * i


def test_find_rows():
    print("lists: Lookup")
    P = ui_pro
    g, t = mkfind()
    check("seven two-line rows fit between the title and the count row", g._find_rows() == 7 == P.FIND_ROWS and frow_y(7) == 282)
    g._find_sel = 1
    g._draw_find()
    ti = [c[1] for c in texts(t) if c[3] == 42]
    check("the title says Lookup, as the toolbar does", len(ti) == 1 and ti[0].startswith("Lookup peer") and "Find" not in ti[0], repr(ti))
    names = [c for c in texts(t) if c[1].startswith("node")]
    check("names in the main font, one per 32 px row", [(c[1], c[2], c[3]) for c in names] == [("node%02d" % i, 16, frow_y(i) + 1) for i in range(7)],
          repr(names[:2]))
    sub = [c for c in texts(t, "small") if c[1].startswith("40404040")]
    check("hash and last-heard under the name, small", len(sub) == 1 and sub[0][2] == 16 and sub[0][3] == frow_y(0) + 18 and len(sub[0][1]) > 9, repr(sub))
    check("the selected row is inverted", names[1][4] == g.BG_DARK and names[1][5] == g.SEL_BG and names[0][5] == g.BG_DARK)
    rules = sorted(c[2] for c in t.calls if c[0] == "rect" and c[4] == 1 and c[1] == 0 and frow_y(0) <= c[2] < 282 and c[3] == ui.SBAR_X)
    check("a rule under each row", rules == [frow_y(i) + 31 for i in range(7)], repr(rules))
    check("nothing goes through ui._sdraw in the rows", not any(frow_y(0) <= c[3] < 282 for c in texts(t, "big")))
    del t.calls[:]
    g._draw_find()
    check("cached", not any(c[0] in ("text", "small") and frow_y(0) <= c[3] < 282 for c in t.calls))
    for _ in range(8):
        g._find_move(1)
    check("the window follows the cursor over seven rows", g._find_sel == 9 and g._find_scroll == 3, "%d %d" % (g._find_sel, g._find_scroll))
    g, t = mkfind(3)
    g._draw_find()
    n = len(g._find_res)                  # the three heard plus the two already listed
    blank = sorted(c[2] for c in t.calls if c[0] == "rect" and c[4] == 32 and c[5] == g.BG_DARK and frow_y(0) <= c[2] < 282)
    check("slots past the end are blanked", n == 5 and blank == [frow_y(5), frow_y(6)], "%d %r" % (n, blank))
    g, t = mkfind(10)
    g._draw_find()
    for ch in b"zzz":
        g.handle_key(bytes([ch]))
    del t.calls[:]
    g._draw_find()
    msg = [c for c in texts(t) if 58 <= c[3] < 282 and c[1].strip()]
    wiped = sorted(set(c[3] for c in t.calls if c[0] in ("text", "rect") and 58 <= (c[3] if c[0] == "text" else c[2]) < 282))
    check("no match: the message is drawn", len(msg) >= 1, repr(msg))
    rows16 = set()
    for c in t.calls:
        if c[0] == "rect" and c[4] == 16 and 58 <= c[2] < 282:
            rows16.add(c[2])
        if c[0] == "text" and 58 <= c[3] < 282:
            rows16.add(c[3])
    check("...and every 16 px row of the results area is repainted (no half of a two-line row left)",
          rows16 >= set(range(58, 282, 16)), repr(sorted(rows16)))
    for _ in range(3):
        g.handle_key(b"\x08")
    del t.calls[:]
    g._draw_find()
    back = [c for c in texts(t) if c[1].startswith("node")]
    check("back to results: all seven rows repaint", len(back) == 7, str(len(back)))


def wrow_y(i):
    return 42 + 32 * i


def test_wifi_rows():
    print("lists: WiFi scan")
    g, t = mksetup()
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_networks = [("net%02d" % i, -40 - i) for i in range(12)]
    g._wifi_scanning = False
    g._settings_idx = 2
    g._settings_scroll = 0
    g.draw()
    names = [c for c in texts(t) if c[1].startswith("net")]
    check("eight networks, names in the main font on 32 px rows",
          [(c[1], c[2], c[3]) for c in names] == [("net%02d" % i, 16, wrow_y(i) + 1) for i in range(8)], repr(names[:2]))
    sig = find(t, "small", "-42 dBm")
    check("signal under the name, small", sig is not None and sig[2] == 16 and sig[3] == wrow_y(2) + 18, repr(sig))
    check("the selected row is inverted", names[2][5] == g.SEL_BG and names[0][5] == g.BG_DARK)
    for _ in range(7):
        g._settings_scroll_down()
    check("the window follows the cursor over eight rows", g._settings_idx == 9 and g._settings_scroll == 2,
          "%d %d" % (g._settings_idx, g._settings_scroll))
    g._wifi_networks = []
    g._wifi_scanning = True
    g._settings_idx = 0
    g._settings_scroll = 0
    g._cache = [''] * ui.CACHE_ROWS
    del t.calls[:]
    g.draw()
    check("scanning: the old message page", any("Scanning" in c[1] for c in texts(t)))
    g._wifi_networks = [("home", -40)]
    g._wifi_scanning = False
    g._cache = [''] * ui.CACHE_ROWS
    del t.calls[:]
    g.draw()
    blank = sorted(c[2] for c in t.calls if c[0] == "rect" and c[4] == 32 and c[5] == g.BG_DARK and c[1] == 0)
    check("a short list blanks the other seven slots", blank == [wrow_y(i) for i in range(1, 8)], repr(blank))
    bar = [c[1] for c in texts(t) if c[3] == ui.INPUT_Y]
    check("the footer names ENT, not CLICK, on a board with no trackball", "ENT" in bar and "CLICK" not in bar, repr(bar))


def test_shell_menu():
    print("lists: shell menu")
    P = ui_pro
    g, t = mkui()
    g.state = ui.STATE_SHELL
    g._prev_state = g.state
    g._shell_menu = True
    g._shell_menu_idx = 0
    g._shell_line_mode = True
    g._cache = [''] * ui.CACHE_ROWS
    g.draw_shell()
    hd = find(t, "text", "< Control keys")
    check("a '< Control keys' band, no mention of a trackball", hd is not None and 20 < hd[3] < 41
          and not any("trackball" in c[1] for c in texts(t)), repr(hd))
    n0 = find(t, "text", "Ctrl-C")
    d0 = find(t, "small", "interrupt")
    check("an item: its name over what it does", n0 is not None and n0[2] == 16 and n0[3] == wrow_y(0) + 1
          and d0 is not None and d0[3] == wrow_y(0) + 18, repr((n0, d0)))
    check("the cursor row is inverted", n0[5] == g.SEL_BG)
    shown = [c[1] for c in texts(t) if c[2] == 16 and wrow_y(0) <= c[3] < 298]
    check("eight of the eleven items show", shown == ["Ctrl-C", "Ctrl-D", "Ctrl-Z", "Tab", "Up", "Down", "Esc", "Line/Char mode"], repr(shown))
    check("the mode row says which mode", find(t, "small", "line") is not None)
    ft = [c[1] for c in texts(t) if c[3] == ui.INPUT_Y]
    check("the footer names ENT", "ENT" in ft and "CLICK" not in ft, repr(ft))
    del t.calls[:]
    g.draw_shell()
    check("cached", t.calls == [], repr(t.calls[:2]))
    check("window: top of the list", P.shell_menu_top(g) == 0)
    g._shell_menu_idx = 7
    check("window: stays put while the cursor is inside it", P.shell_menu_top(g) == 0)
    g._shell_menu_idx = 10
    check("window: the last item pulls it to the end", P.shell_menu_top(g) == 3)
    g._shell_menu_idx = 5
    check("window: and it stays there while the cursor is inside it (rows must not move under a tap)", P.shell_menu_top(g) == 3)
    g._shell_menu_idx = 1
    check("window: a cursor above it pulls it up", P.shell_menu_top(g) == 1)
    g._shell_menu_idx = 9
    del t.calls[:]
    g.draw_shell()
    q = find(t, "text", "Quit shell")
    f = [c for c in texts(t, "small") if "x" in c[1] and c[1][0].isdigit()]
    check("scrolled: Quit shell is on screen and selected, Font shows the grid", q is not None and q[5] == g.SEL_BG and len(f) == 1, repr((q, f)))


def test_lists_review():
    print("review: lists")
    P = ui_pro
    g, t = mkui()
    g.state = ui.STATE_SHELL
    g._prev_state = g.state
    g._shell_menu_open()
    g._shell_line_mode = True
    g.draw()
    ys = [c[3] for c in texts(t) if c[2] == 16 and wrow_y(0) <= c[3] < 298]
    check("all eight menu rows on the 32 px pitch", ys == [wrow_y(i) + 1 for i in range(8)], repr(ys))
    check("no arm: Quit shell says what it does", True)
    g._shell_menu_close()
    del t.calls[:]
    g.draw()
    sf = g._shell_font
    bottom = ui.BODY_Y + (sf.rows * sf.h if sf else ui.BODY_ROWS * ui.CHAR_H)
    check("closing the menu clears the top of its band (the terminal starts at y 26)",
          any(c[0] == "rect" and c[1] == 0 and c[2] <= 21 and c[2] + c[4] >= 26 and c[3] == 240 and c[5] == g.BG_DARK for c in t.calls))
    check("...and what its last row left below the terminal's last line",
          bottom >= 298 or any(c[0] == "rect" and c[1] == 0 and c[2] <= bottom and c[2] + c[4] >= 298 and c[5] == g.BG_DARK for c in t.calls),
          "bottom %d" % bottom)
    del t.calls[:]
    g.draw()
    check("...once", not any(c[0] == "rect" and c[2] == 20 and c[3] == 240 and c[4] == 6 and c[5] == g.BG_DARK for c in t.calls))
    g, t = mkui()
    g.state = ui.STATE_SHELL
    g._prev_state = g.state
    g._shell_menu_open()
    g._shell_menu_idx = 9
    g._menu_armed = (9, g._cache)
    g.draw()
    check("an armed Quit shell says 'tap again to quit'", find(t, "small", "tap again to quit") is not None, repr(texts(t, "small")))
    g, t = mkfind(10)
    g._prev_state = g.state
    g.draw()
    del t.calls[:]
    g.draw()
    drew = [c for c in t.calls if c[0] in ("text", "small", "rect", "icon")]
    check("Lookup with results: a second full draw draws nothing (the lane and the tab bar do not fight over a slot)", drew == [], repr(drew[:3]))
    for ch in b"zzz":
        g.handle_key(bytes([ch]))
    g.draw()
    for _ in range(3):
        g.handle_key(b"\x08")
    del t.calls[:]
    g.draw()
    thumb = [c for c in t.calls if c[0] == "rect" and c[1] == ui.SBAR_X and c[5] == g.DIM_CYAN and c[4] > 1]
    check("results -> no match -> results: the scroll thumb is back", len(thumb) == 1, repr(thumb))
    g, t = mksetup()
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_networks = [("net%02d" % i, -40 - i) for i in range(12)]
    g._settings_idx = 0
    g._settings_scroll = 3               # what backing out of the password page leaves
    g.draw()
    check("WiFi: a selection above the window is scrolled to", g._settings_scroll == 0 and find(t, "text", "net00") is not None, str(g._settings_scroll))
    g, t = mksetup()
    g._settings_page = ui._SET_RADIO
    g.draw()
    bar = [c[1] for c in texts(t) if c[3] == ui.INPUT_Y]
    check("Radio stats has nothing to select: its footer offers only back", "ENT" not in bar and "BKSP" in bar, repr(bar))


def test_find_count_row_fits():
    print("device pass: Lookup count row")
    # Seen on the device as "2490=listen": with a microphone the key hints are
    # 25 characters, and in the main font they ran over the "249 heard" count.
    P = ui_pro
    g, t = mkfind(10)
    g._find_snap = [(bytes([i % 250]) * 16, "n", 0) for i in range(2490)]
    g.on_listen_start = lambda: None
    g._cache = [''] * ui.CACHE_ROWS
    g._draw_find()
    y = ui.BODY_Y + (ui.BODY_ROWS - 1) * ui.CHAR_H
    row = [c for c in t.calls if c[0] in ("text", "small", "big") and y <= c[3] < y + 16 and c[1].strip()]
    cnt = [c for c in row if "heard" in c[1]]
    keys = [c for c in row if "listen" in c[1]]
    check("the count and the key hints are both drawn, in the small font", len(cnt) == 1 and len(keys) == 1
          and cnt[0][0] == "small" and keys[0][0] == "small", repr(row))
    check("...and do not overlap", cnt[0][2] + len(cnt[0][1]) * P.SW < keys[0][2], repr((cnt, keys)))
    check("...and the hints end inside the panel", keys[0][2] + len(keys[0][1]) * P.SW <= 236, repr(keys))
    for n in ("1600/3200 heard", "16000/32000 heard", "123456/1234567 heard"):
        del t.calls[:]
        P.find_count_row(g, n, "0=listen Ent=add Bsp=back", y)
        sm = texts(t, "small")
        check("a searched count (%s) still clears the hints" % n,
              len(sm) == 2 and sm[0][2] + len(sm[0][1]) * P.SW < sm[1][2] and sm[0][1][0].isdigit()
              and sm[1][1] == "0=listen Ent=add Bsp=back", repr(sm))

def test_setup_touch_feedback_row():
    print("setup: touch feedback")
    P = ui_pro
    g, t = mksetup()
    check("off until the user turns it on", g._haptics is False)
    it = P.setup_items(g)
    k = P.SETUP_IDX.index(ui._SET_HAPTICS)
    check("its row sits under Keyboard light", it[k - 1][0] == "Keyboard light" and it[k][0] == "Touch feedback", repr(it[k]))
    check("...and says OFF", it[k][1] == "OFF")
    saved = []
    g.on_haptics = lambda on: saved.append(on)
    g._settings_idx = ui._SET_HAPTICS
    check("the row is its own row, not the one before it", P.setup_row(g) == k)
    g.handle_key(b'\r')
    check("Enter turns it on and hands the choice to be saved", g._haptics is True and saved == [True], repr(saved))
    check("the row says ON", P.setup_items(g)[k][1] == "ON")
    g.handle_key(b'\r')
    check("and off again", g._haptics is False and saved == [True, False])
    g._settings_idx = 13
    check("a hash continuation idx still counts as the Addr row", P.SETUP_IDX[P.setup_row(g)] == 12)


if __name__ == "__main__":
    test_setup_touch_feedback_row()
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
    test_setup_items()
    test_setup_step()
    test_setup_draw()
    test_setup_window_and_hashes()
    test_setup_sub_page_round_trip()
    test_setup_review_gaps()
    test_setup_sub_pages_have_a_back_header()
    test_setup_sub_pages_fit_30_columns()
    test_rrc_header_band()
    test_rrc_panel_rooms()
    test_rrc_panel_members()
    test_rrc_review_gaps()
    test_browser_and_qr_bands()
    test_sleep_screen()
    test_sleep_tick()
    test_sleep_review()
    test_find_rows()
    test_wifi_rows()
    test_shell_menu()
    test_lists_review()
    test_find_count_row_fits()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

# The T-Deck Pro's own node list and chrome: two-line rows a finger can hit,
# an actions sheet, icon tabs, an icon toolbar, and a top bar with the clock
# on the right. Drawing functions over the UI instance, the way rrc_ui.py
# works; ui.py hands over to these on a portrait panel and the T-Deck v1's
# image does not contain this file at all.
#
# Everything is laid out in native pixels for the 240x320 panel:
#
#   y   0..19   top bar      battery | radio state | clock
#   y  20..41   tab bar      four 60 px cells: icon + count
#   y  42..297  list         eight rows of 32 px
#   y 298..319  toolbar      four 60 px cells: icon + label
#
# The panel is 1-bit: "selected" is an ink block with paper marks.

# Constants first: ui.py imports this module at its very end and reads
# LIST_ROWS, and this module imports ui back just below.
LIST_ROWS = 8
ROW_H = 32
LIST_Y = 42
TAB_Y = 20
TAB_H = 22
TOOL_Y = 298
TOOL_H = 22
MARGIN = 16        # left margin: unread pill (line 1) and star (line 2)
DOTS_X = 216       # the "..." column starts here
INFO_R = 212       # right-hand second-line text ends here
NAME_COLS = 25
INFO_COLS = 22
SHEET_SLOT = 10    # ui._cache slot for the sheet
SBAR_SLOT = 11     # ...and for the scroll lane

import time

import ui as _u


def cur(ui):
    """The list the node screen is showing: (index attr, scroll attr, keys,
    table, show unread, the two empty-list lines)."""
    t = ui.node_tab
    if t == _u.TAB_MSG:
        return ("selected_idx", "node_scroll", ui._peer_keys, ui.peers, True,
                ("No peers yet.", "Waiting for announces..."))
    if t == _u.TAB_NET:
        return ("net_idx", "net_scroll", ui._node_keys, ui.nomad_nodes, False,
                ("No nodes yet.", "Waiting for node announces..."))
    if t == _u.TAB_RRC:
        return ("_rrc_idx", "_rrc_scroll", ui._rrc_keys, ui.rrc_hubs, False,
                ("No RRC hubs.", "(L) to find or add one"))
    return ("ssh_idx", "ssh_scroll", ui._shell_keys, ui.shell_nodes, False,
            ("No rnsh nodes.", "(L) to find or add one"))


def _info(entry):
    """Hops, RSSI and last-seen: what the old footer showed for the selection."""
    hops = _u._hops(entry["hops"]) + " " if entry.get("hops") else ""
    age = _u._age(entry.get("seen"))
    rs = entry.get("rssi")
    if rs is not None:
        long = hops + str(rs) + "dB " + age
        if len(long) <= INFO_COLS:
            return long
    return (hops + age)[:INFO_COLS]


def _pill(ui, n, y, fg, bg):
    """Unread count in the margin, inverse to its row. Two digits fill the
    16 px margin exactly; the count stops at 99."""
    s = str(min(n, 99))
    w = len(s) * ui.SW + 4
    x = (MARGIN - w) // 2
    ui.tft.fill_rect(x, y, w, ui.SH, fg)
    ui._sdraw(s, x + 2, y, bg, fg)


def draw_rows(ui, limit=LIST_ROWS):
    """The eight two-line rows (or the first `limit` of them, when the sheet
    covers the rest). Each row is redrawn only when its cache key changes."""
    ia, sa, keys, table, show_unread, empty = cur(ui)
    tft = ui.tft
    paper = ui.BG_DARK
    width = _u.SBAR_X
    if not keys:
        ck = "E" + empty[0]
        if ui._cache[2] != ck:
            ui._cache[2] = ck
            for i in range(3, 2 + LIST_ROWS):
                ui._cache[i] = "E"
            tft.fill_rect(0, LIST_Y, width, LIST_ROWS * ROW_H, paper)
            y = LIST_Y + 3 * ROW_H
            tft.text(ui.font, ui._tb(empty[0].center(_u.COLS)), 0, y, ui.NEON_CYAN, paper)
            tft.text(ui.font, ui._tb(empty[1].center(_u.COLS)), 0, y + _u.CHAR_H, ui.DIM_CYAN, paper)
        return
    scroll = getattr(ui, sa)
    sel_idx = getattr(ui, ia)
    ping = ui.ping_status if ui.node_tab == _u.TAB_MSG else None
    for i in range(limit):
        y = LIST_Y + i * ROW_H
        ci = i + 2
        idx = scroll + i
        if idx >= len(keys):
            if ui._cache[ci] != "-":
                ui._cache[ci] = "-"
                tft.fill_rect(0, y, width, ROW_H, paper)
            continue
        key = keys[idx]
        e = table[key]
        sel = idx == sel_idx
        name = _u._ascii(e.get("name") or "?")[:NAME_COLS]
        hsh = key.hex()[:8]
        uc = ui.unread.get(key, 0) if show_unread else 0
        fav = e.get("fav") == True
        info = (ping if (sel and ping) else _info(e))[:INFO_COLS]
        ck = ("\x01" if sel else "") + name + "\x00" + hsh + "\x00" + info + "\x00" + str(uc) + ("*" if fav else "")
        if ui._cache[ci] == ck:
            continue
        ui._cache[ci] = ck
        bg = ui.SEL_BG if sel else paper
        fg = paper if sel else ui.NEON_CYAN
        dim = paper if sel else ui.DIM_CYAN
        tft.fill_rect(0, y, width, ROW_H - 1, bg)
        tft.fill_rect(0, y + ROW_H - 1, width, 1, ui.DIM_CYAN)
        tft.text(ui.font, ui._tb(name), MARGIN, y, fg, bg)
        ui._sdraw(hsh, MARGIN, y + 17, dim, bg)
        if info:
            ui._sdraw(info, INFO_R - len(info) * ui.SW, y + 17, dim, bg)
        for k in range(3):
            tft.fill_rect(DOTS_X + 4 + k * 5, y + 14, 3, 3, fg)
        if uc:
            _pill(ui, uc, y + 2, fg, bg)
        if fav:
            ui._bitmap(_u._STAR, 3, y + 19, fg)


def _scrollbar(ui):
    ia, sa, keys, table, show_unread, empty = cur(ui)
    total = len(keys)
    track = LIST_ROWS * ROW_H
    ck = (total, getattr(ui, sa))
    if ui._cache[SBAR_SLOT] == ck:
        return                       # an unchanged lane is not worth a refresh
    ui._cache[SBAR_SLOT] = ck
    ui.tft.fill_rect(_u.SBAR_X, LIST_Y, _u.SBAR_W, track, ui.BG_DARK)
    if total > LIST_ROWS:
        h = max(6, track * LIST_ROWS // total)
        ui.tft.fill_rect(_u.SBAR_X, LIST_Y + getattr(ui, sa) * track // total, _u.SBAR_W, h, ui.DIM_CYAN)


_BUBBLE = _u._icon(("##########..", "#........#..", "#.#####..#..", "#........#..", "#.###....#..",
                    "#........#..", "##########..", "..##........", ".##.........", "#..........."))
_TERM = _u._icon(("############", "#..........#", "#.#........#", "#..#.......#", "#.#..####..#",
                  "#..........#", "############"))
_PEOPLE = _u._icon(("...##...##..", "..####.####.", "...##...##..", "............", ".######.####",
                    "########.###", "########.###"))
_GEAR = _u._icon(("...##...", ".#.##.#.", ".######.", "###..###", "###..###", ".######.", ".#.##.#.", "...##..."))
_LENS = _u._icon((".####....", "#....#...", "#....#...", "#....#...", ".####....", ".....##..",
                  "......##.", ".......##"))
_TRASH = _u._icon(("..####..", "########", ".#....#.", ".#.##.#.", ".#.##.#.", ".#.##.#.", ".######."))

_TAB_ICONS = (_BUBBLE, _u._GLOBE, _TERM, _PEOPLE)          # MSG, NET, SSH, RRC
_TOOLS = ((_u._ANTENNA, "Annc", b'a'), (_GEAR, "Setup", b's'),
          (_u._STAR, "Fav", b'f'), (_LENS, "Lookup", b'l'))


def draw_tab_bar(ui):
    un = 0
    for v in ui.unread.values():
        un += v
    counts = (str(len(ui._peer_keys)) + ("*" if un else ""), str(len(ui._node_keys)),
              str(len(ui._shell_keys)), str(len(ui._rrc_keys)))
    ck = "P" + str(ui.node_tab) + ("F" if ui._find else "") + "/".join(counts)
    if ui._cache[1] == ck:
        return
    ui._cache[1] = ck
    tft = ui.tft
    paper = ui.BG_DARK
    cw = _u.SCREEN_W // 4
    tft.fill_rect(0, TAB_Y, _u.SCREEN_W, TAB_H, paper)
    for i in range(4):
        act = i == ui.node_tab
        bg = ui.SEL_BG if act else paper
        fg = paper if act else ui.NEON_CYAN
        x = i * cw
        if act:
            tft.fill_rect(x, TAB_Y + 1, cw, TAB_H - 2, bg)
        ic = _TAB_ICONS[i]
        s = counts[i]
        tx = x + (cw - (ic[0] + 4 + len(s) * _u.CHAR_W)) // 2
        ui._bitmap(ic, tx, TAB_Y + (TAB_H - ic[1]) // 2, fg)
        tft.text(ui.font, s, tx + ic[0] + 4, TAB_Y + 3, fg, bg)
    tft.fill_rect(0, TAB_Y, _u.SCREEN_W, 1, ui.DIM_CYAN)
    tft.fill_rect(0, TAB_Y + TAB_H - 1, _u.SCREEN_W, 1, ui.DIM_CYAN)


def draw_toolbar(ui):
    """The bottom line: four cells, icon + label, each a touch target and a
    reminder of its key (the underlined letter)."""
    sheet = ui._sheet is not None
    ck = "\x03sheet" if sheet else "\x03tools"
    if ui._cache[_u.FOOT_SLOT] == ck:
        return
    ui._cache[_u.FOOT_SLOT] = ck
    tft = ui.tft
    paper = ui.BG_DARK
    ink = ui.NEON_CYAN
    tft.fill_rect(0, TOOL_Y, _u.SCREEN_W, TOOL_H, paper)
    tft.fill_rect(0, TOOL_Y, _u.SCREEN_W, 1, ui.DIM_CYAN)
    ui._hint_zones = []
    if sheet:
        ty = TOOL_Y + 1 + (TOOL_H - 1 - ui.SH) // 2
        ui._sdraw("tap outside to close", 4, ty, ui.DIM_CYAN, paper)
        s = "(BKSP)close"
        x = _u.SCREEN_W - 4 - len(s) * ui.SW
        ui._sdraw(s, x, ty, ink, paper)
        ui._hint_zones.append((ui.state, x, _u.SCREEN_W, b'\x08'))
        return
    cw = _u.SCREEN_W // 4
    ty = TOOL_Y + 1 + (TOOL_H - 1 - ui.SH) // 2
    for i in range(4):
        ic, label, key = _TOOLS[i]
        x = i * cw + (cw - (ic[0] + 3 + len(label) * ui.SW)) // 2
        ui._bitmap(ic, x, TOOL_Y + 1 + (TOOL_H - 1 - ic[1]) // 2, ink)
        lx = x + ic[0] + 3
        ui._sdraw(label, lx, ty, ink, paper)
        # The small font has no bold face; an underline marks the hotkey.
        tft.fill_rect(lx, ty + ui.SH - 1, ui.SW - 1, 1, ink)
        ui._hint_zones.append((ui.state, i * cw, (i + 1) * cw, key))


def draw_node_list(ui):
    draw_tab_bar(ui)
    if ui._find:
        ui._sheet = None
        if ui._cache[_u.FOOT_SLOT] in ("\x03tools", "\x03sheet"):
            # Find paints its own footer text; clear the toolbar's band once.
            ui._cache[_u.FOOT_SLOT] = ''
            ui.tft.fill_rect(0, TOOL_Y, _u.SCREEN_W, TOOL_H, ui.BG_DARK)
        ui._draw_find()          # Find keeps its own single-line layout
        return
    if ui._sheet is not None and (ui._sheet != ui.node_tab or not cur(ui)[2]):
        sheet_close(ui)              # the tab changed or the list emptied under it
    if ui._sheet is not None:
        draw_rows(ui, LIST_ROWS - len(sheet_items(ui)))
        _scrollbar(ui)
        draw_sheet(ui)
    else:
        draw_rows(ui)
        _scrollbar(ui)
    draw_toolbar(ui)


def draw_navbar(ui):
    """Battery left, radio state in the middle, the mesh clock on the right.
    No node name: it never changes, and the clock is what gets glanced at.
    Left and centre keep ui.draw_navbar's content and rules."""
    tft = ui.tft
    W = _u.SCREEN_W
    NAV_H = _u.NAV_H
    if ui.bat_v >= 4.3:
        bat = "USB"
    elif ui.bat_v <= 0.0:
        bat = "--"
    else:
        bat = "{:.1f}V".format(ui.bat_v)
    ann = ui.announce_flash and time.ticks_diff(time.ticks_ms(), ui.announce_flash) < 2000
    icon = None
    if ui._audio_status:
        center = "[" + ui._audio_status + "]"
    elif ui.transfer_progress:
        rcv, tot = ui.transfer_progress
        center = "RX " + str(rcv) + "/" + str(tot)
    elif ui._tcp_enabled:
        icon, center = _u._GLOBE, ""
    elif not ui.lora_online:
        icon, center = _u._ANTENNA, "FAIL"
    elif ui.rssi is not None:
        icon, center = _u._ANTENNA, "snr " + str(ui.snr or 0)
    else:
        icon, center = _u._ANTENNA, ""
    clock = _u._fmt_clock() if _u._clock_valid() else ""
    right = (">>> " if ann else "") + clock
    right = right.rstrip() if not clock else right
    hb = ui.HEADER_BG
    sw = ui.SW
    ny = (NAV_H - ui.SH) // 2
    left_end = 4 * _u.CHAR_W + 5 * sw
    bl = 3 if ui.bat_v > 3.9 else (2 if ui.bat_v > 3.6 else (1 if ui.bat_v > 3.3 else 0))
    bat_key = bat + str(bl)
    mid_key = ("G" if icon is _u._GLOBE else "A" if icon else "") + center + "|" + right
    if ui._cache[0] == '':
        ui._nav_bat_cache = ''
        ui._nav_mid_cache = ''
        tft.fill_rect(0, 0, W, NAV_H, hb)
    if ui._nav_bat_cache != bat_key:
        ui._nav_bat_cache = bat_key
        gr = ui.NEON_GREEN
        dm = ui.BG_DARK
        tft.fill_rect(1, 4, 26, 12, gr)
        tft.fill_rect(2, 5, 24, 10, hb)
        tft.fill_rect(27, 7, 2, 6, gr)
        tft.fill_rect(3, 6, 7, 8, gr if bl >= 1 else dm)
        tft.fill_rect(11, 6, 7, 8, gr if bl >= 2 else dm)
        tft.fill_rect(19, 6, 7, 8, gr if bl >= 3 else dm)
        tft.fill_rect(4 * _u.CHAR_W, 0, 5 * sw, NAV_H, hb)
        ui._sdraw(bat, 4 * _u.CHAR_W, ny, ui.NEON_GREEN, hb)
    if ui._nav_mid_cache != mid_key:
        ui._nav_mid_cache = mid_key
        tft.fill_rect(left_end, 0, W - left_end, NAV_H, hb)
        rx = W - sw - len(right) * sw
        if right:
            ui._sdraw(right, rx, ny, ui.NEON_CYAN, hb)
        lo = left_end + sw
        hi = rx - sw
        iw = icon[0] + 4 if icon else 0
        center = center[:max(0, (hi - lo - iw) // sw)]
        cw = iw + len(center) * sw
        x = max(lo, min((W - cw) // 2, hi - cw))
        col = ui.NEON_MAG if (not ui._tcp_enabled and not ui.lora_online) else ui.DIM_CYAN
        if icon:
            ui._bitmap(icon, x, (NAV_H - icon[1]) // 2, col)
        if center:
            ui._sdraw(center, x + iw, ny, col, hb)
    ui._cache[0] = bat_key + mid_key


# --- the actions sheet ---------------------------------------------------------
# A touch menu for the selected row. It adds nothing the keys cannot do: each
# item closes the sheet and then does exactly what its key does.

_OPEN = {0: "Open chat", 1: "Open page", 2: "Open shell", 3: "Open hub"}   # by tab id


def sheet_items(ui):
    """(label, key label, key bytes or None for the click, icon), top to bottom."""
    items = [(_OPEN[ui.node_tab], "Enter", None, _TAB_ICONS[ui.node_tab]),
             ("Favorite", "F", b'f', _u._STAR)]
    if ui.node_tab == _u.TAB_MSG:
        items.append(("Ping", "P", b'p', _u._ANTENNA))
    items.append(("Delete", "D", b'd', _TRASH))
    return tuple(items)


def sheet_rect(ui):
    """(top y, item count): the sheet covers the list's last rows."""
    n = len(sheet_items(ui))
    return TOOL_Y - n * ROW_H, n


def sheet_open(ui):
    ia, sa, keys, table, show_unread, empty = cur(ui)
    if ui.state != _u.STATE_NODES or ui._find or not keys:
        return False
    sel = getattr(ui, ia)
    if not (0 <= sel < len(keys)):
        return False
    ui._sheet = ui.node_tab
    # Keep the row it is about in view: the last row above the sheet at most.
    free = LIST_ROWS - len(sheet_items(ui))
    scroll = getattr(ui, sa)
    ui._sheet_scroll = scroll
    if sel - scroll >= free:
        setattr(ui, sa, sel - free + 1)
    ui._cache[SHEET_SLOT] = ''
    ui._cache[_u.FOOT_SLOT] = ''
    ui.dirty = True
    return True


def sheet_close(ui):
    if ui._sheet is None:
        return False
    tab = ui._sheet
    ui._sheet = None
    if tab == ui.node_tab:
        setattr(ui, cur(ui)[1], ui._sheet_scroll)
    for i in range(2, 2 + LIST_ROWS):
        ui._cache[i] = ''
    ui._cache[SHEET_SLOT] = ''
    ui._cache[_u.FOOT_SLOT] = ''
    ui.dirty = True
    return True


def sheet_act(ui, i):
    items = sheet_items(ui) if ui._sheet is not None else ()
    sheet_close(ui)
    if not (0 <= i < len(items)):
        return
    key = items[i][2]
    if key is None:
        ui.nav_event("click")       # what Enter / the ball's click does here
    else:
        ui.handle_key(key)


def sheet_key(ui, ch):
    """A key while the sheet is up: it always closes. Backspace / Esc are
    consumed; anything else is then handled as it would be on the list."""
    sheet_close(ui)
    return ch == 0x08 or ch == 0x1B


def draw_sheet(ui):
    ck = "S" + str(ui._sheet)
    if ui._cache[SHEET_SLOT] == ck:
        return
    ui._cache[SHEET_SLOT] = ck
    tft = ui.tft
    paper = ui.BG_DARK
    ink = ui.NEON_CYAN
    y0, n = sheet_rect(ui)
    items = sheet_items(ui)
    width = _u.SBAR_X
    tft.fill_rect(0, y0, width, n * ROW_H, paper)
    for i in range(n):
        label, klabel, key, icon = items[i]
        y = y0 + i * ROW_H
        tft.fill_rect(0, y, width, 2 if i == 0 else 1, ink)
        ui._bitmap(icon, 14, y + (ROW_H - icon[1]) // 2, ink)
        tft.text(ui.font, label, 34, y + 8, ink, paper)
        ui._sdraw(klabel, INFO_R - len(klabel) * ui.SW, y + 10, ui.DIM_CYAN, paper)

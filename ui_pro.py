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
    ui.tft.fill_rect(_u.SBAR_X, LIST_Y, _u.SBAR_W, track, ui.BG_DARK)
    if total > LIST_ROWS:
        h = max(6, track * LIST_ROWS // total)
        ui.tft.fill_rect(_u.SBAR_X, LIST_Y + getattr(ui, sa) * track // total, _u.SBAR_W, h, ui.DIM_CYAN)


def draw_node_list(ui):
    ui._draw_tab_bar()
    if ui._find:
        ui._draw_find()          # Find keeps its own single-line layout
        return
    draw_rows(ui)
    _scrollbar(ui)

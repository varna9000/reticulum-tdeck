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
SW = 6             # this module's small font: spleen 6x12, see _sd()
SH = 12
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


_small = None      # ui._SmallText(spleen_6x12), made on first use


def _sd(ui, s, x, y, fg, bg):
    """Small text, top at y. ui.py has no small font on the Pro -- the e-ink
    shim's text() draws 8 px glyphs only, so ui.SW/SH are 8/16 there and
    ui._sdraw is the main font. This layout needs a real 6x12 for the second
    line, the pill and the toolbar, so it composites one and blits it, the way
    the shell's fonts already reach this panel."""
    global _small
    if _small is None:
        try:
            import spleen_6x12
            _small = _u._SmallText(spleen_6x12)
        except ImportError:
            _small = False           # host tests without framebuf
    if _small:
        _small.draw(ui.tft, ui._tb(s), x, y, fg, bg)
    else:
        ui._sdraw(s, x, y, fg, bg)


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
    """Unread count in the margin, inverse to its row; the count stops at
    99. Two digits get 1 px of padding and sit against the panel edge, which
    leaves 2 px of paper before the name."""
    s = str(min(n, 99))
    pad = 1 if len(s) > 1 else 2
    w = len(s) * SW + 2 * pad
    x = 0 if len(s) > 1 else (MARGIN - w) // 2
    ui.tft.fill_rect(x, y, w, SH, fg)
    _sd(ui, s, x + pad, y, bg, fg)


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
        _sd(ui, hsh, MARGIN, y + 17, dim, bg)
        if info:
            _sd(ui, info, INFO_R - len(info) * SW, y + 17, dim, bg)
        for k in range(3):
            tft.fill_rect(DOTS_X + 4 + k * 5, y + 14, 3, 3, fg)
        if uc:
            _pill(ui, uc, y + 2, fg, bg)
        if fav:
            ui._bitmap(_STAR, 2, y + 18, fg)


def _scrollbar(ui):
    ia, sa, keys, table, show_unread, empty = cur(ui)
    total = len(keys)
    track = LIST_ROWS * ROW_H
    ck = (total, getattr(ui, sa))
    if ui._cache[SBAR_SLOT] == ck:
        return                       # an unchanged lane is not worth a refresh
    ui._cache[SBAR_SLOT] = ck
    # To the panel's edge: Find inks its selected row as far as x 239.
    ui.tft.fill_rect(_u.SBAR_X, LIST_Y, _u.SCREEN_W - _u.SBAR_X, track, ui.BG_DARK)
    if total > LIST_ROWS:
        h = max(6, track * LIST_ROWS // total)
        ui.tft.fill_rect(_u.SBAR_X, LIST_Y + getattr(ui, sa) * track // total, _u.SBAR_W, h, ui.DIM_CYAN)


# Icons, pixel for pixel. The canvas draws them in squares -- 14 px on the tab
# bar, 11 px on the toolbar and the sheet, a 12 px star in the row margin --
# so these fill those squares rather than a 7 px strip.
_BUBBLE = _u._icon((
    ".############.", "#............#", "#............#", "#..########..#", "#............#",
    "#............#", "#..#####.....#", "#............#", "#............#", ".############.",
    "...###........", "..###.........", ".###..........", ".#............"))
_GLOBE = _u._icon((
    "....#####....", "..##..#..##..", ".#...###...#.", ".#..#.#.#..#.", "#...#.#.#...#",
    "#..#..#..#..#", "#############", "#..#..#..#..#", "#...#.#.#...#", ".#..#.#.#..#.",
    ".#...###...#.", "..##..#..##..", "....#####...."))
_TERM = _u._icon((
    ".############.", "#............#", "#............#", "#..#.........#", "#...#........#",
    "#....#.......#", "#...#........#", "#..#..#####..#", "#............#", "#............#",
    ".############."))
_PEOPLE = _u._icon((
    "...####.......", "..#....#......", "..#....#.###..", "..#....##...#.", "...####.#...#.",
    ".........###..", "..######......", ".#......#.###.", "#........#...#", "#........#...#",
    "#........#...#"))
_GEAR = _u._icon((
    "....###....", ".#..###..#.", "..#######..", "..##...##..", "###.....###", "###.....###",
    "###.....###", "..##...##..", "..#######..", ".#..###..#.", "....###...."))
_FAV = _u._icon((
    ".....#.....", "....#.#....", "....#.#....", "####...####", "#.........#", ".##.....##.",
    "..#.....#..", "..#..#..#..", ".#..#.#..#.", ".#.#...#.#.", ".##.....##."))
_LENS = _u._icon((
    "..####.....", ".#....#....", "#......#...", "#......#...", "#......#...", "#......#...",
    ".#....#....", "..#####....", "......###..", ".......###.", "........###"))
_PING = _u._icon((
    ".#.......#.", "#..#...#..#", "#.#.....#.#", "#.#.###.#.#", "#.#.###.#.#", "#.#.###.#.#",
    "#.#.....#.#", "#..#...#..#", ".#.......#."))
_TRASH = _u._icon((
    "...#####...", "...#...#...", "###########", ".#.......#.", ".#..#.#..#.", ".#..#.#..#.",
    ".#..#.#..#.", ".#..#.#..#.", ".#..#.#..#.", ".#.......#.", ".#########."))
_STAR = _u._icon((
    ".....##.....", ".....##.....", "....####....", "....####....", "############",
    ".##########.", "..########..", "...######...", "..########..", "..###..###..",
    ".###....###.", ".#........#."))

_TAB_ICONS = (_BUBBLE, _GLOBE, _TERM, _PEOPLE)          # MSG, NET, SSH, RRC
_TOOLS = ((_u._ANTENNA, "Annc", b'a'), (_GEAR, "Setup", b's'),
          (_FAV, "Fav", b'f'), (_LENS, "Lookup", b'l'))


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
        ty = TOOL_Y + 1 + (TOOL_H - 1 - SH) // 2
        _sd(ui, "tap outside to close", 4, ty, ui.DIM_CYAN, paper)
        s = "(BKSP)close"
        x = _u.SCREEN_W - 4 - len(s) * SW
        _sd(ui, s, x, ty, ink, paper)
        ui._hint_zones.append((ui.state, x, _u.SCREEN_W, b'\x08'))
        return
    cw = _u.SCREEN_W // 4
    ty = TOOL_Y + 1 + (TOOL_H - 1 - SH) // 2
    for i in range(4):
        ic, label, key = _TOOLS[i]
        x = i * cw + (cw - (ic[0] + 3 + len(label) * SW)) // 2
        ui._bitmap(ic, x, TOOL_Y + 1 + (TOOL_H - 1 - ic[1]) // 2, ink)
        lx = x + ic[0] + 3
        _sd(ui, label, lx, ty, ink, paper)
        # The small font has no bold face; an underline marks the hotkey.
        tft.fill_rect(lx, ty + SH - 1, SW - 1, 1, ink)
        ui._hint_zones.append((ui.state, i * cw, (i + 1) * cw, key))


def _clamp(ui):
    """Bring the window to the selection. The keys that step the selection
    scroll with it, but a delete, a Find pick, a message bubbling its sender
    up and an eviction all move it with ui.py's own row count in mind, which
    on this layout is twice what is on screen."""
    ia, sa, keys = cur(ui)[:3]
    sel = getattr(ui, ia)
    sc = getattr(ui, sa)
    n = sc
    if sel < n:
        n = sel
    elif sel >= n + LIST_ROWS:
        n = sel - LIST_ROWS + 1
    n = max(0, min(n, len(keys) - LIST_ROWS))
    if n != sc:
        setattr(ui, sa, n)


def draw_node_list(ui):
    draw_tab_bar(ui)
    if ui._find:
        ui._sheet = None
        if ui._tools_up:
            # Find paints its own footer text lower down; clear the toolbar's
            # band once. (A flag, not the cache slot: opening Find wipes the
            # whole cache before this runs.)
            ui._tools_up = False
            ui._cache[_u.FOOT_SLOT] = ''
            ui.tft.fill_rect(0, TOOL_Y, _u.SCREEN_W, TOOL_H, ui.BG_DARK)
        ui._draw_find()          # Find keeps its own single-line layout
        return
    if ui._sheet is not None and (ui._sheet != ui.node_tab or not cur(ui)[2]):
        sheet_close(ui)              # the tab changed or the list emptied under it
    ui._tools_up = True
    if ui._sheet is not None:
        draw_rows(ui, LIST_ROWS - len(sheet_items(ui)))
        _scrollbar(ui)
        draw_sheet(ui)
    else:
        _clamp(ui)
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
    sw = SW
    ny = (NAV_H - SH) // 2
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
        _sd(ui, bat, 4 * _u.CHAR_W, ny, ui.NEON_GREEN, hb)
    if ui._nav_mid_cache != mid_key:
        ui._nav_mid_cache = mid_key
        tft.fill_rect(left_end, 0, W - left_end, NAV_H, hb)
        rx = W - sw - len(right) * sw
        if right:
            _sd(ui, right, rx, ny, ui.NEON_CYAN, hb)
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
            _sd(ui, center, x + iw, ny, col, hb)
    ui._cache[0] = bat_key + mid_key


# --- the actions sheet ---------------------------------------------------------
# A touch menu for the selected row. It adds nothing the keys cannot do: each
# item closes the sheet and then does exactly what its key does.

_OPEN = {0: "Open chat", 1: "Open page", 2: "Open shell", 3: "Open hub"}   # by tab id


def sheet_items(ui):
    """(label, key label, key bytes or None for the click, icon), top to bottom."""
    items = [(_OPEN[ui.node_tab], "Enter", None, _TAB_ICONS[ui.node_tab]),
             ("Favorite", "F", b'f', _FAV)]
    if ui.node_tab == _u.TAB_MSG:
        items.append(("Ping", "P", b'p', _PING))
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
        _sd(ui, klabel, INFO_R - len(klabel) * SW, y + 10, ui.DIM_CYAN, paper)


# --- chat ------------------------------------------------------------------------
# A DM thread as blocks: consecutive messages from one sender, the time on the
# block. One tuple per 16 px row, so ui.py's scrolling, cursor and row cache
# carry on unchanged: (kind, mine, text, mid, flags, extra, status).

K_HDR = 0          # small "name . time" line opening a block
K_INLINE = 1       # a whole one-line block: "name: text", time on the right
K_TEXT = 2
K_TOP = 3          # upper half of a media tile
K_BOT = 4          # lower half
F_LAST = 1         # last row of its block: carries the rule
F_IMG = 2
F_VOICE = 4
CHAT_Y = 42
PEER_X = 6
PEER_COLS = 28
ME_X = 16
ME_COLS = 26
BLOCK_GAP = 600    # seconds of silence that end a block
_MARKS = {1: "..", 2: "\xfb", 3: "!", 4: "~", 5: ">"}


def _markers(text):
    """(text without its [image ...] / [voice ...] markers, {kind: caption})."""
    caps = {}
    for kw in ("image", "voice"):
        p = text.find("[" + kw)
        if p < 0:
            continue
        e = text.find("]", p)
        if e < 0:
            e = len(text)
        caps[kw] = text[p + 1 + len(kw):e].strip()
        text = text[:p] + text[e + 1:]
    return text.strip(), caps


def _block(ui, rows, msgs, mine, name):
    cols = ME_COLS if mine else PEER_COLS
    x0 = ME_X if mine else PEER_X
    first = msgs[0]
    tm = _u._fmt_time(first[2])
    if len(msgs) == 1 and not first[4] and not first[5] and "\n" not in first[1]:
        st = first[3] if (mine and first[3] in _MARKS) else 0
        # My rows always keep two columns for the delivery mark: a proof
        # arriving must change one row's mark, not reflow the thread.
        right = (len(tm) * SW + 4 if tm else 0) + (2 * _u.CHAR_W + 4 if mine else 0)
        line = name + ": " + first[1].replace("\r", "")
        if len(line) * _u.CHAR_W <= cols * _u.CHAR_W - right:
            rows.append((K_INLINE, mine, line, first[6], F_LAST, tm, st))
            return
    rows.append((K_HDR, mine, name + (" \xfa " + tm if tm else ""), first[6], 0, "", 0))
    for m in msgs:
        text, caps = _markers(m[1]) if (m[4] or m[5]) else (m[1], {})
        st = m[3] if (mine and m[3] in _MARKS) else 0
        mark_at = -1
        if m[4]:
            mark_at = len(rows)
            cap = caps.get("image", "")
            rows.append((K_TOP, mine, "image", m[6], F_IMG, cap, 0))
            rows.append((K_BOT, mine, "image", m[6], F_IMG, cap, 0))
        if m[5]:
            mark_at = len(rows)
            cap = caps.get("voice", "")
            cap = (cap + " - " if cap else "") + "tap to play"
            rows.append((K_TOP, mine, "voice", m[6], F_VOICE, cap, 0))
            rows.append((K_BOT, mine, "voice", m[6], F_VOICE, cap, 0))
        if text:
            w = cols - 3 if mine else cols      # mark columns, kept whatever the status
            for para in text.replace("\r", "").split("\n"):
                for wl in ui._wrap_text(para, w):
                    rows.append((K_TEXT, mine, wl, m[6], 0, "", 0))
            mark_at = len(rows) - 1
        if st and mark_at >= 0:
            r = rows[mark_at]
            rows[mark_at] = (r[0], r[1], r[2], r[3], r[4], r[5], st)
    r = rows[-1]
    rows[-1] = (r[0], r[1], r[2], r[3], r[4] | F_LAST, r[5], r[6])


def chat_lines(ui):
    msgs = ui.chat_history.get(ui.selected_peer, [])
    peer = ui.peers.get(ui.selected_peer)
    pname = _u._ascii((peer.get("name") if peer else None) or "?")[:8]
    rows = []
    i = 0
    n = len(msgs)
    while i < n:
        mine = msgs[i][0]
        j = i + 1
        while j < n and msgs[j][0] == mine and msgs[j][2] - msgs[j - 1][2] <= BLOCK_GAP:
            j += 1
        _block(ui, rows, msgs[i:j], mine, "me" if mine else pname)
        i = j
    return rows


ME_BAR_X = 233


# Tile icons: 20 px wide, drawn over the tile's two rows.
_PIC = _u._icon((
    ".##################.", "#..................#", "#..................#", "#...###............#",
    "#..#####...........#", "#..#####...........#", "#...###......#.....#", "#...........###....#",
    "#.....#....#####...#", "#....###..#######..#", "#...#####.########.#", "#..#################",
    "#.##################", "#..................#", "#..................#", ".##################."))
_WAVE = _u._icon((
    ".........##.........", ".........##.........", ".....##..##.........", ".....##..##..##.....",
    ".....##..##..##.....", ".##..##..##..##.....", ".##..##..##..##..##.", ".##..##..##..##..##.",
    ".##..##..##..##..##.", ".##..##..##..##..##.", ".##..##..##..##.....", ".....##..##..##.....",
    ".....##..##..##.....", ".....##..##.........", ".........##.........", ".........##........."))

TILE_W = 150


def _tile(ui, r, x0, y, fg, bg, expired):
    """One 16 px half of a media tile; the two halves stack into a 30 px
    framed box, so a tile cut by the window's edge still draws what is in
    view."""
    tft = ui.tft
    top = r[0] == K_TOP
    h = 16 if top else 14
    if top:
        tft.text(ui.font, r[2], x0 + 32, y, fg, bg)
        if expired:
            tft.fill_rect(x0 + 32, y + 8, len(r[2]) * _u.CHAR_W, 1, fg)
        tft.fill_rect(x0, y, TILE_W, 1, fg)
    else:
        _sd(ui, "expired" if expired else r[5], x0 + 32, y, fg, bg)
        tft.fill_rect(x0, y + 13, TILE_W, 1, fg)
    tft.fill_rect(x0, y, 1, h, fg)
    tft.fill_rect(x0 + TILE_W - 1, y, 1, h, fg)
    icon = _PIC if r[4] & F_IMG else _WAVE
    off = 7 if top else -9           # the icon's 16 rows start 7 px into the tile
    for dx, dy, w in icon[2]:
        yy = dy + off
        if 0 <= yy < h:
            tft.fill_rect(x0 + 6 + dx, y + yy, w, 1, fg)


def draw_chat(ui):
    tft = ui.tft
    paper = ui.BG_DARK
    ink = ui.NEON_CYAN
    W = _u.SCREEN_W
    CH = _u.CHAR_H
    CW = _u.CHAR_W
    peer = ui.peers.get(ui.selected_peer)
    pname = _u._ascii(peer.get("name") or "?")[:20] if peer else "?"
    phash = "[" + ui.selected_peer.hex()[:8] + "]"
    ck = "C" + pname + phash
    if ui._cache[1] != ck:
        ui._cache[1] = ck
        tft.fill_rect(0, TAB_Y, W, TAB_H, paper)
        tft.text(ui.font, ui._tb("< " + pname), 4, TAB_Y + 3, ink, paper)
        _sd(ui, phash, 232 - len(phash) * SW, TAB_Y + 5, ui.DIM_CYAN, paper)
        tft.fill_rect(0, TAB_Y, W, 1, ui.DIM_CYAN)
        tft.fill_rect(0, TAB_Y + TAB_H - 1, W, 1, ink)
        tft.fill_rect(0, 299, W, 1, ui.DIM_CYAN)      # where the body frame's rail was

    lines = ui._build_chat_lines()
    nrows = _u.BODY_ROWS - 1
    total = len(lines)
    max_scroll = max(0, total - nrows)
    if ui.chat_scroll > max_scroll:
        ui.chat_scroll = max_scroll
    view_end = max(0, total - ui.chat_scroll)
    visible = lines[max(0, view_end - nrows):view_end]
    if ui.chat_cursor >= len(visible):
        ui.chat_cursor = len(visible) - 1
    cur = visible[ui.chat_cursor] if ui.chat_cursor >= 0 else None
    vimg = {}
    vaud = {}
    vmsg = {}
    width = _u.SBAR_X
    for i in range(nrows):
        y = CHAT_Y + i * CH
        ci = i + 2
        if i >= len(visible):
            if ui._cache[ci] != "-":
                ui._cache[ci] = "-"
                tft.fill_rect(0, y, width, CH, paper)
            continue
        r = visible[i]
        kind, mine, text, mid, flags, extra, status = r
        vmsg[i] = mid
        tile = kind >= K_TOP
        expired = False
        if tile:
            if flags & F_IMG:
                vimg[i] = mid
                expired = (ui.selected_peer, mid) not in ui._image_cache
            else:
                vaud[i] = mid
        hl = i == ui.chat_cursor or (tile and cur is not None and cur[0] >= K_TOP
                                     and cur[3] == mid and (cur[4] & 6) == (flags & 6))
        ck = (chr(48 + kind) + ("m" if mine else "p") + text + "\x00" + extra + "\x00"
              + chr(48 + flags) + chr(48 + status) + ("H" if hl else "") + ("X" if expired else ""))
        if ui._cache[ci] == ck:
            continue
        ui._cache[ci] = ck
        bg = ui.SEL_BG if hl else paper
        fg = paper if hl else ui.BODY_FG
        dim = paper if hl else ui.DIM_CYAN
        x0 = ME_X if mine else PEER_X
        xr = x0 + (ME_COLS if mine else PEER_COLS) * CW
        tft.fill_rect(0, y, width, CH, bg)
        tft.fill_rect(ME_BAR_X if mine else 0, y, 2, CH, fg)
        if kind == K_HDR:
            _sd(ui, text, x0, y + 2, dim, bg)
        elif tile:
            _tile(ui, r, x0, y, fg, bg, expired)
        else:
            tft.text(ui.font, ui._tb(text), x0, y, fg, bg)
        x = xr
        if kind == K_INLINE and extra:
            x -= len(extra) * SW
            _sd(ui, extra, x, y + 2, dim, bg)
            x -= 4
        if status and kind != K_BOT:
            mk = _MARKS[status]
            tft.text(ui.font, ui._tb(mk), x - len(mk) * CW, y, fg, bg)
        if flags & F_LAST:
            tft.fill_rect(0, y + CH - 1, width, 1, dim)
    ui._visible_image_lines = vimg
    ui._visible_audio_lines = vaud
    ui._visible_msg_lines = vmsg

    track = nrows * CH
    if total > nrows:
        bar_h = max(6, track * nrows // total)
        bar_y = CHAT_Y + (max_scroll - ui.chat_scroll) * (track - bar_h) // max(1, max_scroll)
        sk = str(bar_y) + ":" + str(bar_h)
    else:
        sk = "none"
    if ui._cache[_u.FOOT_SLOT] != sk:
        ui._cache[_u.FOOT_SLOT] = sk
        tft.fill_rect(_u.SBAR_X, CHAT_Y, W - _u.SBAR_X, track, paper)
        if total > nrows:
            tft.fill_rect(_u.SBAR_X, bar_y, _u.SBAR_W, bar_h, ui.DIM_CYAN)


# --- recording ---------------------------------------------------------------------
# Two targets, because a tap has no key identity: the upper half sends, the
# lower half cancels. Painted once per phase -- a redraw during capture holds
# the GIL and starves the mic thread.

REC_MID = 178

# Drawn at twice their size (see _bitmap2), so they read from arm's length.
_SEND = _u._icon((
    "...........##", ".........####", ".......#####.", ".....#######.", "...########..", ".##########..",
    "...#######...", "......####...", "......####...", ".......##....", ".......##....", ".......#....."))
_CROSS = _u._icon((
    "#.......#", "##.....##", ".##...##.", "..##.##..", "...###...", "..##.##..", ".##...##.", "##.....##",
    "#.......#"))


def _bitmap2(ui, icon, x, y, c):
    for dx, dy, w in icon[2]:
        ui.tft.fill_rect(x + 2 * dx, y + 2 * dy, 2 * w, 2, c)


def _disc(tft, cx, cy, r, c):
    rr = r * r + r                   # (r + 0.5)^2: no one-pixel nubs at the poles
    for dy in range(-r, r + 1):
        w = int((rr - dy * dy) ** 0.5)
        tft.fill_rect(cx - w, cy + dy, 2 * w + 1, 1, c)


def draw_recording(ui):
    tft = ui.tft
    paper = ui.BG_DARK
    ink = ui.NEON_CYAN
    W = _u.SCREEN_W
    CW = _u.CHAR_W
    warm = ui._rec_warming
    title = ("Warming mic... %ds" % ui._rec_warm_secs) if warm else "* Recording *"
    if ui._cache[1] != title:
        ui._cache[1] = title
        tft.fill_rect(0, TAB_Y, W, 44, paper)
        tft.text(ui.font, title, (W - len(title) * CW) // 2, 46, ink, paper)
    ck = "W" if warm else "R"
    if ui._cache[2] == ck:
        return
    ui._cache[2] = ck
    tft.fill_rect(0, 64, W, 236, paper)
    cx = W // 2
    if not warm:
        _disc(tft, cx, 104, 28, ink)
        _bitmap2(ui, _SEND, cx - 14, 92, paper)
        s = "tap to SEND"
        tft.text(ui.font, s, (W - len(s) * CW) // 2, 140, ink, paper)
    tft.fill_rect(12, REC_MID, W - 24, 1, ink)
    _disc(tft, cx, 222, 22, ink)
    _disc(tft, cx, 222, 19, paper)
    _bitmap2(ui, _CROSS, cx - 9, 213, ink)
    s = "tap to CANCEL"
    tft.text(ui.font, s, (W - len(s) * CW) // 2, 252, ink, paper)
    ui._hint_zones = []
    s = "BKSP=cancel" if warm else "Enter=send  BKSP=cancel"
    tft.fill_rect(0, 299, W, 1, ui.DIM_CYAN)
    tft.text(ui.font, _u._pad(s.center(_u.COLS)), 0, _u.INPUT_Y, ui.DIM_CYAN, paper)

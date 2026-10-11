# Touch dispatch for the T-Deck Pro: what a gesture at a point means on the
# screen that is up, expressed as calls the UI already has. The recogniser
# (touch_gestures.py) knows no screens; this file knows no hardware.
#
# Laid out for today's single-line rows: a tap selects a row, a tap on the
# selected row acts on it. Nothing opens on a first tap of an unselected row
# -- the rows are 3 mm tall. Each later screen redesign edits only the zones
# here.

import time

import ui as _u
from touch_gestures import Recognizer, UP, TAP, LONG, DRAG, FLICK, SWIPE, BACK

_FOOT_REACH = 8          # px above the hint row that still count as the footer
_FOOT_SNAP = 8           # a footer tap this close to a hint goes to it
_PAGE = _u.BODY_ROWS - 2
PENDING_MAX_MS = 1500    # how long a redraw may stay "pending" before taps count again
DRAG_HOLD_MS = 300       # a drag's redraw waits this long after DOWN for the finger to lift
_STAMP_MAX_AGE = 1 << 28 # ticks_diff is only valid within 2**29 ms (~6 days)
_drag_acc = [None, 0]    # [stroke t_down, 16 px steps not yet turned into rows]


def _pro_list(ui):
    """The Pro's own node list is up (ui_pro.py): 32 px rows, one tap opens."""
    return _u._pro is not None and ui.state == _u.STATE_NODES and not ui._find


def _pro_panel(ui):
    """An RRC panel is open on the Pro layout: five 32 px rows."""
    return (_u._pro is not None and ui._rrc_panel
            and ui.state in (_u.STATE_RRC_ROOMS, _u.STATE_RRC_CHAT))


def _pro_find(ui):
    """Lookup is showing results on the Pro layout: two-line rows from y 58."""
    return (_u._pro is not None and ui.state == _u.STATE_NODES and ui._find
            and len(ui._find_res) > 0 and not ui._find_listen)


def _pro_wifi(ui):
    """Setup's WiFi scan list on the Pro layout: two-line rows from y 42."""
    return (_u._pro is not None and ui.state == _u.STATE_SETTINGS
            and ui._settings_page == _u._SET_WIFI_SCAN and len(ui._wifi_networks) > 0)


def _pro_menu(ui):
    """The shell's control menu on the Pro layout."""
    return _u._pro is not None and ui.state == _u.STATE_SHELL and ui._shell_menu


def _menu_clamp(ui, steps):
    """The shell menu's keys wrap around; a finger dragging past an end must
    stop there, not jump to the other one."""
    idx = ui._shell_menu_idx
    return max(-idx, min(len(_u._SHELL_CTRL_ITEMS) - 1 - idx, steps))


def _tap_pro_rows(ui, y):
    """One tap acts on these three lists, as everywhere on the Pro."""
    p = _u._pro
    if _pro_find(ui):
        i = (y - p.FIND_Y) // p.ROW_H
        idx = ui._find_scroll + i
        if i < 0 or i >= p.FIND_ROWS or idx >= len(ui._find_res):
            return False
        ui._find_sel = idx
        ui.dirty = True
        ui._find_pick()                    # Enter's action, now: a queued click could find the
        return True                        # selection reset by a key handled in the same pass
    i = (y - p.LIST_Y) // p.ROW_H
    if i < 0 or i >= p.MENU_ROWS:
        return False
    if _pro_wifi(ui):
        idx = ui._settings_scroll + i
        if idx >= len(ui._wifi_networks):
            return False
        ui._settings_idx = idx
        ui.dirty = True
        ui.nav_event("click")              # on to the password page
        return True
    idx = p.shell_menu_top(ui) + i         # the shell menu
    if idx >= len(_u._SHELL_CTRL_ITEMS):
        return False
    armed = p.menu_armed(ui, idx)          # judged before the cursor moves
    ui._menu_armed = None
    ui._shell_menu_idx = idx
    ui.dirty = True
    ui._menu_gen = getattr(ui, "_menu_gen", 0) + 1     # in layout_key: no second act inside one refresh
    if _u._SHELL_CTRL_ITEMS[idx][1] == "quit" and not armed:
        ui._menu_armed = (idx, ui._cache)  # it ends the session: a second tap confirms
        ui._cache[2 + i] = ''              # its row now says "tap again to quit"
        return True
    ui._shell_menu_exec()
    return True


def _pro_setup(ui):
    """The Pro's own Setup main page is up (ui_pro.py): 32 px rows, one tap acts."""
    return (_u._pro is not None and ui.state == _u.STATE_SETTINGS
            and ui._settings_page == _u._SET_MAIN)


def _tap_pro_setup(ui, y):
    p = _u._pro
    i = (y - p.LIST_Y) // p.ROW_H
    if i < 0 or i >= p.SETUP_ROWS:
        return False
    idx = p.SETUP_IDX[min(ui._settings_scroll + i, len(p.SETUP_IDX) - 1)]
    armed = getattr(ui, "_setup_armed", -1)
    ui._setup_armed = -1
    ui._settings_idx = idx
    ui.dirty = True
    if idx == 3 and ui.lora_online:
        return True                        # a click would reset a healthy radio: select only
    if (idx == 0 and ui._wifi_connected) or (idx == 1 and ui._tcp_enabled):
        # A click here drops WiFi/TCP, which also clears every chat. It
        # takes a tap that selects and a tap that confirms, however the row
        # came to be selected: Setup opens with WiFi already selected.
        if armed != idx:
            ui._setup_armed = idx
            return True
    # One tap acts: the rows are finger-sized. The count goes into
    # layout_key, so a second tap landing in this one's refresh is dropped
    # instead of toggling the row back.
    ui._setup_gen = getattr(ui, "_setup_gen", 0) + 1
    ui.nav_event("click")
    return True


def _panel(ui):
    """(module, rows) for an open RRC panel, else (None, None)."""
    if not (ui._rrc_panel and ui.state in (_u.STATE_RRC_ROOMS, _u.STATE_RRC_CHAT)):
        return None, None
    import rrc_ui
    return rrc_ui, (rrc_ui.PANEL_X, rrc_ui.PANEL_X + rrc_ui.PANEL_W, rrc_ui.PANEL_Y + 22,
                    rrc_ui.PANEL_ROWS, ui._rrc_panel_scroll, ui._rrc_panel_idx,
                    rrc_ui._panel_len(ui))


def _lists(ui):
    """(index attr, scroll attr, keys) for the node-list tab that is up."""
    t = ui.node_tab
    if t == _u.TAB_MSG:
        return "selected_idx", "node_scroll", ui._peer_keys
    if t == _u.TAB_NET:
        return "net_idx", "net_scroll", ui._node_keys
    if t == _u.TAB_RRC:
        return "_rrc_idx", "_rrc_scroll", ui._rrc_keys
    return "ssh_idx", "ssh_scroll", ui._shell_keys


def layout_key(ui):
    """A fingerprint of what is where on the screen, selection included. A
    stroke that began before this last changed -- or before the change
    reached the panel -- was aimed at a screen the user was not looking at.

    The selection is in it on purpose: "tap the selected row to open it" is
    only safe if the user has SEEN which row is selected."""
    st = ui.state
    g = getattr
    if st == _u.STATE_NODES:
        ia, sa, keys = _lists(ui)
        sc = g(ui, sa)
        # One tap opens a row on the Pro layout, so WHO is on each row is part
        # of the layout: a message that reorders the list under a finger must
        # not open the neighbour's chat.
        vis = tuple(keys[sc:sc + _u.LIST_ROWS]) if _u._pro is not None else None
        return (st, ui.node_tab, sc, g(ui, ia), len(keys), ui._find,
                g(ui, "_find_scroll", 0), g(ui, "_find_sel", 0), len(g(ui, "_find_res", ())),
                ui._sheet, vis)
    if st == _u.STATE_CHAT:
        hist = ui.chat_history.get(ui.selected_peer, ())
        # The last message's id and the row count, not just the history's
        # length: a full history drops one message for each it gains, and
        # every row moves under the finger with the length unchanged.
        return (st, ui.selected_peer, ui.chat_scroll, ui.chat_cursor, len(ui.cmd_buf) > 0,
                len(hist), hist[-1][6] if hist else 0, len(ui._build_chat_lines()))
    if st == _u.STATE_BROWSER:
        return (st, ui.browser_path, ui.browser_scroll, ui.browser_cursor)
    if st == _u.STATE_SETTINGS:
        return (st, ui._settings_page, g(ui, "_settings_scroll", 0), g(ui, "_settings_idx", 0),
                g(ui, "_lora_field", 0), g(ui, "_setup_gen", 0), len(ui._wifi_networks),
                ui._wifi_connected, ui._tcp_enabled, ui.lora_online)
    if st == _u.STATE_SHELL:
        return (st, ui._shell_menu, g(ui, "_shell_view", 0), g(ui, "_shell_menu_idx", 0),
                g(ui, "_menu_gen", 0))
    if st in (_u.STATE_RRC_ROOMS, _u.STATE_RRC_CHAT):
        lst = None
        if ui._rrc_panel:
            # One tap acts on a panel row on the Pro, so what the list holds
            # is part of the layout: a member joining or a /list reply must
            # not move another row under a finger already on its way down.
            src = ui._rrc_rooms if g(ui, "_rrc_panel_kind", None) == "rooms" else ui._rrc_roster
            sc = g(ui, "_rrc_panel_scroll", 0)
            lst = (len(src), tuple(src[max(0, sc - 1):sc + 5]))   # the rows on screen (the picker's first is the join row)
        return (st, ui._rrc_panel, g(ui, "_rrc_panel_kind", None), g(ui, "_rrc_panel_scroll", 0),
                g(ui, "_rrc_panel_idx", 0), ui._rrc_prompt, lst)
    return (st,)


def _rows(ui):
    """The tappable rows on this screen: (x0, x1, y0, visible, top, sel,
    total) or None. Row i is at y0 + i * CHAR_H and is item top + i."""
    st = ui.state
    y0 = _u.BODY_Y + _u.CHAR_H
    full = (0, _u.SCREEN_W)
    mod, prow = _panel(ui)
    if prow is not None:
        return prow
    if st == _u.STATE_NODES:
        if ui._find:
            return full + (y0 + _u.CHAR_H, ui._find_rows(), ui._find_scroll,
                           ui._find_sel, len(ui._find_res))
        ia, sa, keys = _lists(ui)
        return full + (y0, _u.LIST_ROWS, getattr(ui, sa), getattr(ui, ia), len(keys))
    if st == _u.STATE_CHAT:
        n = _u.BODY_ROWS - 1
        return full + (y0, n, 0, ui.chat_cursor, n)
    if st == _u.STATE_BROWSER:
        n = _u.BODY_ROWS - 1
        return full + (y0, n, 0, ui.browser_cursor,
                       min(n, max(0, len(ui.browser_lines) - ui.browser_scroll)))
    if st == _u.STATE_SETTINGS:
        n = _u.BODY_ROWS - 1
        if ui._settings_page == _u._SET_MAIN:
            # Drawn windowed from the selection (_draw_settings_main).
            return full + (y0, n, max(0, ui._settings_idx - n + 1), ui._settings_idx,
                           _u._SET_MAIN_LAST + 1)
        if ui._settings_page == _u._SET_WIFI_SCAN:
            return full + (y0, n, ui._settings_scroll, ui._settings_idx, len(ui._wifi_networks))
        return None
    if st == _u.STATE_SHELL and ui._shell_menu:
        return full + (y0, _u.BODY_ROWS - 1, 0, ui._shell_menu_idx, len(_u._SHELL_CTRL_ITEMS))
    return None


def _steps(ui, n):
    """n navigation steps: + down, - up. The UI's own handle_trackball()
    applies them on the same keyboard pass."""
    name = "down" if n > 0 else "up"
    for _ in range(abs(n)):
        ui.nav_event(name)


def _tap_row(ui, rows, y):
    x0, x1, y0, visible, top, sel, total = rows
    i = (y - y0) // _u.CHAR_H
    if i < 0 or i >= visible:
        return False
    target = top + i
    if target >= total:
        return False
    if target == sel:
        ui.nav_event("click")
    elif sel < 0:
        _steps(ui, target + 1)        # a cursor screen: the first step lands on row 0
    else:
        _steps(ui, target - sel)
    return True


def _footer_live(ui):
    """False while a text field owns the footer line. A hint painted there
    earlier must not fire under the text that replaced it: on those lines
    Enter submits (a half-typed password, a shell command) and Backspace
    eats a character, and the footer sits right above the keyboard."""
    st = ui.state
    if st == _u.STATE_NODES:
        return not ui._find
    if st == _u.STATE_CHAT:
        return not len(ui.cmd_buf)
    if st == _u.STATE_SETTINGS:
        return ui._settings_page not in ui._TEXT_ENTRY_PAGES
    if st == _u.STATE_SHELL:
        return ui._shell_menu or not len(ui._shell_input)
    if st == _u.STATE_RRC_ROOMS:
        return not ui._rrc_prompt
    if st == _u.STATE_RRC_CHAT:
        return False
    return True


def _tap_footer(ui, x):
    if not _footer_live(ui):
        return False
    best = None
    dist = _FOOT_SNAP + 1
    for st, x0, x1, key in ui._hint_zones:
        if st != ui.state or key is None:
            continue
        d = 0 if x0 <= x < x1 else (x0 - x if x < x0 else x - x1 + 1)
        if d < dist:
            dist, best = d, key
    if best is None:
        return False
    if (_pro_setup(ui) or _pro_menu(ui)) and best in ("click", b'\r'):
        return False                  # rows act under the finger there; this would act on another
    if best == "click":
        ui.nav_event("click")         # NOT Enter: in the shell Enter goes to the remote
        return True
    if ui.state == _u.STATE_SHELL and best == b'\x08':
        if ui._shell_menu:
            ui._shell_menu_close()
            return True
        return False                  # (BKSP)quit: one tap must not drop a live session
    ui.handle_key(best)
    return True


def _tap_header(ui, x):
    st = ui.state
    if st == _u.STATE_NODES:
        if ui._find:
            return False
        tab = x * _u.N_TABS // _u.SCREEN_W
        if tab == ui.node_tab:
            return False
        ui._switch_tab(tab)
        return True
    if st in (_u.STATE_RRC_ROOMS, _u.STATE_RRC_CHAT):
        if ui._rrc_prompt:
            return False
        if _u._pro is not None and x < 40 and not ui._rrc_panel:
            return _back(ui, True)    # the "<" at the left of the band, as on chat and Setup
        ui.nav_event("click")         # the room / member panel, as the ball's click
        return True
    if st == _u.STATE_SHELL:
        if _pro_menu(ui):
            ui._shell_menu_close()        # the "< Control keys" band
            return True
        return False
    return _back(ui, True)


def _back(ui, from_header):
    """Leave this screen the way its own back key does. Never discards
    typed text: with a draft in the composer this does nothing."""
    st = ui.state
    if st == _u.STATE_CHAT:
        if len(ui.cmd_buf):
            return False
        ui.handle_key(b'\x1b')
        return True
    if st == _u.STATE_BROWSER:
        if from_header:
            ui._browser_exit()        # the title: straight out to the NET tab
        else:
            ui._browser_back()        # one page back, out at the bottom of the stack
        return True
    if st == _u.STATE_IMAGE:
        ui._exit_image_view()
        return True
    if st == _u.STATE_RECORDING:
        ui.handle_key(b'\x08')
        return True
    if st == _u.STATE_NODES:
        if ui._find:
            if getattr(ui, "_find_q", ""):
                return False          # a hand-typed hash is not thrown away by a swipe
            ui._find_close()
            return True
        return False
    if st == _u.STATE_SETTINGS:
        if ui._settings_page in ui._TEXT_ENTRY_PAGES and not from_header:
            return False              # Esc there cancels what was typed: not on a stray edge swipe
        ui.handle_key(b'\x1b')       # a tap on the "< title" header is deliberate
        return True
    if st == _u.STATE_SHELL:
        if ui._shell_menu:
            ui._shell_menu_close()
            return True
        return False                  # an edge swipe must not drop a live session
    if st in (_u.STATE_RRC_ROOMS, _u.STATE_RRC_CHAT):
        import rrc_ui
        if ui._rrc_panel:
            rrc_ui.panel_close(ui)
            return True
        if ui._rrc_prompt:
            return False
        if st == _u.STATE_RRC_CHAT and ui._rrc_input:
            return False
        rrc_ui.handle_key(ui, 8, b'\x08')
        return True
    return False


def _pro_row(ui, y):
    """Index into the list of the Pro row at y, or -1."""
    p = _u._pro
    i = (y - p.LIST_Y) // p.ROW_H
    ia, sa, keys = _lists(ui)
    if y < p.LIST_Y or i >= _u.LIST_ROWS:
        return -1
    t = getattr(ui, sa) + i
    return t if t < len(keys) else -1


def _pro_select(ui, target):
    setattr(ui, _lists(ui)[0], target)     # visible by construction: no scroll needed
    ui.dirty = True


def _tap_pro_list(ui, x, y):
    p = _u._pro
    if ui._sheet is not None:
        y0, n = p.sheet_rect(ui)
        if y0 <= y < p.TOOL_Y and x < _u.SBAR_X:
            p.sheet_act(ui, (y - y0) // p.ROW_H)
        else:
            p.sheet_close(ui)              # anywhere else: just close
        return True
    if y >= p.TOOL_Y:                      # the list's last row runs right up to it
        return _tap_footer(ui, x)
    if y < p.LIST_Y:
        if y < _u.NAV_H and x >= _u.SCREEN_W * 2 // 3:
            ui.handle_key(b's')
            return True
        return _tap_header(ui, x)
    target = _pro_row(ui, y)
    if target < 0:
        return False
    _pro_select(ui, target)
    if x >= p.DOTS_X:
        return p.sheet_open(ui)
    ui.nav_event("click")                  # one tap opens: the rows are finger-sized
    return True


def _tap_pro_chat(ui, y):
    """The Pro's chat: tiles are the only tap targets in the body. A tap on
    text does nothing -- the time is on every block, so a selection would buy
    a 700 ms refresh and no information."""
    i = -1
    ys = getattr(ui, "_chat_ys", ())       # where draw_chat put each row: blocks have gaps
    for k in range(len(ys)):
        if ys[k] <= y < ys[k] + _u.CHAR_H:
            i = k
            break
    if i < 0:
        return False
    mid = ui._visible_image_lines.get(i)
    if mid is not None:
        if (ui.selected_peer, mid) not in ui._image_cache:
            return False                   # expired: nothing to open
    elif i not in ui._visible_audio_lines:
        return False
    else:
        # A voice clip plays where it is: borrow the cursor for the call and
        # put it back, or the tile stays inverted and the mic hint is gone.
        old = ui.chat_cursor
        ui.chat_cursor = i
        ui._chat_open_cursor_media()
        ui.chat_cursor = old
        return True
    ui.chat_cursor = i
    ui.dirty = True
    ui._chat_open_cursor_media()
    return True


def _tap(ui, x, y):
    st = ui.state
    if _pro_list(ui):
        return _tap_pro_list(ui, x, y)
    if st == _u.STATE_IMAGE:
        ui._exit_image_view()
        return True
    if st == _u.STATE_RECORDING:
        # Nothing may redraw while the mic thread runs, so the whole panel is
        # two targets: the upper half sends, the lower half cancels.
        if _u._pro is not None:
            mid = _u._pro.REC_MID          # the divider the Pro screen draws
        else:
            mid = _u.BODY_Y + (_u.BODY_ROWS // 2) * _u.CHAR_H
        ui.handle_key(b'\r' if y < mid else b'\x08')
        return True
    if _pro_setup(ui) and _u._pro.LIST_Y <= y < _u._pro.TOOL_Y:
        return _tap_pro_setup(ui, y)
    if _pro_find(ui) and _u._pro.FIND_Y <= y < _u._pro.FIND_Y + _u._pro.FIND_ROWS * _u._pro.ROW_H:
        return _tap_pro_rows(ui, y)
    if (_pro_wifi(ui) or _pro_menu(ui)) and _u._pro.LIST_Y <= y < _u._pro.TOOL_Y:
        return _tap_pro_rows(ui, y)
    if y >= _u.SEP_Y - 6:
        if _pro_panel(ui):
            # Outside the box closes it. The footer's (ENT) hint must not
            # reach Enter here: with a panel up Enter acts on its cursor row,
            # so the hint that opened the picker would join a room.
            import rrc_ui
            rrc_ui.panel_close(ui)
            return True
        return _tap_footer(ui, x)
    mod, prow = _panel(ui)
    if prow is not None:
        inside = (prow[0] <= x < prow[1] and mod.PANEL_Y <= y < mod.PANEL_Y + mod.PANEL_H)
        if not inside:
            mod.panel_close(ui)
            return True
        if _u._pro is not None:
            # The Pro's panel has 32 px two-line rows and one tap acts. The
            # title and foot bands and empty slots are not targets.
            i = (y - prow[2]) // _u._pro.ROW_H
            if i < 0 or i >= prow[3] or prow[4] + i >= prow[6]:
                return False
            ui._rrc_panel_idx = prow[4] + i
            ui.dirty = True
            mod.panel_click(ui)
            return True
        return _tap_row(ui, prow, y)
    if y < _u.BODY_Y + _u.CHAR_H:
        # Navbar + first body row. On the node list the navbar's right third
        # is the clock (Setup); everything else up here is the header row,
        # which makes the tab bar a 42 px target.
        if st == _u.STATE_NODES and not ui._find and y < _u.NAV_H and x >= _u.SCREEN_W * 2 // 3:
            ui.handle_key(b's')
            return True
        return _tap_header(ui, x)
    if st == _u.STATE_CHAT and _u._pro is not None:
        return _tap_pro_chat(ui, y)
    rows = _rows(ui)
    if rows is not None and rows[0] <= x < rows[1]:
        if _tap_row(ui, rows, y):
            return True
        if st == _u.STATE_SHELL and ui._shell_menu:
            ui._shell_menu_close()    # a tap below the last item
            return True
    return False


def _long(ui, x, y):
    if _pro_list(ui):
        if ui._sheet is not None:
            return False
        target = _pro_row(ui, y)
        if target < 0:
            return False
        _pro_select(ui, target)
        return _u._pro.sheet_open(ui)
    st = ui.state
    if st == _u.STATE_IMAGE:
        ui.handle_key(b'r')
        return True
    if st == _u.STATE_SHELL and not ui._shell_menu:
        ui.nav_event("click")         # opens the control-key menu
        return True
    return False


def _scroll_content(ui, n):
    """Scrolling content follows the finger: n > 0 (finger moved down)
    brings earlier content into view. True if this screen is such content."""
    st = ui.state
    if st == _u.STATE_CHAT:
        top = ui._chat_max_scroll()
        ui.chat_scroll = max(0, min(top, ui.chat_scroll + n))
        ui.chat_cursor = -1
        ui.dirty = True
        return True
    if st == _u.STATE_BROWSER:
        ui._browser_page(-n)
        return True
    if st == _u.STATE_SHELL and not ui._shell_menu:
        ui._shell_scroll(n)           # delta > 0 = toward older lines
        return True
    if st in (_u.STATE_RRC_ROOMS, _u.STATE_RRC_CHAT) and not ui._rrc_panel:
        _steps(ui, -n)                # the scrollback's own up = older
        return True
    return False


def _drag(ui, n):
    st = ui.state
    if st == _u.STATE_IMAGE or st == _u.STATE_RECORDING:
        return False
    if _scroll_content(ui, n):
        return True
    if st == _u.STATE_SETTINGS and ui._settings_page in (_u._SET_LORA, _u._SET_RADIO):
        _steps(ui, n)                 # the field cursor / the stats scroll
        return True
    if _rows(ui) is not None:
        _steps(ui, n)                 # a selection list: it follows the finger
        return True
    return False


def _swipe(ui, d, y):
    if _pro_list(ui) and ui._sheet is not None:
        return False
    st = ui.state
    if st == _u.STATE_NODES and not ui._find:
        # The finger moves the way the arrow keys do: right = next tab.
        # (Phone-style page turning, finger left = next, was tried on the
        # deck and felt backwards: nothing slides on e-ink to sell it.)
        ui.nav_event("right" if d > 0 else "left")
        return True
    if st == _u.STATE_SETTINGS:
        page = ui._settings_page
        if page == _u._SET_LORA:
            ui._settings_adjust(d)    # the field under the cursor
            return True
        if page == _u._SET_MAIN and _u._pro is not None:
            p = _u._pro
            i = (y - p.LIST_Y) // p.ROW_H
            if (i < 0 or i >= p.SETUP_ROWS or ui._settings_scroll + i != p.setup_row(ui)
                    or ui._settings_idx not in (4, 7, 8, 11)):
                return False               # only the selected row, and only one with a value to step
            ui._settings_adjust(d)
            return True
        if page == _u._SET_MAIN:
            # The swipe must be on the selected row: stepping a value you
            # did not aim at is worse than a swipe that misses.
            rows = _rows(ui)
            if rows[4] + (y - rows[2]) // _u.CHAR_H != rows[5]:
                return False
            ui._settings_adjust(d)
            return True
    return False


def handle(ui, g, changed_ms):
    """Act on one gesture. True when it did something (the caller gives the
    haptic click for that), False when it was aimed at nothing."""
    kind, x, y, n, t_down = g
    if ui.locked or not ui._screen_on:
        return False
    if kind != DRAG and kind != FLICK and time.ticks_diff(t_down, changed_ms) < 0:
        return False                  # aimed at a screen that has since changed
    if kind == TAP:
        return _tap(ui, x, y)
    if kind == LONG:
        return _long(ui, x, y)
    if kind == DRAG:
        if (_pro_list(ui) or _pro_setup(ui) or _pro_panel(ui) or _pro_find(ui)
                or _pro_wifi(ui) or _pro_menu(ui)):
            if _drag_acc[0] != t_down:
                _drag_acc[0] = t_down
                _drag_acc[1] = 0
            acc = _drag_acc[1] + n
            rows = int(acc / 2)             # toward zero: two steps per 32 px row
            _drag_acc[1] = acc - rows * 2
            if _pro_menu(ui):
                rows = _menu_clamp(ui, rows)
            if ui._sheet is not None:
                return False
            if rows:
                _steps(ui, rows)
            return True
        return _drag(ui, n)
    if kind == FLICK:
        if _pro_panel(ui):
            import rrc_ui
            _steps(ui, n * (rrc_ui.PANEL_ROWS - 1))
            return True
        if _pro_menu(ui):
            _steps(ui, _menu_clamp(ui, n * (_u.LIST_ROWS - 1)))
            return True
        if _pro_list(ui) or _pro_setup(ui) or _pro_find(ui) or _pro_wifi(ui):
            if ui._sheet is not None:
                return False
            _steps(ui, n * (_u.LIST_ROWS - 1))
            return True
        return _drag(ui, n * _PAGE)
    if kind == SWIPE:
        return _swipe(ui, n, y)
    if kind == BACK:
        if _pro_list(ui) and ui._sheet is not None:
            return _u._pro.sheet_close(ui)
        if ui.state == _u.STATE_NODES and not ui._find:
            return _swipe(ui, 1, y)
        # Setup's labels start inside the edge band, so a rightward swipe on
        # a value row arrives as BACK. It is a value step there, not "leave
        # and drop the pending edits".
        if ui.state == _u.STATE_SETTINGS and _swipe(ui, 1, y):
            return True
        return _back(ui, False)
    return False


def _redraw_pending(ui):
    if ui.state == _u.STATE_IMAGE:
        return not ui._image_drawn    # the viewer paints outside the dirty flag
    return ui.dirty


class Controller:
    """The per-pass logic between the driver and handle(): the layout stamp,
    the long-press tick, feedback, and a net under all of it. The board
    module only wires this up, so everything here runs on a host too.

    The stamp: when the layout fingerprint changes, taps are dropped until
    the redraw that change asked for has reached the panel, and any stroke
    that began before that moment stays dropped. The key loop does not run
    during the ~700 ms refresh, so "the first pass with nothing left to
    draw" is when the new frame became visible."""

    def __init__(self, ui, feedback=None, report=None):
        self.ui = ui
        self.rec = Recognizer(_u.SCREEN_W, _u.SCREEN_H)
        self._feedback = feedback
        self._report = report
        self._key = None
        self._changed = 0
        self._pending = False         # a layout change is waiting for its redraw
        self._pending_since = 0
        self._drag_t0 = None          # the stroke whose drag already clicked

    def key(self, now_ms):
        self.rec.note_key(now_ms)

    def _fail(self, e):
        self.rec.cancel()
        self.ui._redraw_hold = None
        if self._report is not None:
            try:
                self._report("touch", e)
            except Exception:
                pass

    def service(self, now_ms):
        """Once per keyboard pass, before this pass's events."""
        try:
            self._service(now_ms)
        except Exception as e:
            self._fail(e)

    def _service(self, now_ms):
        ui = self.ui
        if not ui._screen_on:
            self.rec.cancel()
            ui._redraw_hold = None
            self._key = None          # re-stamp when the screen comes back
            return
        k = layout_key(ui)
        if k != self._key:
            self._key = k
            self._pending = True
            self._pending_since = now_ms
        if self._pending:
            # PENDING_MAX_MS is a backstop: a screen that keeps its redraw
            # flag set (nothing may repaint while recording) must not be
            # able to switch touch off.
            if (not _redraw_pending(ui)
                    or time.ticks_diff(now_ms, self._pending_since) > PENDING_MAX_MS):
                self._pending = False
                self._changed = now_ms
        elif time.ticks_diff(now_ms, self._changed) > _STAMP_MAX_AGE:
            # Keep the stamp inside ticks_diff's range, or after ~6 days on
            # one screen every tap would compare as "before the change".
            self._changed = time.ticks_add(now_ms, -_STAMP_MAX_AGE)
        for g in self.rec.tick(now_ms):
            self._act(g)

    def event(self, ev):
        try:
            for g in self.rec.feed(ev):
                self._act(g)
            if ev[0] == UP:
                self.ui._redraw_hold = None    # the stroke is over: draw where it ended
        except Exception as e:
            self._fail(e)

    def _act(self, g):
        if self._pending and g[0] != DRAG and g[0] != FLICK:
            return                    # aimed at a frame that is being replaced
        try:
            ok = handle(self.ui, g, self._changed)
        except Exception as e:
            self._fail(e)
            return
        if not ok:
            return
        try:
            # The redraw this caused is the user's doing: the e-ink shim
            # spends its whole-panel refresh on those.
            self.ui.note_user_input()
            if g[0] == DRAG:
                # A refresh is ~750 ms and a flick is over in 100..250: a
                # frame started on the first step would show one row of a
                # swipe that goes on to move a page. Wait for the lift, but
                # not long -- a finger still down after DRAG_HOLD_MS is
                # dragging, and wants frames that follow it.
                self.ui._redraw_hold = time.ticks_add(g[4], DRAG_HOLD_MS)
            if self._feedback is not None:
                if g[0] == DRAG:
                    if self._drag_t0 == g[4]:
                        return        # one click per drag, at its start
                    self._drag_t0 = g[4]
                self._feedback()
        except Exception as e:
            self._fail(e)

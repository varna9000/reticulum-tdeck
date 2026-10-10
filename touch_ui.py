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
from touch_gestures import Recognizer, TAP, LONG, DRAG, FLICK, SWIPE, BACK

_FOOT_REACH = 8          # px above the hint row that still count as the footer
_FOOT_SNAP = 8           # a footer tap this close to a hint goes to it
_PAGE = _u.BODY_ROWS - 2


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
    """A fingerprint of what is where on the screen. A stroke that began
    before this last changed was aimed at a screen that is gone."""
    st = ui.state
    if st == _u.STATE_NODES:
        return (st, ui.node_tab, getattr(ui, _lists(ui)[1]), ui._find,
                getattr(ui, "_find_scroll", 0))
    if st == _u.STATE_CHAT:
        return (st, ui.selected_peer, ui.chat_scroll)
    if st == _u.STATE_BROWSER:
        return (st, ui.browser_path, ui.browser_scroll)
    if st == _u.STATE_SETTINGS:
        return (st, ui._settings_page, getattr(ui, "_settings_scroll", 0))
    if st == _u.STATE_SHELL:
        return (st, ui._shell_menu, getattr(ui, "_shell_view", 0))
    if st in (_u.STATE_RRC_ROOMS, _u.STATE_RRC_CHAT):
        return (st, ui._rrc_panel, getattr(ui, "_rrc_panel_kind", None),
                getattr(ui, "_rrc_panel_scroll", 0), ui._rrc_prompt)
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
        return full + (y0, _u.BODY_ROWS - 1, getattr(ui, sa), getattr(ui, ia), len(keys))
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


def _tap_footer(ui, x):
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
        ui.nav_event("click")         # the room / member panel, as the ball's click
        return True
    if st == _u.STATE_SHELL:
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
            ui._find_close()
            return True
        return False
    if st == _u.STATE_SETTINGS:
        if ui._settings_page in ui._TEXT_ENTRY_PAGES:
            return False              # Esc there cancels what was typed
        ui.handle_key(b'\x1b')
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


def _tap(ui, x, y):
    st = ui.state
    if st == _u.STATE_IMAGE:
        ui._exit_image_view()
        return True
    if st == _u.STATE_RECORDING:
        # Nothing may redraw while the mic thread runs, so the whole panel is
        # two targets: the upper half sends, the lower half cancels.
        mid = _u.BODY_Y + (_u.BODY_ROWS // 2) * _u.CHAR_H
        ui.handle_key(b'\r' if y < mid else b'\x08')
        return True
    if y >= _u.SEP_Y - 6:
        return _tap_footer(ui, x)
    mod, prow = _panel(ui)
    if prow is not None:
        inside = (prow[0] <= x < prow[1] and mod.PANEL_Y <= y < mod.PANEL_Y + mod.PANEL_H)
        if not inside:
            mod.panel_close(ui)
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
    rows = _rows(ui)
    if rows is not None and rows[0] <= x < rows[1]:
        if _tap_row(ui, rows, y):
            return True
        if st == _u.STATE_SHELL and ui._shell_menu:
            ui._shell_menu_close()    # a tap below the last item
            return True
    return False


def _long(ui):
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
        top = max(0, len(ui._build_chat_lines()) - (_u.BODY_ROWS - 1))
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
    if _rows(ui) is not None:
        _steps(ui, n)                 # a selection list: it follows the finger
        return True
    return False


def _swipe(ui, d, y):
    st = ui.state
    if st == _u.STATE_NODES and not ui._find:
        # Like turning a page: the finger moving left brings the next tab in.
        ui.nav_event("right" if d < 0 else "left")
        return True
    if st == _u.STATE_SETTINGS:
        rows = _rows(ui)
        if rows is not None:
            # On a list page the swipe must be on the selected row: stepping
            # a value you did not aim at is worse than a swipe that misses.
            if rows[4] + (y - rows[2]) // _u.CHAR_H != rows[5]:
                return False
        elif ui._settings_page in ui._TEXT_ENTRY_PAGES:
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
        return _long(ui)
    if kind == DRAG:
        return _drag(ui, n)
    if kind == FLICK:
        return _drag(ui, n * _PAGE)
    if kind == SWIPE:
        return _swipe(ui, n, y)
    if kind == BACK:
        if ui.state == _u.STATE_NODES and not ui._find:
            return _swipe(ui, 1, y)
        return _back(ui, False)
    return False


class Controller:
    """The per-pass logic between the driver and handle(): the layout stamp,
    the long-press tick, feedback, and a net under all of it. The board
    module only wires this up, so everything here runs on a host too."""

    def __init__(self, ui, feedback=None, report=None):
        self.ui = ui
        self.rec = Recognizer(_u.SCREEN_W, _u.SCREEN_H)
        self._feedback = feedback
        self._report = report
        self._key = None
        self._changed = 0
        self._drag_t0 = None          # the stroke whose drag already clicked

    def key(self, now_ms):
        self.rec.note_key(now_ms)

    def service(self, now_ms):
        """Once per keyboard pass, before this pass's events."""
        k = layout_key(self.ui)
        if k != self._key:
            self._key = k
            self._changed = now_ms
        if not self.ui._screen_on:
            self.rec.cancel()
        for g in self.rec.tick(now_ms):
            self._act(g)

    def event(self, ev):
        for g in self.rec.feed(ev):
            self._act(g)

    def _act(self, g):
        try:
            ok = handle(self.ui, g, self._changed)
        except Exception as e:
            self.rec.cancel()
            if self._report is not None:
                self._report("touch", e)
            return
        if not ok or self._feedback is None:
            return
        if g[0] == DRAG:
            if self._drag_t0 == g[4]:
                return                # one click per drag, at its start
            self._drag_t0 = g[4]
        self._feedback()

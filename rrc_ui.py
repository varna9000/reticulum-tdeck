# All RRC drawing and key handling.
#
# ui.py is already ~4,000 lines, so the RRC surfaces live here and ui.py
# gains only the tab constant, two states and a dispatch. Every function
# takes the UI instance and uses its display, font, palette and row
# cache, so the geometry constants stay in one place.
#
# The client interprets structured fields; everything the hub says in
# prose is painted as prose. That is why there is no parser here.

from ui import (UI, BODY_Y, CACHE_ROWS, CHAR_H, CHAR_W, COLS, INPUT_Y,
                SCREEN_W, BODY_ROWS, STATE_NODES, STATE_RRC_CHAT,
                STATE_RRC_ROOMS, TAB_RRC, FOOT_SLOT, _pad, _ascii, _ascii_keep_spacing)
# SCREEN_H and SEP_Y are unused here.

import rrc_proto as _P      # constants only -- for the composer's fallback cap
import time


def open_hub(ui, dest):
    """Open the hub console for dest and ask the client to connect.

    Every field of the previous session is reset here, not just the three
    that were: a stale scroll anchor opened hub B's console part-way up an
    empty buffer, the header showed hub A's name until B's WELCOME landed,
    alt+w showed A's roster, the status line showed A's last message and a
    half-typed composer line carried over into a different room.

    The client's connect() tears down any live session rather than
    refusing, so committing the UI here is safe: the two cannot end up
    describing different hubs.
    """
    ui._rrc_lines = []
    ui._rrc_flat = None
    ui._rrc_room = None
    ui._rrc_members = 0
    ui._rrc_scroll_chat = 0
    ui._rrc_hub_name = None
    ui._rrc_roster = []
    ui._rrc_status = ""
    ui._rrc_input = ""
    ui._rrc_prompt = False
    ui._rrc_panel = False
    ui._rrc_panel_kind = "members"
    ui._rrc_panel_idx = 0
    ui._rrc_panel_scroll = 0
    # Hub B must not inherit hub A's rooms, and _rrc_list_seen has to go
    # back with them or the picker would never ask this hub for its own.
    ui._rrc_rooms = []
    ui._rrc_list_seen = False
    ui._rrc_list_ms = 0
    _invalidate_rows(ui)
    ui.state = STATE_RRC_ROOMS
    if ui.on_rrc_connect:
        ui.on_rrc_connect(dest)
    ui.dirty = True
    return True


def open_selected_hub(ui):
    """RRC tab click: connect to the highlighted hub."""
    if not ui._rrc_keys or not (0 <= ui._rrc_idx < len(ui._rrc_keys)):
        return False
    return open_hub(ui, ui._rrc_keys[ui._rrc_idx])


def _draw_header(ui, left, right):
    # Cached like draw_browser's header -- the key covers everything the
    # row shows, so a hub-name or status change still repaints it.
    cache_key = left + "\x01" + right
    if ui._cache[1] != cache_key:
        ui._cache[1] = cache_key
        ui.tft.text(ui.font, _pad(""), 0, BODY_Y, ui.DIM_CYAN, ui.BG_DARK)
        # One column in from the body frame's rail, like the room header.
        ui.tft.text(ui.font, "<", CHAR_W, BODY_Y, ui.NEON_GREEN, ui.BG_DARK)
        # left is hub-controlled (announced hub name); _tb() carries it
        # through the same glyph-index path _row() uses, so a kept
        # Cyrillic char doesn't hit tft.text() as a raw non-ASCII str.
        # "< " + text, like the browser's "> title": drawn straight after
        # the glyph it read as "<finding path...". One blank column before
        # a right-hand status, one margin at the right edge.
        room = COLS - 3 - (len(right) + 2 if right else 1)
        ui.tft.text(ui.font, ui._tb(_ascii(left)[:room]),
                    3 * CHAR_W, BODY_Y, ui.NEON_CYAN, ui.BG_DARK)
        if right:
            # right is ui._rrc_status, which carries str(e) from a failed
            # send or connect. Same glyph-index path as everything else on
            # this row rather than a raw str straight at the driver.
            x = (COLS - len(right) - 1) * CHAR_W
            ui.tft.text(ui.font, ui._tb(right), x, BODY_Y, ui.DIM_CYAN,
                        ui.BG_DARK)
    ui.tft.fill_rect(0, BODY_Y + CHAR_H - 1, SCREEN_W, 1, ui.DIM_CYAN)


def _line_color(ui, kind):
    if kind == "error":
        return ui.NEON_MAG
    if kind in ("notice", "event"):
        return ui.DIM_CYAN
    return ui.NEON_CYAN


def _wrap_line(kind, nick, text):
    """Wrap one scrollback entry into its display rows.

    Shared by _flatten() and ui.rrc_line()'s scroll anchor, so the nick
    prefixes that decide a line's height live in exactly one place.

    _ascii_keep_spacing() rather than _ascii(): this is the one screen
    whose whole job is rendering hub prose verbatim, and _ascii() is
    ' '.join(raw.split()), which turns a hub's space-aligned "/list" table
    into a single run-on line. Both drop control characters; only _ascii()
    collapses the runs of spaces between columns. _draw_composer() makes
    the same distinction for the same reason.

    The wrapper is ui.UI._wrap_text -- the app's, not a second copy of it.

    Newlines are split on BEFORE the character filter, not after: rrcd
    answers /list and /who with ONE envelope whose body is "\n".join(lines)
    (commands.py), and _ascii_keep_spacing() keeps 32..126 plus Cyrillic,
    so a newline was dropped with nothing put in its place --
    "#varna (3)\n#general (12)" rendered as "#varna (3)#general (12)".
    The hub's room list arrived intact and was painted as an unreadable
    run-on line. _wrap_text() has no newline handling of its own; it wraps
    one paragraph. An empty paragraph yields one blank row, which keeps the
    blank lines a hub puts between sections.
    """
    body = text
    if kind == "msg" and nick:
        body = nick + "> " + text
    elif kind == "action" and nick:
        body = "* " + nick + " " + text
    rows = []
    for para in body.split("\n"):
        rows.extend(UI._wrap_text(_ascii_keep_spacing(para), COLS))
    return rows


def _flatten(ui):
    """Wrap every scrollback line to COLS, newest last, cached.

    Shared by _visible_lines (which windows it) and the trackball scroll
    clamp (which only needs the total count) -- and a five-tick trackball
    drain called the clamp five times, then draw_room made it six, each
    one re-wrapping all 120 entries inside a 50 ms redraw budget on a
    device already paying ~220 us per composited cell. The scrollback only
    changes in rrc_line() and open_hub(), and both drop the cache.
    """
    flat = ui._rrc_flat
    if flat is None:
        flat = []
        for kind, nick, text in ui._rrc_lines:
            for piece in _wrap_line(kind, nick, text):
                flat.append((kind, piece))
        ui._rrc_flat = flat
    return flat


def _visible_lines(ui, rows):
    """Flatten scrollback into wrapped display lines, newest last."""
    flat = _flatten(ui)
    start = max(0, len(flat) - rows - ui._rrc_scroll_chat)
    return flat[start:start + rows]


def draw_rooms(ui):
    """Hub console: the MOTD and any /list reply, verbatim."""
    if ui._rrc_hub_name:
        # The status gets all but ~8 columns of hub name: cut at 10 it read
        # "rate limit" / "send faile", which says nothing.
        _draw_header(ui, ui._rrc_hub_name,
                     ui._rrc_status[:COLS - 13] if ui._rrc_status else "")
    else:
        # Before WELCOME there is no hub name, and the connect status is the
        # only thing on this screen worth reading. Give it the whole row:
        # squeezed into the right-hand corner it truncates to "waiting fo",
        # which tells the user nothing and hides the failure from us too.
        _draw_header(ui, ui._rrc_status or "connecting...", "")
    _draw_scrollback(ui)
    if ui._rrc_panel:
        draw_panel(ui)
    _draw_footer(ui)


def _draw_scrollback(ui):
    """The body rows under the header, from the cached row painter.

    Rows the open panel overlaps are left alone: repainting one draws it
    full-width over the panel, and the panel then repaints on top -- a
    visible flash for every line that arrives or scrolls while a panel is
    up. What sits under the panel is already correct on screen, and closing
    it invalidates the cache so every row repaints then.
    """
    rows = BODY_ROWS - 1
    lines = _visible_lines(ui, rows)
    for i in range(rows):
        y = BODY_Y + (i + 1) * CHAR_H
        if ui._rrc_panel and y + CHAR_H > PANEL_Y and y < PANEL_Y + PANEL_H:
            continue
        if i < len(lines):
            kind, text = lines[i]
            ui._draw_row_cached(i + 2, _pad(text), y, _line_color(ui, kind))
        else:
            ui._draw_row_cached(i + 2, "", y, ui.NEON_CYAN)


def _draw_footer(ui):
    if ui._rrc_prompt:
        ui._cache[FOOT_SLOT] = ''         # hints repaint once the prompt closes
        ui.tft.text(ui.font, _pad("room> " + ui._rrc_input)[:COLS], 0, INPUT_Y,
                    ui.NEON_CYAN, ui.BG_DARK)
        return
    # No letter keys left: the picker owns the room list and joining by
    # name, and backspace owns the exit. One gesture, one key, both named.
    if ui._cache[FOOT_SLOT] != "\x02rooms":
        ui._cache[FOOT_SLOT] = "\x02rooms"
        ui._draw_hints((("BKSP", "exit"), ("CLICK", "rooms")))


# There is no modifier key on this hardware, and that is settled, not
# assumed. LilyGO publishes the v1 keyboard's firmware source
# (examples/Keyboard_ESP32C3/Keyboard_ESP32C3.ino): an ESP32-C3 scans the
# matrix, resolves the layer ITSELF and hands the host one already-resolved
# ASCII byte -- board_tdeck_v1.get_key() is `i2c.readfrom(KBD_ADDR, 1)`,
# exactly one byte, and the Pro keeps that contract. Its printMatrix()
# consults the Sym key alone when choosing between the two character maps;
# ALT is read in precisely two hardcoded combos, Alt+B (backlight, sends
# nothing) and Alt+C (0x0C). So alt+w never becomes a byte of its own: it
# arrives as a plain 'w'.
#
# Sym is no better here. It IS a layer, but its layer is the digits and
# punctuation, which have no other key on this board -- Sym+w is the only
# way to type "1". Binding it would take a character away from the
# composer. The trackball click carries the panels instead (ui.py, the
# click block in handle_trackball), which is also how the shell tab solved
# the same wall.


def _invalidate_rows(ui):
    """Drop the body row cache so the next draw repaints every row.

    The member panel is an overlay: it fill_rect()s over rows the cache
    believes are already correct, so closing it leaves the panel painted on
    screen -- the next draw_room() emits one text call and no fills. ui.py
    already solves exactly this for the shell control menu
    (_shell_menu_open/_shell_menu_close); this follows that precedent, on
    open and on all three close paths.
    """
    ui._cache = [''] * CACHE_ROWS


def handle_key(ui, ch, key):
    # Either panel holds focus for as long as it is up, in both states.
    # A trackball click acts on the highlighted row and closes; backspace
    # joins it because it is this app's universal "back" and the panel
    # swallows every other key -- pressing it here did nothing at all,
    # which reads as a stuck overlay. esc is kept for completeness; this
    # keyboard does not send it (ui.py:2640).
    if ui._rrc_panel:
        if ch == 27 or ch == 8:
            panel_close(ui)
            return True
        return True              # panel holds focus; keys do not compose
    if ui._rrc_prompt:
        return _handle_prompt_key(ui, ch, key)
    if ui.state == STATE_RRC_ROOMS:
        # Backspace is the only key this screen binds, and every letter is
        # free again. (l) is gone because opening the picker refreshes the
        # list, and (b) because backspace is this app's universal "back"
        # (ui.py:2635-2641) and there is no text field here to want it.
        # _settled() guards the same phantom keystroke the room's exit does:
        # without it a stray byte on arrival tears the hub link straight
        # back down. esc is kept for completeness; this keyboard does not
        # send it (ui.py:2640).
        if (ch == 8 or ch == 27) and _settled(ui):
            if ui.on_rrc_disconnect:
                ui.on_rrc_disconnect()
            ui.state = STATE_NODES
            ui.node_tab = TAB_RRC
            ui.dirty = True
            return True
    if ui.state == STATE_RRC_CHAT:
        if ch == 13:                     # enter sends the composer line
            text = ui._rrc_input.strip()
            ui._rrc_input = ""
            if text.lower() == "/who":
                # Open the member panel instead of sending this to the hub.
                #
                # Not sending it loses nothing. rrcd answers /who in ONE
                # unchunked envelope and applies no size guard, so past
                # roughly thirteen members the reply exceeds the link MDU,
                # fails hub-side and the client receives nothing at all --
                # which is why the roster is built from JOINED/PARTED in the
                # first place. The panel shows that roster, so it is more
                # accurate than the hub's own answer.
                #
                # The trackball click opens the same panel. This stays
                # because it costs one comparison and a composer is already
                # open here: someone mid-sentence can ask without reaching
                # for the ball. The match is exact, so /whois still goes to
                # the hub.
                panel_toggle(ui, "members")
                return True
            # Sending returns the view to the live tail. rrc_line() holds a
            # reader's anchor against arriving traffic, so without this a
            # message sent while scrolled back -- and its local echo --
            # would land off screen. Same rule as the LXMF view: your own
            # message snaps, everybody else's does not.
            ui._rrc_scroll_chat = 0
            ui.dirty = True
            if text and ui.on_rrc_say:
                ui.on_rrc_say(text)
            return True
        if ch == 8:                      # backspace: edit, or leave when empty
            if ui._rrc_input:
                ui._rrc_input = ui._rrc_input[:-1]
                ui.dirty = True
                return True
            if _settled(ui):
                # Empty composer + backspace parts the room, exactly as the
                # esc branch below does -- which this keyboard cannot
                # reach. The 500 ms guard is the app's (ui.py:2639, 2710):
                # a phantom keyboard byte arriving on the state change must
                # not bounce the user straight back out of the room.
                _leave_room(ui)
            return True
        if ch == 27:                     # esc leaves the room, keeps the link
            _leave_room(ui)
            return True
        # The composer is capped on the session's real byte budget, never on
        # a column count: the protocol allows ~350 bytes against a 38-column
        # row, and the old COLS - 2 cap made rrc_client.compose_cap() dead
        # code. The line tail-scrolls instead (see _draw_composer), so typing
        # past the visible width stays visible rather than being refused.
        if 32 <= ch < 127 and _blen(ui._rrc_input) < _cap(ui):
            ui._rrc_input += chr(ch)
            ui.dirty = True
            return True
    return False


def _blen(text):
    """UTF-8 byte length. The cap is a byte budget -- a mention token
    inserted from the member panel carries a hub-controlled nick, which is
    not necessarily ASCII."""
    return len(text.encode("utf-8"))


def _cap(ui):
    """The composer's byte budget for this session.

    Computed per session by rrc_client.compose_cap() (the hub's WELCOME
    limit against the live link MDU) and reached through a GUI callback,
    the way every other on_rrc_* slot works. With no session up -- or a
    client that answers with nonsense -- fall back to the protocol default
    so the composer is never refused before a WELCOME lands.
    """
    fn = getattr(ui, "on_rrc_cap", None)
    if fn is not None:
        try:
            cap = fn()
        except Exception:
            cap = None
        if isinstance(cap, int) and cap > 0:
            return cap
    return _P.DEFAULT_MAX_BODY


def _member_count(ui):
    """"12 users" when the roster is the room, "3+ users" when it is only
    who we have seen, "? users" when we know nobody.

    The RRC spec is explicit that "any member list provided by the hub is a
    snapshot, not a promise", and rrcd leaves its JOINED member list off by
    default -- so a bare number would be asserting something the protocol
    never offers. The "+" is the difference between a count the hub backed
    and a count we assembled from arrivals.
    """
    n = ui._rrc_members
    if not n:
        return "? users"
    if getattr(ui, "_rrc_members_exact", False):
        return "%d users" % n
    return "%d+ users" % n


def _hashed(room):
    """The room name as we show it: "#" + name, IRC style.

    Display only. rrcd has no "#" semantics -- _norm_room() is just
    strip().lower() -- so the character is ours, and rrc_client.join()
    takes it back off before the name reaches the wire. A room whose real
    name already starts with "#" is left alone rather than doubled.
    """
    if not room:
        return "?"
    return room if room.startswith("#") else "#" + room


def _settled(ui):
    """Has the current screen been up long enough to trust a keystroke?

    The app's own guard (ui.py:2639, 2710): a state change can be followed
    by a phantom byte from the keyboard controller, and a backspace that
    means "go back" would act on it and bounce the user straight out of the
    screen they just entered.
    """
    return time.ticks_diff(time.ticks_ms(), ui._state_change_ms) > 500


def _leave_room(ui):
    """Part the room, keep the hub link, return to the console."""
    if ui.on_rrc_part:
        ui.on_rrc_part()
    ui.state = STATE_RRC_ROOMS
    ui._rrc_panel = False
    ui.dirty = True


def _handle_prompt_key(ui, ch, key):
    if ch == 13:                     # enter
        text = ui._rrc_input.strip()
        ui._rrc_prompt = False
        ui._rrc_input = ""
        ui.dirty = True
        if text and ui.on_rrc_join:
            room, _, room_key = text.partition(" ")
            ui.on_rrc_join(room, room_key.strip() or None)
        return True
    if ch == 8:                      # backspace: edit, or leave when empty
        if ui._rrc_input:
            ui._rrc_input = ui._rrc_input[:-1]
        else:
            # Empty input + backspace = cancel, the idiom every other
            # screen in this app uses (ui.py:2635-2641, 2706-2712). The
            # esc branch below is the only other way out of this prompt
            # and the T-Deck keyboard never emits esc -- ui.py:2640 says
            # so in as many words -- so without this the prompt was a
            # dead end: nothing typed, nothing sent, no way back.
            ui._rrc_prompt = False
        ui.dirty = True
        return True
    if ch == 27:                     # esc
        ui._rrc_prompt = False
        ui._rrc_input = ""
        ui.dirty = True
        return True
    if 32 <= ch < 127 and len(ui._rrc_input) < COLS - 6:
        ui._rrc_input += chr(ch)
        ui.dirty = True
        return True
    return False


# Focused member panel (alt+w). Drawn with graphics primitives -- fill_rect
# for the body, drawn rules for the frame, SEL_BG strips for the title and
# footer, a selection fill plus a magenta accent bar, and a track/thumb
# scrollbar -- never box-drawing characters: the app has a display driver,
# so the panel looks like the rest of the UI rather than ASCII art.
#
# Only PANEL_W derives from board geometry (SCREEN_W, itself board-aware --
# the Pro is 240px/30 columns wide). PANEL_Y, PANEL_H and PANEL_ROWS below
# are fixed constants sized for the v1's 320x240 landscape panel and
# verified against the host harness's bounds check; they do not scale with
# SCREEN_H, so the Pro's taller 320px portrait screen leaves unused space
# below the panel rather than showing more rows. That's deliberately not
# exploited here -- it belongs with the Pro's own boot-test pass, not this
# task -- and the arithmetic still stays in bounds on the Pro either way.
PANEL_X = 8
PANEL_W = SCREEN_W - 16
PANEL_Y = 58
PANEL_H = 158
PANEL_ROWS = 7
_PANEL_TEXT_X = PANEL_X + 8
_PANEL_HASH_X = PANEL_X + PANEL_W - 8 - 10 * CHAR_W
# Columns the nick may use before it runs into the [hash8] column, less one
# for a separating space. Derived, not a fixed 20: _PANEL_HASH_X scales with
# PANEL_W (and so with SCREEN_W) but a constant slice does not, and on the
# Pro's 240px panel a 20-character name ran to x=176 over a hash column
# starting at x=144.
_PANEL_NAME_COLS = max(1, (_PANEL_HASH_X - _PANEL_TEXT_X) // CHAR_W - 1)
# A room row carries no [hash8], so it gets the whole interior: 36 columns
# on the v1 against a member's 25. Derived the same way _PANEL_NAME_COLS is,
# so the Pro's narrower panel shrinks all three together rather than
# overrunning the frame.
_PANEL_FULL_COLS = max(1, (PANEL_X + PANEL_W - 8 - _PANEL_TEXT_X) // CHAR_W)
_PANEL_TOPIC_X = _PANEL_TEXT_X + 14 * CHAR_W
_PANEL_ROOM_COLS = max(1, (_PANEL_TOPIC_X - _PANEL_TEXT_X) // CHAR_W - 1)
_PANEL_TOPIC_COLS = max(1, (PANEL_X + PANEL_W - 8 - _PANEL_TOPIC_X) // CHAR_W)


JOIN_ROW = "+ join by name..."


def _room_rows(ui):
    """Rows for the picker: the action row, then whatever /list returned.

    The action row is always present and always first, because it is the
    only way to reach a room /list never names -- an on-demand room, a +k
    room needing a key, or one that does not exist yet. On a hub that
    registers nothing (FR-rrc-client.md: "a hub whose rooms are all
    on-demand lists nothing") it is the only row there is, which is why
    the empty picker is something to click rather than a sign.
    """
    return [None] + list(ui._rrc_rooms)


def _panel_len(ui):
    """How many rows the open panel has, whichever kind it is."""
    if ui._rrc_panel_kind == "rooms":
        return len(_room_rows(ui))
    return len(ui._rrc_roster)


def _pc(ui, part, key):
    """True when this panel part must repaint, and records its new key.

    The panel used to repaint whole on every draw -- a black fill_rect over
    the box, then frame, title, rows, scrollbar and foot -- so every
    trackball tick, arriving line or roster change blanked it for a moment:
    the flicker. Parts now repaint only when what they show changes. The
    cache belongs to the ui._cache list it was built against: every path
    that drops the row cache (panel open/close, a state change's body wipe)
    swaps in a new list, and that invalidates the panel parts with it.
    """
    if getattr(ui, "_rrc_pc_owner", None) is not ui._cache:
        ui._rrc_pc_owner = ui._cache
        ui._rrc_pc = {}
    if ui._rrc_pc.get(part, _PC_MISSING) == key:
        return False
    ui._rrc_pc[part] = key
    return True


_PC_MISSING = object()


def _panel_clear_row(ui, y):
    ui.tft.fill_rect(PANEL_X + 2, y, PANEL_W - 4 - 6, CHAR_H, ui.BG_DARK)


def _panel_chrome(ui, title, count):
    """Box, 2px frame, title band and rule -- identical for both panels.

    Every string drawn here goes through ui._tb(): the room name is
    hub-controlled (K_ROOM) and a kept Cyrillic char must take the
    glyph-index path, same as the Task 8 header fix. The static labels are
    wrapped too, for consistency, though they are only ever ASCII.
    """
    key = (ui._rrc_panel_kind, title, count)
    if not _pc(ui, "chrome", key):
        return
    ui._rrc_pc = {"chrome": key}     # the fill below wipes every other part
    ui.tft.fill_rect(PANEL_X, PANEL_Y, PANEL_W, PANEL_H, ui.BG_DARK)
    for i in range(2):               # 2px frame
        ui.tft.fill_rect(PANEL_X + i, PANEL_Y + i, PANEL_W - 2 * i, 1, ui.NEON_CYAN)
        ui.tft.fill_rect(PANEL_X + i, PANEL_Y + PANEL_H - 1 - i,
                         PANEL_W - 2 * i, 1, ui.NEON_CYAN)
        ui.tft.fill_rect(PANEL_X + i, PANEL_Y + i, 1, PANEL_H - 2 * i, ui.NEON_CYAN)
        ui.tft.fill_rect(PANEL_X + PANEL_W - 1 - i, PANEL_Y + i, 1,
                         PANEL_H - 2 * i, ui.NEON_CYAN)

    ui.tft.fill_rect(PANEL_X + 2, PANEL_Y + 2, PANEL_W - 4, 18, ui.SEL_BG)
    ui.tft.text(ui.font, ui._tb(_ascii(title)[:14]), _PANEL_TEXT_X,
                PANEL_Y + 3, ui.YELLOW, ui.SEL_BG)
    ui.tft.text(ui.font, ui._tb(count), PANEL_X + PANEL_W - 8 - len(count) * CHAR_W,
                PANEL_Y + 3, ui.DIM_CYAN, ui.SEL_BG)
    ui.tft.fill_rect(PANEL_X + 2, PANEL_Y + 20, PANEL_W - 4, 1, ui.DIM_CYAN)


def _panel_row_bg(ui, y, selected):
    """Paint a row's selection fill and accent bar; return its background."""
    if selected:
        ui.tft.fill_rect(PANEL_X + 2, y, PANEL_W - 4 - 6, CHAR_H, ui.SEL_BG)
        ui.tft.fill_rect(PANEL_X + 2, y, 3, CHAR_H, ui.NEON_MAG)
    return ui.SEL_BG if selected else ui.BG_DARK


def _panel_scrollbar(ui, total):
    if not _pc(ui, "sb", (total, ui._rrc_panel_scroll)):
        return
    track_y = PANEL_Y + 22
    track_h = PANEL_ROWS * CHAR_H
    ui.tft.fill_rect(PANEL_X + PANEL_W - 6, track_y, 4, track_h, ui.BG_DARK)
    if total > PANEL_ROWS:
        bar_h = max(6, track_h * PANEL_ROWS // total)
        bar_y = track_y + track_h * ui._rrc_panel_scroll // total
        ui.tft.fill_rect(PANEL_X + PANEL_W - 6, bar_y, 4, bar_h, ui.NEON_CYAN)


def _panel_foot(ui, action):
    """Footer band: how to leave on the left, what a click does on the right,
    in the footer hint style -- (BKSP)close ... (CLICK)mention.

    BKSP and not alt+w: alt+w cannot be pressed on this hardware (see the
    note above handle_key), so naming it here was an instruction to press a
    key that does nothing.
    """
    if not _pc(ui, "foot", action):
        return
    foot_y = PANEL_Y + PANEL_H - 22
    ui.tft.fill_rect(PANEL_X + 2, foot_y, PANEL_W - 4, 20, ui.SEL_BG)
    _panel_hint(ui, "BKSP", "close", _PANEL_TEXT_X, foot_y + 2)
    x = PANEL_X + PANEL_W - 8 - (len(action) + 7) * CHAR_W
    _panel_hint(ui, "CLICK", action, x, foot_y + 2)


def _panel_hint(ui, key, rest, x, y):
    ui.tft.text(ui.font, "(", x, y, ui.DIM_CYAN, ui.SEL_BG)
    ui.tft.text(ui.font, key, x + CHAR_W, y, ui.NEON_GREEN, ui.SEL_BG)
    ui.tft.text(ui.font, ")" + rest, x + (len(key) + 1) * CHAR_W, y,
                ui.DIM_CYAN, ui.SEL_BG)


def draw_panel(ui):
    """Draw whichever panel is open."""
    if ui._rrc_panel_kind == "rooms":
        draw_rooms_panel(ui)
    else:
        draw_member_panel(ui)


def draw_member_panel(ui):
    """Focused, scrollable member list drawn with graphics primitives.

    Rows are "nick or ?" plus a right-aligned [hash8], the same shape
    _draw_list_rows() uses for peers and hubs. No op/voice markers: rrcd
    exposes no member status to clients, so a "@"/"+" marker would be
    inventing data the protocol does not carry.
    """
    roster = ui._rrc_roster
    _panel_chrome(ui, _hashed(ui._rrc_room), _member_count(ui))

    top = ui._rrc_panel_scroll
    for i in range(PANEL_ROWS):
        y = PANEL_Y + 22 + i * CHAR_H
        idx = top + i
        if idx < len(roster):
            src, nick = roster[idx]
            selected = (idx == ui._rrc_panel_idx)
            key = (src, nick, selected)
        else:
            key = None
        if not _pc(ui, i, key):
            continue
        _panel_clear_row(ui, y)
        if key is None:
            continue
        bg = _panel_row_bg(ui, y, selected)
        # A member who has never spoken is known only by hash -- the app's
        # existing convention for an unknown name is "?".
        name = _ascii(nick) if nick else "?"
        ui.tft.text(ui.font, ui._tb(name[:_PANEL_NAME_COLS]), _PANEL_TEXT_X, y,
                    ui.YELLOW if selected else ui.NEON_CYAN, bg)
        # [hash8] is hex from bytes.hex() -- always ASCII, so it
        # deliberately skips ui._tb() rather than being left out by
        # oversight.
        ui.tft.text(ui.font, "[" + src.hex()[:8] + "]", _PANEL_HASH_X, y,
                    ui.DIM_CYAN, bg)

    _panel_scrollbar(ui, len(roster))
    _panel_foot(ui, "mention")


def draw_rooms_panel(ui):
    """The room picker: the join-by-name action, then the hub's /list rooms.

    A room row needs no [hash8] column, so a name gets the wider
    _PANEL_ROOM_COLS and the topic fills the rest of the width. Names are
    stored bare and shown "#name", exactly like every other room name on
    screen -- rrc_client.join() owns the strip on the way out.
    """
    rows = _room_rows(ui)
    listed = len(rows) - 1
    _panel_chrome(ui, "#rooms", "%d rooms" % listed)

    # Say why the picker is empty, and separate the two reasons: no
    # answer yet is worth waiting on, an empty answer is not. A hub
    # with no registered public rooms is healthy, not broken, so that
    # second line is a statement of fact under an action row that still
    # works -- not an error. It sits in the second row's slot.
    msg = None
    if not listed:
        msg = "hub lists no public rooms" if ui._rrc_list_seen else "asking hub..."

    top = ui._rrc_panel_scroll
    for i in range(PANEL_ROWS):
        y = PANEL_Y + 22 + i * CHAR_H
        idx = top + i
        if idx < len(rows):
            row = rows[idx]
            selected = (idx == ui._rrc_panel_idx)
            key = (row, selected)
        elif msg and i == 1:
            key = ("msg", msg)
        else:
            key = None
        if not _pc(ui, i, key):
            continue
        _panel_clear_row(ui, y)
        if key is None:
            continue
        if key[0] == "msg":
            ui.tft.text(ui.font, ui._tb(msg), _PANEL_TEXT_X, y, ui.DIM_CYAN, ui.BG_DARK)
            continue
        bg = _panel_row_bg(ui, y, selected)
        if row is None:
            # The action row spans the full width: it carries no topic, and
            # its label is longer than a room name column.
            ui.tft.text(ui.font, ui._tb(JOIN_ROW[:_PANEL_FULL_COLS]),
                        _PANEL_TEXT_X, y,
                        ui.YELLOW if selected else ui.NEON_GREEN, bg)
            continue
        name, topic = row
        ui.tft.text(ui.font, ui._tb(_ascii(_hashed(name))[:_PANEL_ROOM_COLS]),
                    _PANEL_TEXT_X, y,
                    ui.YELLOW if selected else ui.NEON_CYAN, bg)
        if topic:
            ui.tft.text(ui.font, ui._tb(_ascii(topic)[:_PANEL_TOPIC_COLS]),
                        _PANEL_TOPIC_X, y, ui.DIM_CYAN, bg)


    _panel_scrollbar(ui, len(rows))
    _panel_foot(ui, "join")


def panel_toggle(ui, kind):
    """Open the panel of this kind, or close it if it is already showing.

    The cursor opens where the common action is: on the first real room
    when the hub listed any, on the action row when it listed none, so
    either case is reached in one click rather than a click and a roll.
    """
    if ui._rrc_panel and ui._rrc_panel_kind == kind:
        panel_close(ui)
        return
    ui._rrc_panel = True
    ui._rrc_panel_kind = kind
    ui._rrc_panel_scroll = 0
    ui._rrc_panel_idx = 1 if (kind == "rooms" and ui._rrc_rooms) else 0
    if kind == "rooms":
        _refresh_rooms(ui)
    _invalidate_rows(ui)
    ui.dirty = True


LIST_REFRESH_MS = 10000


def _refresh_rooms(ui):
    """Ask the hub for a current room list, at most once every 10 s.

    The picker IS the room list, so opening it asks what the hub has now
    rather than showing the snapshot WELCOME fetched -- that refresh is the
    one thing (l) did that the gesture did not, and losing it would leave a
    hub whose rooms changed mid-session stale with no way to re-ask.

    Throttled because opening and shutting the panel is one gesture
    repeated and rrcd allows 240 msgs/minute. Nothing waits on the reply:
    the cached rows draw immediately and rrc_rooms() swaps them under the
    open panel when a fresher answer lands, so a throttled open costs the
    reader nothing.
    """
    if not ui.on_rrc_list:
        return
    now = time.ticks_ms()
    if ui._rrc_list_ms and time.ticks_diff(now, ui._rrc_list_ms) < LIST_REFRESH_MS:
        return
    ui._rrc_list_ms = now
    ui.on_rrc_list()


def panel_close(ui):
    ui._rrc_panel = False
    _invalidate_rows(ui)
    ui.dirty = True


def panel_clamp(ui):
    """Re-clamp the selection after the open panel's row list changed.

    Same job rrc_members() does for the roster: a /list reply that arrives
    while the picker is open can shorten it under the cursor, and the next
    draw would index past the end.
    """
    n = _panel_len(ui)
    if n <= 0:
        ui._rrc_panel_idx = 0
        ui._rrc_panel_scroll = 0
        return
    if ui._rrc_panel_idx >= n:
        ui._rrc_panel_idx = n - 1
    max_scroll = max(0, n - PANEL_ROWS)
    if ui._rrc_panel_scroll > max_scroll:
        ui._rrc_panel_scroll = max_scroll
    if ui._rrc_panel_idx < ui._rrc_panel_scroll:
        ui._rrc_panel_scroll = ui._rrc_panel_idx


def panel_click(ui):
    """Trackball click on the highlighted row: act on it, close the panel."""
    if ui._rrc_panel_kind == "rooms":
        return _rooms_panel_click(ui)
    roster = ui._rrc_roster
    if not roster or not (0 <= ui._rrc_panel_idx < len(roster)):
        return False
    src = roster[ui._rrc_panel_idx][0]
    token = ui.on_rrc_mention(src) if ui.on_rrc_mention else "@" + src.hex()[:8]
    # Inserted whole, even if it takes the line past the session's byte
    # budget: the composer's remaining count then goes negative, which says
    # so plainly, and say() trims to the cap before sending. Refusing the
    # insert silently would be worse than either.
    ui._rrc_input += token + " "
    panel_close(ui)
    return True


def _rooms_panel_click(ui):
    """Join the highlighted room, or open the join-by-name prompt.

    The name goes out bare: rrc_client.join() strips a leading "#" anyway,
    and rrcd's _norm_room() has no "#" semantics, so sending the name as
    displayed would join -- or silently create -- a second empty room
    beside the real one.

    A key cannot be carried here: a click has one field. That is what the
    action row is for, and why the prompt it opens splits on a space
    (_handle_prompt_key) into room and key.
    """
    rows = _room_rows(ui)
    if not (0 <= ui._rrc_panel_idx < len(rows)):
        return False
    row = rows[ui._rrc_panel_idx]
    panel_close(ui)
    if row is None:
        ui._rrc_prompt = True
        ui._rrc_input = ""
    elif ui.on_rrc_join:
        ui.on_rrc_join(row[0])
    ui.dirty = True
    return True


def panel_scroll(ui, delta):
    """Move the panel selection by delta rows, clamped to the row list, and
    keep the visible window (ui._rrc_panel_scroll) tracking it. Called for
    both single trackball ticks and multi-tick drains, so delta may be
    more than 1 in either direction."""
    total = _panel_len(ui)
    if not total:
        ui._rrc_panel_idx = 0
        ui._rrc_panel_scroll = 0
        return
    ui._rrc_panel_idx = max(0, min(total - 1, ui._rrc_panel_idx + delta))
    if ui._rrc_panel_idx < ui._rrc_panel_scroll:
        ui._rrc_panel_scroll = ui._rrc_panel_idx
    elif ui._rrc_panel_idx >= ui._rrc_panel_scroll + PANEL_ROWS:
        ui._rrc_panel_scroll = ui._rrc_panel_idx - PANEL_ROWS + 1
    ui.dirty = True


def draw_room(ui):
    """Room scrollback plus the composer."""
    room = _hashed(ui._rrc_room)
    count = _member_count(ui)
    # One column of inset, and one fewer column to fill because of it: drawn
    # at x=0 the name sat on the body frame's left rail. The console header
    # gets the same clearance from its "<" glyph; this row has no glyph, so
    # it pays for the space directly.
    name = _ascii(room)[:COLS - len(count) - 3]
    # Cached like _draw_header/draw_browser's header -- the key covers
    # both the room name and the member count, so either changing repaints.
    cache_key = name + "\x01" + count
    if ui._cache[1] != cache_key:
        ui._cache[1] = cache_key
        ui.tft.text(ui.font, _pad(""), 0, BODY_Y, ui.NEON_CYAN, ui.BG_DARK)
        # room is hub-controlled (echoed by the JOIN reply); _tb() carries
        # it through the same glyph-index path _row() uses.
        #
        # YELLOW, not NEON_CYAN: the scrollback directly underneath is
        # NEON_CYAN, so the heading was the same colour as the messages it
        # was heading. The member panel already titles the room in YELLOW,
        # so this is the colour the room name already has elsewhere.
        ui.tft.text(ui.font, ui._tb(name), CHAR_W, BODY_Y, ui.YELLOW, ui.BG_DARK)
        ui.tft.text(ui.font, count, (COLS - len(count) - 1) * CHAR_W, BODY_Y,
                    ui.DIM_CYAN, ui.BG_DARK)
    ui.tft.fill_rect(0, BODY_Y + CHAR_H - 1, SCREEN_W, 1, ui.DIM_CYAN)

    _draw_scrollback(ui)

    if ui._rrc_panel:
        draw_panel(ui)                 # Task 9

    _draw_composer(ui)


def _draw_composer(ui):
    """'> text' plus the bytes still left of the session's cap.

    The cap is ~350 bytes against a 38-column row, so the line tail-scrolls
    with a '<' continuation marker -- the same idiom ui._draw_input_line()
    uses for the LXMF composer, where the caret is always the last cell.
    The count in the right-hand columns is BYTES remaining, not characters:
    a mention inserted from the member panel carries a hub-controlled nick
    that need not be ASCII.

    The row goes through ui._tb() for that same reason -- a kept Cyrillic
    character reaching tft.text() as a raw str is the Task 8 header bug, and
    click-to-mention is how one gets into the composer. _tb() rather than
    _ascii() because it maps one glyph per character and so preserves the
    spacing the user actually typed, which _ascii() collapses.
    """
    left = _cap(ui) - _blen(ui._rrc_input)
    tail = " %d" % left
    avail = max(2, COLS - 2 - len(tail))   # columns for the text, after "> "
    inp = ui._rrc_input
    if len(inp) > avail:
        inp = "<" + inp[-(avail - 1):]
    row = _pad("> " + inp, COLS - len(tail)) + tail
    fg = ui.DIM_CYAN if ui._rrc_panel else ui.NEON_CYAN
    ui.tft.text(ui.font, ui._tb(row[:COLS]), 0, INPUT_Y, fg, ui.BG_DARK)

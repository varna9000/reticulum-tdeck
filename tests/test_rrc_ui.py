# Host tests for the RRC tab bar and screens, drawn against the recording
# fake display used by the other ui tests. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_ui.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

from test_ui_shell import _mkui as make_ui      # reuse the fake-display bootstrap

import ui as U
import rrc_cbor as C
import rrc_client
import rrc_proto as P

import time as _time


def _time_ms():
    """The clock rrc_ui._settled() reads. test_ui_shell installs the shim."""
    return _time.ticks_ms()


class _FakeLink:
    """Just enough link for compose_cap() and say()."""

    def __init__(self):
        self.mdu = 431
        self.sent = []

    def send(self, data):
        self.sent.append(bytes(data))


def _hub_driven_ui(state=None, room="#varna"):
    """A real UI wired to the real rrc_client dispatcher.

    Critical 1 is a data-flow defect: a hub value is type-checked at parse
    and then trusted all the way into ui.draw(). Testing rrc_client with a
    FakeGui cannot see it and testing rrc_ui with hand-written state cannot
    either -- only the two joined up can. init() is bypassed because it
    registers a urns announce handler; nothing else about the client is
    faked.
    """
    g = make_ui()
    rrc_client._gui = g
    rrc_client._my_identity = types.SimpleNamespace(hash=b"\x69" * 16)
    rrc_client._link = _FakeLink()
    rrc_client._dest = b"\x42" * 16
    rrc_client._state = rrc_client.JOINED if state is None else state
    rrc_client._room = room
    rrc_client._roster = {}
    rrc_client._seen_ids = []
    rrc_client._limits = {}
    rrc_client._hub_name = None
    rrc_client._banned = set()
    rrc_client._backoff_until = 0
    g.on_rrc_say = rrc_client.say
    g.on_rrc_cap = rrc_client.compose_cap
    g.on_rrc_mention = rrc_client.mention_for
    g.node_name = "tdeck"
    return g, rrc_client._link


def _feed(t, **kw):
    """Hand one hub packet to the real inbound dispatcher."""
    kw.setdefault("src", b"\xaa" * 16)
    rrc_client._on_packet(C.dumps(P.make_envelope(t, **kw)))


def _painted(calls):
    """Flatten FakeTFT's recorded text draws into one comparable string.

    Rows drawn through ui._row()/_draw_row_cached() are glyph-index bytes
    (see ui.UI._tb) while header/footer/tab-bar text is drawn as plain str
    -- draw_node_list() and draw_rooms() mix both in the same frame, so a
    naive str.join over g.tft.calls raises TypeError. test_ui_browser.py
    hits the same split and resolves it the same way: decode the bytes
    calls and join everything as text.
    """
    parts = []
    for c in calls:
        if c[0] != "text":
            continue
        s = c[1]
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("latin-1")
        parts.append(s)
    return " ".join(parts)


def test_tab_bar_fits_forty_columns():
    g = make_ui()
    g.node_tab = U.TAB_RRC
    labels = g._tab_labels()
    assert sum(len(x) for x in labels) <= U.COLS, labels
    assert len(labels) == U.N_TABS == 4
    print("ok test_tab_bar_fits_forty_columns")


def test_tab_bar_compresses_on_a_narrow_panel():
    g = make_ui()
    saved = U.COLS
    try:
        U.COLS = 30
        labels = g._tab_labels()
        assert sum(len(x) for x in labels) <= 30, labels
        assert "RNSH" not in "".join(labels), labels
    finally:
        U.COLS = saved
    print("ok test_tab_bar_compresses_on_a_narrow_panel")


def test_hub_list_holds_sixteen_and_drops_the_oldest():
    g = make_ui()
    for i in range(20):
        g.add_rrc_hub(bytes([i]) * 16, name="hub%d" % i, hops=1)
    assert len(g._rrc_keys) == 16
    # The backing dict must shrink with the key list, or it leaks.
    assert len(g.rrc_hubs) == 16
    # Oldest four evicted, newest retained -- not merely "some sixteen".
    assert bytes([0]) * 16 not in g.rrc_hubs
    assert bytes([3]) * 16 not in g.rrc_hubs
    assert bytes([4]) * 16 in g.rrc_hubs
    assert bytes([19]) * 16 in g.rrc_hubs
    assert g._rrc_keys[0] == bytes([4]) * 16
    assert g._rrc_keys[-1] == bytes([19]) * 16
    print("ok test_hub_list_holds_sixteen_and_drops_the_oldest")


def test_rrc_line_appends_to_scrollback_and_is_bounded():
    g = make_ui()
    for i in range(200):
        g.rrc_line("msg", "sam", "line %d" % i)
    assert len(g._rrc_lines) <= U.RRC_SCROLLBACK
    assert g._rrc_lines[0][2] == "line 80"
    assert g._rrc_lines[-1][2] == "line 199"
    print("ok test_rrc_line_appends_to_scrollback_and_is_bounded")


def test_rrc_tab_lists_hubs_like_the_ssh_tab():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x42" * 16, name="Varna Hub", hops=2)
    g.tft.calls = []
    g.draw_node_list()
    painted = _painted(g.tft.calls)
    assert "Varna Hub" in painted, painted
    assert "42424242" in painted, painted
    print("ok test_rrc_tab_lists_hubs_like_the_ssh_tab")


def test_room_browser_renders_hub_notices_verbatim():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = "Varna Hub"
    g.rrc_line("notice", None, "Registered public rooms:")
    g.rrc_line("notice", None, "  #varna - Varna mesh, off-grid")
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)
    painted = _painted(g.tft.calls)
    assert "Registered public rooms:" in painted, painted
    assert "#varna - Varna mesh, off-grid" in painted, painted
    print("ok test_room_browser_renders_hub_notices_verbatim")


def test_j_is_no_longer_a_route_into_the_prompt():
    """One way in per context. Joining by name lives on the picker's action
    row now, so a bare letter on the console does nothing -- the console has
    no text field, and a second entry point to the same prompt is the kind
    of split the click rule exists to remove."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    import rrc_ui
    rrc_ui.handle_key(g, ord("j"), b"j")
    assert g._rrc_prompt is False
    print("ok test_j_is_no_longer_a_route_into_the_prompt")


def test_the_action_row_opens_the_room_name_prompt():
    """The picker's first row is the only way to a room /list never names:
    an on-demand room, a +k room needing a key, or one that does not exist
    yet. It has to reach the same prompt (j) used to."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")
    assert g._rrc_panel_idx == 0, "an empty picker opens on the action row"
    rrc_ui.panel_click(g)
    assert g._rrc_prompt is True
    assert g._rrc_panel is False, "the panel closes behind the prompt"
    print("ok test_the_action_row_opens_the_room_name_prompt")


def test_the_room_header_shows_the_name_irc_style():
    """The hub stores "varna"; we show "#varna" (display only)."""
    import rrc_ui
    g, link = _hub_driven_ui(room="varna")
    g.rrc_joined("varna")            # what the hub echoed: the bare name
    g.tft.calls = []
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "#varna" in painted, painted
    print("ok test_the_room_header_shows_the_name_irc_style")


def test_the_room_header_does_not_double_an_existing_hash():
    import rrc_ui
    g, link = _hub_driven_ui(room="#varna")
    g.rrc_joined("#varna")
    g.tft.calls = []
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "#varna" in painted and "##varna" not in painted, painted
    print("ok test_the_room_header_does_not_double_an_existing_hash")


def test_typing_who_opens_the_panel_instead_of_asking_the_hub():
    """alt+w is unreachable on the v1 keyboard -- it emits a plain 'w'.

    README.md warns the Sym/Alt control-key codes depend on the keyboard
    firmware revision, and this hardware proved it. A typed /who needs no
    modifier, and it must NOT reach the hub: rrcd answers /who in one
    unchunked envelope with no size guard, so past ~13 members the reply
    overruns the link MDU and the client gets nothing.
    """
    import rrc_ui
    g, link = _hub_driven_ui()
    said = []
    g.on_rrc_say = lambda t: said.append(t)
    g.state = U.STATE_RRC_CHAT
    for ch in "/who":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert g._rrc_panel is True, "/who must open the member panel"
    assert said == [], "/who must not be sent to the hub"
    assert g._rrc_input == "", g._rrc_input
    print("ok test_typing_who_opens_the_panel_instead_of_asking_the_hub")


def test_who_is_matched_exactly_not_as_a_prefix():
    """'/whois bob' is a hub command and must still reach the hub."""
    import rrc_ui
    g, link = _hub_driven_ui()
    said = []
    g.on_rrc_say = lambda t: said.append(t)
    g.state = U.STATE_RRC_CHAT
    for ch in "/whois bob":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert said == ["/whois bob"], said
    assert g._rrc_panel is False, "only a bare /who opens the panel"
    print("ok test_who_is_matched_exactly_not_as_a_prefix")


def test_backspace_closes_the_member_panel():
    """The panel swallows every key it does not handle, so backspace did
    nothing there -- indistinguishable from a stuck overlay. A second click
    already closed it; backspace is the app's universal back and now does
    too."""
    import rrc_ui
    g, link = _hub_driven_ui()
    g.state = U.STATE_RRC_CHAT
    rrc_ui.panel_toggle(g, "members")
    assert g._rrc_panel is True
    rrc_ui.handle_key(g, 8, b"\x08")
    assert g._rrc_panel is False, "backspace must close the member panel"
    print("ok test_backspace_closes_the_member_panel")


def test_backspace_edits_the_prompt_then_leaves_it():
    """The room prompt was a dead end on this hardware.

    Its only exit was ch == 27, and the T-Deck keyboard never emits esc --
    ui.py:2640 states that outright, which is why every other screen in the
    app leaves on "empty input + backspace" (ui.py:2635-2641, 2706-2712).
    Open the prompt with no room name in mind and you were stuck in it.
    """
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")       # click opens the picker
    rrc_ui.panel_click(g)                 # its action row opens the prompt
    for ch in "ab":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 8, b"\x08")          # edits first
    assert g._rrc_input == "a", g._rrc_input
    assert g._rrc_prompt is True
    rrc_ui.handle_key(g, 8, b"\x08")          # empties it
    assert g._rrc_input == ""
    assert g._rrc_prompt is True
    rrc_ui.handle_key(g, 8, b"\x08")          # now it leaves
    assert g._rrc_prompt is False, "backspace on an empty prompt must cancel"
    print("ok test_backspace_edits_the_prompt_then_leaves_it")


def test_backspace_on_an_empty_composer_parts_the_room():
    """Same dead end one screen deeper: no esc key means no way out."""
    import rrc_ui
    g, link = _hub_driven_ui()
    parted = []
    g.on_rrc_part = lambda: parted.append(True)
    g.state = U.STATE_RRC_CHAT
    g._rrc_input = "hi"
    g._state_change_ms = 0            # older than the 500 ms settle guard
    rrc_ui.handle_key(g, 8, b"\x08")
    assert g._rrc_input == "h", g._rrc_input
    assert parted == [], "a non-empty composer must only edit"
    rrc_ui.handle_key(g, 8, b"\x08")
    assert g._rrc_input == ""
    assert parted == [], "emptying the composer must not part"
    rrc_ui.handle_key(g, 8, b"\x08")
    assert parted == [True], "backspace on an empty composer must part"
    assert g.state == U.STATE_RRC_ROOMS, g.state
    print("ok test_backspace_on_an_empty_composer_parts_the_room")


def test_a_phantom_backspace_on_arrival_does_not_part_the_room():
    """The app's 500 ms guard, applied to the new leave path.

    rrc_joined() switches the screen; the keyboard controller can deliver a
    stray byte across that switch, and without the guard it would part the
    room in the same instant it was joined.
    """
    import rrc_ui, time
    g, link = _hub_driven_ui()
    parted = []
    g.on_rrc_part = lambda: parted.append(True)
    g.rrc_joined("#varna")                     # stamps _state_change_ms
    assert g.state == U.STATE_RRC_CHAT
    g._rrc_input = ""
    rrc_ui.handle_key(g, 8, b"\x08")
    assert parted == [], "a backspace within 500 ms of arrival must be ignored"
    assert g.state == U.STATE_RRC_CHAT, g.state
    print("ok test_a_phantom_backspace_on_arrival_does_not_part_the_room")


def test_multiline_hub_prose_keeps_one_row_per_line():
    """rrcd sends /list as ONE envelope: "\n".join(lines).

    _ascii_keep_spacing() keeps 32..126 plus Cyrillic, so the newline was
    dropped with nothing put in its place and the hub's room list painted
    as a single run-on row -- the list arrived intact and was unreadable.
    """
    import rrc_ui
    rows = rrc_ui._wrap_line("notice", None, "#varna (3)\n#general (12)")
    assert rows == ["#varna (3)", "#general (12)"], rows
    # A blank line between sections survives as a blank row.
    rows = rrc_ui._wrap_line("notice", None, "Rooms:\n\n#varna")
    assert rows == ["Rooms:", "", "#varna"], rows
    print("ok test_multiline_hub_prose_keeps_one_row_per_line")


def test_prompt_enter_joins_the_typed_room():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    joined = []
    g.on_rrc_join = lambda room, key=None: joined.append((room, key))
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")       # click opens the picker
    rrc_ui.panel_click(g)                 # its action row opens the prompt
    for ch in "#varna":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert joined == [("#varna", None)], joined
    print("ok test_prompt_enter_joins_the_typed_room")


def test_prompt_splits_a_room_key_on_whitespace():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    joined = []
    g.on_rrc_join = lambda room, key=None: joined.append((room, key))
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")       # click opens the picker
    rrc_ui.panel_click(g)                 # its action row opens the prompt
    for ch in "#secret hunter2":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert joined == [("#secret", "hunter2")], joined
    print("ok test_prompt_splits_a_room_key_on_whitespace")


def test_enter_on_rrc_tab_connects_the_hub_not_a_shell():
    # Carried finding from Task 6: with four tabs, the Enter-key fallback in
    # _handle_key_nodes must not fall through to _open_selected_shell() when
    # the RRC tab is selected. Seed a stale SSH selection too, so a wrong
    # dispatch would be caught red-handed rather than merely no-op-ing on an
    # empty shell list.
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x42" * 16, name="Varna Hub", hops=2)
    g._rrc_idx = 0
    g.shell_nodes[b"\xaa" * 16] = {"name": "stale", "hops": 1, "seen": 0}
    g._shell_keys = [b"\xaa" * 16]
    g.ssh_idx = 0
    connected = []
    g.on_rrc_connect = lambda dest: connected.append(dest)
    g.handle_key(b"\r")
    assert connected == [b"\x42" * 16], connected
    assert g.connects == [], g.connects   # on_shell_connect must never fire
    assert g.state == U.STATE_RRC_ROOMS, g.state
    print("ok test_enter_on_rrc_tab_connects_the_hub_not_a_shell")


def test_non_ascii_hub_text_is_transliterated_before_drawing():
    # FakeTFT.text does s.encode("ascii") and the device font has no glyphs
    # beyond ASCII/CP866 either, so an un-_ascii()'d string from a hub is a
    # crash on hardware, not a cosmetic problem. A hub controls its own name
    # and the text of every notice it sends.
    #
    # ui._ascii() keeps ASCII plus a curated Cyrillic range (the CP866 font
    # has those glyphs) and DROPS everything else -- accented Latin, CJK,
    # emoji -- rather than transliterating it (confirmed against ui._ascii
    # and ui._CYR directly, not assumed). Cyrillic itself is not a useful
    # probe for the hub-name line specifically: _draw_header draws
    # _ascii(left) as a plain str without ui._tb(), so a *kept* Cyrillic
    # character still reaches FakeTFT.text() as non-ASCII and raises -- a
    # pre-existing _draw_header gap the coordinator flagged as Task 8's to
    # fix, not evidence that _ascii() didn't run. A dropped-class character
    # (accented Latin) exercises the same _ascii(left) call without
    # tripping that unrelated bug, and a second one in the notice body
    # covers _ascii(body) in _visible_lines.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = "Café Hub"                          # e-acute
    g.rrc_line("notice", None, "room über: тест")  # u-diaeresis + kept Cyrillic
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)                     # must not raise
    painted = _painted(g.tft.calls)
    assert "é" not in painted, painted  # the hub name's e-acute
    assert "ü" not in painted, painted  # the notice's u-diaeresis
    assert "Caf Hub" in painted, painted     # the rest of the hub name survives
    print("ok test_non_ascii_hub_text_is_transliterated_before_drawing")


def test_cyrillic_hub_name_survives_the_console_header():
    # _ascii() KEEPS Cyrillic (ui._CYR), unlike the accented latin chars the
    # sibling test above uses -- so this is the path that must reach _tb().
    # Without the _tb() wrap in _draw_header, the raw str hits tft.text()
    # and FakeTFT's s.encode("ascii") raises; the real driver would
    # silently UTF-8-mangle it instead.
    #
    # The assertion checks for a *bytes* text call specifically, not just
    # "any text call happened": draw_rooms()'s footer always paints
    # something, so a bare any(c[0] == "text" ...) would stay true even if
    # the header swallowed an encode failure and painted nothing. A bytes
    # call can only come out of _tb(), and with an empty scrollback here
    # the header's hub-name draw is the only call that can produce one.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = "Варна Хаб"
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)                       # must not raise
    assert any(c[0] == "text" and isinstance(c[1], (bytes, bytearray))
               for c in g.tft.calls), g.tft.calls
    print("ok test_cyrillic_hub_name_survives_the_console_header")


def test_cyrillic_room_name_survives_the_room_header():
    # Same shape and same reasoning as the console-header test above, for
    # draw_room()'s own header (the room name is hub-echoed via the JOIN
    # reply's K_ROOM field, so it needs the same treatment as a hub name).
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#варна"
    g._rrc_members = 3
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)                        # must not raise
    assert any(c[0] == "text" and isinstance(c[1], (bytes, bytearray))
               for c in g.tft.calls), g.tft.calls
    print("ok test_cyrillic_room_name_survives_the_room_header")


def test_trackball_scroll_on_rrc_tab_does_not_touch_ssh_state():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    for i in range(4):
        g.add_rrc_hub(bytes([i]) * 16, name="hub%d" % i, hops=1)
    g.add_shell_node(b"\xee" * 16, name="listener", hops=1)
    g.ssh_idx = 0
    g._irq_down = 1; g.handle_trackball()
    assert g._rrc_idx == 1, g._rrc_idx
    assert g.ssh_idx == 0, "scrolling the RRC tab moved the SSH selection"
    print("ok test_trackball_scroll_on_rrc_tab_does_not_touch_ssh_state")


def test_trackball_click_on_rrc_tab_opens_the_hub():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x42" * 16, name="Varna Hub", hops=2)
    g._rrc_idx = 0
    connected = []
    g.on_rrc_connect = lambda dest: connected.append(dest)
    g._irq_click = 1; g.handle_trackball()
    assert connected == [b"\x42" * 16], connected
    assert g.state == U.STATE_RRC_ROOMS, g.state
    print("ok test_trackball_click_on_rrc_tab_opens_the_hub")


def test_delete_on_rrc_tab_forgets_a_hub_not_a_listener():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x11" * 16, name="Varna Hub", hops=1)
    g.add_rrc_hub(b"\x22" * 16, name="KC1AWV Hub", hops=2)
    g.add_shell_node(b"\xee" * 16, name="listener", hops=1)
    g._rrc_idx = 0
    g.delete_selected()
    assert b"\x11" * 16 not in g.rrc_hubs, "selected hub was not forgotten"
    assert len(g._rrc_keys) == 1
    assert b"\xee" * 16 in g.shell_nodes, "deleting a hub removed an SSH listener"
    assert len(g._shell_keys) == 1
    print("ok test_delete_on_rrc_tab_forgets_a_hub_not_a_listener")


def test_room_view_shows_room_and_member_count():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    # Through the setter, not the field: a count carries whether the hub
    # backed it, and only a backed one prints bare. See
    # test_the_member_count_says_whether_it_is_the_whole_room.
    g.rrc_roster(12, True)
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "#varna" in painted, painted
    assert "12 users" in painted, painted
    print("ok test_room_view_shows_room_and_member_count")


def test_room_view_prefixes_messages_and_marks_actions():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.rrc_line("msg", "kc1awv", "anyone on 868?")
    g.rrc_line("action", "sam", "waves")
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "kc1awv> anyone on 868?" in painted, painted
    assert "* sam waves" in painted, painted
    print("ok test_room_view_prefixes_messages_and_marks_actions")


def test_typing_and_enter_sends_through_on_rrc_say():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    said = []
    g.on_rrc_say = lambda text: said.append(text)
    import rrc_ui
    for ch in "gm":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert said == ["gm"], said
    assert g._rrc_input == ""
    print("ok test_typing_and_enter_sends_through_on_rrc_say")


def test_empty_enter_does_not_send():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    said = []
    g.on_rrc_say = lambda text: said.append(text)
    import rrc_ui
    rrc_ui.handle_key(g, 13, b"\r")
    assert said == [], said
    assert g._rrc_input == ""
    print("ok test_empty_enter_does_not_send")


def test_backspace_on_empty_input_never_slices_past_the_start():
    """Renamed: this no longer asserts a noop.

    Backspace on an empty composer now parts the room (the T-Deck has no
    esc key, so it is the only way out) -- see
    test_backspace_on_an_empty_composer_parts_the_room. What survives from
    the original is the narrower guarantee this test was written for: the
    key must not raise and must not slice the string past its start. The
    old name outlived the behaviour and would have read as a licence to
    put the dead end back.
    """
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    import rrc_ui
    rrc_ui.handle_key(g, 8, b"\x08")   # must not raise or go negative
    assert g._rrc_input == ""
    print("ok test_backspace_on_empty_input_never_slices_past_the_start")


def test_back_parts_the_room_and_returns_to_the_console():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    parted = []
    g.on_rrc_part = lambda: parted.append(True)
    import rrc_ui
    rrc_ui.handle_key(g, 27, b"\x1b")
    assert parted == [True]
    assert g.state == U.STATE_RRC_ROOMS
    print("ok test_back_parts_the_room_and_returns_to_the_console")


def _click(g):
    """One trackball click, through the real dispatch rather than around it.

    The click path is the whole point of these tests, so they drive
    handle_trackball() the way the ISRs do instead of calling the panel
    helpers directly -- a panel that opens but is unreachable by thumb is
    exactly the bug being fixed.
    """
    g.locked = False
    g._screen_on = True
    g._irq_click = 1
    g.handle_trackball()


def test_console_click_opens_the_room_picker():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    _click(g)
    assert g._rrc_panel is True, "the console footer promises click=open"
    assert g._rrc_panel_kind == "rooms", g._rrc_panel_kind
    print("ok test_console_click_opens_the_room_picker")


def test_room_click_opens_the_member_panel():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    _click(g)
    assert g._rrc_panel is True
    assert g._rrc_panel_kind == "members", g._rrc_panel_kind
    print("ok test_room_click_opens_the_member_panel")


def test_the_panel_still_takes_focus_from_the_composer():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    _click(g)
    import rrc_ui
    rrc_ui.handle_key(g, ord("x"), b"x")
    assert g._rrc_input == "", g._rrc_input
    print("ok test_the_panel_still_takes_focus_from_the_composer")


def test_alt_w_is_not_a_panel_route_any_more():
    """alt+w cannot reach this app, so nothing may pretend to match it.

    The v1 keyboard's ESP32-C3 resolves modifiers itself and sends ONE
    resolved ASCII byte (board_tdeck_v1.get_key() reads exactly one).
    Its printMatrix() consults the Sym key alone when picking a layer --
    ALT appears only in two hardcoded combos, Alt+B and Alt+C -- so alt+w
    arrives as a plain 'w' and must reach the composer like any letter.
    """
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    import rrc_ui
    rrc_ui.handle_key(g, ord("w"), b"w")
    assert g._rrc_panel is False, "a bare letter must not open the panel"
    assert g._rrc_input == "w", g._rrc_input
    print("ok test_alt_w_is_not_a_panel_route_any_more")


def test_panel_footer_names_a_key_that_exists():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)
    painted = _painted(g.tft.calls)
    assert "alt+w" not in painted, painted
    assert "bksp close" in painted, painted
    print("ok test_panel_footer_names_a_key_that_exists")


def test_the_member_count_says_whether_it_is_the_whole_room():
    """The spec calls any member list "a snapshot, not a promise", and on a
    default hub the roster is only who we watched arrive. A bare number
    would assert something the protocol does not support."""
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    import rrc_ui

    def _header():
        g.tft.calls = []
        g._cache = [''] * U.CACHE_ROWS      # the header row is cached
        rrc_ui.draw_room(g)
        return _painted(g.tft.calls)

    g.rrc_roster(0, False)
    assert "? users" in _header(), "nothing known yet"
    g.rrc_roster(3, False)
    assert "3+ users" in _header(), "partial: only who we saw"
    g.rrc_roster(12, True)
    assert "12 users" in _header(), "the hub told us the room"
    print("ok test_the_member_count_says_whether_it_is_the_whole_room")


def test_the_panel_title_agrees_with_the_header_count():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.rrc_members([(b"\x11" * 6, "alice"), (b"\x22" * 6, None)])
    g.rrc_roster(2, False)
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)
    painted = _painted(g.tft.calls)
    assert "2+ users" in painted, painted
    print("ok test_the_panel_title_agrees_with_the_header_count")


def test_the_room_header_is_inset_and_reads_as_a_heading():
    """It was drawn at x=0 in NEON_CYAN: jammed against the body frame rail,
    and the same colour as the scrollback underneath it, so the room you are
    in did not read as a heading. The member panel already titles the room in
    YELLOW -- this matches it, and clears the rail by one column the way the
    console header's "<" does."""
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)

    def _s(c):
        return c[1].decode("latin-1") if isinstance(c[1], (bytes, bytearray)) else c[1]

    hits = [c for c in g.tft.calls if c[0] == "text" and _s(c).startswith("#varna")]
    assert hits, g.tft.calls
    x, fg = hits[0][2], hits[0][4]
    assert x >= U.CHAR_W, "the name must clear the frame rail, got x=%d" % x
    assert fg == g.YELLOW, "the room name must not be body-text coloured"
    print("ok test_the_room_header_is_inset_and_reads_as_a_heading")


def test_the_room_header_still_fits_beside_the_member_count():
    """The inset costs a column, so the truncation has to lose one too or a
    long room name runs under the count."""
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#" + "x" * 60
    g.rrc_roster(12)
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    for c in g.tft.calls:
        if c[0] != "text":
            continue
        s = c[1].decode("latin-1") if isinstance(c[1], (bytes, bytearray)) else c[1]
        end = c[2] + len(s) * U.CHAR_W
        assert end <= U.SCREEN_W, (s, c[2], end)
    print("ok test_the_room_header_still_fits_beside_the_member_count")


def test_the_picker_shows_rooms_irc_style_under_the_action_row():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g.rrc_rooms([("varna", "chat here"), ("general", "")])
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")
    g.tft.calls = []
    rrc_ui.draw_rooms_panel(g)
    painted = _painted(g.tft.calls)
    assert "+ join by name" in painted, painted
    assert "#varna" in painted, painted
    assert "chat here" in painted, painted
    assert "#general" in painted, painted
    print("ok test_the_picker_shows_rooms_irc_style_under_the_action_row")


def test_clicking_a_room_joins_it_with_a_bare_name():
    """The "#" is ours. rrcd's _norm_room() is strip().lower() with no "#"
    semantics, so sending the name as shown would join -- or silently
    create -- a second empty room beside the real one."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    joined = []
    g.on_rrc_join = lambda room, key=None: joined.append((room, key))
    g.rrc_rooms([("varna", "chat here"), ("general", "")])
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")
    rrc_ui.panel_click(g)
    assert joined == [("varna", None)], joined
    assert g._rrc_panel is False, "the panel closes behind the join"
    print("ok test_clicking_a_room_joins_it_with_a_bare_name")


def test_the_picker_opens_on_the_first_real_room_when_the_hub_listed_any():
    """Cursor where the common action is, so joining a listed room is one
    click rather than a click and a roll past the action row."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g.rrc_rooms([("varna", ""), ("general", "")])
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")
    assert g._rrc_panel_idx == 1, g._rrc_panel_idx
    print("ok test_the_picker_opens_on_the_first_real_room_when_the_hub_listed_any")


def test_opening_the_picker_refreshes_the_room_list():
    """The picker is the room list, so opening it asks for a current one.

    rrc_client requests the list automatically on WELCOME, so this is not
    about getting it at all -- it is about it being right. (l) used to be
    the only refresh; with the list living behind a gesture, the gesture
    has to carry it or a hub whose rooms changed mid-session would show a
    stale snapshot with no way to re-ask.
    """
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    asked = []
    g.on_rrc_list = lambda: asked.append(1)
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")
    assert asked == [1], asked
    rrc_ui.panel_close(g)
    g.rrc_rooms([("varna", "")])           # a reply lands
    g._rrc_list_ms = 0                     # ...and the throttle window passes
    rrc_ui.panel_toggle(g, "rooms")
    assert asked == [1, 1], "a second open asks again"
    print("ok test_opening_the_picker_refreshes_the_room_list")


def test_the_picker_does_not_lean_on_the_hub_when_toggled():
    """rrcd allows 240 msgs/minute. Opening and shutting the panel is one
    gesture repeated, so the refresh is throttled rather than sent per
    open."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    asked = []
    g.on_rrc_list = lambda: asked.append(1)
    import rrc_ui
    for _ in range(6):
        rrc_ui.panel_toggle(g, "rooms")    # open
        rrc_ui.panel_toggle(g, "rooms")    # shut
    assert asked == [1], asked
    print("ok test_the_picker_does_not_lean_on_the_hub_when_toggled")


def test_the_empty_picker_separates_asking_from_an_empty_answer():
    """"No rooms yet" and "this hub has no public rooms" looked identical
    before, and only the second one means stop waiting."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")
    g.tft.calls = []
    rrc_ui.draw_rooms_panel(g)
    painted = _painted(g.tft.calls)
    assert "asking hub" in painted, painted
    g.rrc_rooms([])                        # the hub answers: nothing listed
    g.tft.calls = []
    rrc_ui.draw_rooms_panel(g)
    painted = _painted(g.tft.calls)
    assert "hub lists no public rooms" in painted, painted
    print("ok test_the_empty_picker_separates_asking_from_an_empty_answer")


def test_backspace_exits_the_hub_console():
    """The console's last letter key is gone. Backspace is the app's
    universal back (ui.py:2635-2641) and this screen has no text field, so
    nothing else wants the key."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._state_change_ms = 0                 # settled, not just arrived
    left = []
    g.on_rrc_disconnect = lambda: left.append(1)
    import rrc_ui
    rrc_ui.handle_key(g, 8, b"\x08")
    assert left == [1], "backspace must tear the hub link down"
    assert g.state == U.STATE_NODES, g.state
    assert g.node_tab == U.TAB_RRC, g.node_tab
    print("ok test_backspace_exits_the_hub_console")


def test_a_phantom_backspace_on_arrival_does_not_exit_the_console():
    """Same guard the room already has: a state change can be followed by a
    stray byte from the keyboard controller, which would bounce the user
    straight back out of the console they just opened."""
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._state_change_ms = _time_ms()        # just arrived
    left = []
    g.on_rrc_disconnect = lambda: left.append(1)
    import rrc_ui
    rrc_ui.handle_key(g, 8, b"\x08")
    assert left == [], "a keystroke this early is not the user's"
    assert g.state == U.STATE_RRC_ROOMS, g.state
    print("ok test_a_phantom_backspace_on_arrival_does_not_exit_the_console")


def test_b_and_l_are_no_longer_console_keys():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._state_change_ms = 0
    asked, left = [], []
    g.on_rrc_list = lambda: asked.append(1)
    g.on_rrc_disconnect = lambda: left.append(1)
    import rrc_ui
    for ch in "blBL":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    assert asked == [], "the picker owns the list now"
    assert left == [], "backspace owns the exit now"
    assert g.state == U.STATE_RRC_ROOMS, g.state
    print("ok test_b_and_l_are_no_longer_console_keys")


def test_the_empty_picker_says_why_and_still_offers_the_action_row():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g.rrc_rooms([])
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")
    g.tft.calls = []
    rrc_ui.draw_rooms_panel(g)
    painted = _painted(g.tft.calls)
    assert "hub lists no public rooms" in painted, painted
    assert "+ join by name" in painted, painted
    print("ok test_the_empty_picker_says_why_and_still_offers_the_action_row")


def test_panel_lists_nick_or_question_mark_with_hash():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck"), (b"\x22" * 16, None)])
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)
    # Every string but the [hash8] column comes back as _tb() glyph-index
    # bytes now (ruling 2), so this uses the shared _painted() helper --
    # same reasoning as test_room_browser_renders_hub_notices_verbatim and
    # the rest of this file -- rather than a raw str.join over mixed
    # str/bytes calls, which would TypeError before any assertion runs.
    painted = _painted(g.tft.calls)
    assert "sv2rck" in painted, painted
    assert "[11111111]" in painted, painted
    assert "?" in painted, painted
    print("ok test_panel_lists_nick_or_question_mark_with_hash")


def test_panel_wraps_a_cyrillic_nick_through_tb():
    # Nicks are hub-controlled (K_NICK is whatever any peer in the room
    # calls itself) -- attacker-controlled input that lands on screen the
    # instant the panel opens. ui._ascii() KEEPS Cyrillic (ui._CYR) as a
    # keep-filter, so a Cyrillic nick survives it and must go through
    # ui._tb() before tft.text(), exactly like the Task 8 header fix.
    # Without that wrap the raw str hits FakeTFT.text(), which does
    # s.encode("ascii") and raises -- proven RED by temporarily dropping
    # the ui._tb() wrap around the row's name draw (see task-9-report.md).
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "Варна")])
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)                 # must not raise
    assert any(c[0] == "text" and isinstance(c[1], (bytes, bytearray))
               for c in g.tft.calls), g.tft.calls
    print("ok test_panel_wraps_a_cyrillic_nick_through_tb")


def test_panel_click_inserts_the_mention_and_closes():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.on_rrc_mention = lambda h: "@sv2rck"
    import rrc_ui
    rrc_ui.panel_click(g)
    assert g._rrc_input == "@sv2rck "
    assert g._rrc_panel is False
    print("ok test_panel_click_inserts_the_mention_and_closes")


def test_panel_scroll_clamps_on_an_empty_roster():
    # An empty roster must not push _rrc_panel_idx negative or past the
    # (nonexistent) end -- the next draw_member_panel() dereferences it.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([])
    import rrc_ui
    rrc_ui.panel_scroll(g, 1)
    rrc_ui.panel_scroll(g, -1)
    assert g._rrc_panel_idx == 0
    rrc_ui.draw_member_panel(g)                 # must not raise/IndexError
    print("ok test_panel_scroll_clamps_on_an_empty_roster")


def test_panel_scroll_clamps_at_the_roster_ends():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(3)])
    import rrc_ui
    for _ in range(5):                          # walk well past the top
        rrc_ui.panel_scroll(g, -1)
    assert g._rrc_panel_idx == 0
    for _ in range(5):                          # walk well past the bottom
        rrc_ui.panel_scroll(g, 1)
    assert g._rrc_panel_idx == 2
    rrc_ui.draw_member_panel(g)                 # must not raise/IndexError
    print("ok test_panel_scroll_clamps_at_the_roster_ends")


def test_panel_scroll_clamps_when_the_roster_is_shorter_than_the_view():
    # Fewer members than PANEL_ROWS: the scroll offset must stay pinned
    # at 0, or a later-arriving roster (which sizes _rrc_panel_idx down
    # via rrc_members()) could leave _rrc_panel_scroll stranded above it.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    import rrc_ui
    rrc_ui.panel_scroll(g, 1)
    assert g._rrc_panel_scroll == 0, g._rrc_panel_scroll
    print("ok test_panel_scroll_clamps_when_the_roster_is_shorter_than_the_view")


def test_rrc_members_clamps_panel_idx_when_the_roster_shrinks():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(3)])
    g._rrc_panel_idx = 2
    g.rrc_members([(b"\x11" * 16, "sv2rck")])    # roster shrank to one member
    assert g._rrc_panel_idx == 0, g._rrc_panel_idx
    print("ok test_rrc_members_clamps_panel_idx_when_the_roster_shrinks")


def test_roster_shrink_does_not_strand_the_panel_past_the_members():
    # Review finding: the first pass clamped _rrc_panel_scroll against
    # _rrc_panel_idx (the selection) instead of against the panel's actual
    # valid top-of-window range. A mass PART can shrink the roster to fewer
    # members than fit on screen while both idx and scroll are still deep
    # in a long list; clamping scroll to idx alone leaves it stranded above
    # 0 even though every remaining member would now fit from scroll=0.
    # This needs BOTH a stale nonzero scroll and a shrink past it in the
    # same call -- none of the other clamp tests combine those two, which
    # is exactly why this slipped through the first pass.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    big = [(bytes([i]) * 16, "n%d" % i) for i in range(20)]
    g.rrc_members(big)
    g._rrc_panel_idx = 19          # scrolled to the bottom of a long roster
    g._rrc_panel_scroll = 13
    g.rrc_members(big[:6])         # mass PART: 6 members, 7 rows visible
    assert g._rrc_panel_scroll == 0, g._rrc_panel_scroll
    assert g._rrc_panel_idx == 5, g._rrc_panel_idx
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)
    painted = _painted(g.tft.calls)
    for nick in ("n0", "n1", "n2", "n3", "n4", "n5"):
        assert nick in painted, (nick, painted)
    print("ok test_roster_shrink_does_not_strand_the_panel_past_the_members")


def test_draw_room_paints_the_panel_when_it_is_open():
    # Minor 1: every other panel test calls draw_member_panel() directly.
    # draw_room()'s "if ui._rrc_panel: draw_panel(ui)" dispatch is the only
    # thing connecting panel-open state to the panel actually painting, and
    # nothing exercised it.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "sv2rck" in painted, painted
    print("ok test_draw_room_paints_the_panel_when_it_is_open")


def test_trackball_inside_the_panel_scrolls_members_not_scrollback():
    # Full entry point (handle_trackball), not the bare module function --
    # this is the wiring the panel's "takes focus" claim actually rests on.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(3)])
    before_scroll_chat = g._rrc_scroll_chat
    g._irq_down = 1
    g.handle_trackball()
    assert g._rrc_panel_idx == 1, g._rrc_panel_idx
    assert g._rrc_scroll_chat == before_scroll_chat
    print("ok test_trackball_inside_the_panel_scrolls_members_not_scrollback")


def test_trackball_click_inside_the_panel_inserts_mention_via_handle_trackball():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.on_rrc_mention = lambda h: "@sv2rck"
    g._irq_click = 1
    g.handle_trackball()
    assert g._rrc_input == "@sv2rck "
    assert g._rrc_panel is False
    print("ok test_trackball_click_inside_the_panel_inserts_mention_via_handle_trackball")


def test_trackball_up_scrolls_room_history_back():
    # Full entry point (handle_trackball), not _scroll_up() or
    # rrc_ui._visible_lines() directly -- the wiring gap (Task 10's Gap 2)
    # was that nothing drove _rrc_scroll_chat at all with the panel closed,
    # and calling the helpers directly would not have caught that.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(20):
        g.rrc_line("msg", "sam", "line %02d" % i)
    import rrc_ui
    g.tft.calls = []
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "line 19" in painted, painted   # rrc_line() snaps to the bottom
    assert "line 08" not in painted, painted

    g._irq_up = 1
    g.handle_trackball()
    assert g._rrc_scroll_chat == 1, g._rrc_scroll_chat

    g.tft.calls = []
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "line 19" not in painted, painted   # scrolled one line into the past
    assert "line 08" in painted, painted
    print("ok test_trackball_up_scrolls_room_history_back")


def test_trackball_up_clamps_at_the_top_of_scrollback():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(20):
        g.rrc_line("msg", "sam", "line %02d" % i)
    import rrc_ui
    rows = U.BODY_ROWS - 1
    max_scroll = len(rrc_ui._flatten(g)) - rows
    g._irq_up = 50          # spin far past the available scrollback in one drain
    g.handle_trackball()
    assert g._rrc_scroll_chat == max_scroll, (g._rrc_scroll_chat, max_scroll)
    g.tft.calls = []
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "line 00" in painted, painted   # the oldest line is now on screen
    print("ok test_trackball_up_clamps_at_the_top_of_scrollback")


def test_trackball_down_does_not_go_negative():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(5):
        g.rrc_line("msg", "sam", "line %d" % i)
    assert g._rrc_scroll_chat == 0
    g._irq_down = 10        # already at the bottom -- must not underflow
    g.handle_trackball()
    assert g._rrc_scroll_chat == 0, g._rrc_scroll_chat
    print("ok test_trackball_down_does_not_go_negative")


def test_trackball_up_is_a_noop_when_everything_already_fits():
    # Fewer lines than the view holds: _visible_lines' own max(0, ...)
    # clamp already covers this, but the trackball's own clamp must agree
    # -- an off-by-one here would let the counter drift even though
    # nothing ever moves on screen.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.rrc_line("msg", "sam", "hi")
    g.rrc_line("msg", "sam", "there")
    g._irq_up = 5
    g.handle_trackball()
    assert g._rrc_scroll_chat == 0, g._rrc_scroll_chat
    print("ok test_trackball_up_is_a_noop_when_everything_already_fits")


def test_trackball_up_then_down_returns_to_the_bottom():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(20):
        g.rrc_line("msg", "sam", "line %02d" % i)
    g._irq_up = 4
    g.handle_trackball()
    assert g._rrc_scroll_chat == 4, g._rrc_scroll_chat
    g._irq_down = 4
    g.handle_trackball()
    assert g._rrc_scroll_chat == 0, g._rrc_scroll_chat
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    assert "line 19" in _painted(g.tft.calls)
    print("ok test_trackball_up_then_down_returns_to_the_bottom")


def test_panel_open_leaves_room_scrollback_untouched_even_when_scrolled():
    # Stronger version of test_trackball_inside_the_panel_scrolls_members_
    # not_scrollback: that test starts from the default scroll (0), so a
    # broken early-return that let events fall through to the new
    # STATE_RRC_CHAT branch in _scroll_down would still read 0 == 0 and
    # pass. Start from a nonzero scroll so a fallthrough would actually
    # move it.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(20):
        g.rrc_line("msg", "sam", "line %02d" % i)
    g._irq_up = 3
    g.handle_trackball()
    assert g._rrc_scroll_chat == 3, g._rrc_scroll_chat

    g._rrc_panel = True
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(3)])
    g._irq_down = 1
    g.handle_trackball()
    assert g._rrc_scroll_chat == 3, "panel-open trackball must not move room scrollback"
    assert g._rrc_panel_idx == 1, g._rrc_panel_idx
    print("ok test_panel_open_leaves_room_scrollback_untouched_even_when_scrolled")


def test_a_new_message_does_not_yank_a_reader_to_the_bottom():
    # REPLACES test_new_message_resets_scroll_after_reading_back, which
    # pinned the defect: rrc_line() snapped _rrc_scroll_chat to 0 on EVERY
    # inbound line, so reading scrollback in a busy room -- or during a
    # long /list reply or the MOTD burst -- was constantly yanked to the
    # bottom. A nonzero offset means the user deliberately scrolled back.
    #
    # The anchor is held exactly, not merely "not reset": the window is
    # measured back from the end, so the offset must grow by the new
    # line's WRAPPED height. Asserting the same rows are still on screen
    # is what catches an off-by-height -- a bare "scroll != 0" check would
    # pass even if the view crept a row per message.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(20):
        g.rrc_line("msg", "sam", "line %02d" % i)
    g._irq_up = 5
    g.handle_trackball()
    assert g._rrc_scroll_chat == 5, g._rrc_scroll_chat
    import rrc_ui
    # The row cache suppresses an unchanged row, so both frames are drawn
    # from a cleared cache -- otherwise "nothing repainted" would compare
    # equal to "the same rows repainted" and the assertion would be empty.
    g._cache = [''] * U.CACHE_ROWS
    g.tft.calls = []
    rrc_ui.draw_room(g)
    before = _painted(g.tft.calls)

    # One short line (wraps to 1 row) and one long one (wraps to 3), so a
    # naive "+= 1" is caught as well as a "leave it alone".
    g.rrc_line("msg", "sam", "line 20")
    assert g._rrc_scroll_chat == 6, g._rrc_scroll_chat
    long_text = "x" * (U.COLS * 2 + 5)
    g.rrc_line("notice", None, long_text)
    height = len(rrc_ui._wrap_line("notice", None, long_text))
    assert height > 1, height
    assert g._rrc_scroll_chat == 6 + height, g._rrc_scroll_chat

    g._cache = [''] * U.CACHE_ROWS
    g.tft.calls = []
    rrc_ui.draw_room(g)
    after = _painted(g.tft.calls)
    assert after == before, (before, after)   # the same rows, unmoved
    assert "line 20" not in after, after
    print("ok test_a_new_message_does_not_yank_a_reader_to_the_bottom")


def test_sending_your_own_message_returns_the_view_to_the_live_tail():
    # The other half of the LXMF rule the anchor fix borrows: everybody
    # else's message holds your place, your own brings you back. Without
    # this, a message typed while scrolled back -- and say()'s local echo
    # of it -- would land off screen.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    said = []
    g.on_rrc_say = lambda text: said.append(text)
    for i in range(20):
        g.rrc_line("msg", "sam", "line %02d" % i)
    g._irq_up = 5
    g.handle_trackball()
    assert g._rrc_scroll_chat == 5
    for c in "gm":
        g.handle_key(c.encode())
    g.handle_key(b"\r")
    assert said == ["gm"], said
    assert g._rrc_scroll_chat == 0, g._rrc_scroll_chat
    print("ok test_sending_your_own_message_returns_the_view_to_the_live_tail")


def test_a_new_message_still_shows_immediately_when_not_scrolled_back():
    # The other half of the same fix: at the bottom (the normal case) an
    # arriving line must still appear without the user doing anything.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(20):
        g.rrc_line("msg", "sam", "line %02d" % i)
    assert g._rrc_scroll_chat == 0
    g.rrc_line("msg", "sam", "line 20")
    assert g._rrc_scroll_chat == 0, g._rrc_scroll_chat
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    assert "line 20" in _painted(g.tft.calls)
    print("ok test_a_new_message_still_shows_immediately_when_not_scrolled_back")


def test_scrolled_back_reader_survives_the_scrollback_ring_evicting():
    # The ring drops the oldest line once past RRC_SCROLLBACK. Eviction
    # shifts the window and the content by the same amount, so the anchor
    # adjustment is the new line's height either way -- but only if the
    # code does not try to "correct" for the eviction. Drive the buffer
    # past the cap while scrolled back and prove the offset still tracks.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(U.RRC_SCROLLBACK):
        g.rrc_line("msg", "sam", "line %03d" % i)
    g._irq_up = 4
    g.handle_trackball()
    assert g._rrc_scroll_chat == 4, g._rrc_scroll_chat
    import rrc_ui
    # The row cache suppresses an unchanged row, so both frames are drawn
    # from a cleared cache -- otherwise "nothing repainted" would compare
    # equal to "the same rows repainted" and the assertion would be empty.
    g._cache = [''] * U.CACHE_ROWS
    g.tft.calls = []
    rrc_ui.draw_room(g)
    before = _painted(g.tft.calls)

    g.rrc_line("msg", "sam", "overflow")     # forces an eviction
    assert len(g._rrc_lines) == U.RRC_SCROLLBACK
    assert g._rrc_scroll_chat == 5, g._rrc_scroll_chat
    g._cache = [''] * U.CACHE_ROWS
    g.tft.calls = []
    rrc_ui.draw_room(g)
    assert _painted(g.tft.calls) == before
    print("ok test_scrolled_back_reader_survives_the_scrollback_ring_evicting")


def test_trackball_scroll_in_the_hub_console_does_not_touch_lxmf_chat_state():
    # _scroll_up/_scroll_down's bare `else:` meant "the LXMF chat view" --
    # STATE_RRC_ROOMS (the hub console, pre-join) wasn't listed, so
    # trackball up/down there fell through into chat_cursor/chat_scroll,
    # state that belongs to an unrelated screen. Prove the console scrolls
    # its own scrollback and leaves that state alone.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    for i in range(40):
        g.rrc_line("notice", None, "  #room%d - topic" % i)
    g.chat_cursor = 3
    g.chat_scroll = 2
    g._irq_up = 1; g.handle_trackball()
    assert g._rrc_scroll_chat > 0, "the console did not scroll"
    assert (g.chat_cursor, g.chat_scroll) == (3, 2), "it moved the LXMF chat view"
    print("ok test_trackball_scroll_in_the_hub_console_does_not_touch_lxmf_chat_state")


def test_trackball_up_clamps_at_the_top_of_the_hub_console():
    # Mirrors test_trackball_up_clamps_at_the_top_of_scrollback for the
    # room view, but for STATE_RRC_ROOMS: the console's own buffer must
    # not be scrollable past its oldest line either.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    for i in range(40):
        g.rrc_line("notice", None, "  #room%d - topic" % i)
    import rrc_ui
    rows = U.BODY_ROWS - 1
    max_scroll = len(rrc_ui._flatten(g)) - rows
    g._irq_up = 100         # spin far past the available scrollback in one drain
    g.handle_trackball()
    assert g._rrc_scroll_chat == max_scroll, (g._rrc_scroll_chat, max_scroll)
    g.tft.calls = []
    rrc_ui.draw_rooms(g)
    painted = _painted(g.tft.calls)
    assert "#room0 - topic" in painted, painted   # the oldest notice is now on screen
    print("ok test_trackball_up_clamps_at_the_top_of_the_hub_console")


# --- Critical 1: keys must reach RRC through UI.handle_key ------------------
#
# Every RRC key test above calls rrc_ui.handle_key() directly, which skips
# UI.handle_key()'s _bare_nav_key() lookup -- and that lookup ran BEFORE state
# dispatch, turning a bare e/x into a scroll event on every RRC screen. The
# tests below drive the real entry point instead. That convention is exactly
# what hid the bug: test_alt_w_opens_the_panel_and_takes_focus even feeds a
# literal "x" that the real path would have eaten.


def test_typing_e_and_x_reaches_the_room_composer_through_handle_key():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    said = []
    g.on_rrc_say = lambda text: said.append(text)
    for c in "hex test":
        g.handle_key(c.encode())
    assert g._rrc_input == "hex test", g._rrc_input   # was "h tst"
    # ...and the swallowed keys must not have queued scroll events either.
    assert (g._irq_up, g._irq_down) == (0, 0), (g._irq_up, g._irq_down)
    g.handle_key(b"\r")
    assert said == ["hex test"], said
    print("ok test_typing_e_and_x_reaches_the_room_composer_through_handle_key")


def test_typing_e_and_x_reaches_the_join_prompt_through_handle_key():
    # #mesh cannot be joined at all without this: drop the e and the name the
    # hub gets is a different room.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    joined = []
    g.on_rrc_join = lambda room, key=None: joined.append((room, key))
    import rrc_ui
    rrc_ui.panel_toggle(g, "rooms")    # click opens the picker
    rrc_ui.panel_click(g)              # its action row opens the prompt
    assert g._rrc_prompt is True
    for c in "#mesh-extra":
        g.handle_key(c.encode())
    assert g._rrc_input == "#mesh-extra", g._rrc_input   # was "#msh-tra"
    g.handle_key(b"\r")
    assert joined == [("#mesh-extra", None)], joined
    print("ok test_typing_e_and_x_reaches_the_join_prompt_through_handle_key")


def test_e_and_x_still_scroll_the_hub_console_when_no_prompt_is_open():
    # The other half of the _accepting_text() change: the console has no text
    # field until (j) opens one, so e/x must keep their bare-nav meaning there
    # rather than being made inert across the whole tab.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    for i in range(40):
        g.rrc_line("notice", None, "  #room%d - topic" % i)
    g.handle_key(b"e")
    assert g._rrc_input == "", g._rrc_input
    g.handle_trackball()
    assert g._rrc_scroll_chat > 0, "e stopped scrolling the console"
    print("ok test_e_and_x_still_scroll_the_hub_console_when_no_prompt_is_open")


# --- Critical 2: the panel's way in, per board ----------------------------


def test_the_panel_opens_on_a_click_not_a_control_code():
    """0x17 used to open the panel, on the theory that the v1 put its alt
    layer on the control codes. It does not: LilyGO's keyboard firmware
    resolves the layer itself and alt+w leaves as a plain 'w'. The byte is
    real on the Pro, whose alt layer is our own tca8418 keymap -- so that
    board now sends a click instead (board_tdeck_pro._NAV), and both boards
    open the panel through the one gesture.
    """
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.handle_key(b"\x17")
    assert g._rrc_panel is False, "no control code opens the panel any more"
    g.nav_event("click")
    g.handle_trackball()
    assert g._rrc_panel is True
    assert g._rrc_input == "", "the click must not compose a character"
    g.nav_event("click")
    g.handle_trackball()
    assert g._rrc_panel is False, "and a second click toggles it shut"
    print("ok test_the_panel_opens_on_a_click_not_a_control_code")


# --- Important 4: the panel must not stay painted after it closes ----------


def test_every_panel_close_path_invalidates_the_row_cache():
    # The panel is an overlay: fill_rect over rows the row cache believes are
    # already correct. None of the three close paths invalidated it, so the
    # next draw_room() emitted one text call and no fills -- the panel stayed
    # on screen. ui._shell_menu_open/_close solve exactly this for the shell
    # control menu; this follows that precedent.
    #
    # The assertion is that the covered rows are REPAINTED after the close,
    # not merely that _cache is empty: an implementation that cleared some
    # other cache would pass the weaker check.
    import rrc_ui

    def _room_with_panel_open():
        g = make_ui()
        g.state = U.STATE_RRC_CHAT
        g._rrc_room = "#varna"
        for i in range(6):
            g.rrc_line("msg", "sam", "line %d" % i)
        g.rrc_members([(b"\x11" * 16, "sv2rck")])
        g.on_rrc_mention = lambda h: "@sv2rck"
        rrc_ui.draw_room(g)                  # fills the row cache
        g.nav_event("click")                 # the gesture that opens it
        g.handle_trackball()
        assert g._rrc_panel is True
        rrc_ui.draw_room(g)                  # paints the panel over those rows
        return g

    closers = (
        ("second click", lambda g: (g.nav_event("click"), g.handle_trackball())),
        ("esc", lambda g: g.handle_key(b"\x1b")),
        ("panel click", lambda g: rrc_ui.panel_click(g)),
    )
    for name, close in closers:
        g = _room_with_panel_open()
        close(g)
        assert g._rrc_panel is False, name
        g.tft.calls = []
        rrc_ui.draw_room(g)
        painted = _painted(g.tft.calls)
        assert "sam> line 5" in painted, (name, painted)
    print("ok test_every_panel_close_path_invalidates_the_row_cache")


# --- Important 5: the composer's cap is the session's byte budget ----------


def test_composer_caps_on_the_session_budget_not_the_column_count():
    # The old cap was COLS - 2 = 38 characters against a ~350-byte protocol
    # allowance, which made rrc_client.compose_cap() dead code.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.on_rrc_cap = lambda: 350
    import rrc_ui
    for _ in range(400):
        rrc_ui.handle_key(g, ord("a"), b"a")
    assert len(g._rrc_input) == 350, len(g._rrc_input)
    assert len(g._rrc_input) > U.COLS - 2
    print("ok test_composer_caps_on_the_session_budget_not_the_column_count")


def test_composer_cap_follows_a_smaller_session_budget():
    # A path that negotiates a lower MTU inverts the usual headroom, so the
    # cap has to be able to come back SMALLER than the hub's 350 too.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.on_rrc_cap = lambda: 12
    import rrc_ui
    for _ in range(30):
        rrc_ui.handle_key(g, ord("b"), b"b")
    assert g._rrc_input == "b" * 12, g._rrc_input
    print("ok test_composer_cap_follows_a_smaller_session_budget")


def test_composer_budget_counts_utf8_bytes_not_characters():
    # This branch has already had two byte-vs-character bugs. A mention
    # inserted from the member panel carries a hub-controlled nick, so a
    # multi-byte composer buffer is reachable, not hypothetical.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.on_rrc_cap = lambda: 20
    g._rrc_input = "Я" * 9                 # 9 characters, 18 UTF-8 bytes
    import rrc_ui
    for _ in range(5):
        rrc_ui.handle_key(g, ord("a"), b"a")
    assert g._rrc_input == "Я" * 9 + "aa", g._rrc_input
    print("ok test_composer_budget_counts_utf8_bytes_not_characters")


def test_composer_falls_back_to_the_protocol_default_with_no_session():
    # No session up (on_rrc_cap unwired, or a client that answers nonsense):
    # typing must not be refused before a WELCOME lands.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    import rrc_proto as P
    import rrc_ui
    assert g.on_rrc_cap is None
    assert rrc_ui._cap(g) == P.DEFAULT_MAX_BODY
    g.on_rrc_cap = lambda: None                 # a client with no link yet
    assert rrc_ui._cap(g) == P.DEFAULT_MAX_BODY

    def _boom():
        raise RuntimeError("no session")

    g.on_rrc_cap = _boom                        # and one that raises
    assert rrc_ui._cap(g) == P.DEFAULT_MAX_BODY
    print("ok test_composer_falls_back_to_the_protocol_default_with_no_session")


def test_composer_shows_the_remaining_byte_count():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.on_rrc_cap = lambda: 350
    import rrc_ui
    for c in "gm":
        rrc_ui.handle_key(g, ord(c), c.encode())
    g.tft.calls = []
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "> gm" in painted, painted
    assert "348" in painted, painted          # bytes left, not characters
    print("ok test_composer_shows_the_remaining_byte_count")


def test_composer_tail_scrolls_a_long_line_instead_of_refusing_it():
    # ui._draw_input_line()'s idiom: the caret is always the last cell, so a
    # line longer than the row shows its tail behind a "<" marker. Without
    # this, capping at 350 bytes on a 40-column row would type into space the
    # user cannot see.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.on_rrc_cap = lambda: 350
    import rrc_ui
    for c in "HEAD" + "." * 60 + "TAIL":
        rrc_ui.handle_key(g, ord(c), c.encode())
    assert len(g._rrc_input) == 68, len(g._rrc_input)
    g.tft.calls = []
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "TAIL" in painted, painted
    assert "HEAD" not in painted, painted
    assert "<" in painted, painted
    assert "282" in painted, painted          # 350 - 68, still counted
    print("ok test_composer_tail_scrolls_a_long_line_instead_of_refusing_it")


def test_composer_carries_a_cyrillic_mention_through_tb():
    # Critical 2 makes click-to-mention reachable for the first time, and a
    # nick is hub-controlled: a kept Cyrillic character reaching tft.text()
    # as a raw str is the Task 8 header bug, in the composer row this time.
    # FakeTFT.text() does s.encode("ascii") on a str, so this raises without
    # the ui._tb() wrap.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "Варна")])
    g.on_rrc_mention = lambda h: "@Варна"
    import rrc_ui
    rrc_ui.panel_click(g)
    assert g._rrc_input == "@Варна ", g._rrc_input
    g.tft.calls = []
    rrc_ui.draw_room(g)                       # must not raise
    print("ok test_composer_carries_a_cyrillic_mention_through_tb")


# --- Critical 1: a hub value must never reach the draw path untyped --------
#
# gui_loop wraps the redraw in spi_acquire_display() with no try/except, so a
# TypeError raised inside ui.draw() kills the task WHILE IT HOLDS THE DISPLAY
# LOCK: the screen freezes permanently and the mutex leaks. Every test below
# therefore drives the real dispatcher and then the real draw path, not the
# module helpers in isolation.


def test_a_bytes_nick_from_a_hub_cannot_kill_the_draw_task():
    # _wrap_line does nick + "> " + text inside ui.draw().
    g, link = _hub_driven_ui()
    g.state = U.STATE_RRC_CHAT
    _feed(P.T_MSG, src=b"\x55" * 16, room="#varna", body="anyone on 868?",
          nick=b"\xff\xfe")
    import rrc_ui
    g.tft.calls = []
    rrc_ui.draw_room(g)                          # must not raise
    painted = _painted(g.tft.calls)
    assert "anyone on 868?" in painted, painted  # the message still renders
    print("ok test_a_bytes_nick_from_a_hub_cannot_kill_the_draw_task")


def test_a_non_string_room_from_a_hub_cannot_kill_the_draw_task():
    # rrc_joined(room) -> ui._rrc_room -> _ascii(room) in draw_room.
    for bad in (12345, b"#varna", ["#varna"]):
        g, link = _hub_driven_ui(state=rrc_client.READY)
        _feed(P.T_JOINED, room=bad, body=[b"\x11" * 16])
        assert g.state == U.STATE_RRC_CHAT, g.state
        import rrc_ui
        g.tft.calls = []
        rrc_ui.draw_room(g)                      # must not raise
    print("ok test_a_non_string_room_from_a_hub_cannot_kill_the_draw_task")


def test_the_header_stops_reporting_the_connect_step_once_welcomed():
    """The reported field failure: a live session showing "waiting fo".

    connect() ends on _status("waiting for WELCOME...") and never calls
    _status() again -- the wait loop exits because _state left CONNECTING
    and the function returns. The header renders ui._rrc_status[:10] in its
    right-hand corner, so a session that was linked, identified and
    welcomed painted "waiting fo" for as long as it stayed open. Only the
    UI and the client joined up show it: the client alone never draws, and
    the UI alone is handed the status it is asked to assume.
    """
    import rrc_ui
    g, link = _hub_driven_ui(state=rrc_client.CONNECTING)
    rrc_client._status("waiting for WELCOME...")
    _feed(P.T_WELCOME, body={P.B_WELCOME_HUB: "Varna Hub"})
    assert rrc_client._state == rrc_client.READY
    g.tft.calls = []
    rrc_ui.draw_rooms(g)
    painted = _painted(g.tft.calls)
    assert "waiting" not in painted, painted
    assert "Varna Hub" in painted, painted
    print("ok test_the_header_stops_reporting_the_connect_step_once_welcomed")


def test_the_header_gives_the_whole_row_to_the_status_before_welcome():
    """Before WELCOME there is no hub name, so the status is all there is.

    Squeezed into the ten-character right-hand corner it truncated to
    "waiting fo", which names neither the step nor the failure.
    """
    import rrc_ui
    g, link = _hub_driven_ui(state=rrc_client.CONNECTING)
    rrc_client._status("waiting for WELCOME...")
    g.tft.calls = []
    rrc_ui.draw_rooms(g)
    painted = _painted(g.tft.calls)
    assert "waiting for WELCOME" in painted, painted
    print("ok test_the_header_gives_the_whole_row_to_the_status_before_welcome")


def test_a_non_string_welcome_hub_name_cannot_kill_the_draw_task():
    # _draw_header does left + "\x01" + right on the announced hub name.
    for bad in (["evil"], 7, b"hub"):
        g, link = _hub_driven_ui(state=rrc_client.CONNECTING)
        _feed(P.T_WELCOME, body={P.B_WELCOME_HUB: bad})
        assert g.state == U.STATE_RRC_ROOMS, g.state
        import rrc_ui
        g.tft.calls = []
        rrc_ui.draw_rooms(g)                     # must not raise
        painted = _painted(g.tft.calls)
        # _txt() drops the hostile value and the client falls back to the
        # hub's own hash, so the header names the hub instead of going
        # blank -- and never reads "connecting..." on a live session.
        assert "42424242" in painted, painted
        assert "connecting" not in painted, painted
        assert "evil" not in painted, painted
    print("ok test_a_non_string_welcome_hub_name_cannot_kill_the_draw_task")


def test_a_bytes_nick_in_the_roster_cannot_kill_the_member_panel():
    # Two arrivals: one named, one with a bytes nick. members.sort() raises
    # on the mixed comparison before the panel even gets a chance to draw
    # _ascii(nick).
    g, link = _hub_driven_ui()
    g.state = U.STATE_RRC_CHAT
    _feed(P.T_JOINED, room="#varna", body=[b"\x11" * 16], nick="sam")
    _feed(P.T_JOINED, room="#varna", body=[b"\x22" * 16], nick=b"\xff\xfe")
    assert len(g._rrc_roster) == 2, g._rrc_roster
    g._rrc_panel = True
    import rrc_ui
    g.tft.calls = []
    rrc_ui.draw_room(g)                          # must not raise
    painted = _painted(g.tft.calls)
    assert "sam" in painted, painted
    print("ok test_a_bytes_nick_in_the_roster_cannot_kill_the_member_panel")


def test_a_bytes_nick_mention_cannot_kill_kbd_loop():
    # mention_for() builds "@" + nick, and the whole click-to-mention path
    # runs inside handle_trackball() -- the task that owns all input.
    g, link = _hub_driven_ui()
    g.state = U.STATE_RRC_CHAT
    _feed(P.T_JOINED, room="#varna", body=[b"\x22" * 16], nick=b"\xff\xfe")
    g.nav_event("click")                     # a click opens the panel
    g.handle_trackball()
    assert g._rrc_panel is True
    g._irq_click = 1
    g.handle_trackball()                         # must not raise
    # Not merely "a str": the mention must actually have been inserted, or
    # an implementation that swallowed the click would pass on "" alone.
    assert isinstance(g._rrc_input, str), g._rrc_input
    assert g._rrc_input.startswith("@") and g._rrc_input.endswith(" "), \
        g._rrc_input
    assert g._rrc_panel is False
    import rrc_ui
    g.tft.calls = []
    rrc_ui.draw_room(g)                          # must not raise
    print("ok test_a_bytes_nick_mention_cannot_kill_kbd_loop")


# --- Critical 2: a non-integer hub limit must not kill the keyboard --------


def test_a_string_body_limit_from_a_hub_cannot_kill_the_keyboard():
    # rrc_ui._cap() wraps compose_cap() in try/except for drawing, but
    # say() does not: enter on the composer runs UI.handle_key ->
    # rrc_ui.handle_key -> on_rrc_say -> compose_cap -> min("350", 367),
    # and the TypeError propagates all the way out into kbd_loop, taking
    # the keyboard and the trackball with it.
    g, link = _hub_driven_ui(state=rrc_client.CONNECTING)
    _feed(P.T_WELCOME, body={P.B_WELCOME_HUB: "Evil Hub",
                             P.B_WELCOME_LIMITS: {P.L_MAX_BODY: "350"}})
    rrc_client._state = rrc_client.JOINED
    g.state = U.STATE_RRC_CHAT
    for c in "gm":
        g.handle_key(c.encode())
    g.handle_key(b"\r")                          # must not raise
    assert link.sent, "the message never went out"
    assert P.parse(link.sent[-1])[P.K_BODY] == "gm"
    print("ok test_a_string_body_limit_from_a_hub_cannot_kill_the_keyboard")


# --- Important 2: the scroll anchor must not climb past the scrollback -----


def test_the_scroll_anchor_cannot_climb_past_the_scrollback():
    # rrc_line() adds the new line's wrapped height to the anchor with no
    # clamp. That is exact while the 120-line ring is filling, but once
    # pop(0) starts the flattened length plateaus while the anchor keeps
    # climbing: the view sticks on the OLDEST screen and _scroll_down needs
    # one trackball tick per excess line to recover, with nothing on screen
    # to say so.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(U.RRC_SCROLLBACK):
        g.rrc_line("msg", "sam", "line %03d" % i)
    g._irq_up = 4
    g.handle_trackball()
    assert g._rrc_scroll_chat == 4, g._rrc_scroll_chat
    import rrc_ui
    rows = U.BODY_ROWS - 1
    for i in range(200):                    # well past the ring's capacity
        g.rrc_line("msg", "sam", "more %03d" % i)
        max_scroll = max(0, len(rrc_ui._flatten(g)) - rows)
        assert g._rrc_scroll_chat <= max_scroll, \
            (i, g._rrc_scroll_chat, max_scroll)
    # ...and one tick down still moves the view, rather than burning 200
    # ticks working off an anchor that was never reachable.
    g.tft.calls = []
    rrc_ui.draw_room(g)
    before = _painted(g.tft.calls)
    g._irq_down = 1
    g.handle_trackball()
    g._cache = [''] * U.CACHE_ROWS
    g.tft.calls = []
    rrc_ui.draw_room(g)
    assert _painted(g.tft.calls) != before, "one tick down did not move the view"
    print("ok test_the_scroll_anchor_cannot_climb_past_the_scrollback")


# --- Important 3/4: opening a hub starts from a clean console --------------


def test_opening_a_hub_resets_every_field_of_the_previous_session():
    # open_selected_hub() reset _rrc_lines/_rrc_room/_rrc_members and
    # nothing else, so hub B's console showed hub A's name until B's
    # WELCOME landed, alt+w showed A's roster, and the view opened scrolled
    # back against an empty buffer.
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x11" * 16, name="Hub A", hops=1)
    g.add_rrc_hub(b"\x22" * 16, name="Hub B", hops=1)
    connected = []
    g.on_rrc_connect = lambda dest: connected.append(dest)
    # ...a live session on hub A:
    g._rrc_idx = 0
    g._rrc_hub_name = "Hub A"
    g._rrc_status = "linking..."
    g._rrc_scroll_chat = 7
    g._rrc_panel = True
    g._rrc_panel_idx = 3
    g._rrc_panel_scroll = 2
    g._rrc_input = "half-typed"
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(5)])
    for i in range(20):
        g.rrc_line("msg", "sam", "hub A line %d" % i)

    g._rrc_idx = 1
    import rrc_ui
    rrc_ui.open_selected_hub(g)

    assert connected == [b"\x22" * 16], connected
    assert g._rrc_lines == [], g._rrc_lines
    assert g._rrc_room is None, g._rrc_room
    assert g._rrc_members == 0, g._rrc_members
    assert g._rrc_scroll_chat == 0, g._rrc_scroll_chat
    assert g._rrc_hub_name is None, g._rrc_hub_name
    assert g._rrc_roster == [], g._rrc_roster
    assert g._rrc_status == "", g._rrc_status
    assert g._rrc_panel is False, g._rrc_panel
    assert g._rrc_panel_idx == 0 and g._rrc_panel_scroll == 0
    assert g._rrc_input == "", g._rrc_input
    # The consequence, not just the fields: hub B's first notice is the one
    # on screen, at the live tail.
    g.rrc_line("notice", None, "Hub B MOTD")
    g.tft.calls = []
    rrc_ui.draw_rooms(g)
    painted = _painted(g.tft.calls)
    assert "Hub B MOTD" in painted, painted
    assert "hub A line" not in painted, painted
    assert "Hub A" not in painted, painted
    print("ok test_opening_a_hub_resets_every_field_of_the_previous_session")


def test_opening_a_second_hub_ends_the_first_session_through_the_real_click():
    # Important 4 end to end: the click commits the UI to hub B, so the
    # client has to accept it. With the old "busy - session in progress"
    # refusal the user sat on an empty hub-B console while still joined to
    # hub A, with A's traffic painting into it.
    g, link = _hub_driven_ui()                   # JOINED to hub A
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x22" * 16, name="Hub B", hops=1)
    g._rrc_idx = 0
    dialled = []
    g.on_rrc_connect = lambda dest: (dialled.append(dest),
                                     rrc_client.connect(dest))
    created = []

    import uasyncio
    saved = getattr(uasyncio, "create_task", None)
    uasyncio.create_task = lambda coro: created.append(coro)
    try:
        g._irq_click = 1
        g.handle_trackball()                     # the real click path
    finally:
        if saved is None:
            del uasyncio.create_task
        else:
            uasyncio.create_task = saved
    assert dialled == [b"\x22" * 16], dialled
    assert created, "no session task was started for hub B"
    assert rrc_client.is_active() is False, "hub A's session survived the switch"
    assert rrc_client._link is None
    for coro in created:
        coro.close()
    print("ok test_opening_a_second_hub_ends_the_first_session_through_the_real_click")


# --- Important 6: eviction must not move the selection to another hub ------


def test_hub_eviction_keeps_the_selection_on_the_same_hub():
    # _rrc_keys.pop(0) shifted every index down one while _rrc_idx stayed
    # put, so the highlighted row silently became a different hub and the
    # next click opened it.
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    for i in range(U.MAX_RRC_HUBS):
        g.add_rrc_hub(bytes([i]) * 16, name="hub%02d" % i, hops=1)
    g._rrc_idx = 3
    selected = g._rrc_keys[3]
    g.add_rrc_hub(b"\xf0" * 16, name="newcomer", hops=1)   # forces an eviction
    assert len(g._rrc_keys) == U.MAX_RRC_HUBS
    assert selected in g._rrc_keys, "the selected hub itself was evicted"
    assert g._rrc_keys[g._rrc_idx] == selected, \
        "eviction moved the selection to a different hub"
    connected = []
    g.on_rrc_connect = lambda dest: connected.append(dest)
    g._irq_click = 1
    g.handle_trackball()
    assert connected == [selected], connected
    print("ok test_hub_eviction_keeps_the_selection_on_the_same_hub")


def test_hub_eviction_drops_the_least_recently_seen_not_the_oldest_added():
    # add_shell_node()'s shape: a hub that is still announcing must outlive
    # one that went quiet, whatever order they were first heard in.
    g = make_ui()
    for i in range(U.MAX_RRC_HUBS):
        g.add_rrc_hub(bytes([i]) * 16, name="hub%02d" % i, hops=1)
    first = bytes([0]) * 16
    quiet = bytes([1]) * 16
    g.add_rrc_hub(first, name="hub00", hops=1)   # first-added, still announcing
    g.add_rrc_hub(b"\xf0" * 16, name="newcomer", hops=1)
    assert first in g.rrc_hubs, "a hub that is still announcing was evicted"
    assert quiet not in g.rrc_hubs, (
        "eviction was FIFO, not least-recently-seen")
    assert len(g._rrc_keys) == len(g.rrc_hubs) == U.MAX_RRC_HUBS
    print("ok test_hub_eviction_drops_the_least_recently_seen_not_the_oldest_added")


# --- Minor 2: the panel's nick column must not run into the hash column ----


def test_the_panel_nick_column_clears_the_hash_column_on_both_boards():
    # _PANEL_HASH_X derives from PANEL_W (board-aware) but the nick was
    # sliced to a fixed 20 characters. On the Pro (SCREEN_W 240) the name
    # ran to x=176 while the hash column starts at 144.
    import importlib
    import rrc_ui
    saved_w = U.SCREEN_W
    try:
        for screen_w in (320, 240):
            U.SCREEN_W = screen_w
            importlib.reload(rrc_ui)
            g = make_ui()
            g.state = U.STATE_RRC_CHAT
            g._rrc_room = "#varna"
            g._rrc_panel = True
            g.rrc_members([(b"\x11" * 16, "n" * 40)])   # a nick far too long
            g.tft.calls = []
            rrc_ui.draw_member_panel(g)
            name_call = [c for c in g.tft.calls
                         if c[0] == "text" and c[2] == rrc_ui._PANEL_TEXT_X
                         and c[3] > rrc_ui.PANEL_Y + 20][0]
            end_x = name_call[2] + len(name_call[1]) * U.CHAR_W
            assert end_x <= rrc_ui._PANEL_HASH_X, \
                (screen_w, end_x, rrc_ui._PANEL_HASH_X)
    finally:
        U.SCREEN_W = saved_w
        importlib.reload(rrc_ui)
    print("ok test_the_panel_nick_column_clears_the_hash_column_on_both_boards")


# --- Minor 3: (m) on the RRC tab has to do what the hint says --------------
# Since PR #15 + Find, (m) is "search, or add by hash" on every tab, and a
# manual add lands in the list selected rather than connecting on the spot.


def test_m_on_the_rrc_tab_opens_find_and_adds_the_hub():
    # The empty state says "(m) to find or add one"; the key must do that.
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    connected = []
    g.on_rrc_connect = lambda dest: connected.append(dest)
    g.handle_key(b"m")
    assert g._find is True, "the (m) hint still does nothing"
    for c in "bb" * 16:
        g.handle_key(c.encode())
    g.draw_node_list()                     # the entry screen must render
    g.handle_key(b"\r")
    assert g._rrc_keys == [bytes([0xBB]) * 16], g._rrc_keys
    assert g._rrc_keys[g._rrc_idx] == bytes([0xBB]) * 16
    assert connected == [], connected      # added, not connected
    assert g.state == U.STATE_NODES, g.state
    assert g._find is False
    print("ok test_m_on_the_rrc_tab_opens_find_and_adds_the_hub")


def test_manual_entry_on_the_rrc_tab_never_starts_a_shell():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.handle_key(b"m")
    for c in "cc" * 16:
        g.handle_key(c.encode())
    g.handle_key(b"\r")
    assert g._rrc_keys == [bytes([0xCC]) * 16], g._rrc_keys
    assert g._shell_keys == [], g._shell_keys
    assert g.connects == [], g.connects       # on_shell_connect must not fire
    assert g._terminal is None
    print("ok test_manual_entry_on_the_rrc_tab_never_starts_a_shell")


def test_manual_entry_accepts_the_hex_digits_e_and_x_is_not_one():
    # 'e' IS a hex digit, and UI.handle_key runs _bare_nav_key() before
    # state dispatch: on a screen not "accepting text" a bare e is eaten as
    # a scroll event and never reaches the entry. Roughly seven in eight
    # 16-byte hashes contain an 'e', so without this adding by hash is
    # unusable.
    for tab, keys in ((U.TAB_RRC, "_rrc_keys"), (U.TAB_SSH, "_shell_keys")):
        g = make_ui()
        g.state = U.STATE_NODES
        g._switch_tab(tab)
        g.handle_key(b"m")
        for c in "ee" * 16:
            g.handle_key(c.encode())
        assert g._find_q == "ee" * 16, (keys, g._find_q)
        assert (g._irq_up, g._irq_down) == (0, 0), (keys, g._irq_up, g._irq_down)
        g.handle_key(b"\r")
        assert getattr(g, keys) == [bytes([0xEE]) * 16], (keys, getattr(g, keys))
    print("ok test_manual_entry_accepts_the_hex_digits_e_and_x_is_not_one")


def test_manual_entry_still_works_on_the_ssh_tab():
    # The SSH tab's own add-by-hash must survive being generalised.
    g = make_ui()
    g._switch_tab(U.TAB_SSH)
    g.handle_key(b"m")
    assert g._find is True
    for c in "aa" * 16:
        g.handle_key(c.encode())
    g.handle_key(b"\r")
    assert g._shell_keys == [bytes([0xAA] * 16)]
    assert g.connects == []
    assert g.state == U.STATE_NODES
    print("ok test_manual_entry_still_works_on_the_ssh_tab")


# --- Minor 4: hub prose is rendered verbatim, spacing and all -------------


def test_hub_prose_keeps_its_column_spacing_in_the_scrollback():
    # _ascii() is ' '.join(raw.split()), so a hub's space-aligned /list
    # table arrived as one run-on line -- on the one screen whose entire
    # purpose is rendering hub prose verbatim. _draw_composer already uses
    # _tb() rather than _ascii() for exactly this reason.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g.rrc_line("notice", None, "Registered public rooms:")
    g.rrc_line("notice", None, "  #varna     12  Varna mesh")
    g.rrc_line("notice", None, "  #dx         3  DX chat")
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)
    painted = _painted(g.tft.calls)
    assert "#varna     12  Varna mesh" in painted, painted
    assert "#dx         3  DX chat" in painted, painted
    print("ok test_hub_prose_keeps_its_column_spacing_in_the_scrollback")


def test_control_characters_are_still_stripped_from_hub_prose():
    # Preserving spacing must not preserve control bytes: the font has no
    # glyphs for them and a raw \x1b could steer a terminal-ish driver.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g.rrc_line("notice", None, "ro\x00om\x1b[31m  spaced\ttab\nnewline")
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)
    for c in g.tft.calls:
        if c[0] != "text":
            continue
        s = c[1]
        if isinstance(s, str):
            s = s.encode("latin-1")
        for b in s:
            assert b >= 32, (b, c[1])
    print("ok test_control_characters_are_still_stripped_from_hub_prose")


# --- Minor 5: the flattened scrollback is cached, not rebuilt per tick -----


def test_the_flattened_scrollback_is_not_rebuilt_on_every_trackball_tick():
    # Each tick called _scroll_up, which ran _flatten() over all 120
    # entries doing _ascii() and _wrap(); a five-tick drain did five full
    # flattens and then draw_room did a sixth, inside a 50 ms redraw budget
    # on a device already spending ~220 us per composited cell.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    for i in range(U.RRC_SCROLLBACK):
        g.rrc_line("msg", "sam", "line %03d" % i)
    import rrc_ui
    calls = [0]
    real = rrc_ui._wrap_line

    def counting(kind, nick, text):
        calls[0] += 1
        return real(kind, nick, text)

    rrc_ui._wrap_line = counting
    try:
        rrc_ui.draw_room(g)                  # primes the cache
        calls[0] = 0
        g._irq_up = 5
        g.handle_trackball()
        rrc_ui.draw_room(g)
        assert calls[0] == 0, (
            "a five-tick drain plus a redraw re-wrapped %d entries" % calls[0])
        # ...and the cache still follows the scrollback.
        g.rrc_line("msg", "sam", "fresh")
        assert ("msg", "sam> fresh") in rrc_ui._flatten(g)
    finally:
        rrc_ui._wrap_line = real
    print("ok test_the_flattened_scrollback_is_not_rebuilt_on_every_trackball_tick")


def test_rrc_ui_uses_the_wrapper_ui_already_has():
    # rrc_ui._wrap() was a character-for-character duplicate of
    # ui.UI._wrap_text().
    import rrc_ui
    assert not hasattr(rrc_ui, "_wrap"), "the duplicate wrapper is still here"
    assert rrc_ui._wrap_line("notice", None, "x" * (U.COLS * 2 + 3)) == \
        U.UI._wrap_text("x" * (U.COLS * 2 + 3), U.COLS)
    print("ok test_rrc_ui_uses_the_wrapper_ui_already_has")


# --- The durable guard: no hub field reaches the UI untyped ---------------
#
# Critical 1 was a data-flow defect, and four review passes walked past it
# because every one of them asked "can _on_packet raise?" -- it cannot, it is
# well guarded -- rather than following the values OUT of it. The tests above
# pin the five exit points that were found. This one pins the RULE, so that
# the next field added to _on_packet without routing through
# rrc_client._txt() fails here instead of freezing a display on someone's
# desk with the SPI lock still held.
#
# IF YOU ARE READING THIS BECAUSE IT WENT RED AFTER YOU ADDED A FIELD:
# the rule is that no value originating from a hub may leave rrc_client as
# anything but a str or None. Read it out of the envelope through
# rrc_client._txt(), once, at the point of read -- not defensively at each
# use downstream. See _txt()'s docstring for why the downstream is fatal.
#
# Deterministic by construction, in two layers:
#   - an exhaustive product (every fuzzable envelope key x every hostile
#     value x every rendering message type) with no randomness in it at all;
#   - a tail that puts SEVERAL hostile fields in one envelope, seeded with
#     the fixed constant below.
# Nothing here reads the clock or os.urandom. A fuzz test that fails once a
# fortnight on a value nobody can reproduce is deleted by whoever hits it,
# and then the guard is gone. Change _SWEEP_SEED only deliberately, and
# re-run the neutered-_txt check documented below when you do.
_SWEEP_SEED = 20260919
_SWEEP_COMBOS = 200          # multi-field envelopes, on top of the floor

# Floats are absent on purpose, not by oversight: rrc_cbor cannot encode one,
# and its decoder skips floats to None, so a float never reaches a field.
_HOSTILE = (b"\xff\xfe", b"", b"\x11" * 16, b"\x00" * 300,
            12345, -1, 0, True, False,
            ["x"], [], [b"\x11" * 16, 7, "str"],
            {"k": 1}, {},
            "ok", "", "\x00\x1b[31m", "варна" * 8,
            "x" * 600)

# K_V and K_T are excluded: P.parse() rejects a bad version or a non-int
# type outright, so fuzzing them would test the parser, not the boundary.
# K_DST and the extension key 64 are in deliberately -- neither is read by
# _on_packet today, which is exactly what makes them stand in for the next
# field somebody adds.
_FUZZ_KEYS = (P.K_ID, P.K_TS, P.K_SRC, P.K_ROOM, P.K_BODY, P.K_NICK,
              P.K_DST, 64)

# The WELCOME body is a NESTED map whose keys are read exactly the way the
# envelope's are, so they are fuzzed the same way -- and this is not
# belt-and-braces, it is a hole the first draft of this test actually had.
# A hostile K_BODY of {"k": 1} is a dict, so it survives the
# isinstance(body, dict) guard, but B_WELCOME_HUB is key 0, so
# body.get(B_WELCOME_HUB) just returns None: the sweep never produced a
# hostile hub name at all, and the hub-name boundary was uncovered while
# the test still printed "ok". Proven by the neutering run in the report --
# that site alone came back GREEN until these were added.
_WELCOME_KEYS = (P.B_WELCOME_HUB, P.B_WELCOME_VER, P.B_WELCOME_CAPS,
                 P.B_WELCOME_LIMITS)
# Third level down: a limits map that IS a map but whose values are not
# integers. That is Critical 2's exact shape ({L_MAX_BODY: "350"}), and a
# sweep that only ever made _limits a non-dict would not reach it.
_LIMIT_KEYS = (P.L_MAX_NICK, P.L_MAX_ROOM, P.L_MAX_BODY, P.L_MAX_ROOMS,
               P.L_RATE)

# The types whose handlers put something on screen. A hostile value only
# proves anything if it reaches a renderer.
_RENDER_TYPES = (P.T_WELCOME, P.T_JOINED, P.T_PARTED, P.T_MSG, P.T_NOTICE,
                 P.T_ACTION, P.T_ERROR)

_STATES = (rrc_client.CONNECTING, rrc_client.READY, rrc_client.JOINED)


def _sweep_cases():
    """(type, {key: hostile value}, state) tuples -- floor first, then tail."""
    cases = []
    for i in range(len(_FUZZ_KEYS)):
        for j in range(len(_HOSTILE)):
            for k in range(len(_RENDER_TYPES)):
                # The state is rotated rather than drawn, so the floor stays
                # a pure product: no seed touches it.
                state = _STATES[(i + j + k) % len(_STATES)]
                cases.append((_RENDER_TYPES[k],
                              {_FUZZ_KEYS[i]: _HOSTILE[j]}, state))
    for i in range(len(_WELCOME_KEYS)):
        for j in range(len(_HOSTILE)):
            cases.append((P.T_WELCOME,
                          {P.K_BODY: {_WELCOME_KEYS[i]: _HOSTILE[j]}},
                          _STATES[(i + j) % len(_STATES)]))
    for i in range(len(_LIMIT_KEYS)):
        for j in range(len(_HOSTILE)):
            cases.append((P.T_WELCOME,
                          {P.K_BODY: {P.B_WELCOME_LIMITS:
                                      {_LIMIT_KEYS[i]: _HOSTILE[j]}}},
                          _STATES[(i + j) % len(_STATES)]))
    import random
    rng = random.Random(_SWEEP_SEED)
    for _ in range(_SWEEP_COMBOS):
        env = {}
        for key in rng.sample(_FUZZ_KEYS, rng.randint(2, 4)):
            env[key] = rng.choice(_HOSTILE)
        if rng.random() < 0.5:           # half the tail carries a WELCOME body
            body = {}
            for key in rng.sample(_WELCOME_KEYS, rng.randint(1, 3)):
                body[key] = rng.choice(_HOSTILE)
            if rng.random() < 0.5:
                body[P.B_WELCOME_LIMITS] = dict(
                    (k, rng.choice(_HOSTILE))
                    for k in rng.sample(_LIMIT_KEYS, rng.randint(1, 3)))
            env[P.K_BODY] = body
        cases.append((rng.choice(_RENDER_TYPES), env, rng.choice(_STATES)))
    return cases


def _exercise_every_ui_path(g):
    """Every real path a hub value can travel once it is in UI state."""
    import rrc_ui
    g.state = U.STATE_RRC_CHAT
    rrc_ui.draw_room(g)
    g._rrc_panel = True
    rrc_ui.draw_room(g)                      # with the member panel over it
    g._rrc_panel = False
    g.state = U.STATE_RRC_ROOMS
    rrc_ui.draw_rooms(g)
    g.state = U.STATE_RRC_CHAT
    g.nav_event("click")                     # a click opens the panel
    g.handle_trackball()
    g._irq_down = 1
    g.handle_trackball()                     # move the panel selection
    g._irq_click = 1
    g.handle_trackball()                     # click = mention insert
    g._irq_up = 2
    g.handle_trackball()                     # scroll the room back
    g.handle_key(b"z")
    g.handle_key(b"\r")                      # compose + send through say()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.draw_node_list()


def _assert_ui_state_is_typed(g, case):
    for kind, nick, text in g._rrc_lines:
        assert nick is None or isinstance(nick, str), (case, "nick", nick)
        assert isinstance(text, str), (case, "text", text)
    for src, nick in g._rrc_roster:
        assert isinstance(src, (bytes, bytearray)), (case, "src", src)
        assert nick is None or isinstance(nick, str), (case, "roster", nick)
    assert g._rrc_room is None or isinstance(g._rrc_room, str), \
        (case, "room", g._rrc_room)
    assert g._rrc_hub_name is None or isinstance(g._rrc_hub_name, str), \
        (case, "hub name", g._rrc_hub_name)
    assert isinstance(g._rrc_status, str), (case, "status", g._rrc_status)
    assert isinstance(g._rrc_input, str), (case, "input", g._rrc_input)
    cap = rrc_client.compose_cap()
    assert isinstance(cap, int) and cap >= 0, (case, "cap", cap)
    for src, _nick in g._rrc_roster:
        assert isinstance(rrc_client.mention_for(src), str), (case, "mention")


def test_no_hub_field_reaches_the_ui_untyped():
    cases = _sweep_cases()
    # Guard the guard: a shrinking case list would quietly stop covering
    # things while still printing "ok".
    expected = (len(_FUZZ_KEYS) * len(_HOSTILE) * len(_RENDER_TYPES)
                + len(_WELCOME_KEYS) * len(_HOSTILE)
                + len(_LIMIT_KEYS) * len(_HOSTILE)
                + _SWEEP_COMBOS)
    assert len(cases) == expected, (len(cases), expected)
    for t, fields, state in cases:
        g, link = _hub_driven_ui(state=state)
        # A benign arrival first, so every case has a str nick already in the
        # roster: the mixed-type comparison in _roster_changed()'s sort only
        # exists when a hostile nick lands BESIDE a good one, and a sweep
        # that never pairs them would miss it entirely.
        _feed(P.T_JOINED, room="#varna", body=[b"\x11" * 16], nick="sam")
        env = {P.K_V: P.RRC_VERSION, P.K_T: t, P.K_ID: b"\x01" * 8,
               P.K_TS: 0, P.K_SRC: b"\xaa" * 16}
        env.update(fields)
        rrc_client._on_packet(C.dumps(env))
        _exercise_every_ui_path(g)
        _assert_ui_state_is_typed(g, (t, sorted(fields)))
    print("ok test_no_hub_field_reaches_the_ui_untyped (%d cases)" % len(cases))


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_ui tests passed")

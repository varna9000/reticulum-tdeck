# Host-side test for the tab model: every tab's key list starts with the + New
# row (a key that is never in the tab's table), then favourites in pin order,
# then the rest most recent first. The cursor stays on its entry when rows move;
# eviction spares favourites, the open chat and, while it can, unread chats.
#
# Run:  python3 tests/test_tab_model.py

import os
import sys
import types
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# lib/ holds spleen_6x12, the small font the v1 draws secondary text in
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

_time.ticks_ms = lambda: int(_time.time() * 1000)
_time.ticks_diff = lambda a, b: a - b
_time.sleep_ms = lambda ms: None
sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))


class _Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    IRQ_FALLING = 4
    IRQ_RISING = 8

    def __init__(self, *a, **k):
        pass

    def irq(self, *a, **k):
        pass

    def value(self, *a):
        return 1


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
sys.modules["machine"] = _machine

import ui


class FakeTFT:
    """Records every string drawn, so tests can read the screen back."""

    def __init__(self):
        self.texts = []
        self.colors = []      # (s, x, y, fg, bg) for every text call
        self.rects = []       # (x, y, w, h, c)

    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, (bytes, bytearray)):
            s = s.decode()
        s.encode("ascii")
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.texts.append((s, x, y))
        self.colors.append((s, x, y, fg, bg))

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.rects.append((x, y, w, h, c))

    def fill(self, c):
        pass

    def at_y(self, y):
        """Text drawn in the 16px row at y (small-font text sits a few px
        lower, centred in the row), left to right."""
        row = sorted((_x, s) for s, _x, _y in self.texts if y <= _y < y + 16)
        return "".join(s for _x, s in row)

    def drawn(self):
        return " | ".join(s for s, _x, _y in self.texts)


import json

import rrc_ui

TABS = (ui.TAB_MSG, ui.TAB_NET, ui.TAB_SSH, ui.TAB_RRC)
A, B, C, D = (bytes([i]) * 16 for i in (0xA0, 0xB0, 0xC0, 0xD0))
F, F1, F2 = (bytes([i]) * 16 for i in (0xF0, 0xF1, 0xF2))
NEW_KEY = b"\xee" * 16


def key_n(i):
    return bytes([1, i]) * 8


def mk(store=None):
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t")
    g._screen_on = True
    # Favourites on a dict instead of /rns: JSON round trip, like the file.
    store = {} if store is None else store
    g.store = store
    g._get_favorites = lambda t=None: json.loads(store.get(t, "{}"))
    g._save_favorites = lambda d, t=None: store.__setitem__(t, json.dumps(d)) or True
    return g


def keys(g, tab):
    return g._tab_list(tab)[0]


def table(g, tab):
    return g._tab_list(tab)[1]


def add(g, tab, k, name="n", **kw):
    if tab == ui.TAB_MSG:
        g.add_peer(k, name, **kw)
    elif tab == ui.TAB_NET:
        g.add_nomad_node(k, name, **kw)
    elif tab == ui.TAB_SSH:
        g.add_shell_node(k, name=name, **kw)
    else:
        g.add_rrc_hub(k, name=name, **kw)


def select(g, tab, i):
    g.node_tab = tab
    setattr(g, ui._TAB_ATTRS[tab][0], i)


def cursor(g, tab):
    return getattr(g, ui._TAB_ATTRS[tab][0])


def scroll(g, tab):
    return getattr(g, ui._TAB_ATTRS[tab][1])


def mk_filled(tab):
    g = mk()
    g.node_tab = tab
    add(g, tab, A, "a")
    add(g, tab, B, "b")
    return g


def snapshot(g, tab):
    return (list(keys(g, tab)), json.dumps({k.hex(): v for k, v in table(g, tab).items()}, sort_keys=True),
            cursor(g, tab), scroll(g, tab), g.ping_status, g.ping_pending, g._find, g.state, dict(g.store))


def cap(tab):
    return ui.MAX_RRC_HUBS if tab == ui.TAB_RRC else ui.MAX_PEERS


def mk_at_cap(tab):
    g = mk()
    g.node_tab = tab
    for i in range(cap(tab)):
        add(g, tab, key_n(i))
    return g


def add_one_more(g, tab):
    add(g, tab, NEW_KEY)


def with_five_pins():
    g = mk()
    for i in range(ui.MAX_FAVORITES):
        g.add_peer(key_n(i), "p")
        select(g, ui.TAB_MSG, g._peer_keys.index(key_n(i)))
        g.handle_key(b"f")
        assert g.peers[key_n(i)]["fav"] is True
    g.add_peer(A, "a")
    return g


def select_chat(g):
    select(g, ui.TAB_MSG, g._peer_keys.index(A))


# --- the top row and the order ---------------------------------------------

def test_top_row_is_first_on_every_tab():
    g = mk()
    for tab in TABS:
        assert keys(g, tab) == [ui._NEW], tab
        assert cursor(g, tab) == 0, tab
        assert ui._NEW not in table(g, tab), tab


def test_tab_attrs_are_in_tab_order():
    assert ui._TAB_ATTRS[ui.TAB_MSG] == ("selected_idx", "node_scroll")
    assert ui._TAB_ATTRS[ui.TAB_NET] == ("net_idx", "net_scroll")
    assert ui._TAB_ATTRS[ui.TAB_SSH] == ("ssh_idx", "ssh_scroll")
    assert ui._TAB_ATTRS[ui.TAB_RRC] == ("_rrc_idx", "_rrc_scroll")


def test_order_new_then_pins_then_recent():
    g = mk()
    g.add_peer(A, "a")
    g.add_peer(F, "f", fav=True)
    g.add_peer(B, "b")
    assert g._peer_keys == [ui._NEW, F, B, A]


def test_order_holds_on_the_other_tabs():
    for tab in TABS[1:]:
        g = mk()
        add(g, tab, A)
        add(g, tab, F, fav=True)
        add(g, tab, B)
        assert keys(g, tab) == [ui._NEW, F, B, A], tab


def test_pins_keep_pin_order_and_do_not_move_on_message():
    g = mk()
    g.add_peer(F1, "1", fav=True)
    g.add_peer(F2, "2", fav=True)
    g.add_peer(A, "a")
    g.add_chat_message(F2, False, "hi")
    assert g._peer_keys == [ui._NEW, F1, F2, A]


def test_message_moves_chat_under_pins_and_cursor_stays():
    g = mk()
    g.add_peer(F, "f", fav=True)
    g.add_peer(A, "a")
    g.add_peer(B, "b")
    g.selected_idx = g._peer_keys.index(A)
    g.add_chat_message(A, False, "hi")
    assert g._peer_keys[1:3] == [F, A] and g._peer_keys[g.selected_idx] == A


def test_message_keeps_the_cursor_on_another_peer():
    g = mk()
    g.add_peer(A, "a")
    g.add_peer(B, "b")
    g.add_peer(C, "c")                 # [+, C, B, A]
    g.selected_idx = g._peer_keys.index(B)
    g.add_chat_message(A, False, "hi")
    assert g._peer_keys == [ui._NEW, A, C, B]
    assert g._peer_keys[g.selected_idx] == B


def test_own_send_also_moves_the_chat():
    g = mk()
    g.add_peer(A, "a")
    g.add_peer(B, "b")
    g.add_chat_message(A, True, "out")
    assert g._peer_keys[1] == A
    assert A not in g.unread


def test_message_for_a_chat_with_no_row_does_not_raise():
    g = mk()
    g.add_chat_message(A, False, "hi")
    assert g._peer_keys == [ui._NEW]
    assert g.unread[A] == 1


def test_update_does_not_move_a_row():
    for tab in TABS:
        g = mk()
        add(g, tab, A, "a")
        add(g, tab, B, "b")
        add(g, tab, A, "a2")
        assert keys(g, tab) == [ui._NEW, B, A], tab
        assert table(g, tab)[A]["name"] == "a2", tab


def test_cursor_stays_on_screen_when_rows_push_it_down():
    g = mk()
    for i in range(ui.LIST_ROWS + 3):
        g.add_peer(key_n(i), "p")
    g.selected_idx = ui.LIST_ROWS - 1      # the last visible row
    sel = g._peer_keys[g.selected_idx]
    g.add_peer(A, "a")                     # lands above it
    assert g._peer_keys[g.selected_idx] == sel
    assert g.node_scroll <= g.selected_idx < g.node_scroll + ui.LIST_ROWS, (g.selected_idx, g.node_scroll)


def test_a_row_placed_on_another_tab_leaves_this_tabs_cursor():
    g = mk()
    g.add_peer(A, "a")
    g.add_peer(B, "b")
    select(g, ui.TAB_MSG, 2)
    add(g, ui.TAB_NET, C)
    assert g.selected_idx == 2 and g.net_idx == 0
    assert g.node_tab == ui.TAB_MSG


# --- actions on the top row ------------------------------------------------

def test_top_row_actions_are_inert():
    for tab in TABS:
        g = mk_filled(tab)
        pings = []
        deleted = []
        g.on_ping = pings.append
        g.on_delete_peer = deleted.append
        select(g, tab, 0)
        before = snapshot(g, tab)
        for k in (b"f", b"d", b"p", b"F", b"D", b"P"):
            g.handle_key(k)
        assert snapshot(g, tab) == before, tab
        assert pings == [] and deleted == [], tab


def test_click_and_enter_on_top_row_open_lookup():
    for tab in TABS:
        for how in ("enter", "click"):
            g = mk_filled(tab)
            select(g, tab, 0)
            state = g.state
            if how == "enter":
                g.handle_key(b"\r")
            else:
                g.nav_event("click")
                g.handle_trackball()
            assert g._find is True, (tab, how)
            assert g.state == state, (tab, how)
            assert g.selected_peer is None, (tab, how)


def test_opening_the_top_row_connects_nothing():
    g = mk()
    calls = []
    g.on_browse = g.on_shell_connect = g.on_rrc_connect = lambda *a: calls.append(a)
    for tab, opener in ((ui.TAB_MSG, g._enter_chat), (ui.TAB_NET, g._open_selected_node),
                        (ui.TAB_SSH, g._open_selected_shell), (ui.TAB_RRC, lambda: rrc_ui.open_selected_hub(g))):
        g._find = False
        select(g, tab, 0)
        opener()
        assert g._find is True, tab
        assert g.state == ui.STATE_NODES, tab
    assert calls == []


def test_real_rows_still_open():
    g = mk_filled(ui.TAB_MSG)
    select(g, ui.TAB_MSG, g._peer_keys.index(A))
    g.handle_key(b"\r")
    assert g.state == ui.STATE_CHAT and g.selected_peer == A and g._find is False

    for tab in (ui.TAB_NET, ui.TAB_SSH, ui.TAB_RRC):
        g = mk_filled(tab)
        got = []
        g.on_browse = lambda d: got.append(d)
        g._start_shell = got.append
        g.on_rrc_connect = lambda d: got.append(d)
        select(g, tab, keys(g, tab).index(A))
        g.handle_key(b"\r")
        assert got == [A], tab
        assert g._find is False, tab


def test_lookup_skips_the_top_row():
    # The trap: _find_open looked every listed key up in the table.
    for tab in TABS:
        g = mk_filled(tab)
        g.on_contact_snapshot = lambda t: []
        g.handle_key(b"m")
        assert g._find is True, tab
        got = [e[0] for e in g._find_snap]
        assert ui._NEW not in got, tab
        assert sorted(got) == sorted([A, B]), tab


def test_delete_a_real_row_leaves_the_top_row():
    for tab in TABS:
        g = mk_filled(tab)
        select(g, tab, keys(g, tab).index(A))
        g.handle_key(b"d")
        assert keys(g, tab) == [ui._NEW, B], tab
        select(g, tab, 1)
        g.handle_key(b"d")
        assert keys(g, tab) == [ui._NEW], tab
        assert cursor(g, tab) == 0, tab
        g.handle_key(b"d")                 # now on the top row
        assert keys(g, tab) == [ui._NEW], tab


# --- eviction --------------------------------------------------------------

def _msg_at_cap():
    g = mk()
    pin, unread_oldest, open_chat, oldest_read = key_n(200), key_n(201), key_n(202), key_n(203)
    g.add_peer(pin, "pin", fav=True)
    for k in (unread_oldest, open_chat, oldest_read):
        g.add_peer(k, "x")
    for i in range(ui.MAX_PEERS - 4):
        g.add_peer(key_n(i), "x")
        g.peers[key_n(i)]["seen"] = 1000 + i
    g.peers[pin]["seen"] = 1
    g.peers[unread_oldest]["seen"] = 2
    g.peers[open_chat]["seen"] = 3
    g.peers[oldest_read]["seen"] = 4
    g.unread[unread_oldest] = 1
    g.selected_peer = open_chat
    assert len(g.peers) == ui.MAX_PEERS
    return g, pin, unread_oldest, open_chat, oldest_read


def test_cap_is_64():
    assert ui.MAX_PEERS == 64
    assert ui.MAX_RRC_HUBS == 16


def test_eviction_order():
    g, pin, unread_oldest, open_chat, oldest_read = _msg_at_cap()
    g.add_chat_message(oldest_read, True, "x")          # sets a history time: older than the rest
    g.chat_history[oldest_read][-1] = g.chat_history[oldest_read][-1][:2] + (4,) + g.chat_history[oldest_read][-1][3:]
    g.add_peer(NEW_KEY, "n")
    assert unread_oldest in g.peers and open_chat in g.peers and pin in g.peers
    assert oldest_read not in g.peers and len(g.peers) == ui.MAX_PEERS
    assert oldest_read not in g._peer_keys
    assert oldest_read not in g.chat_history, "the evicted chat's history stayed behind"
    assert g.selected_peer == open_chat, "the open chat lost its selection"
    assert g._peer_keys[0] == ui._NEW and len(g._peer_keys) == ui.MAX_PEERS + 1


def test_msg_eviction_goes_by_last_message_not_last_announce():
    g, pin, unread_oldest, open_chat, oldest_read = _msg_at_cap()
    # Announced just now but its last message is older than everyone's
    # last-seen, so it goes first; a chat with a recent message stays.
    victim = key_n(0)
    g.add_chat_message(victim, True, "old")
    g.chat_history[victim][-1] = g.chat_history[victim][-1][:2] + (3,) + g.chat_history[victim][-1][3:]
    g.peers[victim]["seen"] = _time.time()
    g.add_chat_message(oldest_read, True, "recent")
    g.add_peer(NEW_KEY, "n")
    assert victim not in g.peers
    assert oldest_read in g.peers


def test_unread_goes_only_when_nothing_else_is_left():
    g = mk()
    pin, opened = key_n(200), key_n(201)
    g.add_peer(pin, "pin", fav=True)
    g.add_peer(opened, "o")
    g.selected_peer = opened
    for i in range(ui.MAX_PEERS - 2):
        g.add_peer(key_n(i), "x")
        g.peers[key_n(i)]["seen"] = 1000 + i
        g.unread[key_n(i)] = 1
    g.peers[pin]["seen"] = g.peers[opened]["seen"] = 1
    g.add_peer(NEW_KEY, "n")
    assert key_n(0) not in g.peers, "the oldest unread goes"
    assert key_n(0) not in g.unread
    assert pin in g.peers and opened in g.peers
    assert len(g.peers) == ui.MAX_PEERS


def test_other_tabs_evict_oldest_by_seen_never_a_favourite():
    for tab in (ui.TAB_NET, ui.TAB_SSH, ui.TAB_RRC):
        g = mk()
        pin = key_n(200)
        add(g, tab, pin, fav=True)
        for i in range(cap(tab) - 1):
            add(g, tab, key_n(i))
            table(g, tab)[key_n(i)]["seen"] = 1000 + i
        table(g, tab)[pin]["seen"] = 1
        table(g, tab)[key_n(5)]["seen"] = 2
        add(g, tab, NEW_KEY)
        assert key_n(5) not in table(g, tab) and key_n(5) not in keys(g, tab), tab
        assert pin in table(g, tab) and NEW_KEY in table(g, tab), tab
        assert len(table(g, tab)) == cap(tab), tab
        assert len(keys(g, tab)) == cap(tab) + 1, tab


def test_eviction_never_touches_the_sentinel_at_the_cap():
    for tab in TABS:
        g = mk_at_cap(tab)
        add_one_more(g, tab)  # must not raise
        assert keys(g, tab)[0] == ui._NEW, tab
        assert keys(g, tab).count(ui._NEW) == 1, tab
        assert len(table(g, tab)) == cap(tab), tab
        assert NEW_KEY in table(g, tab), tab
        g.draw_node_list()                 # every remaining key still draws


def test_eviction_keeps_the_cursor_on_its_entry():
    for tab in TABS:
        g = mk_at_cap(tab)
        for i in range(cap(tab)):
            table(g, tab)[key_n(i)]["seen"] = 1000 + i
        sel = key_n(cap(tab) - 3)
        select(g, tab, keys(g, tab).index(sel))
        add_one_more(g, tab)
        assert key_n(0) not in table(g, tab), tab
        assert keys(g, tab)[cursor(g, tab)] == sel, tab


def test_evicting_the_row_under_the_cursor_leaves_a_valid_cursor():
    for tab in TABS:
        g = mk_at_cap(tab)
        for i in range(cap(tab)):
            table(g, tab)[key_n(i)]["seen"] = 1000 + i
        select(g, tab, keys(g, tab).index(key_n(0)))    # last row, the oldest
        add_one_more(g, tab)
        assert 0 <= cursor(g, tab) < len(keys(g, tab)), tab
        g.draw_node_list()


def test_a_cap_of_nothing_but_pins_does_not_raise():
    for tab in TABS:
        g = mk_at_cap(tab)
        for e in table(g, tab).values():
            e["fav"] = True
        add_one_more(g, tab)
        assert keys(g, tab)[0] == ui._NEW, tab


# --- cursor home, favourites, names ----------------------------------------

def test_cursor_home_lands_on_first_entry():
    g = mk()
    g.add_peer(F, "f", fav=True)
    g._cursor_home()
    assert g.selected_idx == 1


def test_cursor_home_on_every_tab_with_entries():
    g = mk()
    add(g, ui.TAB_NET, A)
    add(g, ui.TAB_RRC, A)
    g.net_scroll = 3
    g._cursor_home()
    assert (g.net_idx, g.net_scroll, g._rrc_idx) == (1, 0, 1)
    assert (g.selected_idx, g.ssh_idx) == (0, 0), "an empty tab stays on its top row"


def test_selected_key():
    g = mk()
    g.add_peer(A, "a")
    select(g, ui.TAB_MSG, 0)
    assert g._selected_key() == ui._NEW
    select(g, ui.TAB_MSG, 1)
    assert g._selected_key() == A
    select(g, ui.TAB_MSG, 9)
    assert g._selected_key() is None
    select(g, ui.TAB_NET, 0)
    assert g._selected_key() == ui._NEW


def test_nameless_update_keeps_the_name():
    for tab in TABS:
        g = mk()
        add(g, tab, A, "alex")
        add(g, tab, A, None)
        assert table(g, tab)[A]["name"] == "alex", tab
        add(g, tab, A, "")
        assert table(g, tab)[A]["name"] == "alex", tab


def test_update_keeps_the_star():
    for tab in TABS:
        g = mk()
        add(g, tab, A, "a", fav=True)
        add(g, tab, A, "b")
        assert table(g, tab)[A]["fav"] is True, tab


def test_sixth_favourite_says_so():
    g = with_five_pins()
    select_chat(g)
    g.handle_key(b"f")
    assert g.ping_status == "max 5 favs"
    assert g._ping_status_ms and g.dirty
    assert not g.peers[A].get("fav")
    assert g._peer_keys[g.selected_idx] == A


def test_sixth_favourite_is_silent_off_msg_but_changes_nothing():
    g = mk()
    g.node_tab = ui.TAB_NET
    for i in range(ui.MAX_FAVORITES + 1):
        add(g, ui.TAB_NET, key_n(i))
        g.net_idx = g._node_keys.index(key_n(i))
        g.handle_key(b"f")
    assert g.ping_status is None
    assert not g.nomad_nodes[key_n(ui.MAX_FAVORITES)].get("fav")
    assert sum(1 for e in g.nomad_nodes.values() if e.get("fav")) == ui.MAX_FAVORITES


def test_pin_moves_to_the_end_of_the_pins_and_unpin_under_them():
    for tab in TABS:
        g = mk()
        g.node_tab = tab
        for k in (D, C, B, A):
            add(g, tab, k)                 # [+, A, B, C, D]
        select(g, tab, 3)
        g.handle_key(b"f")                 # C
        assert keys(g, tab) == [ui._NEW, C, A, B, D], tab
        assert keys(g, tab)[cursor(g, tab)] == C, tab
        select(g, tab, 4)
        g.handle_key(b"f")                 # D
        assert keys(g, tab) == [ui._NEW, C, D, A, B], tab
        assert keys(g, tab)[cursor(g, tab)] == D, tab
        select(g, tab, 1)
        g.handle_key(b"f")                 # C off
        assert keys(g, tab) == [ui._NEW, D, C, A, B], tab
        assert keys(g, tab)[cursor(g, tab)] == C, tab
        assert not table(g, tab)[C].get("fav"), tab


def _pin_in_order(g, tab, order):
    g.node_tab = tab
    for k in order:
        select(g, tab, keys(g, tab).index(k))
        g.handle_key(b"f")


def test_favourites_loaded_at_boot_keep_pin_order():
    for tab in TABS:
        g = mk()
        for k in (A, B, C, D):
            add(g, tab, k)
        _pin_in_order(g, tab, (B, D, A))
        assert keys(g, tab)[1:4] == [B, D, A], tab

        booted = mk(g.store)
        booted.load_favorites()
        assert keys(booted, tab) == [ui._NEW, B, D, A], (tab, keys(booted, tab))
        assert cursor(booted, tab) == 1 and scroll(booted, tab) == 0, tab


def test_unpinning_keeps_the_pin_order_across_a_boot():
    for tab in TABS:
        g = mk()
        for k in (A, B, C, D):
            add(g, tab, k)
        _pin_in_order(g, tab, (A, B, C))
        _pin_in_order(g, tab, (A,))        # off
        _pin_in_order(g, tab, (D,))        # after the two left
        assert keys(g, tab)[1:4] == [B, C, D], tab

        booted = mk(g.store)
        booted.load_favorites()
        assert keys(booted, tab) == [ui._NEW, B, C, D], (tab, keys(booted, tab))


def test_unpinning_frees_a_slot_on_the_file():
    g = with_five_pins()
    select(g, ui.TAB_MSG, 1)
    g.handle_key(b"f")
    select_chat(g)
    g.handle_key(b"f")
    assert g.peers[A]["fav"] is True and g.ping_status is None


def test_load_favorites_with_nothing_stored():
    g = mk()
    g.load_favorites()
    for tab in TABS:
        assert keys(g, tab) == [ui._NEW] and cursor(g, tab) == 0, tab


# --- announces only count ---------------------------------------------------

def stranger(i):
    return bytes([0xA0, i >> 8, i & 255]) + bytes(13)


def test_300_stranger_announces_change_nothing():
    for tab in TABS:
        g = mk_filled(tab)
        select(g, tab, 2)
        before = snapshot(g, tab)
        g.dirty = False
        for i in range(300):
            assert g.heard(tab, stranger(i), "s") is False, tab
        assert snapshot(g, tab) == before, tab
        assert g.dirty is False, tab
        assert g._new_row_label() == "99+ newly heard" and len(g._heard[tab]) == 100, tab


def test_a_stranger_counts_once_and_only_on_its_own_tab():
    g = mk()
    g.heard(ui.TAB_NET, stranger(1), "s")
    g.heard(ui.TAB_NET, stranger(1), "s")
    assert [len(h) for h in g._heard] == [0, 1, 0, 0]
    assert next(iter(g._heard[ui.TAB_NET])) == stranger(1)[:4]


def test_known_announce_updates_in_place():
    g = mk()
    g.add_peer(A, None)
    g.add_peer(B, "b")
    order = list(g._peer_keys)
    assert g.heard(ui.TAB_MSG, A, "ridge", hops=3) is True
    assert g.peers[A]["name"] == "ridge" and g.peers[A]["hops"] == 3 and g._peer_keys == order
    assert not any(g._heard), "a known row is not a stranger"


def test_known_announce_moves_nothing_on_any_tab():
    for tab in TABS:
        g = mk_filled(tab)
        add(g, tab, C, "c")
        select(g, tab, keys(g, tab).index(B))
        setattr(g, ui._TAB_ATTRS[tab][1], 1)
        order = list(keys(g, tab))
        at, sc = cursor(g, tab), scroll(g, tab)
        table(g, tab)[A]["seen"] = 5
        assert g.heard(tab, A, "z", hops=2) is True, tab
        assert keys(g, tab) == order and (cursor(g, tab), scroll(g, tab)) == (at, sc), tab
        assert table(g, tab)[A]["name"] == "z" and table(g, tab)[A]["hops"] == 2, tab
        assert table(g, tab)[A]["seen"] > 5, tab


def test_only_a_changed_name_repaints():
    for tab in TABS:
        g = mk_filled(tab)
        g.dirty = False
        g.heard(tab, A, "a", hops=7)
        assert g.dirty is False, tab
        g.heard(tab, A, None, hops=8)
        assert g.dirty is False, tab
        g.heard(tab, A, "renamed")
        assert g.dirty is True, tab


def test_nameless_announce_keeps_the_name():
    for tab in TABS:
        g = mk_filled(tab)
        g.heard(tab, A, None)
        g.heard(tab, A, "")
        assert table(g, tab)[A]["name"] == "a", tab


def test_an_announce_that_knows_no_route_keeps_the_last_one():
    g = mk()
    g.add_peer(A, "a", rssi=-80, hops=3, via="ab12")
    g.heard(ui.TAB_MSG, A, "a")
    e = g.peers[A]
    assert (e["rssi"], e["hops"], e["via"]) == (-80, 3, "ab12")


def test_a_shorter_route_drops_the_old_relay():
    g = mk()
    g.add_peer(A, "a", hops=3, via="ab12")
    g.heard(ui.TAB_MSG, A, "a", hops=1)
    assert g.peers[A]["hops"] == 1 and g.peers[A]["via"] is None


def test_heard_survives_an_unknown_tab():
    g = mk()
    add(g, ui.TAB_SSH, A, "a")
    g.dirty = False
    for tab in (4, 99, -1, None):
        assert g.heard(tab, A, "x", hops=1) is False, tab
        assert g.heard(tab, stranger(1), "x") is False, tab
    assert g.shell_nodes[A]["name"] == "a", "an unknown tab fell through to the SSH table"
    assert not any(g._heard) and g.dirty is False


def test_heard_survives_a_nameless_entry():
    g = mk()
    g.add_rrc_hub(A)                       # hubs may have no name at all
    assert g.rrc_hubs[A]["name"] is None
    g.dirty = False
    assert g.heard(ui.TAB_RRC, A) is True and g.dirty is False
    assert g.heard(ui.TAB_RRC, A, "hub") is True and g.dirty is True
    del g.rrc_hubs[A]["name"]
    assert g.heard(ui.TAB_RRC, A) is True
    assert g.heard(ui.TAB_RRC, A, "hub") is True and g.rrc_hubs[A]["name"] == "hub"


def test_heard_with_the_cursor_on_the_top_row():
    for tab in TABS:
        g = mk_filled(tab)
        select(g, tab, 0)
        g.heard(tab, A, "z")
        g.heard(tab, stranger(1), "s")
        assert g._selected_key() == ui._NEW, tab
        g.draw_node_list()


# --- the message path's row -------------------------------------------------

S = b"\xc0\xff\xee\x42" + bytes(12)


def test_first_message_makes_one_row_under_the_pins():
    g = mk()
    g.add_peer(F, "f", fav=True)
    g.message_row(S, None)
    g.add_chat_message(S, False, "hi")
    g.message_row(S, None)
    g.add_chat_message(S, False, "again")
    assert g._peer_keys == [ui._NEW, F, S] and g.unread[S] == 2
    assert g._chat_cols(S)[0] == "? " + S.hex()[:8]


def test_first_message_uses_the_cached_name():
    g = mk()
    g.message_row(S, "ridge")
    assert g.peers[S]["name"] == "ridge"


def test_message_row_keeps_a_known_name():
    g = mk()
    g.add_peer(S, "alex")
    g.message_row(S, None)
    assert g.peers[S]["name"] == "alex"
    g.message_row(S, "other")
    assert g.peers[S]["name"] == "alex", "only an announce renames a row"


def test_message_row_keeps_the_route_and_star_of_a_known_row():
    g = mk()
    g.add_peer(S, "alex", hops=4, via="ab12", fav=True)
    g.message_row(S, None)
    assert g.peers[S]["hops"] == 4 and g.peers[S]["via"] == "ab12" and g.peers[S]["fav"] is True
    g.message_row(S, None, hops=1)
    assert g.peers[S]["hops"] == 4, "only an announce changes a known row's route"


def test_message_row_gives_a_new_row_its_route():
    g = mk()
    g.message_row(S, None, hops=3, via="ab12")
    assert g.peers[S]["hops"] == 3 and g.peers[S]["via"] == "ab12"


def test_a_message_at_the_cap_evicts_the_oldest_read_chat():
    g = mk_at_cap(ui.TAB_MSG)
    for i in range(ui.MAX_PEERS):
        g.peers[key_n(i)]["seen"] = 1000 + i
    g.message_row(S, None)
    g.add_chat_message(S, False, "hi")
    assert S in g.peers and key_n(0) not in g.peers and len(g.peers) == ui.MAX_PEERS
    assert g.unread[S] == 1


# --- a link change ----------------------------------------------------------

def mk_with_unread_chat(k):
    g = mk()
    g.add_peer(k, "a", rssi=-80, hops=3, via="ab12")
    g.add_chat_message(k, False, "hi", image=b"\xff\xd8", audio=b"\x00", audio_mode=0)
    return g


def test_link_change_keeps_rows_history_and_unread():
    g = mk_with_unread_chat(A)
    g.forget_routes()
    assert A in g.peers and g.unread[A] == 1 and g.chat_history[A] and g.peers[A]["hops"] is None
    assert g.peers[A]["rssi"] is None and g.peers[A]["via"] is None
    assert g.peers[A]["name"] == "a"


def test_link_change_keeps_the_media():
    g = mk_with_unread_chat(A)
    img, aud, order = dict(g._image_cache), dict(g._audio_cache), list(g._image_cache_order)
    assert img and aud and order
    g.forget_routes()
    assert g._image_cache == img and g._audio_cache == aud and g._image_cache_order == order


def test_link_change_with_chat_open_stays_in_the_chat():
    g = mk_with_unread_chat(A)
    g.selected_peer = A
    g.state = ui.STATE_CHAT
    g.chat_scroll = 2
    g.forget_routes()
    assert g.state == ui.STATE_CHAT and g.selected_peer == A and g.chat_scroll == 2


def test_link_change_clears_routes_and_counts_on_every_tab():
    g = mk()
    for tab in TABS:
        add(g, tab, A, "a", hops=2)
        g.heard(tab, stranger(1), "s")
    g.dirty = False
    g._cache = ["x"] * ui.CACHE_ROWS
    g._route_cache = "aacf7098 2 hops"
    g.forget_routes()
    for tab in TABS:
        assert table(g, tab)[A]["hops"] is None and table(g, tab)[A]["name"] == "a", tab
        assert not g._heard[tab], tab
    assert g.dirty is True and g._cache == [""] * ui.CACHE_ROWS and g._route_cache == ""


def test_link_change_leaves_order_cursor_window_and_pins():
    for tab in TABS:
        g = mk()
        for k in (A, B, C):
            add(g, tab, k)
        _pin_in_order(g, tab, (B, A))
        select(g, tab, 3)
        setattr(g, ui._TAB_ATTRS[tab][1], 1)
        order = list(keys(g, tab))
        g.forget_routes()
        assert keys(g, tab) == order and keys(g, tab)[0] == ui._NEW, tab
        assert (cursor(g, tab), scroll(g, tab)) == (3, 1), tab
        assert table(g, tab)[B]["fav"] is True, tab
        g.draw_node_list()


def test_link_change_adds_no_route_fields_a_table_never_had():
    g = mk()
    add(g, ui.TAB_NET, A, "a", hops=2)
    g.forget_routes()
    assert "rssi" not in g.nomad_nodes[A] and "via" not in g.nomad_nodes[A]


def test_a_route_comes_back_with_the_next_announce():
    g = mk_with_unread_chat(A)
    g.forget_routes()
    assert g.heard(ui.TAB_MSG, A, "a", rssi=-70, hops=1) is True
    assert g.peers[A]["hops"] == 1 and g.peers[A]["rssi"] == -70


def test_the_clear_methods_are_gone():
    for name in ("clear_peers", "clear_nomad_nodes", "clear_shell_nodes", "clear_rrc_hubs"):
        assert not hasattr(ui.UI, name), name


# --- the tab clients, on the real table -------------------------------------

import nomad_browser
import rnsh_client
import rrc_client

CLIENTS = (
    (ui.TAB_NET, nomad_browser, "_nomad_hash", b"node", "node"),
    (ui.TAB_SSH, rnsh_client, "_rnsh_hash", None, None),
    (ui.TAB_RRC, rrc_client, "_hub_hash", rrc_client.cbor.dumps({"proto": "rrc", "v": 1, "hub": "hub"}), "hub"),
)


def _wired(client, hash_fn):
    g = mk()
    client._gui = g
    setattr(client, hash_fn, lambda h: h)
    client._node_hops = lambda h: 4
    return g


def test_the_clients_name_the_tab_ui_does():
    assert nomad_browser.TAB == ui.TAB_NET
    assert rnsh_client.TAB == ui.TAB_SSH
    assert rrc_client.TAB == ui.TAB_RRC


def test_client_announce_from_a_stranger_only_counts():
    for tab, client, hash_fn, data, _name in CLIENTS:
        g = _wired(client, hash_fn)
        g.dirty = False
        client._on_announce(A, data, None)
        assert keys(g, tab) == [ui._NEW] and table(g, tab) == {}, tab
        assert [len(h) for h in g._heard] == [1 if t == tab else 0 for t in TABS], tab
        assert g.dirty is False, tab


def test_client_announce_of_something_else_does_not_count():
    for tab, client, hash_fn, data, _name in CLIENTS:
        g = _wired(client, hash_fn)
        setattr(client, hash_fn, lambda h: b"other")
        client._on_announce(A, data, None)
        assert not any(g._heard), tab


def test_client_announce_updates_its_row_in_place():
    for tab, client, hash_fn, data, name in CLIENTS:
        g = _wired(client, hash_fn)
        add(g, tab, A, "old")
        add(g, tab, B, "b")
        order = list(keys(g, tab))
        client._on_announce(A, data, None)
        assert keys(g, tab) == order and table(g, tab)[A]["hops"] == 4, tab
        assert table(g, tab)[A]["name"] == (name or "old"), tab
        assert not any(g._heard), tab


def test_client_announce_wakes_only_under_wake_all():
    for tab, client, hash_fn, data, _name in CLIENTS:
        for mode, wakes in ((0, 0), (1, 1), (2, 0)):
            g = _wired(client, hash_fn)
            got = []
            g.wake_screen = lambda *a, **k: got.append(1)
            g._wake_mode = mode
            client._on_announce(A, data, None)
            assert len(got) == wakes, (tab, mode)


def test_client_clears_leave_the_rows_to_the_link_change():
    for tab, client, hash_fn, _data, _name in CLIENTS:
        g = _wired(client, hash_fn)
        add(g, tab, A, "a")
        (client.clear_hubs if client is rrc_client else client.clear_nodes)()
        assert A in table(g, tab), tab


# --- the receive path, with the cursor on the top row -----------------------

def test_announces_and_messages_with_the_cursor_on_the_top_row():
    for tab in TABS:
        g = mk()
        select(g, tab, 0)
        for i in range(cap(tab) + 3):
            add(g, tab, key_n(i))
            add(g, tab, key_n(i), None)    # the same again, nameless
            if tab == ui.TAB_MSG:
                g.add_chat_message(key_n(i), False, "hi")
            g.draw_node_list()
            assert g._selected_key() == ui._NEW, (tab, i)
        assert keys(g, tab)[0] == ui._NEW, tab


def test_lookup_pick_selects_the_row_it_added():
    for tab in TABS:
        g = mk_filled(tab)
        g.on_contact_snapshot = lambda t: [(C, "c", 1)]
        g.handle_key(b"m")
        g.handle_key(b"\r")
        assert g._find is False, tab
        assert keys(g, tab) == [ui._NEW, C, B, A], tab
        assert keys(g, tab)[cursor(g, tab)] == C, tab


# --- age is the last announce -----------------------------------------------

def _ages(g, tab):
    """The small text the v1 draws in the list body: the top row's label aside,
    those are the ages (the footer's hash is below the body)."""
    drawn = []
    g.node_tab = tab
    g.SW = 6
    g._stext = lambda s, x, y, fg, bg=None: drawn.append((s, y))
    g._cache = [""] * ui.CACHE_ROWS
    g.draw_node_list()
    return [t for t, y in drawn if t != g._new_row_label() and y < ui.BODY_Y + ui.BODY_ROWS * ui.CHAR_H]


def _booted_with_a_pin(tab):
    g = mk()
    add(g, tab, A, "a")
    _pin_in_order(g, tab, (A,))
    booted = mk(g.store)
    booted.load_favorites()
    return booted


def test_a_favourite_shows_no_age_until_an_announce():
    real = _time.time
    for tab in TABS:
        g = _booted_with_a_pin(tab)
        assert not table(g, tab)[A]["seen"], tab
        if tab != ui.TAB_MSG:                  # a chat's age is its last message
            assert _ages(g, tab) == [], (tab, _ages(g, tab))
            start = real()
            _time.time = lambda: start + 125   # a dead node must not count up
            try:
                assert _ages(g, tab) == [], (tab, _ages(g, tab))
            finally:
                _time.time = real

        g.dirty = False
        assert g.heard(tab, A) is True, tab
        assert table(g, tab)[A]["seen"] > 0, tab
        assert g.dirty is True, "the age has to appear: this announce is what it waited for"
        if tab != ui.TAB_MSG:
            assert _ages(g, tab) == ["now"], (tab, _ages(g, tab))
        g.dirty = False
        g.heard(tab, A)
        assert g.dirty is False, "only the first announce repaints"


def test_a_lookup_pick_shows_when_it_was_really_heard():
    for tab in TABS:
        g = mk()
        g.node_tab = tab
        then = _time.time() - 300
        g.on_contact_snapshot = lambda t: [(A, "a", then)]
        g.handle_key(b"m")
        g.handle_key(b"\r")
        assert table(g, tab)[A]["seen"] == then, tab
        if tab != ui.TAB_MSG:
            g.state = ui.STATE_NODES
            assert _ages(g, tab) == ["5min"], (tab, _ages(g, tab))


def test_a_row_added_by_hash_shows_no_age():
    for tab in TABS:
        g = mk()
        g.node_tab = tab
        g.on_contact_snapshot = lambda t: []
        g.handle_key(b"m")
        for ch in A.hex():
            g.handle_key(ch.encode())
        g.handle_key(b"\r")
        assert table(g, tab)[A]["seen"] == 0, tab
        if tab != ui.TAB_MSG:
            assert _ages(g, tab) == [], (tab, _ages(g, tab))


def test_add_rrc_hub_takes_a_seen_like_the_other_two():
    g = mk()
    g.add_rrc_hub(A, "a", seen=50)
    assert g.rrc_hubs[A]["seen"] == 50
    g.add_rrc_hub(A, "a", seen=20)
    assert g.rrc_hubs[A]["seen"] == 50, "a stale time never replaces a fresher one"
    g.add_rrc_hub(A, "a", seen=60)
    assert g.rrc_hubs[A]["seen"] == 60
    before = _time.time()
    g.add_rrc_hub(B, "b")
    assert g.rrc_hubs[B]["seen"] >= before


def test_a_row_never_heard_goes_first_at_the_cap_but_a_pin_stays():
    for tab in TABS:
        g = mk_at_cap(tab)
        for i in range(cap(tab)):
            table(g, tab)[key_n(i)]["seen"] = 1000 + i
        table(g, tab)[key_n(4)]["seen"] = 0
        table(g, tab)[key_n(5)]["seen"] = 0
        table(g, tab)[key_n(5)]["fav"] = True
        add_one_more(g, tab)
        assert key_n(4) not in table(g, tab), tab
        assert key_n(5) in table(g, tab) and key_n(0) in table(g, tab), tab


# --- favourites files the old code wrote -------------------------------------

def _file(tab, slots):
    """A favourites file with these {slot: key} filled. The old code took the
    last free slot, so its files have holes at the front."""
    t = ui._FAV_TYPES[tab]
    d = {}
    for i, k in slots.items():
        d[t + "Key_" + str(i)] = k.hex()
        d[t + "_" + str(i)] = {"name": "n"}
    return t, json.dumps(d)


def _counting(store):
    g = mk(store)
    g.saves = 0
    save = g._save_favorites

    def counted(d, t=None):
        g.saves += 1
        return save(d, t)
    g._save_favorites = counted
    return g


def test_a_new_pin_stays_last_across_a_boot_on_a_legacy_file():
    for tab in TABS:
        t, text = _file(tab, {3: A, 4: B})
        g = mk({t: text})
        g.load_favorites()
        assert keys(g, tab) == [ui._NEW, A, B], tab
        add(g, tab, C)
        _pin_in_order(g, tab, (C,))
        assert keys(g, tab)[1:4] == [A, B, C], tab
        for _ in range(2):
            booted = mk(g.store)
            booted.load_favorites()
            assert keys(booted, tab) == [ui._NEW, A, B, C], (tab, keys(booted, tab))
        saved = json.loads(g.store[t])
        assert [saved.get(t + "Key_" + str(i)) for i in range(ui.MAX_FAVORITES)] \
            == [A.hex(), B.hex(), C.hex(), None, None], (tab, saved)
        assert saved[t + "_0"]["name"] == "n" and saved.get(t + "_3") is None, tab


def test_a_hole_in_the_middle_is_closed_too():
    for tab in TABS:
        t, text = _file(tab, {0: A, 2: B})
        g = _counting({t: text})
        g.load_favorites()
        assert keys(g, tab) == [ui._NEW, A, B], tab
        assert g.saves == 1, tab
        assert json.loads(g.store[t])[t + "Key_1"] == B.hex(), tab


def test_a_file_with_no_hole_is_not_rewritten():
    for tab in TABS:
        t, text = _file(tab, {0: A, 1: B})
        g = _counting({t: text})
        g.load_favorites()
        assert keys(g, tab) == [ui._NEW, A, B], tab
        assert g.saves == 0 and g.store[t] == text, tab


def test_a_missing_or_corrupt_file_is_not_rewritten():
    for tab in TABS:
        t = ui._FAV_TYPES[tab]
        g = _counting({})                      # no file
        g.load_favorites()
        assert g.saves == 0 and keys(g, tab) == [ui._NEW], tab

        bad = json.dumps({t + "Key_1": "not hex", t + "_1": {"name": "n"},
                          t + "Key_3": A.hex(), t + "_3": {"name": "n"}})
        g = _counting({t: bad})                # the load stops at the bad slot, as ever
        g.load_favorites()
        assert g.saves == 0 and g.store[t] == bad and keys(g, tab) == [ui._NEW], tab

        bad = json.dumps({t + "Key_1": A.hex(), t + "_1": {"name": "n"},
                          t + "Key_2": "not hex", t + "_2": {"name": "n"}})
        g = _counting({t: bad})
        g.load_favorites()
        assert g.saves == 0 and g.store[t] == bad and keys(g, tab) == [ui._NEW, A], tab

        g = _counting({t: "[]"})               # valid JSON, wrong shape
        g.load_favorites()
        assert g.saves == 0 and keys(g, tab) == [ui._NEW], tab


def test_a_slot_with_no_key_is_discarded_and_the_gap_closed():
    for tab in TABS:
        t, text = _file(tab, {1: A})
        d = json.loads(text)
        d[t + "_0"] = {"name": "keyless"}
        g = mk({t: json.dumps(d)})
        g.load_favorites()
        assert keys(g, tab) == [ui._NEW, A], tab
        saved = json.loads(g.store[t])
        assert saved[t + "Key_0"] == A.hex() and saved.get(t + "_1") is None, (tab, saved)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok  ", fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", fn.__name__, type(e).__name__, e)
    if failed:
        print("\n%d of %d tab-model tests FAILED." % (failed, len(fns)))
        sys.exit(1)
    print("\nAll %d tab-model tests passed." % len(fns))


if __name__ == "__main__":
    _run()

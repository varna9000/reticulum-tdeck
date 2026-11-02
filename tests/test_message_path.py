# Host-side test for tdeck_node's receive path. tdeck_node starts hardware at
# import, so this lifts on_announce and _on_message_inner out of its source and
# runs them, as written, against the real ui.UI and a stand-in urns: a mistake
# here loses messages on a device that is hard to debug.
#
# Run:  python3 tests/test_message_path.py

import ast
import io
import os
import sys
import time as _time
import types
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# lib/ holds spleen_6x12, the small font the v1 draws secondary text in
sys.path.insert(1, os.path.join(ROOT, "lib"))

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
    def text(self, *a, **k):
        pass

    def fill_rect(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass


# --- a stand-in urns --------------------------------------------------------

class CountingTable(dict):
    """The path table; on_announce must leave it alone for a stranger."""

    def __init__(self):
        super().__init__()
        self.lookups = []

    def get(self, k, default=None):
        self.lookups.append(k)
        return super().get(k, default)


class Identity:
    known_destinations = {}


class Transport:
    path_table = CountingTable()
    requested = []

    @staticmethod
    def has_path(dest):
        return dest in Transport.path_table

    @staticmethod
    def request_path(dest):
        Transport.requested.append(dest)


_urns = types.ModuleType("urns")
_urns_identity = types.ModuleType("urns.identity")
_urns_identity.Identity = Identity
_urns_transport = types.ModuleType("urns.transport")
_urns_transport.Transport = Transport
_urns_const = types.ModuleType("urns.const")
_urns_const.IDX_PT_HOPS = 0
_urns_const.IDX_PT_NEXT_HOP = 1
_urns.const = _urns_const
sys.modules.update({"urns": _urns, "urns.identity": _urns_identity,
                    "urns.transport": _urns_transport, "urns.const": _urns_const})


class LXMRouter:
    @staticmethod
    def display_name_from_app_data(app_data):
        return app_data.decode()


def _lifted():
    """tdeck_node's receive functions and Lookup's add, compiled from its
    source as they stand, with nothing else from that file."""
    with open(os.path.join(ROOT, "tdeck_node.py")) as f:
        tree = ast.parse(f.read())
    want = ("on_announce", "_on_message_inner", "add_contact", "_route_to")
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
    assert sorted(n.name for n in nodes) == sorted(want)
    return compile(ast.Module(body=nodes, type_ignores=[]), "tdeck_node.py", "exec")


_CODE = _lifted()


class Node:
    """The module globals those two functions see."""

    def __init__(self, debug=0):
        self.gui = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t")
        self.gui._screen_on = True
        self.wakes = []
        self.gui.wake_screen = lambda why=None: self.wakes.append(why)
        self.rings = []
        self.iface = types.SimpleNamespace(rssi=None, snr=None)
        self.not_lxmf = set()
        Identity.known_destinations = {}
        Transport.path_table = CountingTable()
        Transport.requested = []
        self.g = {
            "gui": self.gui,
            "time": _time,
            "DEBUG": debug,
            "TAB_MSG": ui.TAB_MSG,
            "LXMRouter": LXMRouter,
            "rns": types.SimpleNamespace(interfaces=[self.iface]),
            "_lxmf_to_peer": {},
            "_compute_lxmf_hash": lambda d: b"not-this" if d in self.not_lxmf else d,
            "_parse_attachments": lambda fields: (None, None, None, []),
            "_with_markers": lambda content, markers: content,
            "sound": types.SimpleNamespace(play_rx=lambda: self.rings.append(1)),
            "gc": types.SimpleNamespace(collect=lambda: None, mem_free=lambda: 1),
        }
        exec(_CODE, self.g)

    @property
    def map(self):
        return self.g["_lxmf_to_peer"]

    def announce(self, dest, name=None):
        self.g["on_announce"](dest, name)

    def message(self, source, text="hi"):
        self.g["_on_message_inner"](types.SimpleNamespace(
            content_as_string=lambda: text, source_hash=source, fields={}))

    def path(self, dest, hops, next_hop=b"\xab\xcd" + bytes(14)):
        Transport.path_table[dest] = [hops, next_hop]

    def known(self, dest, pub=None, app_data=None):
        Identity.known_destinations[dest] = [1, None, pub, app_data]


A, B, C = (bytes([i]) * 16 for i in (0xA0, 0xB0, 0xC0))
S = b"\xc0\xff\xee\x42" + bytes(12)
PUB = b"\x55" * 64


# --- announces --------------------------------------------------------------

def test_a_stranger_only_counts():
    n = Node()
    n.gui.add_peer(A, "a")
    n.gui.dirty = False
    n.path(S, 3)
    n.announce(S, "someone")
    assert n.gui._peer_keys == [ui._NEW, A] and S not in n.gui.peers
    assert len(n.gui._heard[ui.TAB_MSG]) == 1
    assert n.gui.dirty is False


def test_a_stranger_costs_no_path_lookup_and_no_map_entry():
    n = Node()
    n.announce(S, "someone")
    assert Transport.path_table.lookups == []
    assert n.map == {}


def test_a_known_peer_updates_in_place_with_its_route():
    n = Node()
    n.gui.add_peer(A, "a")
    n.gui.add_peer(B, "b")
    order = list(n.gui._peer_keys)
    n.iface.rssi, n.iface.snr = -90, 5
    n.path(A, 3)
    n.gui.dirty = False
    n.announce(A, "ridge")
    e = n.gui.peers[A]
    assert (e["name"], e["hops"], e["via"], e["rssi"]) == ("ridge", 3, "abcd", -90)
    assert n.gui._peer_keys == order
    assert n.map == {A: A}
    assert not any(n.gui._heard)


def test_a_direct_route_shows_no_relay():
    n = Node()
    n.gui.add_peer(A, "a", hops=3, via="abcd")
    n.path(A, 1)
    n.announce(A)
    assert n.gui.peers[A]["hops"] == 1 and n.gui.peers[A]["via"] is None


def test_a_non_lxmf_announce_is_neither_a_row_nor_a_count():
    n = Node()
    n.not_lxmf.add(S)
    n.announce(S, "a page node")
    assert not any(n.gui._heard) and S not in n.gui.peers


def test_a_second_destination_updates_the_row_and_is_no_stranger():
    n = Node()
    n.gui.add_peer(A, "a", hops=2, via="abcd")
    n.gui.add_peer(B, "b")
    order = list(n.gui._peer_keys)
    n.map[S] = A
    n.gui.peers[A]["seen"] = 5
    n.gui.dirty = False
    n.announce(S, "renamed")
    e = n.gui.peers[A]
    assert e["name"] == "renamed" and e["seen"] > 5
    assert (e["hops"], e["via"]) == (2, "abcd"), "the other destination's route is not this row's"
    assert S not in n.gui.peers and n.gui._peer_keys == order
    assert not any(n.gui._heard), "a dedup hit counted as a stranger"
    assert n.gui.dirty is True and n.map == {S: A}


def test_a_second_destination_with_the_same_name_does_not_repaint():
    n = Node()
    n.gui.add_peer(A, "a")
    n.map[S] = A
    n.gui.dirty = False
    n.announce(S, "a")
    n.announce(S, None)
    assert n.gui.dirty is False and n.gui.peers[A]["name"] == "a"


def test_a_map_entry_whose_row_went_is_dropped_by_the_next_announce():
    n = Node()
    n.map[S] = A                       # A was evicted: no row any more
    n.announce(S, "someone")
    assert n.map == {}, "a message would land on the evicted key"
    assert len(n.gui._heard[ui.TAB_MSG]) == 1
    n.message(S)
    assert S in n.gui.peers and A not in n.gui.peers


# --- the header's snr -------------------------------------------------------

def _shown(n):
    return ui.band_slots(n.gui._backbone, n.gui._tcp_enabled, True, n.gui.rssi, n.gui.snr)[1]


def test_snr_follows_a_stranger_and_repaints_only_when_it_changes():
    n = Node()
    n.iface.rssi, n.iface.snr = -90, 5
    n.gui.dirty = False
    n.announce(S)
    assert (n.gui.rssi, n.gui.snr) == (-90, 5) and n.gui.dirty is True
    assert _shown(n) == "snr 5"
    n.gui.dirty = False
    n.iface.rssi = -95                 # the header shows only the snr
    n.announce(S)
    assert n.gui.rssi == -95 and n.gui.dirty is False
    n.iface.snr = 7
    n.announce(S)
    assert n.gui.snr == 7 and n.gui.dirty is True
    assert _shown(n) == "snr 7"


def test_the_first_reading_repaints_even_at_snr_zero():
    n = Node()
    n.gui.snr = 0
    n.iface.rssi, n.iface.snr = -90, 0
    n.gui.dirty = False
    n.announce(S)
    assert n.gui.dirty is True and _shown(n) == "snr 0"


def test_snr_follows_a_dedup_hit_and_a_non_lxmf_announce():
    n = Node()
    n.gui.add_peer(A, "a")
    n.map[S] = A
    n.iface.rssi, n.iface.snr = -90, 5
    n.announce(S)
    assert n.gui.snr == 5
    n.not_lxmf.add(C)
    n.iface.snr = 6
    n.gui.dirty = False
    n.announce(C)
    assert n.gui.snr == 6 and n.gui.dirty is True


def test_no_radio_reading_leaves_the_header_alone():
    n = Node()                         # a TCP interface: rssi None
    n.gui.dirty = False
    n.announce(S)
    assert n.gui.rssi is None and n.gui.snr is None and n.gui.dirty is False


# --- wake mode --------------------------------------------------------------

def test_wake_mode_all_still_wakes_on_any_lxmf_announce():
    for mode, wakes in ((0, []), (1, ["announce", "announce"]), (2, [])):
        n = Node()
        n.gui._wake_mode = mode
        n.gui.add_peer(A, "a")
        n.announce(S)
        n.announce(A)
        assert n.wakes == wakes, mode


def test_wake_mode_all_does_not_wake_on_a_dedup_hit_or_non_lxmf():
    n = Node()
    n.gui._wake_mode = 1
    n.gui.add_peer(A, "a")
    n.map[S] = A
    n.announce(S)
    n.not_lxmf.add(C)
    n.announce(C)
    assert n.wakes == []


# --- messages ---------------------------------------------------------------

def test_a_stranger_who_announced_lands_under_the_pins_with_the_cached_name():
    n = Node()
    n.gui.add_peer(A, "pin", fav=True)
    n.gui.add_peer(B, "b")
    n.known(S, PUB, b"ridge")
    n.announce(S, "ridge")
    n.message(S)
    assert n.gui._peer_keys == [ui._NEW, A, S, B]
    assert n.gui.peers[S]["name"] == "ridge"
    assert n.gui.unread[S] == 1 and n.gui.chat_history[S][-1][1] == "hi"
    assert n.rings == [1] and n.wakes == ["msg"]


def test_a_stranger_never_heard_shows_a_short_hash():
    n = Node()
    n.message(S)
    assert n.gui._peer_keys == [ui._NEW, S]
    assert n.gui._chat_cols(S)[0] == "? " + S.hex()[:8]
    assert n.gui.unread[S] == 1


def test_a_cached_identity_with_no_name_shows_a_short_hash():
    n = Node()
    n.known(S, PUB, None)
    n.message(S)
    assert n.gui._chat_cols(S)[0] == "? " + S.hex()[:8]


def test_two_messages_make_one_row():
    n = Node()
    n.message(S)
    n.message(S, "again")
    assert n.gui._peer_keys == [ui._NEW, S] and n.gui.unread[S] == 2


def test_a_message_keeps_a_known_name():
    n = Node()
    n.gui.add_peer(S, "alex")
    n.known(S, PUB, b"announced")
    n.message(S)
    assert n.gui.peers[S]["name"] == "alex"


def test_the_identity_fallback_finds_the_row_past_the_top_row():
    n = Node()
    n.gui.add_peer(A, "a")
    n.known(A, PUB)
    n.known(S, PUB)
    n.message(S)
    assert n.gui._peer_keys == [ui._NEW, A], "the same identity grew a second row"
    assert n.gui.chat_history[A][-1][1] == "hi" and n.map == {S: A}


def test_the_identity_fallback_with_only_the_top_row():
    n = Node()
    n.known(S, PUB)
    n.message(S)
    assert n.gui._peer_keys == [ui._NEW, S] and n.map == {}


def test_a_message_after_a_link_change_finds_its_row_again():
    n = Node()
    n.gui.add_peer(A, "a")
    n.known(A, PUB)
    n.known(S, PUB)
    n.message(S)
    n.gui.forget_routes()
    n.map.clear()
    n.message(S, "again")
    assert n.gui._peer_keys == [ui._NEW, A]
    assert [m[1] for m in n.gui.chat_history[A]] == ["hi", "again"]
    assert n.gui.unread[A] == 2


def test_an_open_chat_takes_the_message_without_unread():
    n = Node()
    n.gui.add_peer(A, "a")
    n.gui.selected_peer = A
    n.gui.state = ui.STATE_CHAT
    n.message(A)
    assert A not in n.gui.unread and n.gui.chat_history[A][-1][1] == "hi"


def test_a_stranger_first_message_takes_the_known_route():
    n = Node()
    n.path(S, 3)
    n.message(S)
    e = n.gui.peers[S]
    assert (e["hops"], e["via"]) == (3, "abcd")


def test_a_stranger_first_message_on_a_direct_path_shows_no_relay():
    n = Node()
    n.path(S, 1)
    n.message(S)
    e = n.gui.peers[S]
    assert (e["hops"], e["via"]) == (1, None)


def test_a_first_message_with_no_path_has_no_route():
    n = Node()
    n.message(S)
    e = n.gui.peers[S]
    assert (e["hops"], e["via"]) == (None, None)


def test_a_broken_path_entry_does_not_lose_the_message():
    n = Node()
    Transport.path_table[S] = [3]      # no next hop
    n.message(S)
    assert n.gui.chat_history[S][-1][1] == "hi" and n.gui.unread[S] == 1
    assert n.gui.peers[S]["via"] is None


def test_a_message_on_a_known_row_leaves_its_route():
    n = Node()
    n.gui.add_peer(S, "s", hops=5, via="ffff")
    n.path(S, 2)
    n.message(S)
    e = n.gui.peers[S]
    assert (e["hops"], e["via"]) == (5, "ffff")


# --- a Lookup add -----------------------------------------------------------

def _lookup_add(n, tab, q=None):
    n.gui.node_tab = tab
    n.gui.on_add_contact = n.g["add_contact"]
    n.gui.on_contact_snapshot = lambda t: [] if q else [(S, "s", 1)]
    n.gui.handle_key(b"m")
    for ch in q or "":
        n.gui.handle_key(ch.encode())
    n.gui.handle_key(b"\r")
    return n.gui._tab_list(tab)[1][S]


LOOKUP_TABS = (ui.TAB_MSG, ui.TAB_NET, ui.TAB_SSH, ui.TAB_RRC)


def test_a_lookup_add_with_a_known_path_gets_its_route():
    for tab in LOOKUP_TABS:
        n = Node()
        n.path(S, 3)
        e = _lookup_add(n, tab)
        assert (e["hops"], e["via"]) == (3, "abcd"), tab
        assert Transport.requested == [], tab
        assert not any(n.gui._heard), "a row just added is no stranger"


def test_a_lookup_add_by_hash_with_a_known_path_gets_its_route():
    for tab in LOOKUP_TABS:
        n = Node()
        n.path(S, 2)
        e = _lookup_add(n, tab, S.hex())
        assert e["hops"] == 2, tab


def test_a_route_from_the_path_table_is_not_an_announce():
    """The age stays the snapshot's: add_contact fills in the route, and a row
    added by hash still has no age to show."""
    for tab in LOOKUP_TABS:
        n = Node()
        n.path(S, 3)
        assert _lookup_add(n, tab)["seen"] == 1, tab
        n = Node()
        n.path(S, 2)
        assert _lookup_add(n, tab, S.hex())["seen"] == 0, tab


def test_a_lookup_add_with_no_path_asks_for_one():
    for tab in LOOKUP_TABS:
        n = Node()
        e = _lookup_add(n, tab)
        assert e.get("hops") is None, tab
        assert Transport.requested == [S], tab
        assert not any(n.gui._heard), tab


def test_the_debug_prints_still_run():
    n = Node(debug=2)
    n.not_lxmf.add(C)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        n.gui.add_peer(A, "a")
        n.map[B] = A
        n.announce(C)
        n.announce(B, "dup")
        n.announce(A, "a")
        n.announce(S, "s")
        n.message(S)
    text = out.getvalue()
    for want in ("[Peer] Skip non-LXMF", "[Peer] (dedup)", "[Peer] a", "[Peer] s",
                 "[RX] from", "[RX] done"):
        assert want in text, (want, text)


# --- the link-change sites --------------------------------------------------

def test_every_link_change_forgets_routes_and_keeps_the_rest():
    src = open(os.path.join(ROOT, "tdeck_node.py")).read()
    assert "clear_peers" not in src
    chunks = src.split("gui.forget_routes()")
    assert len(chunks) == 4, len(chunks)
    for chunk in chunks[1:]:
        lines = [ln.strip() for ln in chunk.strip().splitlines()[:4]]
        assert lines == ["_lxmf_to_peer.clear()", "nomad_browser.clear_nodes()",
                         "rnsh_client.clear_nodes()", "rrc_client.clear_hubs()"], lines


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
        print("\n%d of %d message-path tests FAILED." % (failed, len(fns)))
        sys.exit(1)
    print("\nAll %d message-path tests passed." % len(fns))


if __name__ == "__main__":
    _run()

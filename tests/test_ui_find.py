# Host-side test for the MSG tab's Find contact typeahead: (m) opens a search
# over every LXMF address the node has heard (urns' known_destinations, handed
# over by tdeck_node as a snapshot), filtered as you type by display-name
# substring or hash prefix. Enter adds the pick to the peer list and opens the
# chat; a full 32-hex hash nobody has announced is added anyway, as "?", and
# the node is asked to go find a path to it.
#
# Run:  python3 tests/test_ui_find.py

import os
import sys
import types
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, (bytes, bytearray)):
            s = s.decode()
        s.encode("ascii")
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.texts.append((s, x, y))

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)

    def fill(self, c):
        pass

    def at_y(self, y):
        return "".join(s for s, _x, _y in self.texts if _y == y)

    def drawn(self):
        return " | ".join(s for s, _x, _y in self.texts)


RATS = bytes.fromhex("b90dae78f09912b3f1af49ea1b1227d1")
DEEJ = bytes.fromhex("4c21f0e9a7d35b6e0c9f12aa88e3b701")
RPI = bytes.fromhex("b90d1f7c22aa90e4d3b5c6e8f01a2b3c")
ALICE = bytes.fromhex("0aa3c8e1f5b7d9e2c4a6b8d0f1e3c5a7")
UNKNOWN = "3fa1c9e07d2b48aa91e5c07f6b2d1e44"


def _snap():
    now = _time.time()
    # newest first, the order tdeck_node hands it over in
    return [(RATS, "Ratspeak", now - 720),
            (ALICE, "nomad-alice", now - 5400),
            (DEEJ, "Dr Deej home", now - 3 * 3600),
            (RPI, "deejay-rpi", now - 2 * 86400)]


def _mkui(snap=_snap):
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t")
    g._screen_on = True
    g.node_tab = ui.TAB_MSG
    g.on_contact_snapshot = snap
    g.added = []
    g.on_add_contact = lambda h: g.added.append(h)
    return g


def _type(g, s):
    for c in s:
        g.handle_key(c.encode())


def _names(res):
    return [e[1] for e in res]


# --- match_contacts --------------------------------------------------------

def test_empty_query_lists_everything_in_snapshot_order():
    assert _names(ui.match_contacts(_snap(), "")) == [
        "Ratspeak", "nomad-alice", "Dr Deej home", "deejay-rpi"]


def test_name_substring_ignores_case():
    assert _names(ui.match_contacts(_snap(), "DEEJ")) == ["Dr Deej home", "deejay-rpi"]
    assert _names(ui.match_contacts(_snap(), "speak")) == ["Ratspeak"]


def test_hex_query_matches_hash_prefix():
    assert _names(ui.match_contacts(_snap(), "b90d")) == ["Ratspeak", "deejay-rpi"]
    assert _names(ui.match_contacts(_snap(), "B90DAE")) == ["Ratspeak"]


def test_hex_prefix_is_a_prefix_not_a_substring():
    # "ae78" sits inside Ratspeak's hash, but not at the start
    assert ui.match_contacts(_snap(), "ae78") == []


def test_a_hex_word_matches_names_and_hashes():
    # "dee" is valid hex AND part of two names: both kinds of hit count
    snap = _snap() + [(bytes.fromhex("dee0" + "00" * 14), "zed", 0)]
    assert _names(ui.match_contacts(snap, "dee")) == ["Dr Deej home", "deejay-rpi", "zed"]


def test_no_match_is_empty():
    assert ui.match_contacts(_snap(), "zzz") == []


# --- opening and typing ----------------------------------------------------

def test_m_opens_find_on_the_msg_tab():
    g = _mkui()
    g.handle_key(b"m")
    assert g._find
    assert _names(g._find_res) == ["Ratspeak", "nomad-alice", "Dr Deej home", "deejay-rpi"]


def test_find_is_msg_tab_only():
    g = _mkui()
    g.node_tab = ui.TAB_NET
    g.handle_key(b"m")
    assert not g._find


def test_find_accepts_text_so_e_and_x_are_letters():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "ex")
    assert g._find_q == "ex"
    assert (g._irq_up, g._irq_down) == (0, 0)


def test_hotkeys_are_letters_while_finding():
    g = _mkui()
    fired = []
    g.on_announce = lambda: fired.append("a")
    g.handle_key(b"m")
    _type(g, "ads")
    assert g._find_q == "ads"
    assert fired == []
    assert g.state == ui.STATE_NODES


def test_typing_filters_and_backspace_widens():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "deej")
    assert _names(g._find_res) == ["Dr Deej home", "deejay-rpi"]
    for _ in range(4):
        g.handle_key(b"\x08")
    assert g._find_q == ""
    assert len(g._find_res) == 4


def test_query_is_capped_at_32():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "a" * 40)
    assert g._find_q == "a" * 32


def test_esc_closes_find():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "rat")
    g.handle_key(b"\x1b")
    assert not g._find
    assert g.state == ui.STATE_NODES


def test_switching_tab_closes_find():
    g = _mkui()
    g.handle_key(b"m")
    g._switch_tab(ui.TAB_NET)
    assert not g._find


def test_no_snapshot_callback_falls_back_to_peer_list():
    g = _mkui(snap=None)
    g.add_peer(ALICE, "nomad-alice")
    g.handle_key(b"m")
    assert _names(g._find_res) == ["nomad-alice"]


# --- selecting -------------------------------------------------------------

def test_trackball_moves_selection_within_results():
    g = _mkui()
    g.handle_key(b"m")
    g.nav_event("down")
    g.nav_event("down")
    g.handle_trackball()
    assert g._find_sel == 2
    g.nav_event("up")
    g.handle_trackball()
    assert g._find_sel == 1
    for _ in range(10):
        g.nav_event("down")
    g.handle_trackball()
    assert g._find_sel == 3          # clamped to the last result


def test_typing_resets_selection():
    g = _mkui()
    g.handle_key(b"m")
    g.nav_event("down")
    g.handle_trackball()
    _type(g, "d")
    assert g._find_sel == 0


def test_enter_adds_the_pick_and_opens_its_chat():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "deej")
    g.nav_event("down")
    g.handle_trackball()
    g.handle_key(b"\r")
    assert g.state == ui.STATE_CHAT
    assert g.selected_peer == RPI
    assert g.peers[RPI]["name"] == "deejay-rpi"
    assert g.added == [RPI]
    assert not g._find


def test_click_opens_like_enter():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "rats")
    g.nav_event("click")
    g.handle_trackball()
    assert g.state == ui.STATE_CHAT
    assert g.selected_peer == RATS


def test_enter_on_a_known_peer_does_not_duplicate_it():
    g = _mkui()
    g.add_peer(RATS, "Ratspeak", rssi=-80)
    g.handle_key(b"m")
    _type(g, "rats")
    g.handle_key(b"\r")
    assert g._peer_keys.count(RATS) == 1
    assert g.selected_peer == RATS
    assert g.peers[RATS]["rssi"] == -80   # announce data kept, not overwritten
    assert g.added == []


def test_unknown_full_hash_is_added_as_unknown_and_path_requested():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, UNKNOWN.upper())
    assert g._find_res == []
    g.handle_key(b"\r")
    dest = bytes.fromhex(UNKNOWN)
    assert g.state == ui.STATE_CHAT
    assert g.selected_peer == dest
    assert g.peers[dest]["name"] == "?"
    assert g.added == [dest]


def test_enter_with_no_match_and_a_partial_hash_stays_put():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "3fa1")
    g.handle_key(b"\r")
    assert g._find
    assert g.state == ui.STATE_NODES
    assert g.added == []


# --- drawing ---------------------------------------------------------------

def _draw(g):
    g.tft.texts = []
    g._cache = [''] * ui.CACHE_ROWS
    g.draw_node_list()
    return g.tft


def test_find_screen_draws_results_and_input():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "rat")
    t = _draw(g)
    out = t.drawn()
    assert "Ratspeak" in out
    assert "b90dae78" in out
    assert "12m" in out
    assert "rat_" in t.at_y(ui.INPUT_Y)


def test_find_screen_explains_an_unknown_full_hash():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, UNKNOWN)
    assert "Not heard yet" in _draw(g).drawn()


def test_find_screen_draws_with_long_names_and_no_results():
    g = _mkui(snap=lambda: [(RATS, "x" * 80, 0)])
    g.handle_key(b"m")
    _draw(g)
    _type(g, "qqq")
    assert "No match" in _draw(g).drawn()


def test_msg_footer_reads_setup_hash_del():
    g = _mkui()
    foot = _draw(g).at_y(ui.INPUT_Y)
    for word in ("nnc", "etup", "hash", "el"):
        assert word in foot, foot
    assert "ing" not in foot, foot       # ping still works, just not advertised


def test_p_still_pings():
    g = _mkui()
    g.add_peer(ALICE, "nomad-alice")
    pinged = []
    g.on_ping = lambda h: pinged.append(h)
    g.handle_key(b"p")
    assert pinged == [ALICE]


def test_settings_header_reads_setup():
    g = _mkui()
    g.handle_key(b"s")
    g.tft.texts = []
    g._cache = [''] * ui.CACHE_ROWS
    g.draw_settings()
    assert "Setup" in g.tft.drawn()
    assert "Settings" not in g.tft.drawn()


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
        print("\n%d of %d find-contact tests FAILED." % (failed, len(fns)))
        sys.exit(1)
    print("\nAll %d find-contact tests passed." % len(fns))


if __name__ == "__main__":
    _run()

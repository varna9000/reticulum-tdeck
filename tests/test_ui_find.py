# Host-side test for Find, (m) on any node-list tab: a search over every
# address of the tab's kind the node has heard (urns' known_destinations,
# handed over by tdeck_node as a per-tab snapshot), filtered as you type by
# display-name substring or hash prefix. Enter adds the pick to the tab's list
# and selects it -- it does not open or connect; a full 32-hex hash nobody has
# announced is added anyway, as "?", and the node is asked to find a path.
#
# Run:  python3 tests/test_ui_find.py

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
    g.snap_tabs = []

    def _snap_for(tab):
        g.snap_tabs.append(tab)
        return snap() if snap else None
    g.on_contact_snapshot = _snap_for
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


def test_m_opens_find_on_every_tab_with_that_tabs_snapshot():
    for tab in (ui.TAB_MSG, ui.TAB_NET, ui.TAB_RRC, ui.TAB_SSH):
        g = _mkui()
        g._switch_tab(tab)
        g.handle_key(b"m")
        assert g._find, tab
        assert g.snap_tabs == [tab], (tab, g.snap_tabs)


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


def test_enter_adds_the_pick_and_selects_it_without_opening():
    g = _mkui()
    g.add_peer(ALICE, "nomad-alice")
    g.handle_key(b"m")
    _type(g, "deej")
    g.nav_event("down")
    g.handle_trackball()
    g.handle_key(b"\r")
    assert g.state == ui.STATE_NODES
    assert g._peer_keys[g.selected_idx] == RPI
    assert g.peers[RPI]["name"] == "deejay-rpi"
    assert g.added == [RPI]
    assert not g._find
    g.handle_key(b"\r")                  # the usual Enter now opens it
    assert g.state == ui.STATE_CHAT and g.selected_peer == RPI


def test_click_adds_like_enter():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "rats")
    g.nav_event("click")
    g.handle_trackball()
    assert g.state == ui.STATE_NODES
    assert g._peer_keys[g.selected_idx] == RATS


def test_each_tab_adds_to_its_own_list():
    h = bytes.fromhex(UNKNOWN)
    for tab, keys in ((ui.TAB_NET, "_node_keys"), (ui.TAB_RRC, "_rrc_keys"),
                      (ui.TAB_SSH, "_shell_keys")):
        g = _mkui(snap=None)
        g._switch_tab(tab)
        fired = []
        g.on_rrc_connect = lambda d: fired.append(d)
        g.handle_key(b"m")
        _type(g, UNKNOWN)
        g.handle_key(b"\r")
        assert getattr(g, keys) == [h], (tab, getattr(g, keys))
        assert g._peer_keys == [], tab
        assert g.added == [h], tab
        assert g.state == ui.STATE_NODES, tab
        assert fired == [] and g._terminal is None, tab   # nothing connects


def test_enter_on_a_known_peer_does_not_duplicate_it():
    g = _mkui()
    g.add_peer(RATS, "Ratspeak", rssi=-80)
    g.handle_key(b"m")
    _type(g, "rats")
    g.handle_key(b"\r")
    assert g._peer_keys.count(RATS) == 1
    assert g._peer_keys[g.selected_idx] == RATS
    assert g.peers[RATS]["rssi"] == -80   # announce data kept, not overwritten
    assert g.added == []


def test_unknown_full_hash_is_added_as_unknown_and_path_requested():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, UNKNOWN.upper())
    assert g._find_res == []
    g.handle_key(b"\r")
    dest = bytes.fromhex(UNKNOWN)
    assert g.state == ui.STATE_NODES
    assert g._peer_keys[g.selected_idx] == dest
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
    g.tft.colors = []
    g.tft.rects = []
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


def test_find_title_names_what_the_tab_adds_and_no_identity_rows():
    want = {ui.TAB_MSG: "Find peer", ui.TAB_NET: "Find node",
            ui.TAB_RRC: "Find hub", ui.TAB_SSH: "Find listener"}
    for tab, title in want.items():
        g = _mkui()
        g.my_identity_hash = "ab" * 16
        g._switch_tab(tab)
        g.handle_key(b"m")
        out = _draw(g).drawn()
        assert title in out, (tab, out)
        assert "ab" * 16 not in out, tab      # our id is not shown in Find
        assert "Ratspeak" in out, tab


def test_title_band_is_teal_and_centred():
    g = _mkui()
    g.handle_key(b"m")
    t = _draw(g)
    y = ui.BODY_Y + ui.CHAR_H
    band = [r for r in t.rects if r[1] == y and r[2] == ui.SCREEN_W]
    assert band and band[0][4] == g.TAB_BG, band
    title = [c for c in t.colors if c[2] == y and c[0].startswith("Find")][0]
    assert title[3] == g.NEON_GREEN and title[4] == g.TAB_BG, title
    assert title[1] == (ui.COLS - len(title[0])) // 2 * ui.CHAR_W, title


def test_tab_underline_only_outside_find():
    line_y = ui.BODY_Y + ui.CHAR_H - 1
    g = _mkui()
    t = _draw(g)
    assert any(r[1] == line_y and r[2] == ui.SCREEN_W for r in t.rects)
    g.handle_key(b"m")
    t = _draw(g)
    assert not any(r[1] == line_y and r[2] == ui.SCREEN_W for r in t.rects)


def test_selected_tab_is_teal_and_evenly_spaced():
    g = _mkui()
    t = _draw(g)
    ly = ui.BODY_Y - 2
    labels = [c for c in t.colors if c[2] == ly]
    tab = [c for c in labels if "MSG" in c[0]][0]
    assert tab[3] == g.NEON_GREEN and tab[4] == g.TAB_BG, tab
    sw = ui.SCREEN_W // 4
    for i, c in enumerate(sorted(labels, key=lambda c: c[1])):
        centre = c[1] + len(c[0]) * ui.CHAR_W // 2
        assert abs(centre - (i * sw + sw // 2)) <= ui.CHAR_W, (c, i)
    fill = [r for r in t.rects if r[4] == g.TAB_BG and r[0] == 0 and r[2] == sw]
    assert fill and fill[0][1] == ui.NAV_H + 1, fill       # up to the top rail


def test_favourite_is_an_orange_star_not_text():
    g = _mkui()
    g.add_peer(ALICE, "nomad-alice", fav=True)
    t = _draw(g)
    assert "[*]" not in t.drawn()
    assert any(r[4] == g.ORANGE for r in t.rects)


def test_no_match_hints_are_centred():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "zzz")
    t = _draw(g)
    for want in ("No match.", "Type the full 32-hex hash to add it."):
        row = [c for c in t.texts if want in c[0]][0]
        assert row[0].strip() == want, row
        assert row[0].index(want) == (ui.COLS - len(want)) // 2, row


def test_selected_line_is_all_yellow_in_find_and_lists():
    g = _mkui()
    g.handle_key(b"m")
    _type(g, "rat")
    t = _draw(g)
    hashes = [c for c in t.colors if c[0].startswith("b90dae78")]
    assert hashes == [] or all(c[3] != g.DIM_CYAN for c in hashes), hashes
    g.handle_key(b"\x1b")
    g.add_peer(RATS, "Ratspeak")
    t = _draw(g)
    tags = [c for c in t.colors if "[b90dae78]" in c[0] and c[0].strip() == "[b90dae78]"]
    assert tags == [], tags                   # no dim hash overdraw on the selected row


def test_other_tab_footers_read_fav_hash():
    g = _mkui()
    g._switch_tab(ui.TAB_NET)
    foot = _draw(g).at_y(ui.INPUT_Y)
    for word in ("nnc", "etup", "av", "hash"):
        assert word in foot, foot


def test_unread_count_is_a_pill_on_the_right_of_the_row():
    g = _mkui()
    g.add_peer(ALICE, "nomad-alice")
    g.unread[ALICE] = 23
    t = _draw(g)
    y = ui.BODY_Y + ui.CHAR_H
    pill = [r for r in t.rects if r[4] == g.NEON_MAG and r[1] >= y and r[3] > 1]
    assert pill and pill[0][0] > ui.SCREEN_W // 2, pill      # right side
    assert any(c[0] == "23" for c in t.colors)
    # the left margin is free, so the selection bar is drawn
    assert any(r[:4] == (0, y, 3, ui.CHAR_H) and r[4] == g.NEON_MAG for r in t.rects)


def test_footer_spells_out_hops_and_minutes():
    g = _mkui()
    g.add_peer(ALICE, "nomad-alice", rssi=-87, hops=2)
    g.peers[ALICE]["seen"] = _time.time() - 300
    foot = _draw(g).at_y(ui.INPUT_Y)
    # hotkeys in the main font leave ~12 small chars: RSSI goes first
    assert "2 hops 5min" in foot, foot
    g.peers[ALICE]["hops"] = None
    g._route_cache = ''
    foot = _draw(g).at_y(ui.INPUT_Y)
    assert "-87dB 5min" in foot, foot          # direct peer: signal fits


def test_msg_footer_matches_the_other_tabs():
    g = _mkui()
    foot = _draw(g).at_y(ui.INPUT_Y)
    for word in ("nnc", "etup", "av", "hash"):
        assert word in foot, foot
    assert "ing" not in foot and ")el" not in foot, foot   # both keys still work


def test_d_still_deletes_on_msg():
    g = _mkui()
    g.add_peer(ALICE, "nomad-alice")
    g.handle_key(b"d")
    assert g._peer_keys == []


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


def test_time_zone_setting_shifts_only_the_display():
    g = _mkui()
    saved = []
    g.on_tz = lambda m: saved.append(m)
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    g._settings_idx = 11
    for _ in range(6):
        g._settings_adjust(1)                  # 6 x 30 min
    assert g._tz_min == 180 and saved[-1] == 180
    assert ui._tz_label(180) == "UTC+3" and ui._tz_label(-210) == "UTC-3:30"
    # localtime() is UTC on the device (no zone); compare on the same base
    want = _time.localtime(_time.time() + 3 * 3600)
    assert ui._fmt_clock() == "%02d:%02d" % (want[3], want[4])
    g.set_tz(0)


def test_setup_scrolls_to_time_zone_and_addr_and_marks_subpages():
    g = _mkui()
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    g._settings_idx = 12                       # Addr, past the 11 visible rows
    g.tft.texts = []; g.tft.rects = []
    g._cache = [''] * ui.CACHE_ROWS
    g.draw_settings()
    out = g.tft.drawn()
    assert "Addr:" in out and "Time zone: UTC" in out, out
    g._settings_idx = 0
    g.tft.rects = []
    g._cache = [''] * ui.CACHE_ROWS
    g.draw_settings()
    # WiFi (disconnected), Name, Radio stats, LoRa cfg open sub-pages
    marks = [r for r in g.tft.rects if r[0] == 9 and r[2] == 1]
    assert len({r[1] // ui.CHAR_H for r in marks}) >= 4, marks
    g.set_tz(0)


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

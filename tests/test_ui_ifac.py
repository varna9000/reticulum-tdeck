# Host-side tests for the Setup pages that carry an interface access code
# (issue #20): the TCP form (Host + an optional Net / Pass / IFAC group +
# Connect), the Net and Pass rows on the LoRa editor, and the shared text
# entry page. Shims MicroPython modules so the REAL ui.py runs under CPython.
# Run:  python3 tests/test_ui_ifac.py

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

import ui  # noqa: E402


class FakeTFT:
    def __init__(self):
        self.rects = []

    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, str):
            s.encode("ascii")
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.rects.append((x, y, len(s) * 8, 16, None))   # a glyph run paints its cells too

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        assert x + w <= 320 and y + h <= 240, (x, y, w, h)
        self.rects.append((x, y, w, h, c))

    def fill(self, c):
        pass


def _mkui():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t")
    g._screen_on = True
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    return g


def _type(g, s):
    for c in s.encode():
        g.handle_key(bytes([c]))


ENTER, ESC, BKSP = b"\x0d", b"\x1b", b"\x08"


# --- TCP form ---------------------------------------------------------------

def _open_tcp(target="h:1", saved=("mymesh", "pw", 8)):
    g = _mkui()
    g._wifi_connected = True
    g._tcp_enabled = False
    g._tcp_default = "d.example:4242"
    g._tcp_target = target
    g.set_tcp_ifac(*saved)
    g._settings_idx = 1
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_TCP_HOST
    return g


def test_tcp_row_opens_form_with_saved_values():
    g = _open_tcp()
    assert g._tcp_edit == {"host": "h:1", "networkname": "mymesh",
                           "passphrase": "pw", "ifac_size": 8}
    assert g._tcp_field == 0


def test_tcp_form_falls_back_to_default_host():
    g = _open_tcp(target="", saved=("", "", 16))
    assert g._tcp_edit["host"] == "d.example:4242"
    assert g._tcp_edit["ifac_size"] == 16


def test_host_entry_keeps_current_on_empty_enter():
    g = _open_tcp()
    g.handle_key(ENTER)                        # Host -> text entry
    assert g._settings_page == ui._SET_TEXT
    assert g._accepting_text()
    g.handle_key(ENTER)                        # nothing typed
    assert g._settings_page == ui._SET_TCP_HOST
    assert g._tcp_edit["host"] == "h:1"


def test_host_entry_takes_a_new_value():
    g = _open_tcp()
    g.handle_key(ENTER)
    _type(g, "rns.example.org:4242")
    g.handle_key(ENTER)
    assert g._tcp_edit["host"] == "rns.example.org:4242"


def test_net_entry_sets_and_empty_enter_clears():
    g = _open_tcp()
    g._settings_scroll_down()                  # Net
    assert g._tcp_field == 1
    g.handle_key(ENTER)
    _type(g, "othermesh")
    g.handle_key(ENTER)
    assert g._tcp_edit["networkname"] == "othermesh"
    g.handle_key(ENTER)
    g.handle_key(ENTER)                        # empty clears the field
    assert g._tcp_edit["networkname"] == ""


def test_pass_entry_sets_value():
    g = _open_tcp()
    g._settings_scroll_down()
    g._settings_scroll_down()                  # Pass
    assert g._tcp_field == 2
    g.handle_key(ENTER)
    _type(g, "correct horse")
    g.handle_key(ENTER)
    assert g._tcp_edit["passphrase"] == "correct horse"


def test_text_entry_esc_cancels_and_backspace_on_empty_goes_back():
    g = _open_tcp()
    g.handle_key(ENTER)
    _type(g, "junk")
    g.handle_key(ESC)
    assert g._settings_page == ui._SET_TCP_HOST
    assert g._tcp_edit["host"] == "h:1"
    g.handle_key(ENTER)
    g.handle_key(BKSP)
    assert g._settings_page == ui._SET_TCP_HOST


def test_ifac_size_cycles_between_64_and_128_bit():
    g = _open_tcp()
    for _ in range(3):
        g._settings_scroll_down()              # IFAC
    assert g._tcp_field == 3
    g.handle_key(ENTER)
    assert g._tcp_edit["ifac_size"] == 16
    g.handle_key(ENTER)
    assert g._tcp_edit["ifac_size"] == 8
    g._settings_adjust(1)                      # trackball right cycles too
    assert g._tcp_edit["ifac_size"] == 16
    g._settings_adjust(-1)
    assert g._tcp_edit["ifac_size"] == 8


def test_navigation_stays_within_the_five_rows():
    g = _open_tcp()
    g._settings_scroll_up()
    assert g._tcp_field == 0
    for _ in range(9):
        g._settings_scroll_down()
    assert g._tcp_field == 4                   # Connect is the last row


def test_apply_passes_host_port_and_code():
    g = _open_tcp()
    got = []
    g.on_backbone_apply = lambda host, port, ifac: got.append((host, port, ifac))
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    assert got == [("h", 1, {"networkname": "mymesh", "passphrase": "pw", "ifac_size": 8})]
    assert g._tcp_connecting
    g.set_tcp_result(True, "h:1")
    assert g._settings_page == ui._SET_MAIN
    assert g._tcp_target == "h:1"             # the link's state itself comes through set_backbone_state
    assert g._tcp_ifac == {"networkname": "mymesh", "passphrase": "pw", "ifac_size": 8}
    assert g._tcp_edit is None


def test_apply_with_no_code_passes_empty_fields():
    g = _open_tcp(saved=("", "", 16))
    got = []
    g.on_backbone_apply = lambda host, port, ifac: got.append(ifac)
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    assert got == [{"networkname": "", "passphrase": "", "ifac_size": 16}]


def test_apply_with_bad_host_goes_back_without_dialing():
    g = _open_tcp()
    g.on_backbone_apply = lambda *a: (_ for _ in ()).throw(AssertionError("must not connect"))
    g._tcp_edit["host"] = "no-port-here"
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_MAIN
    assert not g._tcp_connecting


def test_failed_apply_still_records_what_was_typed():
    # Apply writes the typed values before dialing (spec): a wrong code shows
    # as "no server" and the form, seeded from the saved values, is where it
    # gets corrected.
    g = _open_tcp()
    g.on_backbone_apply = lambda *a: None
    g._tcp_edit["networkname"] = "typo"
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    g.set_tcp_result(False, "h:1")
    assert g._tcp_ifac["networkname"] == "typo"


def test_esc_on_form_discards_edits():
    g = _open_tcp()
    g._tcp_edit["networkname"] = "changed"
    g.handle_key(ESC)
    assert g._settings_page == ui._SET_MAIN and g._settings_idx == 1
    assert g._tcp_edit is None
    assert g._tcp_ifac["networkname"] == "mymesh"


def test_backbone_row_opens_a_working_form():
    # Review finding (then): a page set without opening the form crashed on
    # _tcp_edit = None. Now the Backbone row is the only way in.
    g = _mkui()
    g._tcp_default = "d.example:4242"
    g.set_tcp_ifac("mymesh", "pw", 8)
    g._settings_idx = 1
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_TCP_HOST
    assert g._tcp_edit == {"host": "d.example:4242", "networkname": "mymesh",
                           "passphrase": "pw", "ifac_size": 8}
    g.draw()
    g.handle_key(ENTER)                        # Host -> text entry, no crash
    assert g._settings_page == ui._SET_TEXT
    _type(g, "h:1")
    g.handle_key(ENTER)
    assert g._tcp_edit["host"] == "h:1"


def test_text_page_without_an_entry_falls_back_to_setup():
    g = _mkui()
    g._settings_page = ui._SET_TEXT
    g._text_entry = None
    g.draw()                                   # must not raise
    g.handle_key(b"a")
    assert g._settings_page == ui._SET_MAIN


def test_form_page_is_not_a_text_page():
    g = _open_tcp()
    assert not g._accepting_text()             # e/x move the cursor here
    assert ui._SET_TCP_HOST not in g._TEXT_ENTRY_PAGES
    assert ui._SET_TEXT in g._TEXT_ENTRY_PAGES


# --- LoRa editor --------------------------------------------------------------

_RADIO = {"freq_khz": 868000, "bw": 125, "sf": 7, "coding_rate": 5, "tx_power": 14}


def _open_lora(cfg=None):
    g = _mkui()
    g.set_lora_config(cfg or _RADIO)
    g._settings_idx = 10
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_LORA
    return g


def test_set_lora_config_takes_code_keys_and_blanks_none():
    g = _mkui()
    g.set_lora_config(dict(_RADIO, networkname="m", passphrase=None))
    assert g._lora_cfg["networkname"] == "m"
    assert g._lora_cfg["passphrase"] == ""
    g.set_lora_config(_RADIO)                  # absent code keys mean no code (as in LORA_CONFIG)
    assert g._lora_cfg["networkname"] == ""


def test_lora_editor_has_net_pass_then_apply():
    g = _open_lora()
    assert g._lora_edit["networkname"] == "" and g._lora_edit["passphrase"] == ""
    g._lora_field = 4
    g._settings_scroll_down()
    assert g._lora_field == 5                  # Net
    g._settings_scroll_down()
    assert g._lora_field == 6                  # Pass
    g._settings_scroll_down()
    assert g._lora_field == 7                  # Apply, the last row
    g._settings_scroll_down()
    assert g._lora_field == 7


def test_lora_net_and_pass_entry_then_apply_passes_code():
    g = _open_lora()
    got = []
    g.on_lora_config = lambda p: got.append(p) or True
    g._lora_field = 5
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_TEXT
    _type(g, "mesh")
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_LORA
    g._lora_field = 6
    g.handle_key(ENTER)
    _type(g, "pw")
    g.handle_key(ENTER)
    g._lora_field = 7
    g.handle_key(ENTER)
    assert got and got[0]["networkname"] == "mesh" and got[0]["passphrase"] == "pw"
    assert got[0]["freq_khz"] == 868000
    assert g._lora_applying == "applied"
    assert g._lora_cfg["networkname"] == "mesh"


def test_lora_text_entry_back_keeps_pending_radio_edits():
    g = _open_lora()
    g._lora_field = 2
    g._settings_adjust(1)                      # SF 7 -> 8, pending
    g._lora_field = 5
    g.handle_key(ENTER)
    g.handle_key(ESC)
    assert g._settings_page == ui._SET_LORA
    assert g._lora_edit["sf"] == 8


def test_lora_net_adjust_does_nothing():
    g = _open_lora()
    g._lora_field = 5
    g._settings_adjust(1)                      # a text row has nothing to step
    assert g._lora_edit["networkname"] == ""


# --- drawing ------------------------------------------------------------------

def test_pages_draw_within_the_panel():
    g = _open_tcp()
    g.draw()
    g.handle_key(ENTER)                        # text entry
    g.draw()
    g.handle_key(ESC)
    g._tcp_connecting = True
    g.draw()
    g2 = _open_lora()
    g2._lora_field = 5
    g2.draw()
    g2._lora_applying = "failed"
    g2._cache = [''] * ui.CACHE_ROWS
    g2.draw()


def test_lora_page_erases_its_gaps_on_the_lcd():
    # Rows sit at half-row offsets, so the strips between them must be
    # painted too, or the Setup list shows through from before.
    g = _open_lora()
    g.tft.rects = []
    g.draw()
    body = [r for r in g.tft.rects if r[1] >= ui.BODY_Y]
    covered = set()
    for x, y, w, h, c in body:
        covered.update(range(y, y + h))
    for y in range(ui.BODY_Y + ui.CHAR_H, ui.BODY_Y + ui.BODY_ROWS * ui.CHAR_H):
        assert y in covered, y


def test_lcd_group_rows_stay_one_row_apart():
    # The LCD's LoRa page has no room for air inside the group: Net, Pass
    # (and IFAC on the TCP form) sit one row apart there.
    g = _open_lora()
    g.draw()
    ys = dict((f, y) for y, f in g._form_hits)
    assert ys[6] - ys[5] == ui.CHAR_H, (ys[5], ys[6])
    g = _open_tcp()
    g.tft.rects = []
    g.draw()
    ys = dict((f, y) for y, f in g._form_hits)
    assert ys[2] - ys[1] == ui.CHAR_H and ys[3] - ys[2] == ui.CHAR_H, ys
    foot = [(x, y) for x, y, w, h, c in g.tft.rects if x == ui._RAIL_X and w > 2 and h == 2]
    assert len(foot) == 2 and foot[-1] == (ui._RAIL_X, ys[3] + ui.CHAR_H - 2), foot    # the host group's, then under IFAC, no air below


def _run():
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  " + name)
        except Exception as e:
            failed += 1
            print("FAIL  " + name + "  ->  " + repr(e))
            traceback.print_exc()
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return failed


def test_setup_order_is_the_agreed_one():
    assert ui.SETUP_ORDER == (0, 1, 3, 10, 9, 4, 5, 6, 7, 8, 11, 2, 12, 13)   # v1 geometry in this suite
    g = _mkui()
    seen = [g._settings_idx]
    for _ in range(13):
        g._settings_scroll_down()
        seen.append(g._settings_idx)
    assert seen == list(ui.SETUP_ORDER), seen
    g._settings_scroll_down()
    assert g._settings_idx == 13          # clamps at Id
    for _ in range(13):
        g._settings_scroll_up()
    assert g._settings_idx == 0


def test_v1_setup_draws_rows_in_setup_order():
    g = _mkui()
    g._tcp_target = "h:1"
    drawn = []
    g.tft.text = lambda font, s, x, y, *a: drawn.append((s.decode("latin-1") if isinstance(s, (bytes, bytearray)) else s).strip())
    g._draw_settings_main()
    rows = [s for s in drawn if s and s != "Setup"]
    assert rows[0].startswith("WiFi:") and rows[1].startswith("Backbone: h:1") and rows[2].startswith("LoRa:"), rows[:3]
    assert rows[3].startswith("LoRa cfg:") and rows[4] == "Radio stats" and rows[5].startswith("Vol:"), rows[3:6]
    assert rows[6].startswith("Kbd light:"), rows[6]


def test_wifi_row_values_follow_the_backbone_state():
    g = _mkui()
    for state, ssid, ip, want in (("up", "home", "10.0.0.2", "home [10.0.0.2]"),
                                  ("no_server", "home", "10.0.0.2", "home, no server"),
                                  ("no_network", "home", "", "home, no network"),
                                  ("connecting", "", "", "connecting..."),
                                  ("off", "", "", "off")):
        g.set_backbone_state(state, ssid, ip)
        assert g._wifi_value() == want, (state, g._wifi_value())
    g.set_backbone_state("up", "home", "10.0.0.2", link=True)
    assert g._wifi_connected is True and g._tcp_enabled is True
    g.set_backbone_state("no_server", "home", "10.0.0.2")
    assert g._wifi_connected is True and g._tcp_enabled is False
    g.set_backbone_state("no_network", "home", "")
    assert g._wifi_connected is False and g._tcp_enabled is False


def test_backbone_state_change_repaints_setup():
    g = _mkui()
    g.dirty = False
    g.set_backbone_state("no_server", "home", "1.2.3.4")
    assert g.dirty is True
    g.state = ui.STATE_NODES
    g.dirty = False
    g.set_backbone_state("up", "home", "1.2.3.4")
    assert g.dirty is False


def test_wifi_row_while_up_needs_a_second_enter_then_disconnects():
    g = _mkui()
    g.set_backbone_state("no_network", "home", "")      # the 2026-10-31 case: AP lost, link registered
    calls = []
    g.on_wifi_disconnect = lambda: calls.append(1) or True
    g._settings_idx = 0
    g.handle_key(ENTER)
    assert calls == [] and g._setup_armed == 0 and g._settings_page == ui._SET_MAIN
    assert g._wifi_value() == "Enter again to disconnect"
    g.handle_key(ENTER)
    assert calls == [1] and g._setup_armed == -1


def test_wifi_row_arm_is_cleared_by_another_key():
    g = _mkui()
    g.set_backbone_state("up", "home", "1.2.3.4")
    calls = []
    g.on_wifi_disconnect = lambda: calls.append(1) or True
    g._settings_idx = 0
    g.handle_key(ENTER)
    g._settings_scroll_down()
    g._settings_scroll_up()
    g.handle_key(ENTER)
    assert calls == [] and g._setup_armed == 0


def test_wifi_row_while_off_opens_the_scan():
    g = _mkui()
    g.set_backbone_state("off", "", "")
    g._settings_idx = 0
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_WIFI_SCAN


def test_backbone_row_always_opens_the_form():
    for state in ("off", "up", "no_server", "no_network"):
        g = _mkui()
        g.set_backbone_state(state, "home", "")
        g._settings_idx = 1
        g.handle_key(ENTER)
        assert g._settings_page == ui._SET_TCP_HOST, state


def test_lora_row_while_online_only_selects():
    g = _mkui()
    g.lora_online = True
    calls = []
    g.on_lora_reset = lambda: calls.append(1) or True
    g._settings_idx = 3
    g.handle_key(ENTER)
    assert calls == []
    g.lora_online = False
    g.handle_key(ENTER)
    assert calls == [1]


def test_wifi_result_returns_to_setup_without_the_form():
    g = _mkui()
    g._wifi_connecting = True
    g.set_wifi_result("10.0.0.2")
    assert g._settings_page == ui._SET_MAIN and g._settings_idx == 0 and g._wifi_connecting is False
    g._wifi_connecting = True
    g.set_wifi_result(None)
    assert g._settings_page == ui._SET_WIFI_SCAN and g._wifi_err == "connect failed"


def test_tcp_toggle_and_reconnect_callbacks_are_gone():
    g = _mkui()
    assert not hasattr(g, "on_tcp_toggle") and not hasattr(g, "on_wifi_reconnect")


def test_backbone_form_rows_host_group_then_code_then_apply():
    g = _open_tcp()
    drawn = []
    g._draw_form = lambda rows, sel: drawn.extend(rows)
    g.draw()
    kinds = [(r[0], r[1]) for r in drawn if r[1] not in ("blank", "space")]
    assert kinds[0] == ("Server host", "legend"), kinds
    host_row = [r for r in drawn if r[0] == "h:1"]
    assert host_row and host_row[0][1] == "text" and host_row[0][2] == 0 and host_row[0][3] is True, drawn
    assert kinds[-1] == ("[ Apply ]", "action"), kinds
    assert not any(r[0].startswith("Host") for r in drawn)


def test_form_title_is_backbone():
    g = _open_tcp()
    seen = []
    g._draw_row_cached = lambda i, text, *a: seen.append(text) or True
    g.draw()
    assert "< Backbone" in seen, seen


def test_apply_result_saves_target_even_when_the_dial_failed():
    g = _open_tcp()
    calls = []
    g.on_backbone_apply = lambda h, p, ifac: calls.append((h, p, ifac))
    g._tcp_field = ui._TCP_CONNECT
    g.handle_key(ENTER)
    assert calls == [("h", 1, {"networkname": "mymesh", "passphrase": "pw", "ifac_size": 8})], calls
    assert g._tcp_connecting is True
    g.set_tcp_result(False, "h:1")
    assert g._tcp_target == "h:1" and g._settings_page == ui._SET_MAIN and g._settings_idx == 1
    assert g._tcp_enabled is False            # the state setter owns that flag
    assert g._tcp_ifac == {"networkname": "mymesh", "passphrase": "pw", "ifac_size": 8}


def test_on_tcp_connect_is_gone():
    assert not hasattr(_mkui(), "on_tcp_connect")


def test_tcp_enabled_means_the_link_exists_not_that_it_is_online():
    # The main page's header shows the globe while TCP is the transport; a
    # link blip (urns reconnecting) must not flip it to a stale LoRa reading.
    g = _mkui()
    g.set_backbone_state("no_server", "home", "", link=True)
    assert g._tcp_enabled is True and g._wifi_connected is True
    g.set_backbone_state("no_server", "home", "", link=False)
    assert g._tcp_enabled is False
    g.set_backbone_state("no_network", "home", "", link=True)
    assert g._tcp_enabled is True
    g.set_backbone_state("off", "", "", link=False)
    assert g._tcp_enabled is False


def test_wifi_value_keeps_the_state_suffix_for_a_long_name():
    g = _mkui()
    g.set_backbone_state("no_server", "FRITZ!Box 7590 XY Extra", "")
    v = g._wifi_value(ip=False, width=27)
    assert v.endswith(", no server") and len(v) <= 27, v
    g.set_backbone_state("no_network", "FRITZ!Box 7590 XY Extra", "")
    v = g._wifi_value(width=32)
    assert v.endswith(", no network") and len(v) <= 32, v
    g.set_backbone_state("up", "FRITZ!Box 7590 XY Extra Long Name", "10.0.0.2")
    v = g._wifi_value(width=32)
    assert len(v) <= 32 and v.endswith("[10.0.0.2]"), v


def test_wifi_row_arm_expires_after_its_window():
    g = _mkui()
    g.set_backbone_state("up", "home", "1.2.3.4")
    calls = []
    g.on_wifi_disconnect = lambda: calls.append(1) or True
    g._settings_idx = 0
    now = [1000000]
    saved = ui.time.ticks_ms
    ui.time.ticks_ms = lambda: now[0]
    try:
        g.handle_key(ENTER)
        assert g._setup_armed == 0
        now[0] += ui.SETUP_ARM_MS + 1
        g.handle_key(ENTER)
        assert calls == [] and g._setup_armed == 0       # re-armed, not fired
        now[0] += 1000
        g.handle_key(ENTER)
        assert calls == [1]
    finally:
        ui.time.ticks_ms = saved


def test_wifi_row_disarm_by_another_key_repaints():
    g = _mkui()
    g.set_backbone_state("up", "home", "1.2.3.4")
    g._settings_idx = 0
    g.handle_key(ENTER)
    assert g._wifi_value() == "Enter again to disconnect"
    g.dirty = False
    g.handle_key(b" ")                       # any key the page does not act on
    assert g._setup_armed == -1 and g.dirty is True
    assert g._wifi_value() == "home [1.2.3.4]"
    g.handle_key(ENTER)
    g.dirty = False
    g._settings_scroll_down()                # a move disarms and repaints too
    assert g._setup_armed == -1 and g.dirty is True


def test_wifi_row_value_comes_from_the_state_alone():
    # The old _wifi_connecting special case could disagree with the state
    # (a cancelled manual connect left it True for good).
    g = _mkui()
    g.set_backbone_state("up", "home", "1.2.3.4")
    g._wifi_connecting = True
    drawn = []
    g.tft.text = lambda font, s, x, y, *a: drawn.append((s.decode("latin-1") if isinstance(s, (bytes, bytearray)) else s).strip())
    g._draw_settings_main()
    assert any(r.startswith("WiFi: home [1.2.3.4]") for r in drawn), drawn[:3]


def test_band_slots_table():
    A, W, B = ui._ANTENNA, ui._WIFI, ui._WIFI_BANG
    assert ui.band_slots("up", False, None, 0) == (W, "", None)
    assert ui.band_slots("no_server", True, -80, 7) == (A, "snr 7", B)
    assert ui.band_slots("connecting", True, None, 0) == (A, "", B)
    assert ui.band_slots("no_network", False, None, 0) == (B, "", None)      # reconnecting: nothing carries
    assert ui.band_slots("off", True, -80, 7) == (A, "snr 7", None)
    assert ui.band_slots("off", False, None, 0) == (A, "FAIL", None)


def test_wifi_bang_is_the_fan_with_the_badge():
    def pixels(icon):
        w, h, runs = icon
        return {(x + dx, dy) for dx, dy, n in runs for x in range(n)}
    fan, bang = pixels(ui._WIFI), pixels(ui._WIFI_BANG)
    badge = {(x, y) for x in (14, 15) for y in list(range(7, 12)) + [13, 14]}
    assert ui._WIFI[0] == 15 and ui._WIFI[1] == 15 and ui._WIFI_BANG[0] == 17 and ui._WIFI_BANG[1] == 15
    assert bang == fan | badge


def _band_calls(g):
    icons, texts = [], []
    g._bitmap = lambda icon, x, y, c: icons.append((icon, x))
    g._sdraw = lambda s, x, y, c, bg=None: texts.append((s, x))
    g._cache[0] = ''
    g.draw_navbar()
    return icons, texts


def test_v1_band_two_slots_and_a_clock_that_does_not_move():
    g = _mkui()
    g.state = ui.STATE_NODES
    g.lora_online = True
    g.rssi, g.snr = -80, 7
    saved = (ui._clock_valid, ui._fmt_clock)
    ui._clock_valid, ui._fmt_clock = (lambda: True), (lambda: "09:41")
    try:
        g.set_backbone_state("no_server", "home", "", link=False)
        icons, texts = _band_calls(g)
        assert [i for i, _ in icons] == [ui._ANTENNA, ui._WIFI_BANG], icons
        assert ("snr 7", icons[0][1] + ui._ANTENNA[0] + 4) in texts, texts
        clock_x = [x for s, x in texts if s == "09:41"][0]
        g.set_backbone_state("up", "home", "1.2.3.4", link=True)
        icons, texts = _band_calls(g)
        assert [i for i, _ in icons] == [ui._WIFI] and not any(s.startswith("snr") for s, _ in texts)
        assert [x for s, x in texts if s == "09:41"][0] == clock_x
        g.set_backbone_state("no_server", "home", "", link=True)
        g.lora_online = False
        icons, texts = _band_calls(g)
        assert [i for i, _ in icons] == [ui._WIFI_BANG]
        g.set_backbone_state("off", "", "")
        g.lora_online = True
        icons, texts = _band_calls(g)
        assert [i for i, _ in icons] == [ui._ANTENNA] and ("snr 7", icons[0][1] + ui._ANTENNA[0] + 4) in texts
        assert [x for s, x in texts if s == "09:41"][0] == clock_x
    finally:
        ui._clock_valid, ui._fmt_clock = saved


def test_band_precedence_audio_and_transfer_win():
    g = _mkui()
    g.state = ui.STATE_NODES
    g.set_backbone_state("no_server", "home", "", link=False)
    g.lora_online = True
    g._audio_status = "PLAY"
    icons, texts = _band_calls(g)
    assert icons == [] and any(s.startswith("[PLAY]") for s, _ in texts), texts   # the clock may follow the text


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)

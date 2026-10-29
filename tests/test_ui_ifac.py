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


def test_connect_passes_host_port_and_code():
    g = _open_tcp()
    got = []
    g.on_tcp_connect = lambda host, port, ifac: got.append((host, port, ifac))
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    assert got == [("h", 1, {"networkname": "mymesh", "passphrase": "pw", "ifac_size": 8})]
    assert g._tcp_connecting
    g.set_tcp_result(True, "h:1")
    assert g._settings_page == ui._SET_MAIN
    assert g._tcp_enabled and g._tcp_target == "h:1"
    assert g._tcp_ifac == {"networkname": "mymesh", "passphrase": "pw", "ifac_size": 8}
    assert g._tcp_edit is None


def test_connect_with_no_code_passes_empty_fields():
    g = _open_tcp(saved=("", "", 16))
    got = []
    g.on_tcp_connect = lambda host, port, ifac: got.append(ifac)
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    assert got == [{"networkname": "", "passphrase": "", "ifac_size": 16}]


def test_connect_with_bad_host_goes_back_without_connecting():
    g = _open_tcp()
    g.on_tcp_connect = lambda *a: (_ for _ in ()).throw(AssertionError("must not connect"))
    g._tcp_edit["host"] = "no-port-here"
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    assert g._settings_page == ui._SET_MAIN
    assert not g._tcp_connecting


def test_failed_connect_keeps_saved_code():
    g = _open_tcp()
    g.on_tcp_connect = lambda *a: None
    g._tcp_edit["networkname"] = "typo"
    for _ in range(4):
        g._settings_scroll_down()
    g.handle_key(ENTER)
    g.set_tcp_result(False, "h:1")
    assert g._tcp_ifac["networkname"] == "mymesh"


def test_esc_on_form_discards_edits():
    g = _open_tcp()
    g._tcp_edit["networkname"] = "changed"
    g.handle_key(ESC)
    assert g._settings_page == ui._SET_MAIN and g._settings_idx == 1
    assert g._tcp_edit is None
    assert g._tcp_ifac["networkname"] == "mymesh"


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
    g.set_lora_config(_RADIO)                  # radio-only update keeps the code
    assert g._lora_cfg["networkname"] == "m"


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


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)

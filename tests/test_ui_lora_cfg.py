# Host-side test for the Settings > LoRa radio config page (STATE_SETTINGS,
# _SET_LORA). Shims MicroPython modules so the REAL ui.py runs under CPython.
# Run:  python3 tests/test_ui_lora_cfg.py

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
    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, str):
            s.encode("ascii")
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)

    def fill(self, c):
        pass


def _mkui():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t")
    g._screen_on = True
    return g


_DEFAULT = {"freq_khz": 868000, "bw": 125, "sf": 7, "coding_rate": 5, "tx_power": 14}


def _open_lora(cfg=None):
    """Return a UI parked on the open LoRa config page with an edit copy."""
    g = _mkui()
    g.set_lora_config(cfg or _DEFAULT)
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    g._settings_idx = 9  # LoRa cfg row (inserted before the inert Addr row)
    g.handle_key(b"\x0d")
    return g


def test_set_lora_config_syncs_and_normalizes_bw():
    g = _mkui()
    g.set_lora_config({"freq_khz": 868800, "bw": 250, "sf": 8,
                       "coding_rate": 6, "tx_power": 22})
    assert g._lora_cfg["freq_khz"] == 868800
    assert g._lora_cfg["bw"] == "250"       # int coerced to str for choice match
    assert g._lora_cfg["sf"] == 8
    assert g._lora_cfg["coding_rate"] == 6
    assert g._lora_cfg["tx_power"] == 22


def test_enter_page_copies_current_into_edit():
    g = _open_lora()
    assert g._settings_page == ui._SET_LORA
    assert g._lora_edit == g._lora_cfg
    assert g._lora_edit is not g._lora_cfg   # a copy — edits must not leak live


def test_bw_cycles_and_wraps():
    g = _open_lora({"freq_khz": 868000, "bw": 125, "sf": 7,
                    "coding_rate": 5, "tx_power": 14})
    g._lora_field = 1  # BW
    g._settings_adjust(1)
    assert g._lora_edit["bw"] == "250"
    g._settings_adjust(1)
    assert g._lora_edit["bw"] == "500"
    g._settings_adjust(1)                     # wraps
    assert g._lora_edit["bw"] == "125"
    g._settings_adjust(-1)                    # wraps back
    assert g._lora_edit["bw"] == "500"


def test_sf_clamps_at_bounds():
    g = _open_lora({"freq_khz": 868000, "bw": 125, "sf": 12,
                    "coding_rate": 5, "tx_power": 14})
    g._lora_field = 2  # SF
    g._settings_adjust(1)
    assert g._lora_edit["sf"] == 12           # already at max, no wrap
    g._lora_edit["sf"] = ui._LORA_SF_MIN
    g._settings_adjust(-1)
    assert g._lora_edit["sf"] == ui._LORA_SF_MIN


def test_coding_rate_clamps_at_max():
    g = _open_lora()
    g._lora_field = 3  # CR
    g._lora_edit["coding_rate"] = 8
    g._settings_adjust(1)
    assert g._lora_edit["coding_rate"] == 8


def test_tx_power_clamps_both_ends():
    g = _open_lora({"freq_khz": 868000, "bw": 125, "sf": 7,
                    "coding_rate": 5, "tx_power": 22})
    g._lora_field = 4  # TX
    g._settings_adjust(1)
    assert g._lora_edit["tx_power"] == 22
    g._lora_edit["tx_power"] = 0
    g._settings_adjust(-1)
    assert g._lora_edit["tx_power"] == 0


def test_freq_steps_by_100khz_on_adjust():
    g = _open_lora({"freq_khz": 868000, "bw": 125, "sf": 7,
                    "coding_rate": 5, "tx_power": 14})
    g._lora_field = 0  # Freq
    g._settings_adjust(1)
    assert g._lora_edit["freq_khz"] == 868100
    g._settings_adjust(-1)
    assert g._lora_edit["freq_khz"] == 868000


def test_freq_text_entry_parses_and_clamps():
    g = _open_lora()
    g._lora_field = 0
    g.handle_key(b"\x0d")                     # Enter on Freq -> numeric entry page
    assert g._settings_page == ui._SET_LORA_FREQ
    for d in b"915000":
        g.handle_key(bytes([d]))
    g.handle_key(b"\x0d")                     # save
    assert g._settings_page == ui._SET_LORA
    assert g._lora_edit["freq_khz"] == 915000


def test_freq_text_entry_rejects_out_of_range():
    g = _open_lora()
    g._lora_field = 0
    g.handle_key(b"\x0d")
    for d in b"99999999":                     # absurdly high -> clamped
        g.handle_key(bytes([d]))
    g.handle_key(b"\x0d")
    assert g._lora_edit["freq_khz"] == ui._LORA_FREQ_MAX


def test_navigation_stays_in_bounds():
    g = _open_lora()
    g._lora_field = 0
    g._settings_scroll_up()
    assert g._lora_field == 0                  # can't go above first field
    g._lora_field = 5
    g._settings_scroll_down()
    assert g._lora_field == 5                  # Apply row is the last selectable


def test_apply_calls_callback_and_commits_on_success():
    g = _open_lora({"freq_khz": 868000, "bw": 125, "sf": 7,
                    "coding_rate": 5, "tx_power": 14})
    applied = []
    g.on_lora_config = lambda p: applied.append(dict(p)) or True
    # change SF then move to Apply row
    g._lora_field = 2
    g._settings_adjust(1)                      # SF 7 -> 8
    g._lora_field = 5                          # Apply & Save
    g.handle_key(b"\x0d")
    assert len(applied) == 1
    assert applied[0]["sf"] == 8
    assert applied[0]["freq_khz"] == 868000
    assert g._lora_cfg["sf"] == 8              # committed to live cfg
    assert g._lora_applying == "applied"


def test_apply_failure_leaves_live_cfg_unchanged():
    g = _open_lora({"freq_khz": 868000, "bw": 125, "sf": 7,
                    "coding_rate": 5, "tx_power": 14})
    g.on_lora_config = lambda p: False         # radio refused
    g._lora_field = 2
    g._settings_adjust(1)                      # SF 7 -> 8 (pending)
    g._lora_field = 5
    g.handle_key(b"\x0d")
    assert g._lora_cfg["sf"] == 7              # live cfg untouched
    assert g._lora_applying == "failed"


def test_back_discards_unapplied_edits():
    g = _open_lora({"freq_khz": 868000, "bw": 125, "sf": 7,
                    "coding_rate": 5, "tx_power": 14})
    g._lora_field = 2
    g._settings_adjust(1)                      # SF 7 -> 8 (pending, not applied)
    g.handle_key(b"\x1b")                      # Esc -> back
    assert g._settings_page == ui._SET_MAIN
    assert g._lora_edit is None
    assert g._lora_cfg["sf"] == 7              # discarded


def test_pages_draw_without_error():
    g = _open_lora()
    g._cache = [''] * 15
    g.draw_settings()                          # LoRa page
    g._lora_field = 0
    g.handle_key(b"\x0d")                      # -> freq entry page
    g._cache = [''] * 15
    g.draw_settings()
    # and the main settings list still renders with the new row
    g._settings_page = ui._SET_MAIN
    g._cache = [''] * 15
    g.draw_settings()


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("\nAll %d LoRa-config UI tests passed." % len(fns))


if __name__ == "__main__":
    _run()

# Host tests: the navbar "RX n/total" transfer indicator must go away when
# its transfer ends, however it ends. Run:
#   /opt/homebrew/bin/python3 tests/test_transfer_indicator.py

import os
import sys
import time as _time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from test_ui_shell import _mkui

import ui as U

TRANSFERRING, COMPLETE, FAILED = 0x02, 0x05, 0x06


def _res(rcv, tot, status=TRANSFERRING):
    return types.SimpleNamespace(received_count=rcv, total_parts=tot, status=status)


def _navbar_text(g):
    g._cache[0] = ''
    g.tft.calls = []
    g.draw_navbar()
    return " ".join(c[1] if isinstance(c[1], str) else c[1].decode("latin-1")
                    for c in g.tft.calls if c[0] == "text")


def test_progress_shows_while_transferring():
    g = _mkui()
    r = _res(3, 10)
    g.set_transfer(r)
    g._expire_transfer()
    assert g.transfer_progress == (3, 10)
    assert "RX 3/10" in _navbar_text(g)
    print("ok test_progress_shows_while_transferring")


def test_a_duplicate_that_completes_without_delivery_clears():
    """The field bug: the sender re-sends an image it got no proof for; the
    resource completes, urns drops it as a duplicate, on_message never runs."""
    g = _mkui()
    r = _res(10, 10)
    g.set_transfer(r)
    r.status = COMPLETE
    g._progress_dirty = False
    g._expire_transfer()
    assert g.transfer_progress is None
    assert g._progress_dirty                      # the navbar repaints
    assert "RX" not in _navbar_text(g)
    print("ok test_a_duplicate_that_completes_without_delivery_clears")


def test_a_failed_transfer_clears():
    g = _mkui()
    r = _res(4, 10)
    g.set_transfer(r)
    r.status = FAILED
    g._expire_transfer()
    assert g.transfer_progress is None
    print("ok test_a_failed_transfer_clears")


def test_a_stalled_transfer_clears_after_two_minutes():
    g = _mkui()
    g.set_transfer(_res(4, 10))
    real = _time.ticks_ms
    try:
        base = real()
        _time.ticks_ms = lambda: base + U.UI._TRANSFER_STALE_MS - 1000
        g._expire_transfer()
        assert g.transfer_progress == (4, 10)     # not yet
        _time.ticks_ms = lambda: base + U.UI._TRANSFER_STALE_MS + 1000
        g._expire_transfer()
        assert g.transfer_progress is None
    finally:
        _time.ticks_ms = real
    print("ok test_a_stalled_transfer_clears_after_two_minutes")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all transfer indicator tests passed")

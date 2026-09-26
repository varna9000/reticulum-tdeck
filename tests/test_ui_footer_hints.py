# Host tests for the shared footer-hint style (UI._draw_hints) and the RRC
# header row. Run:
#   /opt/homebrew/bin/python3 tests/test_ui_footer_hints.py

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from test_ui_shell import _mkui as make_ui

import ui as U
import rrc_ui


def _s(c):
    return c[1].decode("latin-1") if isinstance(c[1], (bytes, bytearray)) else c[1]


def _footer(calls):
    """Footer-row text calls as (x, text, fg), left to right."""
    return sorted((c[2], _s(c), c[4]) for c in calls
                  if c[0] == "text" and c[3] == U.INPUT_Y)


def _row(calls):
    """Rebuild the footer row as a string from its per-segment draws."""
    cells = {}
    for x, t, _ in _footer(calls):
        for i, ch in enumerate(t):
            cells[x // U.CHAR_W + i] = ch
    return "".join(cells.get(i, " ") for i in range(max(cells) + 1)).rstrip()


def test_keys_are_green_and_labels_dim():
    g = make_ui()
    g.tft.calls = []
    g._draw_hints((("BKSP", "back"),))
    segs = _footer(g.tft.calls)
    assert [t for _, t, _ in segs] == ["(", "BKSP", ")back"], segs
    assert segs[1][2] == g.NEON_GREEN and segs[0][2] == segs[2][2] == g.DIM_CYAN, segs
    print("ok test_keys_are_green_and_labels_dim")


def test_optional_hints_drop_before_anything_clips():
    g = make_ui()
    hints = (("BKSP", "quit"), ("CLICK", "keys"), ("BALL", "scroll", True))
    g.tft.calls = []
    g._draw_hints(hints)
    assert _row(g.tft.calls) == "(BKSP)quit (CLICK)keys (BALL)scroll"
    # A scroll prefix pushes it past 40 columns: the optional hint goes,
    # the exit key stays.
    g.tft.calls = []
    g._draw_hints(hints, "[+123] ")
    row = _row(g.tft.calls)
    assert row == "[+123] (BKSP)quit (CLICK)keys", row
    assert len(row) <= U.COLS
    print("ok test_optional_hints_drop_before_anything_clips")


def test_every_screen_footer_fits_the_30_column_pro():
    old = U.COLS
    U.COLS = 30
    try:
        g = make_ui()
        for hints, prefix in (
                ((("R", "eload", True), ("N", "ext"), ("P", "rev"), ("BKSP", "back")), ""),
                ((("CLICK", "open"), ("BKSP", "back")), "12/34 "),
                ((("BKSP", "quit"), ("CLICK", "keys"), ("BALL", "scroll", True)), "[+9] "),
                ((("CLICK", "send"), ("U/D", "move", True), ("BKSP", "close")), ""),
                ((("BKSP", "exit"), ("CLICK", "rooms")), "")):
            g.tft.calls = []
            g._draw_hints(hints, prefix)
            row = _row(g.tft.calls)
            assert len(row) <= 30, row
            assert "(BKSP)" in row, row
    finally:
        U.COLS = old
    print("ok test_every_screen_footer_fits_the_30_column_pro")


def _header_segments(g):
    return sorted((c[2], _s(c)) for c in g.tft.calls
                  if c[0] == "text" and c[3] == U.BODY_Y and _s(c).strip())


def test_rrc_header_leaves_a_gap_after_the_marker():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = ""
    g._rrc_status = "finding path..."
    g.tft.calls = []
    rrc_ui.draw_rooms(g)
    segs = _header_segments(g)
    assert segs[0] == (U.CHAR_W, "<"), segs          # clear of the frame rail
    assert segs[1] == (3 * U.CHAR_W, "finding path..."), segs
    print("ok test_rrc_header_leaves_a_gap_after_the_marker")


def test_rrc_status_is_readable_beside_the_hub_name():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = "A Rather Long Hub Name Here"
    g._rrc_status = "rate limited - hold on"
    g.tft.calls = []
    rrc_ui.draw_rooms(g)
    segs = _header_segments(g)
    texts = [t for _, t in segs]
    assert "rate limited - hold on" in texts, segs      # was cut to "rate limit"
    name_x, name = segs[1]
    stat_x = [x for x, t in segs if t == "rate limited - hold on"][0]
    assert name_x == 3 * U.CHAR_W, segs
    assert name_x + len(name) * U.CHAR_W < stat_x, segs  # no overlap, a gap
    print("ok test_rrc_status_is_readable_beside_the_hub_name")


def test_dm_footer_uses_the_hint_style_and_a_mic_icon():
    g = make_ui()
    g.state = U.STATE_CHAT
    g.selected_peer = b"\x01" * 8
    g.chat_cursor = -1
    g.cmd_buf = bytearray()
    g.on_record_start = lambda: None
    g.tft.calls = []
    g.draw_input()
    segs = _footer(g.tft.calls)
    texts = [t for _, t, _ in segs]
    assert "0" not in texts and not any("=rec" in t for t in texts), segs
    assert ")rec" in texts, segs                       # (mic)rec
    bx = (U.COLS - 1 - len("(BKSP)back")) * U.CHAR_W
    assert (bx + U.CHAR_W, "BKSP", g.NEON_GREEN) in segs, segs
    assert _row(g.tft.calls).endswith("(BKSP)back"), _row(g.tft.calls)
    # on an image the right-hand hint says what a click does instead
    g._visible_image_lines = {0: 1}
    g.chat_cursor = 0
    g.tft.calls = []
    g.draw_input()
    assert _row(g.tft.calls).endswith("(CLICK)view"), _row(g.tft.calls)
    print("ok test_dm_footer_uses_the_hint_style_and_a_mic_icon")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all footer hint tests passed")

# The UI must not offer hardware the board does not have.
#
# ui.py is shared between the T-Deck v1 and the T-Deck Pro, and the Pro is
# missing things the v1 has -- chiefly the ES7210 microphone, so there is no
# voice recording. tdeck_node.py leaves on_record_start unset on such a board,
# and that absence is the signal the UI keys off. These tests hold both ends of
# it: nothing offers recording without a microphone, and everything still does
# with one.
#
# Run:  python3 tests/test_ui_capabilities.py

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

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


class FakeTFT:
    def __init__(self):
        self.texts = []

    def text(self, font, s, x, y, fg, bg=None):
        self.texts.append(s if isinstance(s, str) else bytes(s).decode("latin-1"))

    def fill_rect(self, x, y, w, h, c):
        pass

    def fill(self, c):
        pass


def _mkui(with_mic):
    tft = FakeTFT()
    g = ui.UI(tft, object(), lambda: b"\x00", node_name="t", trackball=False)
    g._screen_on = True
    g.state = ui.STATE_CHAT
    g.selected_peer = b"\x01" * 8
    g.chat_cursor = -1          # composing, not navigating history
    g.cmd_buf = bytearray()
    if with_mic:
        g.on_record_start = lambda: None
        g.on_record_stop = lambda send: None
    return g, tft


def _drew_rec_hint(tft):
    return any("rec" in t for t in tft.texts)


def test_hint_hidden_without_mic():
    print("the record hint follows the hardware")
    g, tft = _mkui(with_mic=False)
    g.draw_input()
    check("no mic: the chat footer does not offer (mic)rec",
          not _drew_rec_hint(tft), str(tft.texts))

    g, tft = _mkui(with_mic=True)
    g.draw_input()
    check("mic present: the chat footer still offers (mic)rec",
          _drew_rec_hint(tft), str(tft.texts))


def test_hint_hidden_while_navigating():
    """The original condition, which the new one must not have broken: the
    hint is for composing, and disappears while stepping back through
    messages."""
    print("the record hint still hides while navigating history")
    g, tft = _mkui(with_mic=True)
    g.chat_cursor = 0
    g.draw_input()
    check("navigating history hides the hint even with a mic",
          not _drew_rec_hint(tft), str(tft.texts))


def test_recording_state_refuses_without_mic():
    """Belt and braces: even if something reached _enter_recording, a board
    with no microphone must not land on a recording screen that never
    records."""
    print("recording state refuses to open without a mic")
    g, _ = _mkui(with_mic=False)
    g._enter_recording()
    check("no mic: state does not become STATE_RECORDING",
          g.state != ui.STATE_RECORDING, "state=%r" % g.state)

    g, _ = _mkui(with_mic=True)
    g._enter_recording()
    check("mic present: state becomes STATE_RECORDING",
          g.state == ui.STATE_RECORDING, "state=%r" % g.state)


def test_battery_reads_through_the_board():
    """The two boards sense the battery in completely different ways, so the
    UI takes a reader from the board rather than importing one."""
    print("battery comes from the board")
    g, _ = _mkui(with_mic=False)
    check("starts unknown", g.bat_v == 0.0, "got %r" % g.bat_v)

    g.on_battery = lambda: 4.229          # what the Pro's BQ27220 reports
    g.update_battery()
    check("a board reading is adopted", abs(g.bat_v - 4.229) < 1e-6,
          "got %r" % g.bat_v)

    # A gauge that does not answer must not wipe the last good reading -- an
    # unreadable pack is not a flat pack.
    g.on_battery = lambda: None
    g.update_battery()
    check("a failed read keeps the last value", abs(g.bat_v - 4.229) < 1e-6,
          "got %r" % g.bat_v)

    g.on_battery = lambda: 1 / 0          # a driver that raises
    g.update_battery()
    check("a raising reader is survivable", abs(g.bat_v - 4.229) < 1e-6,
          "got %r" % g.bat_v)


def test_unlit_battery_segments_are_visible_on_mono():
    """On a 1-bit panel DIM_CYAN and NEON_GREEN both cross the shim's ink
    threshold, so unlit segments drawn dim come out as black as lit ones and
    the pack always reads full. Unlit must be background there."""
    print("battery icon is legible on a 1-bit panel")

    class MonoTFT(FakeTFT):
        mono = True

        def __init__(self):
            FakeTFT.__init__(self)
            self.rects = []

        def fill_rect(self, x, y, w, h, c):
            self.rects.append((x, y, w, h, c))

    def segments(bat_v):
        """The three bar fills: the 7x8 rects at x=3/11/19, y=6 inside the
        battery outline (found by position -- the navbar paints more after
        them: the voltage, the interface icon)."""
        g = ui.UI(MonoTFT(), object(), lambda: b"\x00", node_name="t",
                  trackball=False)
        g._screen_on = True
        g.bat_v = bat_v
        g.draw_navbar()
        return g, [r[4] for r in g.tft.rects if r[1:4] == (6, 7, 8) and r[0] in (3, 11, 19)]

    g, flat = segments(3.0)             # below every threshold: all unlit
    check("mono panel is detected", g._mono is True)
    check("a flat pack paints no segment in the lit colour",
          g.NEON_GREEN not in flat, str(flat))
    check("a flat pack paints unlit segments as background",
          flat == [g.BG_DARK] * 3, str(flat))

    g, full = segments(4.1)             # above every threshold: all lit
    check("a full pack still paints every segment lit",
          full == [g.NEON_GREEN] * 3, str(full))

    # The whole point: the two states have to look different.
    check("flat and full render differently", flat != full,
          "%s vs %s" % (flat, full))

    colour = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t",
                   trackball=False)
    check("a colour panel is not treated as mono", colour._mono is False)


def _shim_ink(c):
    """What lib/eink_shim.py makes of an RGB565 colour: True for ink."""
    lum = ((((c >> 11) & 0x1F) << 1) + ((c >> 5) & 0x3F) + ((c & 0x1F) << 1))
    return lum > 60


def test_tab_icons_share_a_height():
    check("all four tab icons are 14 px tall", [ic[1] for ic in ui._TAB_ICONS] == [14, 14, 14, 14])


def test_tab_counts():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    g.peers[b"a" * 16] = {"name": "a"}
    g.unread[b"a" * 16] = 2
    check("counts come from the tables, * for unread",
          g._tab_counts() == ("1*", "0", "0", "0"), str(g._tab_counts()))


def test_active_tab_is_marked_on_the_colour_bar():
    """The v1 bar marks the active cell with TAB_BG + NEON_GREEN against
    BG_DARK + DIM_CYAN. (A 1-bit panel is always the Pro, whose own bar,
    ui_pro.draw_tab_bar, inverts the active tab; test_ui_pro covers it.)"""
    print("active tab is marked on the colour tab bar")
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    g.tft.colors = []
    g.tft.text = lambda font, s, x, y, fg, bg=None: g.tft.colors.append((fg, bg))
    g._screen_on = True
    g.node_tab = ui.TAB_NET
    g._draw_tab_bar()
    check("four counts drawn", len(g.tft.colors) == 4, str(g.tft.colors))
    check("the active tab is teal, the rest dim",
          g.tft.colors == [(g.DIM_CYAN, g.BG_DARK), (g.NEON_GREEN, g.TAB_BG),
                           (g.DIM_CYAN, g.BG_DARK), (g.DIM_CYAN, g.BG_DARK)],
          str(g.tft.colors))


def test_selected_rows_invert_on_mono():
    """On a 1-bit panel the selection tint maps to ink, so every selected
    row is drawn as an ink band and the shim puts paper glyphs on it."""
    print("selected rows invert on a 1-bit panel")

    class MonoTFT(FakeTFT):
        mono = True

    g = ui.UI(MonoTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    check("the selection colour is ink on mono", _shim_ink(g.SEL_BG), hex(g.SEL_BG))
    c = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    check("and the tint on a colour panel", c.SEL_BG == 0x2966, hex(c.SEL_BG))


def test_image_viewer_dithers_on_a_mono_panel():
    """The viewer must hand a photo to blit_dither on a 1-bit panel and to
    blit_buffer on a colour one; the shim's plain blit would show an
    inverted, grey-less picture."""
    print("image viewer picks the blit for the panel")
    import types as _t
    dec = _t.ModuleType("tjpgd_fast_xtensawin")
    dec.decode = lambda data, w, h: (4, 2, bytes(16))
    sys.modules["tjpgd_fast_xtensawin"] = dec
    try:
        def view(mono):
            class TFT(FakeTFT):
                def __init__(self):
                    FakeTFT.__init__(self)
                    self.blits = []

                def blit_buffer(self, buf, x, y, w, h):
                    self.blits.append(("buffer", w, h))

                def blit_dither(self, buf, x, y, w, h):
                    self.blits.append(("dither", w, h))
            TFT.mono = mono
            g = ui.UI(TFT(), object(), lambda: b"\x00", node_name="t",
                      trackball=False)
            g._screen_on = True
            g._viewing_image = b"\xff\xd8" + bytes(8)
            g._image_drawn = False
            g.draw_image(lambda: None, lambda: None)
            return g.tft.blits
        check("a mono panel gets the dithered blit", view(True) == [("dither", 4, 2)],
              str(view(True)))
        check("a colour panel keeps the plain blit", view(False) == [("buffer", 4, 2)],
              str(view(False)))
    finally:
        del sys.modules["tjpgd_fast_xtensawin"]

A = b"\xaa" * 16


def mk_with_chat(key, name, text="hi", hops=None):
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t", trackball=False)
    g._screen_on = True
    g.SW = 6                # the device's small font; the bare test UI has none
    g.add_peer(key, name, hops=hops)
    g.add_chat_message(key, False, text)
    g.selected_idx = g._peer_keys.index(key)
    g.drawn = []            # (text, y) of everything drawn, either font

    def text(font, t, x, y, fg, bg=None):
        g.drawn.append((t if isinstance(t, str) else bytes(t).decode("latin-1"), y))
    g.tft.text = text
    g._stext = lambda t, x, y, fg, bg=None: g.drawn.append((t, y))
    return g


def drawn_list_text(g):
    """The list rows only: the footer line is drawn below INPUT_Y."""
    return "\n".join(t for t, y in g.drawn if y < ui.INPUT_Y)


def drawn_footer_text(g):
    return "\n".join(t for t, y in g.drawn if y >= ui.INPUT_Y)


def test_v1_row_has_no_hash_and_no_preview():
    print("v1 row has no hash and no preview")
    g = mk_with_chat(b"\xaa" * 16, "low_tide", text="are you on the mesh")
    g.draw_node_list()
    t = drawn_list_text(g)
    check("no hash in the row", "aaaaaaaa" not in t, t)
    check("no message text", "are you on the mesh" not in t)


def test_v1_long_name_cut_at_28_on_every_row():
    print("v1 long names are cut at 28")
    g = mk_with_chat(b"\xbb" * 16, "x" * 40)
    g.draw_node_list()
    t = drawn_list_text(g)
    check("28 shown, not 29", ("x" * 28) in t and ("x" * 29) not in t, t)


def test_v1_footer_shows_hash_and_hops():
    print("v1 footer shows hash and hops")
    g = mk_with_chat(b"\xaa" * 16, "low_tide", hops=3)
    g.draw_node_list()
    check("hash and hops", "aaaaaaaa 3 hops" in drawn_footer_text(g), drawn_footer_text(g))


def test_v1_footer_new_hint_and_ping_precedence():
    print("v1 footer hint and ping precedence")
    g = mk_with_chat(b"\xaa" * 16, "low_tide", hops=3)
    g.ping_status = "ping 1.2s"
    g.draw_node_list()
    t = drawn_footer_text(g)
    check("ping result wins", "ping 1.2s" in t and "aaaaaaaa 3 hops" not in t, t)
    check("(M)new hint", ")new" in t.split("\n") and ")hash" not in t.split("\n"), t)


def test_new_row_is_drawn_and_has_no_footer_entry():
    print("the + New sentinel draws")
    g = mk_with_chat(A, "bob")
    check("the + New row is the list's first", g._peer_keys[0] == ui._NEW, repr(g._peer_keys))
    g.draw_node_list()
    check("label", "  + New chat" in drawn_list_text(g), drawn_list_text(g))
    check("hint when nothing heard", "search or add by hash" in drawn_list_text(g))
    g._heard[ui.TAB_MSG].update((1, 2))
    check("heard label", g._new_row_label() == "2 newly heard", g._new_row_label())


def test_age_guards():
    print("_age guards")
    check("future is blank", ui._age(_time.time() + 500) == "")
    check("over ten years is blank", ui._age(_time.time() - 10 * 366 * 86400) == "")
    check("5 minutes", ui._age(_time.time() - 300) == "5min")


def test_unnamed_peer_shows_its_hash_as_name():
    print("unnamed peer")
    g = mk_with_chat(b"\xc0" * 16, None)
    check("? hash", g._chat_cols(b"\xc0" * 16)[0] == "? c0c0c0c0", g._chat_cols(b"\xc0" * 16)[0])


def test_preview_marks_attachments():
    """The app's own text already leads with a marker (test_attachments.py has
    those); this is the fallback for an attachment whose text has none."""
    print("preview marks attachments")
    g = mk_with_chat(A, "a")
    g.add_chat_message(A, True, "look", image=b"x")
    check("mine with image", g._chat_cols(A)[1] == "me: [image] look", g._chat_cols(A)[1])
    g.add_chat_message(A, False, "", audio=b"x", audio_mode=0)
    check("voice only", g._chat_cols(A)[1] == "[voice]", g._chat_cols(A)[1])


if __name__ == "__main__":
    test_hint_hidden_without_mic()
    test_hint_hidden_while_navigating()
    test_recording_state_refuses_without_mic()
    test_battery_reads_through_the_board()
    test_unlit_battery_segments_are_visible_on_mono()
    test_tab_icons_share_a_height()
    test_tab_counts()
    test_active_tab_is_marked_on_the_colour_bar()
    test_selected_rows_invert_on_mono()
    test_image_viewer_dithers_on_a_mono_panel()
    test_v1_row_has_no_hash_and_no_preview()
    test_v1_long_name_cut_at_28_on_every_row()
    test_v1_footer_shows_hash_and_hops()
    test_v1_footer_new_hint_and_ping_precedence()
    test_new_row_is_drawn_and_has_no_footer_entry()
    test_age_guards()
    test_unnamed_peer_shows_its_hash_as_name()
    test_preview_marks_attachments()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

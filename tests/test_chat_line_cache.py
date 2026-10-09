# A message that arrives while its chat is not on screen shows up on reopen.
#
# The chat screen caches its word-wrapped lines, tagged with the peer they
# were built for. Leaving a chat keeps the tag, so the cache has to be dropped
# whenever that peer's history changes off-screen -- otherwise reopening the
# chat serves the old lines, and only a reply of your own (which invalidates
# from inside the chat) makes the missed message appear (issue #12).
#
# Run:  python3 tests/test_chat_line_cache.py

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
    def text(self, *a, **k):
        pass

    def fill_rect(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass


PEER = b"\x01" * 16
OTHER = b"\x02" * 16


def mkui():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t",
              trackball=False)
    g.add_peer(PEER, "alice")
    g.add_peer(OTHER, "bob")
    return g


def open_chat(g, key):
    g.state = ui.STATE_NODES
    g.selected_idx = g._peer_keys.index(key)
    g._enter_chat()


def texts(g):
    return " ".join(l[1] for l in g._build_chat_lines())


def test_chat_cursor_row_has_an_accent_bar():
    """The highlighted chat line used to differ only by its tinted
    background, which the Pro's 1-bit panel renders as paper, so the cursor
    could not be seen moving. A left-margin bar marks it on every board."""
    g = mkui()
    rects = []
    g.tft.fill_rect = lambda x, y, w, h, c: rects.append((x, y, w, h, c))
    for n in range(3):
        g.add_chat_message(PEER, False, "hello %d" % n)
    open_chat(g, PEER)
    g.draw()
    assert not [r for r in rects if r[2] == 3 and r[4] == g.NEON_MAG], "no cursor, no bar"
    g.nav_event("down")                       # alt+X on the Pro
    g.handle_trackball()
    del rects[:]
    g.draw()
    bars = [r for r in rects if r[2] == 3 and r[4] == g.NEON_MAG]
    assert len(bars) == 1, bars
    assert bars[0][0] == 0 and bars[0][3] == ui.CHAR_H
    print("ok test_chat_cursor_row_has_an_accent_bar")


def test_enter_on_a_media_line_opens_it():
    """The Pro has no trackball to click a highlighted [image] or [voice]
    line. With nothing typed, Enter does what the click does."""
    g = mkui()
    g.add_chat_message(PEER, False, "look", image=b"\xff\xd8jpeg")
    played = []
    g.on_audio_play = lambda data, mode: played.append((data, mode))
    open_chat(g, PEER)
    g.draw()
    g.nav_event("down")                       # alt+X: cursor onto the first line
    g.handle_trackball()
    g.draw()
    assert g.chat_cursor in g._visible_image_lines, (g.chat_cursor, g._visible_image_lines)
    g.handle_key(b"\r")
    assert g.state == ui.STATE_IMAGE, g.state
    assert g._viewing_image == b"\xff\xd8jpeg"
    print("ok test_enter_on_a_media_line_opens_it")

    g2 = mkui()
    g2.add_chat_message(PEER, False, "hi")
    open_chat(g2, PEER)
    g2.draw()
    sent = []
    g2.on_send = lambda peer, text, mid: sent.append(text)
    for c in b"yo":
        g2.handle_key(bytes([c]))
    g2.handle_key(b"\r")
    assert sent == ["yo"], sent
    assert g2.state == ui.STATE_CHAT
    print("ok test_enter_with_text_still_sends")


def test_newlines_in_a_message_break_lines():
    """Sideband and MeshChat send multi-line messages. Each paragraph wraps on
    its own, an empty one keeps a blank row, and the status suffix lands on
    the very last line."""
    g = mkui()
    g.add_chat_message(PEER, False, "first line\nsecond\n\nfourth after a blank")
    g.add_chat_message(PEER, True, "mine\r\nalso mine", status=2)
    open_chat(g, PEER)
    rows = [l[1] for l in g._build_chat_lines()]
    assert rows[:4] == ["alice> first line", "second", "", "fourth after a blank"], rows
    assert rows[4] == "me> mine", rows
    assert rows[5].startswith("also mine") and rows[5].endswith(" \xfb"), rows
    firsts = [l[2] for l in g._build_chat_lines()]
    assert firsts == [True, False, False, False, True, False], firsts
    print("ok test_newlines_in_a_message_break_lines")


def test_message_while_on_node_list():
    g = mkui()
    open_chat(g, PEER)
    g.add_chat_message(PEER, False, "first")
    check("first message visible in open chat", "first" in texts(g))
    g.state = ui.STATE_NODES                  # back to the node list
    g.add_chat_message(PEER, False, "missed")
    open_chat(g, PEER)
    check("message received on node list shows on reopen",
          "missed" in texts(g), texts(g))


def test_message_while_recording():
    # Recording returns straight to STATE_CHAT without _enter_chat.
    g = mkui()
    open_chat(g, PEER)
    g.add_chat_message(PEER, False, "first")
    texts(g)
    g.state = ui.STATE_RECORDING
    g.add_chat_message(PEER, False, "during-rec")
    g.state = ui.STATE_CHAT
    check("message received while recording shows after",
          "during-rec" in texts(g), texts(g))


def test_status_update_off_screen():
    g = mkui()
    open_chat(g, PEER)
    mid = g.add_chat_message(PEER, True, "hello", status=1)
    check("pending suffix shown", texts(g).endswith(".."), texts(g))
    g.state = ui.STATE_NODES
    g.update_message_status(PEER, mid, 2)
    open_chat(g, PEER)
    check("delivered suffix shown after reopen",
          texts(g).endswith("\xfb"), repr(texts(g)))


def test_other_peer_does_not_disturb():
    g = mkui()
    open_chat(g, PEER)
    g.add_chat_message(PEER, False, "mine")
    lines = g._build_chat_lines()
    g.add_chat_message(OTHER, False, "elsewhere")
    check("cache kept when another peer gets a message",
          g._build_chat_lines() is lines)


def test_scrolled_up_keeps_position():
    g = mkui()
    open_chat(g, PEER)
    for i in range(10):
        g.add_chat_message(PEER, False, "m%d" % i)
    g._build_chat_lines()
    g.chat_scroll = 5
    g.add_chat_message(PEER, False, "new")
    check("scrolled-up reader keeps position", g.chat_scroll == 6, g.chat_scroll)
    check("new message in lines", "new" in texts(g))


for fn in (test_message_while_on_node_list, test_message_while_recording,
           test_status_update_off_screen, test_other_peer_does_not_disturb,
           test_scrolled_up_keeps_position, test_chat_cursor_row_has_an_accent_bar,
           test_enter_on_a_media_line_opens_it, test_newlines_in_a_message_break_lines):
    print(fn.__name__)
    fn()

if _failures:
    print("FAILED:", len(_failures))
    sys.exit(1)
print("all ok")

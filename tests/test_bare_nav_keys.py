# e and x move the selection where nothing is being typed.
#
# Navigation lives on the alt layer -- alt+E and alt+X -- behind a sticky
# modifier, so moving one row down a list is two keystrokes on a device whose
# main screen is a list. On a screen with no text field there is nothing for a
# bare e or x to collide with: the node list binds a, s, b, m, d and p, the
# browser r, n, p, b, g and G, and neither takes e or x.
#
# The rule is not "e and x are arrows" but "e and x are arrows wherever the
# screen is not accepting text". These tests hold both halves, because the
# expensive failure is the second one: a bare letter that stops reaching the
# message you are typing.
#
# Run:  python3 tests/test_bare_nav_keys.py

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


def mkui(state, page=None):
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t",
              trackball=False)
    g.state = state
    if page is not None:
        g._settings_page = page
    g.selected_peer = b"\x01" * 8
    g.chat_cursor = -1
    g.cmd_buf = bytearray()
    return g


def moved(g):
    """(up, down) counted since the last call, the way handle_trackball reads
    them."""
    u, d = g._irq_up, g._irq_down
    g._irq_up = g._irq_down = 0
    return u, d


def test_e_and_x_move_on_the_node_list():
    print("the node list moves on a bare e and x")
    g = mkui(ui.STATE_NODES)
    g.handle_key(b'e')
    check("e moves up", moved(g) == (1, 0))
    g.handle_key(b'x')
    check("x moves down", moved(g) == (0, 1))
    g.handle_key(b'E')
    check("shifted E moves too", moved(g) == (1, 0))
    g.handle_key(b'X')
    check("shifted X moves too", moved(g) == (0, 1))


def test_the_other_hotkeys_still_work():
    """e and x are only free because nothing else claimed them. If a future
    binding takes one, this is where it should show up."""
    print("the node list's own hotkeys are untouched")
    g = mkui(ui.STATE_NODES)
    fired = []
    g.on_announce = lambda: fired.append("announce")
    g.handle_key(b'a')
    check("a still announces", fired == ["announce"], str(fired))
    check("and did not move the selection", moved(g) == (0, 0))

    g.handle_key(b's')
    check("s still opens settings", g.state == ui.STATE_SETTINGS)


def test_settings_main_moves_but_its_text_pages_type():
    print("settings moves on the menu and types in its fields")
    g = mkui(ui.STATE_SETTINGS, ui._SET_MAIN)
    g.handle_key(b'x')
    check("the settings menu moves on x", moved(g) == (0, 1))

    for page, name in ((ui._SET_NODE_NAME, "node name"),
                       (ui._SET_WIFI_PASS, "wifi password"),
                       (ui._SET_TCP_HOST, "tcp host")):
        g = mkui(ui.STATE_SETTINGS, page)
        g.cmd_buf = bytearray()
        g.handle_key(b'e')
        check("the %s field types an e" % name,
              moved(g) == (0, 0) and b'e' in g.cmd_buf, repr(bytes(g.cmd_buf)))

    # The frequency field is digits only, so it drops an e rather than typing
    # it. What matters is that it drops it instead of moving the selection --
    # a numeric field is still a field.
    g = mkui(ui.STATE_SETTINGS, ui._SET_LORA_FREQ)
    g.cmd_buf = bytearray()
    g.handle_key(b'e')
    check("the frequency field ignores a letter rather than navigating",
          moved(g) == (0, 0) and bytes(g.cmd_buf) == b'', repr(bytes(g.cmd_buf)))
    g.handle_key(b'9')
    check("and still takes digits", bytes(g.cmd_buf) == b'9', repr(bytes(g.cmd_buf)))


def test_chat_still_types():
    """The expensive regression: a bare letter that stops reaching the message
    being composed. Chat navigates message history AND types, so it keeps the
    alt layer."""
    print("chat still types e and x")
    g = mkui(ui.STATE_CHAT)
    for k in (b'e', b'x', b'E', b'X'):
        g.handle_key(k)
    check("all four letters landed in the message",
          bytes(g.cmd_buf) == b'exEX', repr(bytes(g.cmd_buf)))
    check("and none of them moved the selection", moved(g) == (0, 0))


def test_the_shell_still_types():
    print("the shell still types e and x")
    g = mkui(ui.STATE_SHELL)
    check("the shell is accepting text", g._accepting_text() is True)
    g.handle_key(b'e')
    check("e did not move the selection", moved(g) == (0, 0))


def test_the_image_view_still_closes_on_any_key():
    """Any key exits the image, e and x included -- the nav mapping must not
    swallow the keypress that closes it."""
    print("the image view still closes on e")
    g = mkui(ui.STATE_IMAGE)
    g.handle_key(b'e')
    check("e closed the image", g.state != ui.STATE_IMAGE)
    check("and did not move a selection behind it", moved(g) == (0, 0))


def test_alt_arrows_are_unaffected():
    """The alt layer keeps working everywhere, including where a bare letter
    has to stay a letter. Nothing is taken away by this."""
    print("alt+E and alt+X still work, including in chat")
    g = mkui(ui.STATE_CHAT)
    g.nav_event("up")
    g.nav_event("down")
    check("alt arrows still drive the counters in chat", moved(g) == (1, 1))


if __name__ == "__main__":
    test_e_and_x_move_on_the_node_list()
    test_the_other_hotkeys_still_work()
    test_settings_main_moves_but_its_text_pages_type()
    test_chat_still_types()
    test_the_shell_still_types()
    test_the_image_view_still_closes_on_any_key()
    test_alt_arrows_are_unaffected()
    print()
    if _failures:
        print("%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("all passed")

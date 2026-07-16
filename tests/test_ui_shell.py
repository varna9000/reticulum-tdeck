# Host-side smoke test for the SSH tab + rnsh shell screen (STATE_SHELL).
# Shims MicroPython modules so the REAL ui.py + terminal.py run under CPython.
# Run:  python3 tests/test_ui_shell.py

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
    def __init__(self):
        self.calls = []

    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, str):
            s.encode("ascii")
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.calls.append(("text", s, x, y))

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)

    def fill(self, c):
        pass


def _mkui():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="t")
    g._screen_on = True
    g.sent = []
    g.connects = []
    g.disconnects = []
    g.on_shell_input = lambda d: g.sent.append(bytes(d))
    g.on_shell_connect = lambda d, c, r: g.connects.append((d, c, r))
    g.on_shell_disconnect = lambda: g.disconnects.append(True)
    g.on_shell_seed = lambda: None
    return g


def _type(g, s):
    for c in s:
        g.handle_key(bytes([c]) if isinstance(c, int) else c.encode())


def test_trackball_cycles_into_ssh_tab():
    # Regression: the trackball must reach the SSH tab (was capped at MSG/NET).
    g = _mkui()
    assert g.node_tab == ui.TAB_MSG
    g._irq_right = 1; g.handle_trackball()
    assert g.node_tab == ui.TAB_NET
    g._irq_right = 1; g.handle_trackball()
    assert g.node_tab == ui.TAB_SSH          # previously unreachable
    g._irq_right = 1; g.handle_trackball()
    assert g.node_tab == ui.TAB_MSG          # wraps around
    g._irq_left = 1; g.handle_trackball()
    assert g.node_tab == ui.TAB_SSH          # left wraps back to SSH


def test_ssh_tab_lists_nodes():
    g = _mkui()
    g._switch_tab(ui.TAB_SSH)
    assert g.node_tab == ui.TAB_SSH
    g.add_shell_node(b"\xa3" * 16, hops=2)
    g.draw()
    # SSH(1) in the tab bar; the hash prefix shows as the row name
    assert any("SSH(1)" in str(c[1]) for c in g.tft.calls if c[0] == "text")
    assert g._shell_keys == [b"\xa3" * 16]


def test_manual_hash_entry_connects():
    g = _mkui()
    g._switch_tab(ui.TAB_SSH)
    g.handle_key(b"m")
    assert g._shell_manual
    _type(g, "aa" * 16)               # 32 hex chars
    g.handle_key(b"\r")
    assert g.connects and g.connects[-1][0] == bytes([0xAA] * 16)
    assert g.state == ui.STATE_SHELL
    assert g._terminal is not None


def test_manual_bad_hash_rejected():
    g = _mkui()
    g._switch_tab(ui.TAB_SSH)
    g.handle_key(b"m")
    _type(g, "xyz")                   # non-hex ignored by the input filter
    assert len(g._shell_hex) == 0
    _type(g, "ab")                    # too short
    g.handle_key(b"\r")
    assert not g.connects
    assert g._shell_manual            # stays in entry mode
    assert "bad hash" in (g._shell_status or "")


def test_line_mode_sends_line():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    assert g.state == ui.STATE_SHELL and g._shell_line_mode
    _type(g, "ls -la")
    g.handle_key(b"\r")
    assert g.sent[-1] == b"ls -la\n", g.sent


def test_tilde_dot_disconnects():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    _type(g, "~.")
    g.handle_key(b"\r")
    assert g.disconnects
    assert g.state == ui.STATE_NODES and g.node_tab == ui.TAB_SSH


def test_char_mode_toggle_and_send():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    _type(g, "~l")                    # toggle to char mode
    g.handle_key(b"\r")
    assert not g._shell_line_mode
    g.handle_key(b"x")                # sent immediately, raw
    assert g.sent[-1] == b"x"
    g.handle_key(b"\r")               # Enter -> CR
    assert g.sent[-1] == b"\r"


def test_control_byte_passthrough():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)      # line mode
    g.handle_key(bytes([0x03]))       # Ctrl-C
    assert g.sent[-1] == b"\x03"
    g.handle_key(bytes([0x04]))       # Ctrl-D
    assert g.sent[-1] == b"\x04"


def test_trackball_scrolls_scrollback():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    # fill enough lines to have scrollback
    g.shell_feed(1, ("\r\n".join("line%d" % i for i in range(40)) + "\r\n").encode())
    assert g._shell_view == 0                 # starts at the live bottom
    g._scroll_up()
    assert g._shell_view == 1                 # trackball up -> older, NOT an arrow send
    assert g.sent == []                       # nothing sent to the remote
    g._scroll_up(); g._scroll_up()
    assert g._shell_view == 3
    g._scroll_down()
    assert g._shell_view == 2
    # page scroll (trackball left = page toward older)
    g._shell_scroll(ui.BODY_ROWS - 1)
    assert g._shell_view == 2 + (ui.BODY_ROWS - 1)
    # scroll clamps at the top and never goes negative
    for _ in range(100):
        g._scroll_up()
    assert g._shell_view == max(0, len(g._terminal.lines) - ui.BODY_ROWS)
    for _ in range(100):
        g._scroll_down()
    assert g._shell_view == 0


def test_typing_snaps_to_bottom():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    g.shell_feed(1, ("\r\n".join("x%d" % i for i in range(40)) + "\r\n").encode())
    g._scroll_up(); g._scroll_up()
    assert g._shell_view == 2
    g.handle_key(b"l")                        # typing a char snaps back to bottom
    assert g._shell_view == 0
    assert g._shell_input == b"l"


def test_backspace_empty_exits_line_mode():
    # The T-Deck keyboard has no Esc key, so empty-input Backspace leaves the
    # shell (same convention as chat/browser).
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    assert g.state == ui.STATE_SHELL and g._shell_line_mode
    # Backspace with text edits the line (does not exit)
    g.handle_key(b"a"); g.handle_key(b"b")
    g.handle_key(b"\x08")
    assert g._shell_input == b"a" and g.state == ui.STATE_SHELL
    g.handle_key(b"\x08")                      # now empty
    assert g._shell_input == b""
    assert g.state == ui.STATE_SHELL           # still in shell (single empty bksp)
    # empty + Backspace past the 500ms entry guard -> leave
    g._state_change_ms = 0
    g.handle_key(b"\x08")
    assert g.disconnects
    assert g.state == ui.STATE_NODES and g.node_tab == ui.TAB_SSH


def _click(g):
    g._irq_click = 1
    g.handle_trackball()


def test_ctrl_menu_open_and_send_ctrl_c():
    # The keyboard has no Ctrl/Esc/~ keys, so control keys come from a
    # trackball-click menu. Ctrl-C must be sendable this way.
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    assert not g._shell_menu
    _click(g)                                   # open the control menu
    assert g._shell_menu and g._shell_menu_idx == 0
    g.draw()                                    # menu renders without bad coords
    assert any("Ctrl-C" in str(c[1]) for c in g.tft.calls if c[0] == "text")
    _click(g)                                   # first item = Ctrl-C -> send \x03
    assert not g._shell_menu
    assert g.sent[-1] == b"\x03"


def test_ctrl_menu_navigate_and_send():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    _click(g)                                   # open
    g._scroll_down()                            # move to Ctrl-D
    g._scroll_down()                            # Ctrl-Z
    g._scroll_down()                            # Tab
    _click(g)                                   # send Tab
    assert g.sent[-1] == b"\t"
    # Up-arrow item sends the escape sequence
    _click(g); g._scroll_down(); g._scroll_down(); g._scroll_down(); g._scroll_down()
    # idx now at "Up history" (index 4)
    assert ui._SHELL_CTRL_ITEMS[g._shell_menu_idx][0].startswith("Up")
    _click(g)
    assert g.sent[-1] == b"\x1b[A"


def test_ctrl_menu_mode_toggle_and_quit():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    assert g._shell_line_mode
    _click(g)
    # navigate to "Line/Char mode"
    while not ui._SHELL_CTRL_ITEMS[g._shell_menu_idx][1] == "mode":
        g._scroll_down()
    _click(g)
    assert not g._shell_line_mode               # toggled to char mode
    # quit via the menu
    _click(g)
    while not ui._SHELL_CTRL_ITEMS[g._shell_menu_idx][1] == "quit":
        g._scroll_down()
    _click(g)
    assert g.disconnects and g.state == ui.STATE_NODES


def test_ctrl_menu_backspace_closes():
    g = _mkui()
    g._start_shell(b"\xbb" * 16)
    _click(g)
    assert g._shell_menu
    g.handle_key(b"\x08")                       # Backspace closes the menu
    assert not g._shell_menu and g.state == ui.STATE_SHELL


def test_feed_renders_and_draws():
    g = _mkui()
    g._start_shell(b"\xcc" * 16)
    g.shell_connected()
    g.shell_feed(1, b"hello\r\nworld\r\n")
    assert "hello" in g._terminal.lines
    assert "world" in g._terminal.lines
    g.draw()   # must not raise
    assert any(str(c[1]).find("hello") >= 0 for c in g.tft.calls if c[0] == "text")


def test_exit_then_any_key_leaves():
    g = _mkui()
    g._start_shell(b"\xcc" * 16)
    g.shell_connected()
    g.shell_exited(0)
    assert g._shell_status and "exited" in g._shell_status
    g.handle_key(b"x")
    assert g.state == ui.STATE_NODES


if __name__ == "__main__":
    n = 0
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
            print("ok", name)
            n += 1
    print("all %d ui-shell tests passed" % n)

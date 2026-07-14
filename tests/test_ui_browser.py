# Host-side smoke test for the tabbed node screen + browser page view.
# Shims MicroPython modules (machine, uasyncio, time.ticks_*) so the REAL
# ui.py runs under CPython with a recording fake display. Run:
#   python3 tests/test_ui_browser.py

import os
import sys
import types
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- MicroPython shims (before importing ui) -------------------------------
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
import micron


class FakeTFT:
    """Records draw calls; catches bad coordinates and non-latin1 text."""

    def __init__(self):
        self.calls = []

    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, str):
            s.encode("ascii")  # C driver rejects chars > 0xFF
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.calls.append(("text", s, x, y))

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.calls.append(("rect", x, y, w, h))

    def fill(self, c):
        self.calls.append(("fill", c))


def _mkui():
    tft = FakeTFT()
    g = ui.UI(tft, object(), lambda: b"\x00", node_name="test")
    g._screen_on = True
    return g, tft


PAGE = (
    ">Test Node\n"
    "Some `F0f0colored`f text that should wrap around the narrow display "
    "just fine and produce several rows of content.\n"
    "-\n"
    "`[Board`:/page/board.mu]\n"
    "plain line\n"
    "`[Files`:/page/files.mu]\n"
)


def test_tabbed_node_list_draws():
    g, tft = _mkui()
    g.add_peer(b"\x01" * 16, "peer-one", rssi=-80, hops=1)
    g.add_nomad_node(b"\x02" * 16, "node-two", hops=2)
    g.draw()                      # MSG tab
    assert any("MSG(1)" in str(c[1]) for c in tft.calls if c[0] == "text")
    g._switch_tab(1)
    g.draw()                      # NET tab
    assert any("node-two" in str(c[1]) for c in tft.calls if c[0] == "text")
    assert g.node_tab == 1


def test_net_seed_called_once_on_tab_switch():
    g, _ = _mkui()
    calls = []
    g.on_net_seed = lambda: calls.append(1)
    g._switch_tab(1)
    g._switch_tab(0)
    g._switch_tab(1)
    assert len(calls) == 2  # seed guard lives in nomad_browser, UI calls each switch


def test_open_node_enters_browser():
    g, _ = _mkui()
    opened = []
    g.on_browse = lambda dest: opened.append(dest)
    g.add_nomad_node(b"\x02" * 16, "node-two")
    g._switch_tab(1)
    g._open_selected_node()
    assert opened == [b"\x02" * 16]
    assert g.state == ui.STATE_BROWSER
    assert g.browser_status == "connecting..."
    g.draw()  # empty page + status footer must draw cleanly


def test_show_page_and_draw():
    g, tft = _mkui()
    lines, links = micron.render(PAGE, 40)
    g.show_page("node-two", "/page/index.mu", lines, links, can_back=False)
    assert g.state == ui.STATE_BROWSER
    g.draw()
    texts = [str(c[1]) for c in tft.calls if c[0] == "text"]
    assert any("Test Node" in t for t in texts)
    assert any("Board" in t for t in texts)


def test_cursor_and_link_follow():
    g, _ = _mkui()
    lines, links = micron.render(PAGE, 40)
    g.show_page("n", "/page/index.mu", lines, links)
    g.draw()
    followed = []
    g.on_browse_follow = lambda url: followed.append(url)
    g._jump_next_link()
    g.draw()
    assert g.browser_cursor >= 0
    assert g.browser_cursor in g._browser_link_rows
    g._browser_follow_cursor()
    assert followed == [":/page/board.mu"]
    # next link
    g._jump_next_link()
    g.draw()
    g._browser_follow_cursor()
    assert followed[-1] == ":/page/files.mu"


def test_scroll_clamps():
    g, _ = _mkui()
    lines, links = micron.render("\n".join("row %d" % i for i in range(40)), 40)
    g.show_page("n", "/p", lines, links)
    for _ in range(100):
        g._scroll_down()
    g.draw()
    assert g.browser_scroll == len(lines) - (ui.BODY_ROWS - 1)
    for _ in range(200):
        g._scroll_up()
    assert g.browser_scroll == 0
    g.draw()


def test_back_exits_to_net_tab():
    g, _ = _mkui()
    exited = []
    g.on_browser_exit = lambda: exited.append(1)
    g.on_browse_back = lambda: False  # stack bottom
    lines, links = micron.render(PAGE, 40)
    g.show_page("n", "/p", lines, links)
    g._browser_back()
    assert exited == [1]
    assert g.state == ui.STATE_NODES
    assert g.node_tab == 1
    g.draw()


def test_trackball_dominance_filter():
    g, _ = _mkui()
    g.add_nomad_node(b"\x02" * 16, "n")
    # vertical-dominant cycle: horizontal pulses discarded
    g._irq_up = 3
    g._irq_left = 1
    g.handle_trackball()
    assert g.node_tab == 0
    # horizontal-dominant cycle: switches tab
    g._irq_right = 2
    g._irq_up = 1
    g.handle_trackball()
    assert g.node_tab == 1
    # left goes back to MSG
    g._irq_left = 1
    g.handle_trackball()
    assert g.node_tab == 0


def test_browser_left_goes_back():
    g, _ = _mkui()
    backs = []
    g.on_browse_back = lambda: backs.append(1) or True
    lines, links = micron.render(PAGE, 40)
    g.show_page("n", "/p", lines, links)
    g._irq_left = 1
    g.handle_trackball()
    assert backs == [1]
    assert g.state == ui.STATE_BROWSER  # back() True -> stays in browser


def test_cyrillic_transcodes_to_glyph_bytes():
    # А-Я -> 0x80.., а-п -> 0xA0.., р-я -> 0xE0.., ё -> 0xF1, ѝ -> 0xFD
    out = ui.UI._tb("Аяё ѝ z\xfb中")
    assert out == bytes([0x80, 0xEF, 0xF1, 0x20, 0xFD, 0x20, 0x7A, 0xFB, 0x3F])
    # _ascii keeps Cyrillic now, still strips emoji/CJK
    assert ui._ascii("Здравей \U0001F600 свят") == "Здравей свят"


def test_cyrillic_page_renders():
    g, tft = _mkui()
    g.add_nomad_node(b"\x02" * 16, "Възел БГ")  # Cyrillic node name
    g._switch_tab(1)
    g.draw()
    lines, links = micron.render("Здравей свят\n`[Още`:/page/more.mu]", 40)
    g.show_page("Възел БГ", "/page/index.mu", lines, links)
    g.draw()  # FakeTFT rejects any non-latin1 str / bad coords
    # dynamic rows reach the driver as glyph bytes, never > 0xFF
    byte_calls = [c[1] for c in tft.calls if c[0] == "text" and isinstance(c[1], bytes)]
    assert byte_calls and all(max(b) < 0x100 for b in byte_calls if b)


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

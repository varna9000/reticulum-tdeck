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


def _chat_ui(peer=b"\x02" * 16):
    g, tft = _mkui()
    g.add_peer(peer, "peer")
    g.selected_peer = peer
    g.state = ui.STATE_CHAT
    return g, tft, peer


def test_input_tail_scroll():
    g, tft = _mkui()
    g.selected_peer = b"\x01" * 16
    g.state = ui.STATE_CHAT
    g.cmd_buf = bytearray(b"x" * 60)
    g.draw_input()
    joined = b"".join(c[1] for c in tft.calls
                      if c[0] == "text" and isinstance(c[1], (bytes, bytearray)))
    txt = joined.decode("latin-1")
    assert "<" in txt          # continuation marker shown
    assert "xxxxx" in txt      # tail of the buffer, not the (also x) head — but caret present
    assert txt.rstrip(" ").endswith("_")


def test_record_key_is_zero_only():
    g, _, peer = _chat_ui()
    started = []
    g.on_record_start = lambda: started.append(1)
    g.handle_key(b"r")
    assert bytes(g.cmd_buf) == b"r" and started == []   # 'r' types
    g.cmd_buf = bytearray()
    g.handle_key(b"0")
    assert started == [1]                                # '0' records


def test_peer_eviction_least_recently_seen():
    g, _ = _mkui()
    keys = [bytes([i]) * 16 for i in range(1, ui.MAX_PEERS + 1)]
    for i, k in enumerate(keys):
        g.add_peer(k, "p%d" % i)
        g.peers[k]["seen"] = 1000 + i          # keys[0] oldest
    g.add_chat_message(keys[5], False, "hi")   # a RECENT peer bubbles to front
    assert g._peer_keys[0] == keys[5]
    new = bytes([200]) * 16
    g.add_peer(new, "new")
    assert len(g.peers) == ui.MAX_PEERS
    assert keys[0] not in g.peers               # oldest evicted (not the bubbled one)
    assert keys[5] in g.peers                   # bubbled-recent survived
    assert new in g.peers
    assert keys[0] not in g.chat_history and keys[0] not in g._peer_keys


def test_selection_follows_bubbled_peer():
    g, _ = _mkui()
    for i in range(3):
        g.add_peer(bytes([i + 1]) * 16, "p%d" % i)
    g.state = ui.STATE_NODES
    g.selected_idx = 0                          # on p0
    sel = g._peer_keys[0]
    g.add_chat_message(bytes([3]) * 16, False, "hi")  # p2 bubbles to front
    assert g._peer_keys[0] == bytes([3]) * 16
    assert g._peer_keys[g.selected_idx] == sel  # cursor stayed on p0


def test_chat_scroll_anchor():
    g, _, peer = _chat_ui()
    for i in range(20):
        g.add_chat_message(peer, False, "msg %d" % i)
    g.chat_scroll = 5
    n0 = len(g._build_chat_lines())
    g.add_chat_message(peer, False, "incoming while scrolled up")
    n1 = len(g._build_chat_lines())
    assert g.chat_scroll == 5 + (n1 - n0)       # position preserved
    g.chat_scroll = 0
    g.add_chat_message(peer, False, "at bottom")
    assert g.chat_scroll == 0                   # stays at bottom
    g.chat_scroll = 5
    g.add_chat_message(peer, True, "my own send")
    assert g.chat_scroll == 0                   # own send snaps to bottom


def test_chat_scroll_up_keeps_full_window():
    # Scrolling to the top must keep a full 11-row window (lines[0:rows]),
    # never shrink it and drop messages off the bottom.
    g, tft, peer = _chat_ui()
    for i in range(25):
        g.add_chat_message(peer, False, "line %d" % i)
    for _ in range(200):
        g._scroll_up()
    total = len(g._build_chat_lines())
    rows = ui.BODY_ROWS - 1
    assert g.chat_scroll == max(0, total - rows)   # clamped at the true top
    g.draw()
    # window is full: view_end - view_start == rows
    view_end = total - g.chat_scroll
    view_start = max(0, view_end - rows)
    assert view_end - view_start == rows


def test_delete_selected_peer():
    g, _ = _mkui()
    deleted = []
    g.on_delete_peer = lambda k: deleted.append(k)
    a, b = b"\x01" * 16, b"\x02" * 16
    g.add_peer(a, "a")
    g.add_peer(b, "b")
    g.add_chat_message(a, False, "hi")          # a -> front, unread
    g.selected_idx = 0
    key = g._peer_keys[0]
    g.delete_selected()
    assert key not in g.peers and key not in g._peer_keys
    assert key not in g.chat_history and key not in g.unread
    assert deleted == [key]


def test_marker_span_metadata():
    assert ui.UI._marker_span("me> [voice 3s]", "voice") == (4, 10)
    assert ui.UI._marker_span("p> [image 42k] hi", "image") == (3, 11)
    assert ui.UI._marker_span("plain text", "voice") == (-1, 0)


def test_footer_hops_label():
    g, tft = _mkui()
    g.add_peer(b"\x01" * 16, "p", rssi=-80, hops=3)
    g.draw()
    texts = [c[1] for c in tft.calls if c[0] == "text" and isinstance(c[1], str)]
    assert any("3hp" in t for t in texts)       # hops labelled 'hp', not 'h'


def test_settings_timeout_and_volume_adjust():
    g, _ = _mkui()
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    saved_t, saved_v = [], []
    g.on_screen_timeout = lambda ms: saved_t.append(ms)
    g.on_volume = lambda v: saved_v.append(v)
    g._settings_idx = 7                          # Sleep
    t0 = g._screen_timeout_ms
    g._settings_adjust(1)
    assert g._screen_timeout_ms != t0 and saved_t[-1] == g._screen_timeout_ms
    g._settings_idx = 4                          # Volume
    g._volume = 5
    g._settings_adjust(1)
    assert g._volume == 6 and saved_v[-1] == 6
    g._volume = 10
    g._settings_adjust(1)
    assert g._volume == 10                       # clamped
    g._settings_idx = 0
    for _ in range(20):
        g._settings_scroll_down()
    assert g._settings_idx == 10                  # 11 items now (LoRa cfg added)


def test_browser_prev_next_and_paging():
    g, _ = _mkui()
    lines, links = micron.render(PAGE, 40)
    g.show_page("n", "/p", lines, links)
    g.draw()
    followed = []
    g.on_browse_follow = lambda u: followed.append(u)
    g._jump_next_link(); g.draw()
    g._jump_next_link(); g.draw()
    g._jump_prev_link(); g.draw()
    g._browser_follow_cursor()
    assert followed[-1] == ":/page/board.mu"     # prev returned to first link

    lines2, _ = micron.render("\n".join("row %d" % i for i in range(40)), 40)
    g.show_page("n", "/p2", lines2, [])
    g._browser_page(ui.BODY_ROWS - 2)
    assert g.browser_scroll == ui.BODY_ROWS - 2
    g._browser_goto(False)
    assert g.browser_scroll == max(0, len(lines2) - (ui.BODY_ROWS - 1))
    g._browser_goto(True)
    assert g.browser_scroll == 0


def test_wifi_result_flow():
    g, _ = _mkui()
    g._wifi_ssid = "net"
    g._wifi_connecting = True
    g.set_wifi_result("10.0.0.5")
    assert g._wifi_connected and g._wifi_ip == "10.0.0.5"
    assert g._settings_page == ui._SET_TCP_HOST and not g._wifi_connecting
    g._wifi_connecting = True
    g.set_wifi_result(None)
    assert g._wifi_err == "connect failed"
    assert g._settings_page == ui._SET_WIFI_SCAN and not g._wifi_connecting


def test_all_screens_and_radio_scroll_draw():
    g, tft = _mkui()
    g.add_peer(b"\x01" * 16, "p")
    g.draw()                                     # node list
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    g._prev_state = -1
    g.draw()                                     # settings
    g.selected_peer = b"\x01" * 16
    g.state = ui.STATE_CHAT
    g._prev_state = -1
    g.add_chat_message(b"\x01" * 16, False, "hi")
    g.draw()                                     # chat
    g.get_radio_stats = lambda: [("k%d" % i, str(i)) for i in range(14)]
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_RADIO
    g._prev_state = -1
    g.draw()                                     # radio (14 rows > 11 visible)
    assert g._radio_rows == 14
    g._settings_scroll_down()
    assert g._settings_scroll == 1
    g.state = ui.STATE_RECORDING
    g._rec_warming = False                       # capture phase (static screen)
    g._prev_state = -1
    g.draw()                                     # recording
    joined = b"".join(c[1] for c in tft.calls
                      if c[0] == "text" and isinstance(c[1], (bytes, bytearray)))
    assert b"Recording" in joined


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

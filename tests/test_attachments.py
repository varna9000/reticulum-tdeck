# Host-side test for LXMF attachment handling: the parsing half is lifted
# out of tdeck_node.py (which claims the radio at import time), the drawing
# half runs the REAL ui.py against a recording fake display. Run:
#   python3 tests/test_attachments.py

import ast
import os
import sys
import types
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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

import ui

FIELD_FILE_ATTACHMENTS = 0x05
FIELD_IMAGE = 0x06
FIELD_AUDIO = 0x07


def _load_attach_helpers():
    """Importing tdeck_node would bring up SPI, LoRa and the display, so
    compile only its pure attachment helpers into a namespace of our own."""
    with open(os.path.join(ROOT, "tdeck_node.py")) as f:
        tree = ast.parse(f.read())
    funcs = {"_sniff_image", "_kb", "_image_marker", "_with_markers",
             "_parse_attachments"}
    consts = {"_VIEWABLE_IMG"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in funcs:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in consts for t in node.targets):
            body.append(node)
    assert len(body) == len(funcs) + len(consts), \
        "attachment helpers moved or were renamed — update this test"
    ns = {"DEBUG": 0, "FIELD_IMAGE": FIELD_IMAGE, "FIELD_AUDIO": FIELD_AUDIO,
          "FIELD_FILE_ATTACHMENTS": FIELD_FILE_ATTACHMENTS}
    exec(compile(ast.Module(body=body, type_ignores=[]), "tdeck_node.py", "exec"), ns)
    return ns


A = _load_attach_helpers()

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 3000          # ~3k
WEBP = b"RIFF\x00\x10\x00\x00WEBPVP8 " + b"\x00" * 3000
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 3000
PDF = b"%PDF-1.4\n" + b"\x00" * 1200
C2_3200 = b"\x01" * 400   # 8 B/frame, 20 ms/frame -> 50 frames -> 1 s


# --- Parsing ---------------------------------------------------------------

def test_meshchat_jpeg_type_is_accepted():
    # MeshChat labels the field from the browser MIME type: image/jpeg ->
    # "jpeg". The old allow-list only knew "jpg" and dropped these whole.
    img, aud, mode, markers = A["_parse_attachments"]({FIELD_IMAGE: ["jpeg", JPEG]})
    assert img == JPEG
    assert markers == ["[image 2k]"]
    assert aud is None and mode is None


def test_unknown_declared_type_trusts_the_bytes():
    for declared in ("image/jpeg", "JPG", "", b"jpeg", None):
        img, _, _, markers = A["_parse_attachments"]({FIELD_IMAGE: [declared, JPEG]})
        assert img == JPEG, declared
        assert markers == ["[image 2k]"], declared


def test_webp_accepted():
    img, _, _, markers = A["_parse_attachments"]({FIELD_IMAGE: ["webp", WEBP]})
    assert img == WEBP
    assert markers == ["[image 2k]"]


def test_undecodable_format_is_named():
    _, _, _, markers = A["_parse_attachments"]({FIELD_IMAGE: ["png", PNG]})
    assert markers == ["[image 2k png]"]


def test_marker_leads_the_caption():
    assert A["_with_markers"]("look at this", ["[image 2k]"]) == "[image 2k] look at this"


def test_marker_replaces_empty_body():
    assert A["_with_markers"]("(binary)", ["[image 2k]"]) == "[image 2k]"
    assert A["_with_markers"]("", ["[image 2k]"]) == "[image 2k]"


def test_plain_text_is_untouched():
    assert A["_with_markers"]("hello", []) == "hello"


def test_codec2_voice():
    img, aud, mode, markers = A["_parse_attachments"]({FIELD_AUDIO: [0x09, C2_3200]})
    assert aud == C2_3200
    assert mode == 0                      # CODEC2_MODE_3200
    assert markers == ["[voice 1s]"]
    assert img is None
    _, _, mode24, m24 = A["_parse_attachments"]({FIELD_AUDIO: [0x08, b"\x01" * 300]})
    assert mode24 == 1                    # CODEC2_MODE_2400


def test_unplayable_audio_is_still_announced():
    # Opus (0x10+) has no decoder on board, but the row must not look empty.
    img, aud, mode, markers = A["_parse_attachments"]({FIELD_AUDIO: [0x10, b"o" * 3000]})
    assert aud is None and mode is None
    assert markers == ["[audio 2k]"]


def test_file_attachment_image_is_promoted():
    fields = {FIELD_FILE_ATTACHMENTS: [["snap.jpg", JPEG]]}
    img, _, _, markers = A["_parse_attachments"](fields)
    assert img == JPEG                    # opens in the viewer like FIELD_IMAGE
    assert markers == ["[image 2k]"]


def test_file_attachment_non_image_is_listed():
    fields = {FIELD_FILE_ATTACHMENTS: [["notes.pdf", PDF]]}
    img, _, _, markers = A["_parse_attachments"](fields)
    assert img is None
    assert markers == ["[file notes.pdf 1k]"]


def test_image_and_voice_together():
    fields = {FIELD_IMAGE: ["jpeg", JPEG], FIELD_AUDIO: [0x09, C2_3200]}
    img, aud, _, markers = A["_parse_attachments"](fields)
    assert img == JPEG and aud == C2_3200
    assert markers == ["[image 2k]", "[voice 1s]"]


def test_malformed_fields_still_announce_something():
    for fields in ({FIELD_IMAGE: "garbage"}, {FIELD_IMAGE: ["jpeg"]},
                   {FIELD_IMAGE: ["jpeg", "not-bytes"]}):
        _, _, _, markers = A["_parse_attachments"](fields)
        assert markers == ["[image ?]"], fields
    _, _, _, markers = A["_parse_attachments"]({FIELD_AUDIO: [0x09]})
    assert markers == ["[audio ?]"]
    _, _, _, markers = A["_parse_attachments"]({FIELD_FILE_ATTACHMENTS: ["junk"]})
    assert markers == ["[file ?]"]


def test_no_fields_no_markers():
    assert A["_parse_attachments"]({}) == (None, None, None, [])


def test_sniff_rejects_short_and_unknown_payloads():
    assert A["_sniff_image"](b"\xff\xd8") is None      # too short to judge
    assert A["_sniff_image"](PDF) is None
    assert A["_sniff_image"](PNG) == "png"
    assert A["_sniff_image"](b"GIF89a" + b"\x00" * 20) == "gif"


# --- Drawing ---------------------------------------------------------------

class FakeTFT:
    """Records draw calls with their colour; catches bad coords/non-latin1."""

    def __init__(self):
        self.calls = []

    def text(self, font, s, x, y, fg, bg=None):
        if isinstance(s, str):
            s.encode("ascii")  # C driver rejects chars > 0xFF
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.calls.append(("text", s, x, y, fg))

    def fill_rect(self, x, y, w, h, c):
        assert 0 <= x <= 320 and 0 <= y <= 240, (x, y)
        self.calls.append(("rect", x, y, w, h, c))

    def blit_buffer(self, buf, x, y, w, h):
        self.calls.append(("blit", x, y, w, h))

    def fill(self, c):
        self.calls.append(("fill", c))

    def texts(self, colour=None):
        # Rows reach the driver as glyph-index bytes (ui._tb), not str.
        return [c[1].decode("latin-1") if isinstance(c[1], bytes) else c[1]
                for c in self.calls
                if c[0] == "text" and (colour is None or c[4] == colour)]


PEER = b"\x01" * 16


def _chat_ui():
    tft = FakeTFT()
    g = ui.UI(tft, object(), lambda: b"\x00", node_name="test")
    g._screen_on = True
    g.add_peer(PEER, "meshchat")
    g.selected_idx = g._peer_keys.index(PEER)
    g._enter_chat()
    return g, tft


def test_captioned_image_row_shows_a_clickable_marker():
    g, tft = _chat_ui()
    g.add_chat_message(PEER, False, A["_with_markers"]("look at this", ["[image 2k]"]),
                       image=JPEG)
    g.draw()
    # The marker is drawn separately in magenta — that redraw is what makes
    # the attachment visible on a row that also carries text.
    assert "[image 2k]" in tft.texts(g.NEON_MAG)
    assert any("look at this" in t for t in tft.texts())
    assert list(g._visible_image_lines.values()) == [0]


def test_captioned_voice_row_shows_a_marker():
    g, tft = _chat_ui()
    g.add_chat_message(PEER, False, A["_with_markers"]("heard this", ["[voice 1s]"]),
                       audio=C2_3200, audio_mode=0)
    g.draw()
    assert "[voice 1s]" in tft.texts(g.NEON_GREEN)
    assert list(g._visible_audio_lines.values()) == [0]


def test_sent_voice_row_is_marked_and_replayable():
    g, tft = _chat_ui()
    idx = g.add_chat_message(PEER, True, "[voice 3s]", status=1,
                             audio=C2_3200, audio_mode=0)
    g.draw()
    assert "[voice 3s]" in tft.texts(g.NEON_GREEN)
    assert (PEER, idx) in g._audio_cache
    # Highlight the row and click: it must hand the codec2 bytes back.
    played = []
    g.on_audio_play = lambda data, mode: played.append((data, mode))
    g.chat_cursor = next(iter(g._visible_audio_lines))
    g._irq_click = 1
    g.handle_trackball()
    assert played == [(C2_3200, 0)]


def test_evicted_image_is_struck_through_not_hidden():
    g, tft = _chat_ui()
    g.add_chat_message(PEER, False, "[image 2k] one", image=JPEG)
    for i in range(ui.MAX_CACHED_IMAGES):
        g.add_chat_message(PEER, False, "[image 2k] more %d" % i, image=JPEG)
    g.draw()
    assert (PEER, 0) not in g._image_cache          # first one aged out
    assert "[image 2k]" in tft.texts(g.DIM_CYAN)    # still shown, dimmed


# --- Message ids survive history trimming ---------------------------------

def _fill_history(g, n, **kw):
    return [g.add_chat_message(PEER, False, "msg %d" % i, **kw) for i in range(n)]


def test_ids_are_unique_across_a_trim():
    g, _ = _chat_ui()
    ids = _fill_history(g, ui.MAX_HISTORY + 10)
    assert len(set(ids)) == len(ids)                     # never reused
    assert len(g.chat_history[PEER]) == ui.MAX_HISTORY   # still trimmed


def test_status_lands_on_its_own_message_after_a_trim():
    g, _ = _chat_ui()
    # Sit the tracked message where a later trim shifts it, but not so far
    # back that it is dropped: 9 before it, 25 after, cap 30 -> 5 rows fall
    # off the front, so its list position moves from 9 to 4.
    _fill_history(g, 9)
    tracked = g.add_chat_message(PEER, True, "the tracked one", status=1)
    _fill_history(g, ui.MAX_HISTORY - 5)
    hist = g.chat_history[PEER]
    assert len(hist) == ui.MAX_HISTORY          # a trim really happened
    assert hist[4][6] == tracked                # and it moved
    g.update_message_status(PEER, tracked, 2)
    rows = {m[6]: m for m in hist}
    assert rows[tracked][1] == "the tracked one"
    assert rows[tracked][3] == 2
    assert all(m[3] == 0 for m in hist if m[6] != tracked)


def test_status_for_an_aged_out_message_is_dropped():
    g, _ = _chat_ui()
    gone = g.add_chat_message(PEER, True, "long gone", status=1)
    _fill_history(g, ui.MAX_HISTORY)       # pushes `gone` out of history
    assert all(m[6] != gone for m in g.chat_history[PEER])
    g.update_message_status(PEER, gone, 2)  # must be a no-op, not a mis-hit
    assert all(m[3] == 0 for m in g.chat_history[PEER])


def test_image_click_opens_its_own_image_after_a_trim():
    g, _ = _chat_ui()
    # The wanted image has to outlive a trim (so its position shifts) while
    # staying inside the visible window (so it is still clickable).
    g.add_chat_message(PEER, False, "[image 2k] first", image=JPEG)
    _fill_history(g, ui.MAX_HISTORY - 5)
    wanted = g.add_chat_message(PEER, False, "[image 2k] second", image=WEBP)
    _fill_history(g, 8)
    hist = g.chat_history[PEER]
    assert len(hist) == ui.MAX_HISTORY
    assert all(m[1] != "[image 2k] first" for m in hist)   # the first fell off
    g.draw()
    assert set(g._visible_image_lines.values()) == {wanted}
    g.chat_cursor = next(iter(g._visible_image_lines))
    g._irq_click = 1
    g.handle_trackball()
    assert g.state == ui.STATE_IMAGE
    assert g._viewing_image == WEBP                  # not the trimmed JPEG


def test_trimmed_messages_release_their_media():
    g, _ = _chat_ui()
    g.add_chat_message(PEER, False, "[voice 1s]", audio=C2_3200, audio_mode=0)
    g.add_chat_message(PEER, False, "[image 2k]", image=JPEG)
    _fill_history(g, ui.MAX_HISTORY)   # both scroll out
    assert g._audio_cache == {}
    assert g._image_cache == {}
    assert g._image_cache_order == []


def test_clear_peers_releases_media():
    g, _ = _chat_ui()
    g.add_chat_message(PEER, False, "[image 2k]", image=JPEG)
    g.add_chat_message(PEER, False, "[voice 1s]", audio=C2_3200, audio_mode=0)
    g.clear_peers()
    assert g._image_cache == {} and g._audio_cache == {}
    assert g._image_cache_order == []


def test_visible_rows_resolve_to_live_messages_after_a_trim():
    g, tft = _chat_ui()
    _fill_history(g, ui.MAX_HISTORY + 5)
    g.draw()
    hist = g.chat_history[PEER]
    assert g._visible_msg_lines
    for mid in g._visible_msg_lines.values():
        assert g._msg_index(hist, mid) >= 0        # no dangling row ids
    # The bottom row is the newest message, and the highlighted-row timestamp
    # lookup (which resolves the id) must not blow up.
    assert g._visible_msg_lines[max(g._visible_msg_lines)] == hist[-1][6]
    g.chat_cursor = 0
    g.cmd_buf = bytearray()
    g._cache[14] = ''
    tft.calls.clear()
    g.draw_input()


def test_unsupported_format_names_itself_in_the_viewer():
    g, tft = _chat_ui()
    g.add_chat_message(PEER, False, "[image 2k png]", image=PNG)
    g.draw()
    g.chat_cursor = next(iter(g._visible_image_lines))
    g._irq_click = 1
    g.handle_trackball()
    assert g.state == ui.STATE_IMAGE
    tft.calls.clear()
    g.draw_image(lambda: None, lambda: None)
    assert any("PNG is not supported" in t for t in tft.texts())
    assert any("any key = back" in t for t in tft.texts())


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:
                failures += 1
                print("FAIL", name, "->", repr(e))
    print("\n%d failure(s)" % failures)
    sys.exit(1 if failures else 0)

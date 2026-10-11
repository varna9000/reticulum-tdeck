# Render the T-Deck Pro's screens on the host, pixel for pixel, so a layout
# can be looked at before it is flashed.
#
#   /opt/homebrew/bin/python3 tools/render_pro.py OUT_DIR
#
# It drives the real ui.py + ui_pro.py with the Pro's geometry against a 1-bit
# framebuffer that follows lib/eink_shim.py's rules (luminance > 60 is ink;
# glyphs on an ink background come out as paper), with the real 8x16 and 6x12
# fonts. Writes list.png, sheet.png, chat.png, chat_cursor.png, rec.png and
# rec_warm.png, each at 4x.

import os
import struct
import sys
import types
import zlib
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

_clock = [100000]
_time.ticks_ms = lambda: _clock[0]
_time.ticks_diff = lambda a, b: a - b
_time.ticks_add = lambda a, d: a + d
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


_m = types.ModuleType("machine")
_m.Pin = _Pin
sys.modules["machine"] = _m
_g = types.ModuleType("board_geometry")
_g.SCREEN_W = 240
_g.SCREEN_H = 320
sys.modules["board_geometry"] = _g

import ui  # noqa: E402
import ui_pro  # noqa: E402
import spleen_8x16 as font8  # noqa: E402
import spleen_6x12 as font6  # noqa: E402

W, H = 240, 320


def lum(c):
    return (((c >> 11) & 31) << 1) + ((c >> 5) & 63) + ((c & 31) << 1)


class FB:
    """The mono framebuffer: 1 = ink."""
    mono = True

    def __init__(self):
        self.px = [bytearray(W) for _ in range(H)]

    def fill(self, c):
        self.fill_rect(0, 0, W, H, c)

    def fill_rect(self, x, y, w, h, c):
        v = 1 if lum(c) > 60 else 0
        for yy in range(max(0, y), min(H, y + h)):
            row = self.px[yy]
            for xx in range(max(0, x), min(W, x + w)):
                row[xx] = v

    def glyphs(self, f, s, x, y, fg, bg, bold=False):
        """Paint glyph cells; fg/bg are already 0/1."""
        gw, gh = f.WIDTH, f.HEIGHT
        for ch in s:
            ch = ch if isinstance(ch, int) else ord(ch)
            idx = (ch - f.FIRST) * gh
            for r in range(gh):
                b = f.FONT[idx + r]
                for k in range(gw):
                    if 0 <= x + k < W and 0 <= y + r < H:
                        on = (b >> (7 - k)) & 1
                        if bold and k and (b >> (8 - k)) & 1:
                            on = 1
                        self.px[y + r][x + k] = fg if on else bg
            x += gw

    def text(self, f, s, x, y, color=0xFFFF, background=0):
        bg = 1 if lum(background) > 60 else 0
        fg = 0 if bg else (1 if lum(color) > 60 else 0)
        self.glyphs(f, s, x, y, fg, bg)

    def __getattr__(self, n):
        return lambda *a, **k: None

    def png(self, path, scale=4):
        raw = bytearray()
        for y in range(H * scale):
            raw.append(0)
            src = self.px[y // scale]
            for x in range(W * scale):
                raw.append(0 if src[x // scale] else 255)

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale, 8, 0, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


class Small:
    """ui_pro's 6x12 blit: each pixel thresholded on its own colour."""
    f = font6
    bold = False

    def draw(self, tft, slots, x, y, fg, bg):
        tft.glyphs(self.f, slots, x, y, 1 if lum(fg) > 60 else 0, 1 if lum(bg) > 60 else 0, self.bold)


def mk():
    _clock[0] = 100000
    t = FB()
    g = ui.UI(t, font8, lambda: b"\x00", node_name="t", trackball=False)
    g.state = ui.STATE_NODES
    g._screen_on = True
    g._state_change_ms = 0
    g.lora_online = True
    g.rssi = -80
    g.snr = 7
    g.bat_v = 4.0
    ui_pro._small = Small()
    ui._clock_valid = lambda: True
    ui._fmt_clock = lambda: "09:41"
    return g, t


def scene_list(out):
    g, t = mk()
    for i in range(12):
        g.add_peer(bytes([i + 1]) * 16, "peer%02d" % i, hops=2, rssi=-87)
    k = g._peer_keys
    g.unread[k[1]] = 3
    g.unread[k[2]] = 12
    g.peers[k[2]]["fav"] = True
    g.peers[k[5]]["fav"] = True
    g.peers[k[3]]["name"] = "A really long node name here x"
    g.selected_idx = 5
    g.node_scroll = 0
    g.draw()
    t.png(os.path.join(out, "list.png"))
    g.selected_idx = 2
    ui_pro.sheet_open(g)
    g.draw()
    t.png(os.path.join(out, "sheet.png"))


PEER = b"\x93\x48\x1a\x35" * 4


def _say(g, mine, text, at, status=0, image=None, audio=None):
    mid = g.add_chat_message(PEER, mine, text, status=status, image=image, audio=audio)
    h = g.chat_history[PEER]
    m = h[-1]
    h[-1] = (m[0], m[1], at, m[3], m[4], m[5], m[6])
    g._invalidate_chat_lines()
    return mid


def scene_chat(out):
    g, t = mk()
    ui._fmt_time = lambda ts: "09:%02d" % (int(ts) // 60 % 60)
    g.add_peer(PEER, "Alex", hops=2)
    g.selected_peer = PEER
    g.state = ui.STATE_CHAT
    g.cmd_buf = bytearray()
    _say(g, False, "hey, are you on the mesh tonight?", 36 * 60)
    _say(g, True, "yes, LoRa only", 37 * 60, status=2)
    old = _say(g, False, "[image 9k]", 38 * 60, image=b"x")
    _say(g, False, "[image 12k] nice shot of the antenna mast", 38 * 60 + 20, image=b"jpg")
    _say(g, True, "[voice 3s]", 40 * 60, status=1, audio=b"c2")
    _say(g, False, "got it, 73", 41 * 60)
    g._image_cache.pop((PEER, old), None)
    g.draw()
    t.png(os.path.join(out, "chat.png"))
    g.chat_cursor = 6
    g.draw()
    t.png(os.path.join(out, "chat_cursor.png"))


def scene_rec(out):
    g, t = mk()
    g.add_peer(PEER, "Alex", hops=2)
    g.selected_peer = PEER
    g.state = ui.STATE_RECORDING
    g._rec_warming = False
    g._rec_warm_secs = 0
    g.draw()
    t.png(os.path.join(out, "rec.png"))
    g, t = mk()
    g.add_peer(PEER, "Alex", hops=2)
    g.selected_peer = PEER
    g.state = ui.STATE_RECORDING
    g._rec_warming = True
    g._rec_warm_secs = 2
    g.draw()
    t.png(os.path.join(out, "rec_warm.png"))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    scene_list(out)
    scene_chat(out)
    scene_rec(out)
    print("wrote 6 PNGs to", out)

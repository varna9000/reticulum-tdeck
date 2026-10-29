# Render the T-Deck v1's Setup pages on the host, pixel for pixel and in
# colour, so a layout can be looked at before it is flashed. The LCD
# counterpart of render_pro.py: the real ui.py with its own 320x240 fallback
# geometry (no board_geometry module), RGB565 colours kept as the driver
# would show them.
#
#   /opt/homebrew/bin/python3 tools/render_v1.py OUT_DIR
#
# Writes setup.png, setup_lora.png, setup_lora_code.png, setup_tcp.png,
# setup_tcp_off.png and setup_text.png, each at 3x.

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
    IRQ_RISING = 8

    def __init__(self, *a, **k):
        pass

    def irq(self, *a, **k):
        pass

    def value(self, *a):
        return 1


_m = types.ModuleType("machine")
_m.Pin = _Pin
sys.modules["machine"] = _m
assert "board_geometry" not in sys.modules and not os.path.exists(os.path.join(ROOT, "board_geometry.py")), \
    "a board_geometry module would turn this into the Pro"

import ui  # noqa: E402
import spleen_8x16 as font8  # noqa: E402

W, H = ui.SCREEN_W, ui.SCREEN_H
assert (W, H) == (320, 240), (W, H)


def rgb(c):
    """RGB565 -> (r, g, b)"""
    return (((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)


class FB:
    """A colour framebuffer with the st7789 driver's text()/fill_rect()."""
    mono = False

    def __init__(self):
        self.px = [[0] * W for _ in range(H)]

    def fill(self, c):
        self.fill_rect(0, 0, W, H, c)

    def fill_rect(self, x, y, w, h, c):
        for yy in range(max(0, y), min(H, y + h)):
            row = self.px[yy]
            for xx in range(max(0, x), min(W, x + w)):
                row[xx] = c

    def text(self, f, s, x, y, color=0xFFFF, background=0):
        gw, gh = f.WIDTH, f.HEIGHT
        for ch in s:
            ch = ch if isinstance(ch, int) else ord(ch)
            idx = (ch - f.FIRST) * gh
            for r in range(gh):
                b = f.FONT[idx + r]
                for k in range(gw):
                    if 0 <= x + k < W and 0 <= y + r < H:
                        self.px[y + r][x + k] = color if (b >> (7 - k)) & 1 else background
            x += gw

    def __getattr__(self, n):
        return lambda *a, **k: None

    def png(self, path, scale=3):
        raw = bytearray()
        for y in range(H * scale):
            raw.append(0)
            src = self.px[y // scale]
            for x in range(W * scale):
                raw.extend(rgb(src[x // scale]))

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def mk():
    _clock[0] = 100000
    t = FB()
    g = ui.UI(t, font8, lambda: b"\x00", node_name="milen")
    g._small = None                 # no framebuf on the host: the driver's text() path
    g.state = ui.STATE_SETTINGS
    g._screen_on = True
    g._state_change_ms = 0
    g.lora_online = True
    g.rssi = -80
    g.snr = 7
    g.bat_v = 4.0
    ui._clock_valid = lambda: True
    ui._fmt_clock = lambda: "09:41"
    return g, t


def _page(g, page):
    g._settings_page = page
    g._cache = [''] * ui.CACHE_ROWS
    g.dirty = True
    g.draw()


def scene_setup(out):
    g, t = mk()
    g._wifi_connected = True
    g._wifi_ssid_current = "homenet"
    g._wifi_ip = "192.168.1.106"
    g._tcp_enabled = False
    g.my_address = "93481a35c0ffee11" * 2
    g.my_identity_hash = "0123456789abcdef" * 2
    g.my_lxma_uri = "lxma://x"
    g._settings_idx = 1
    _page(g, ui._SET_MAIN)
    t.png(os.path.join(out, "setup.png"))

    g.set_lora_config({"freq_khz": 868800, "bw": 125, "sf": 8, "coding_rate": 5, "tx_power": 22})
    g._settings_idx = 10
    g.handle_key(b"\r")             # the LoRa cfg row opens the editor
    g.draw()
    t.png(os.path.join(out, "setup_lora.png"))
    g.handle_key(b"\x1b")
    g.set_lora_config({"networkname": "mymesh", "passphrase": "correct horse"})
    g._settings_idx = 10
    g.handle_key(b"\r")
    g._lora_field = 5
    g.draw()
    t.png(os.path.join(out, "setup_lora_code.png"))
    g.handle_key(b"\x1b")

    g._tcp_target = "rns.varnatransport.com:4242"
    g.set_tcp_ifac("mymesh", "correct horse", 16)
    g._settings_idx = 1
    g.handle_key(b"\r")             # the TCP row opens the form
    g.draw()
    t.png(os.path.join(out, "setup_tcp.png"))
    g._tcp_field = 2
    g.handle_key(b"\r")             # Pass -> text entry
    g.cmd_buf = bytearray(b"correct horse")
    g.draw()
    t.png(os.path.join(out, "setup_text.png"))
    g.handle_key(b"\x1b")
    g.handle_key(b"\x1b")

    g.set_tcp_ifac("", "", 16)
    g._settings_idx = 1
    g.handle_key(b"\r")
    g.draw()
    t.png(os.path.join(out, "setup_tcp_off.png"))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    scene_setup(out)
    print("wrote 6 PNGs to " + out)

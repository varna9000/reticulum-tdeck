# Render the T-Deck Pro's screens on the host, pixel for pixel, so a layout
# can be looked at before it is flashed.
#
#   /opt/homebrew/bin/python3 tools/render_pro.py OUT_DIR
#
# It drives the real ui.py + ui_pro.py with the Pro's geometry against a 1-bit
# framebuffer that follows lib/eink_shim.py's rules (luminance > 60 is ink;
# glyphs on an ink background come out as paper), with the real 8x16 and 6x12
# fonts. Writes list.png, sheet.png, chat.png, chat_cursor.png, rec.png,
# rec_warm.png, setup.png, setup_end.png and setup_noserver.png, each at 4x.

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


def scene_setup(out):
    g, t = mk()
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_MAIN
    g.node_name = "Pro"
    g.set_backbone_state("up", "homenet", "192.168.1.106")
    g._tcp_target = "rns.varnatransport.com:4242"
    g.my_address = "93481a35c0ffee11" * 2
    g.my_identity_hash = "0123456789abcdef" * 2
    g.my_lxma_uri = "lxma://x"
    g._settings_idx = 4
    g.draw()
    t.png(os.path.join(out, "setup.png"))
    g._settings_idx = 14
    g.draw()
    t.png(os.path.join(out, "setup_end.png"))
    g._settings_idx = 0
    g.set_backbone_state("no_server", "homenet", "")
    g.draw()
    t.png(os.path.join(out, "setup_noserver.png"))
    g.set_backbone_state("up", "homenet", "192.168.1.106")
    g._settings_page = ui._SET_NODE_NAME
    g.cmd_buf = bytearray(b"Pro")
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    t.png(os.path.join(out, "setup_name.png"))
    g._settings_page = ui._SET_RADIO
    g._settings_scroll = 0
    g.cmd_buf = bytearray()
    g.get_radio_stats = lambda: [
        ("LoRa", "online"), ("RF", "868000kHz SF8 17dBm"), ("last RX", "-87dBm snr 7.5"),
        ("TX", "12 pkts 2048B"), ("RX", "240 pkts 51200B"), ("CRC err", "0"),
        ("LBT", "12 waits 3 forced"), ("paths", "31"), ("identities", "42"), ("announces", "118"),
        ("uptime", "3h12m"), ("sleep", "light 12 key 3 lora 9 timer 4"), ("battery", "87% 4.08V"),
        ("free mem", "5321kB"), ("clock", "mesh-synced")]
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    t.png(os.path.join(out, "setup_radio.png"))
    g._settings_page = ui._SET_LORA
    g._lora_applying = ""
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    t.png(os.path.join(out, "setup_lora.png"))
    # The editor open on a mesh with an access code, Net selected (issue #20)
    g.set_lora_config({"networkname": "mymesh", "passphrase": "correct horse"})
    g._lora_edit = dict(g._lora_cfg)
    g._lora_field = 5
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    t.png(os.path.join(out, "setup_lora_code.png"))
    g._lora_edit = None
    g._lora_field = 0
    g.set_lora_config({"networkname": "", "passphrase": ""})
    # The TCP form: saved link with a code, then the Passphrase entry page
    g._tcp_target = "rns.varnatransport.com:4242"
    g.set_tcp_ifac("mymesh", "correct horse", 16)
    g._open_tcp_form()
    g.draw()
    t.png(os.path.join(out, "setup_tcp.png"))
    g._tcp_field = 2
    g.handle_key(b"\r")                        # Pass -> text entry
    g.cmd_buf = bytearray(b"correct horse")
    g.draw()
    t.png(os.path.join(out, "setup_text.png"))
    g.handle_key(b"\x1b")
    g.handle_key(b"\x1b")
    g._settings_idx = 0
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_networks = [("homenet", -41), ("A-rather-long-network-name", -67), ("cafe", -80)]
    g._settings_idx = 1
    g._settings_scroll = 0
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    t.png(os.path.join(out, "setup_wifi.png"))


def scene_rrc(out):
    import rrc_ui
    g, t = mk()
    g.state = ui.STATE_RRC_ROOMS
    g._rrc_hub_name = "Varna Transport Hub and friends"
    g._rrc_status = "connected"
    for s in ("*** Welcome to Varna Hub", "*** 4 rooms, 7 users online", "*** be kind, keep it short please, this line is long",
              "#mesh (7)\n#ops (2)\n#bg (3)"):
        g.rrc_line("notice", None, s)
    g._rrc_rooms = [("mesh", "general LoRa chatter"), ("ops", "hub operators"), ("bg", ""),
                    ("a-room-with-a-very-long-name-indeed", "a topic that is far too long to fit on one small line"),
                    ("trading", "swap & sell"), ("late", "")]
    g._rrc_list_seen = True
    g.draw()
    t.png(os.path.join(out, "rrc_console.png"))
    rrc_ui.panel_toggle(g, "rooms")
    g.draw()
    t.png(os.path.join(out, "rrc_rooms.png"))
    rrc_ui.panel_close(g)
    g.state = ui.STATE_RRC_CHAT
    g._rrc_room = "mesh"
    g._rrc_members = 7
    g._rrc_members_exact = False
    g._rrc_lines = []
    g._rrc_flat = None
    for nick, s in (("kpn3o", "anyone on 868 tonight"), ("alex", "yes, SF8 from the mast, snr 7 here, 2 hops"),
                    ("vesna", "o/"), ("a-very-long-nickname-here", "hello from a long nick")):
        g.rrc_line("msg", nick, s)
    g.rrc_line("notice", None, "*** vesna joined")
    g._rrc_roster = [(bytes([0x93, 0x48, 0x1a, 0x35]) * 4, "alex"), (b"\xaa\xcf\x70\x98" * 4, "kpn3o"),
                     (b"\xc0\xff\xee\x42" * 4, None), (b"\x96\x99\xf9\x8b" * 4, "a-very-long-nickname-here-x"),
                     (b"\x63\x35\xc5\xb9" * 4, "bigv"), (b"\x11" * 16, "wraith"), (b"\x22" * 16, "vesna")]
    g._rrc_input = "vesna: welcome"
    g.draw()
    t.png(os.path.join(out, "rrc_room.png"))
    rrc_ui.panel_toggle(g, "members")
    g._rrc_panel_idx = 3
    g.draw()
    t.png(os.path.join(out, "rrc_members.png"))


def scene_misc(out):
    import micron
    g, t = mk()
    src = (">Board index\n\nWelcome to the CICADA forums. Pick a board below and mind the rules.\n\n"
           "`[General chatter`:/page/general.mu]\n`[LoRa and antennas, masts, feedlines and everything else`:/page/lora.mu]\n"
           "`[Node announcements`:/page/nodes.mu]\n\n>>Rules\nBe kind, no spam, keep it on topic. "
           "Averyveryverylongwordwithoutanyspacesinitthatmustbebrokensomewhere ok.\n\n-\n`c centred line `a\n")
    lines, links = micron.render(src, ui.COLS)
    g.show_page("The CICADA forums of Varna", "/page/boards/index.mu", lines, links)
    g.browser_cursor = 4
    g.draw()
    t.png(os.path.join(out, "browser.png"))
    g, t = mk()
    g.state = ui.STATE_SHELL
    g._shell_menu = True
    g._shell_menu_idx = 1
    g.draw()
    t.png(os.path.join(out, "shell_menu.png"))
    g, t = mk()
    for i in range(3):
        g.add_peer(bytes([i + 1]) * 16, "peer%02d" % i, hops=2)
    snap = [(bytes([0x40 + i]) * 16, n, 1700000000) for i, n in enumerate(
        ("Alexander the Great of Varna and Burgas", "alex", "Alexa home node", "Tula Group", "malex"))]
    g.on_contact_snapshot = lambda tab: snap
    g.on_listen_start = lambda: None      # the Pro has a microphone: the longest key hints
    g._find_open()
    g._find_snap = g._find_snap * 400     # a busy mesh: a four-digit "heard" count
    for ch in b"alex":
        g.handle_key(bytes([ch]))
    g.draw()
    t.png(os.path.join(out, "find.png"))
    g, t = mk()
    g.state = ui.STATE_SETTINGS
    g._settings_page = ui._SET_WIFI_SCAN
    g._wifi_networks = [("homenet", -41), ("A-rather-long-network-name-indeed", -67), ("cafe", -80)]
    g._settings_idx = 1
    g.draw()
    t.png(os.path.join(out, "wifi_scan.png"))
    g, t = mk()
    g.state = ui.STATE_SHELL
    g._shell_menu = True
    g._shell_menu_idx = 9
    g.draw()
    t.png(os.path.join(out, "shell_menu_end.png"))
    g, t = mk()
    g.node_name = "Varna-Pro"
    g._bl = None
    g.unread = {b"a" * 16: 3}
    g._panel_flush = lambda force_full=False, user=False: None
    g.sleep_screen()
    t.png(os.path.join(out, "sleep.png"))
    g, t = mk()
    g.node_name = "Varna-Pro"
    g._bl = None
    g.bat_v = 4.9
    g._panel_flush = lambda force_full=False, user=False: None
    g.sleep_screen()
    t.png(os.path.join(out, "sleep_none.png"))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    scene_list(out)
    scene_chat(out)
    scene_rec(out)
    scene_setup(out)
    scene_rrc(out)
    scene_misc(out)
    print("wrote 24 PNGs to", out)

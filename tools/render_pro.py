# Render the T-Deck Pro's screens on the host, pixel for pixel, so a layout
# can be looked at before it is flashed.
#
#   /opt/homebrew/bin/python3 tools/render_pro.py OUT_DIR
#
# It drives the real ui.py + ui_pro.py with the Pro's geometry against a 1-bit
# framebuffer that follows lib/eink_shim.py's rules (luminance > 60 is ink;
# glyphs on an ink background come out as paper), with the real 8x16 and 6x12
# fonts. Writes list.png, sheet.png, chat.png, chat_cursor.png, rec.png,
# rec_warm.png, setup.png, setup_end.png, setup_noserver.png, the tab lists
# (list_*.png, tab_*.png), Lookup (find_*.png) and the hdr_*.png header
# states (the top 20 px), each at 4x. All names in the scenes are invented.

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

    def png(self, path, scale=4, h=H):
        raw = bytearray()
        for y in range(h * scale):
            raw.append(0)
            src = self.px[y // scale]
            for x in range(W * scale):
                raw.append(0 if src[x // scale] else 255)

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, h * scale, 8, 0, 0, 0, 0))
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


NOW = 1793000000.0
_time.time = lambda: NOW


def _pad(h):
    return bytes.fromhex(h) + b"\x5a" * 12


# (key, name, fav, last message mine?, text, age in s, unread)
CHATS = [
    ("93481a35", "blue-heron", True, False, "antenna is back up, try me on 868", 240, 0),
    ("6335c5b9", "Hut 7 relay", True, None, None, 0, 0),
    ("aacf7098", "low_tide", False, False, "are you on the mesh tonight? I can see", 120, 2),
    ("c0ffee42", None, False, False, "hello from the ridge, who is this?", 660, 1),
    ("9699f98b", "The Very Long Named Community Node", False, True, "thanks, page loads fine now", 3000, 0),
    ("11aa5e07", "ridgerunner", False, False, "[voice 3s]", 7300, 0),
    ("7e02bb31", "mast-cam-3", False, True, "[image 12k] nice shot of the mast", 90000, 0),
]
MORE = [("%02xab%02xcd" % (i * 7 + 16, i), n, False, i % 2 == 0, t, 100000 + i * 40000, 0) for i, (n, t) in enumerate((
    ("kestrel", "see you at the hut then"), ("pine-ridge", "repeater is back"), ("harbor watch", "[voice 5s]"),
    ("nightjar", "copy that, 73"), ("tinker", "try SF8 instead"), ("old-mill", "thanks!"),
    ("quail", "[image 9k]"), ("saltmarsh", "who has the spare battery?"), ("dock-4", "ok")))]
HEARD = [
    ("b90d1f2e", "newcomer-kay", 40), ("a1c4f70e", "Anonymous Peer", 90), ("3e61f0c7", None, 130),
    ("93481a35", "blue-heron", 240), ("0be2917d", "MeshChat User", 300), ("7f30c2aa", "solar-shed-node", 610),
    ("e4a85c13", "The Very Long Named Community Node of the North", 1400), ("1d77e4b0", "Anonymous Peer", 3700),
    ("f1039c7b", "hilltop relay 2", 7300), ("52c0b9f1", "bbs.lowtide", 12000), ("47d2c8f5", "kettle", 90000),
]


def _snapshot():
    fill = [("%02x%02x77aa" % (0x20 + i, i), ("Anonymous Peer" if i % 3 == 0 else None if i % 5 == 0 else "node-%02d" % i), 20000 + i * 2000)
            for i in range(46)]
    return [(_pad(k), n or "?", NOW - a) for k, n, a in HEARD + fill]


def _heard(g, n, tab=None):
    g._heard[ui.TAB_MSG if tab is None else tab].update(i.to_bytes(4, "big") for i in range(n))


def _chats(g, chats=CHATS, sel=None):
    """A session's worth of chats through the real add_peer: pins in pin order,
    the rest oldest first so the newest ends up on top."""
    for key, name, fav, mine, text, age, un in chats:
        if fav:
            g.add_peer(_pad(key), name, fav=True)
    for key, name, fav, mine, text, age, un in reversed(chats):
        if not fav:
            g.add_peer(_pad(key), name)
    for key, name, fav, mine, text, age, un in chats:
        k = _pad(key)
        g.peers[k]["seen"] = NOW - age
        if text is not None:
            g.chat_history[k] = [(bool(mine), text, NOW - age, 2, False, False, 900)]
        if un:
            g.unread[k] = un
    if sel is not None:
        g.selected_idx = sel
    g.state = ui.STATE_NODES


def _other_tabs(g):
    """Pins first, then the rest oldest first: a new row lands under the pins."""
    for key, name, hops, age, fav in sorted((("52c0b9f1", "bbs.lowtide", 3, 400, True), ("e4a85c13", "The Very Long Named Community Node of the North", 5, 1500, False),
                                             ("7f30c2aa", "solar-shed-node", 1, 3000, False), ("f1039c7b", "hilltop relay 2", 2, 9000, False)),
                                            key=lambda r: (not r[4], -r[3])):
        g.add_nomad_node(_pad(key), name, hops=hops, seen=NOW - age, fav=fav)
    for key, hops, age, fav in sorted((("3e61f0c7", 1, 700, True), ("a1c4f70e", 3, 7500, False)), key=lambda r: (not r[3], -r[2])):
        g.add_shell_node(_pad(key), None, hops=hops, seen=NOW - age, fav=fav)
    for key, name, hops, age, fav in sorted((("b75f03c4", "mesh-cafe", 2, 900, True), ("28de91a6", "varna-hub", 4, 5400, False)), key=lambda r: (not r[4], -r[3])):
        g.add_rrc_hub(_pad(key), name, hops=hops, fav=fav)
        g.rrc_hubs[_pad(key)]["seen"] = NOW - age
    _heard(g, 21, ui.TAB_NET)
    _heard(g, 4, ui.TAB_SSH)
    _heard(g, 6, ui.TAB_RRC)
    g.net_idx = g.ssh_idx = g._rrc_idx = 1


def mk_list(heard=37):
    g, t = mk()
    g.state = ui.STATE_NODES
    g.my_address = "12" * 16
    g.set_backbone_state("up", "homenet", "192.168.1.20", link=True)
    _heard(g, heard)
    return g, t


def _shot(g, t, out, name, h=H):
    g._cache = [''] * ui.CACHE_ROWS
    g.dirty = True
    g.draw()
    t.png(os.path.join(out, name + ".png"), h=h)


def _lookup(g, snap):
    g.on_contact_snapshot = lambda tab: snap
    g.on_listen_start = lambda: None      # the Pro has a microphone: the longest key hints
    g._find_open()


def scene_list(out):
    g, t = mk_list()
    _chats(g, sel=3)
    _shot(g, t, out, "list_mixed")

    g, t = mk_list()                # the sheet over a chat, with its route known
    _chats(g, sel=3)
    g.peers[_pad("aacf7098")].update(hops=3, seen=NOW - 700)
    g.draw()
    ui_pro.sheet_open(g)
    _shot(g, t, out, "sheet")

    g, t = mk_list()
    _chats(g, sel=0)
    _shot(g, t, out, "list_new_chat_selected")

    g, t = mk_list(0)
    _shot(g, t, out, "list_first_boot")

    g, t = mk_list(0)               # a reboot keeps only the pins
    _chats(g, chats=[("93481a35", "blue-heron", True, None, None, 0, 0),
                     ("6335c5b9", "Hut 7 relay", True, None, None, 0, 0)], sel=1)
    _shot(g, t, out, "list_reboot_pins_only")

    g, t = mk_list(120)
    _chats(g, chats=CHATS + MORE)
    g.selected_idx = len(g._peer_keys) - 1
    g.node_scroll = len(g._peer_keys) - ui.LIST_ROWS
    _shot(g, t, out, "list_full_end")

    g, t = mk_list()
    _other_tabs(g)
    for tab, nm in ((ui.TAB_NET, "net"), (ui.TAB_SSH, "rnsh"), (ui.TAB_RRC, "rrc")):
        g.node_tab = tab
        _shot(g, t, out, "tab_" + nm)
    g, t = mk_list(0)
    g.node_tab = ui.TAB_NET
    _shot(g, t, out, "tab_net_empty")

    g, t = mk_list()
    _chats(g)
    g.selected_idx = 0
    _lookup(g, _snapshot())
    _shot(g, t, out, "find_msg")
    for ch in b"lowt":
        g.handle_key(bytes([ch]))
    _shot(g, t, out, "find_msg_search")

    g, t = mk_list(0)
    _lookup(g, [])
    _shot(g, t, out, "find_empty")

    g, t = mk_list()
    _other_tabs(g)
    g.node_tab = ui.TAB_NET
    _lookup(g, _snapshot())
    _shot(g, t, out, "tab_net_find")


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


def scene_header(out):
    """The header band alone: the top 20 px of the list screen."""
    cv = ui._clock_valid

    def one(name, bb=None, link=False, nosync=False, bat=4.0, **kw):
        g, t = mk_list(0)
        g.bat_v = bat
        g.set_backbone_state(bb or "off", "homenet" if bb else "", "", link=link)
        g._tcp_enabled = link
        for k, v in kw.items():
            setattr(g, k, v)
        ui._clock_valid = (lambda: False) if nosync else cv
        _shot(g, t, out, name, h=20)
        ui._clock_valid = cv

    flash = ui.time.ticks_ms() or 1
    one("hdr_1_lora")
    one("hdr_13_snr_neg", snr=-12)
    one("hdr_2_backbone", bb="up", link=True)
    one("hdr_3_lora_waiting", bb="connecting")
    one("hdr_14_both_neg", bb="connecting", snr=-12, announce_flash=flash)
    one("hdr_4_reconnecting", bb="no_server", link=True)
    one("hdr_5_announce", announce_flash=flash)
    one("hdr_6_fail", lora_online=False)
    one("hdr_7_rx", transfer_progress=(3, 12))
    one("hdr_12_rx_backbone", transfer_progress=(142, 300), bb="up", link=True)
    one("hdr_8_voice", _audio_status="sending")
    one("hdr_11_voice_long", _audio_status="encode 12s audio")
    one("hdr_15_voice_long_both", _audio_status="decode 12s audio", bb="connecting")
    one("hdr_9_usb", bat=4.8)
    one("hdr_10_nosync", nosync=True)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    scene_list(out)
    scene_chat(out)
    scene_rec(out)
    scene_setup(out)
    scene_rrc(out)
    scene_misc(out)
    scene_header(out)
    print("wrote %d PNGs to %s" % (len(os.listdir(out)), out))

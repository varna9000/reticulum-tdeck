# Render the T-Deck v1's Setup pages on the host, pixel for pixel and in
# colour, so a layout can be looked at before it is flashed. The LCD
# counterpart of render_pro.py: the real ui.py with its own 320x240 fallback
# geometry (no board_geometry module), RGB565 colours kept as the driver
# would show them.
#
#   /opt/homebrew/bin/python3 tools/render_v1.py OUT_DIR
#
# Writes setup.png, setup_lora.png, setup_lora_code.png, setup_tcp.png,
# setup_tcp_off.png and setup_text.png, the tab lists (list_*.png, tab_*.png),
# Lookup (find_*.png) and the header's states (hdr_*.png, the top 20 px),
# each at 3x. All names in the scenes are invented.

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

    def png(self, path, scale=3, h=H):
        raw = bytearray()
        for y in range(h * scale):
            raw.append(0)
            src = self.px[y // scale]
            for x in range(W * scale):
                raw.extend(rgb(src[x // scale]))

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, h * scale, 8, 2, 0, 0, 0))
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


# ---- the tab lists and the header ------------------------------------------
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


def _lists(g, tab=None):
    g.state = ui.STATE_NODES
    if tab is not None:
        g.node_tab = tab


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
    _lists(g)


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
    g.on_listen_start = lambda: None
    g._find_open()


def scene_lists(out):
    g, t = mk_list()
    _chats(g, sel=3)
    g.peers[_pad("93481a35")].update(hops=2)
    g.peers[_pad("aacf7098")].update(hops=3)
    _shot(g, t, out, "list_mixed")

    g, t = mk_list()
    _chats(g, sel=0)
    _shot(g, t, out, "list_new_chat_selected")

    g, t = mk_list(0)
    _shot(g, t, out, "list_first_boot")

    g, t = mk_list(12)
    _shot(g, t, out, "list_first_boot_heard")

    g, t = mk_list(0)           # a reboot keeps only the pins
    _chats(g, chats=[("93481a35", "blue-heron", True, None, None, 0, 0),
                     ("6335c5b9", "Hut 7 relay", True, None, None, 0, 0)], sel=1)
    _shot(g, t, out, "list_reboot_pins_only")

    g, t = mk_list(120)
    _chats(g, chats=CHATS + MORE)
    g.selected_idx = len(g._peer_keys) - 1
    g.node_scroll = len(g._peer_keys) - ui.LIST_ROWS
    _shot(g, t, out, "list_full_end")

    g, t = mk_list(3)           # a stranger's first message lands under the pins
    _chats(g, sel=5)
    stranger = _pad("deadbe01")
    g.add_peer(stranger, None)
    g.add_chat_message(stranger, False, "hi, saw your announce")
    _shot(g, t, out, "list_stranger_message")

    g, t = mk_list()
    _other_tabs(g)
    for tab, nm in ((ui.TAB_NET, "net"), (ui.TAB_SSH, "rnsh"), (ui.TAB_RRC, "rrc")):
        g.node_tab = tab
        _shot(g, t, out, "tab_" + nm)
    g, t = mk_list(0)
    g.node_tab = ui.TAB_NET
    _shot(g, t, out, "tab_net_empty")


def scene_lookup(out):
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
    _shot(g, t, out, "find_net")


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

    one("hdr_1_lora")
    one("hdr_2_backbone", bb="up", link=True)
    one("hdr_3_lora_waiting", bb="connecting")
    one("hdr_4_reconnecting", bb="no_server", link=True)
    one("hdr_5_announce", announce_flash=ui.time.ticks_ms() or 1)
    one("hdr_6_fail", lora_online=False)
    one("hdr_7_rx", transfer_progress=(3, 12))
    one("hdr_8_voice", _audio_status="sending")
    one("hdr_9_usb", bat=4.8)
    one("hdr_10_nosync", nosync=True)
    one("hdr_11_voice_long", _audio_status="encode 12s audio", bb="connecting")
    one("hdr_12_rx_backbone", transfer_progress=(142, 300), bb="up", link=True)
    one("hdr_13_snr_neg", snr=-12)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    scene_setup(out)
    scene_lists(out)
    scene_lookup(out)
    scene_header(out)
    print("wrote %d PNGs to %s" % (len(os.listdir(out)), out))

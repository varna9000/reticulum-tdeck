# The Pro's redesigned list must not exist on the T-Deck v1: with the v1's
# landscape geometry (no board_geometry module) ui.py keeps its old layout.
#   /opt/homebrew/bin/python3 tests/test_ui_pro_inactive.py

import os
import sys
import types
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

_time.ticks_ms = lambda: 100000
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
sys.modules["board_geometry"] = None        # the v1 ships without it

import ui  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("  %s %s %s" % ("ok  " if cond else "FAIL", name, "" if cond else detail))
    if not cond:
        fails.append(name)


check("v1 geometry", ui.SCREEN_W == 320 and ui.SCREEN_H == 240 and ui.COLS == 40 and ui.BODY_ROWS == 12)
check("the Pro layout is off", ui._pro is None)
check("the list keeps its 11 rows", ui.LIST_ROWS == ui.BODY_ROWS - 1 == 11)


class T:
    def __getattr__(self, n):
        return lambda *a, **k: None


g = ui.UI(T(), object(), lambda: b"\x00", node_name="t", trackball=False)
for i in range(14):
    g.add_peer(bytes([i + 1]) * 16, "p%d" % i)
g.selected_idx = 0
g.node_scroll = 0
for _ in range(12):
    g._scroll_down()
check("v1 list window is still 11 rows", g.selected_idx == 12 and g.node_scroll == 2, "%d %d" % (g.selected_idx, g.node_scroll))
g.draw()
check("the old draw path runs without the Pro module", True)
P = b"\x07" * 16
g.add_peer(P, "Alex")
g.selected_peer = P
g.add_chat_message(P, False, "hello there")
rows = g._build_chat_lines()
check("v1 chat lines are the old 8-tuples with the name prefix",
      len(rows) == 1 and len(rows[0]) == 8 and rows[0][1].startswith("Alex> "), repr(rows))
g.state = ui.STATE_CHAT
g._cache = [''] * ui.CACHE_ROWS
g.draw()
g.state = ui.STATE_RECORDING
g._rec_warming = False
g._cache = [''] * ui.CACHE_ROWS
g.draw()


class Rec:
    def __init__(self):
        self.rects = []

    def fill_rect(self, x, y, w, h, c):
        self.rects.append((x, y, w, h))

    def __getattr__(self, n):
        return lambda *a, **k: None


for st, nm in ((ui.STATE_CHAT, "chat"), (ui.STATE_RECORDING, "recording")):
    g.tft = Rec()
    g.state = st
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    arms = [r for r in g.tft.rects if r[2] == 1 and r[3] == 12]
    rails = [r for r in g.tft.rects if r[0] == 0 and r[2] == ui.SCREEN_W and r[3] == 1 and r[1] == ui.NAV_H]
    check("v1 %s still has its body frame" % nm, len(arms) == 4 and len(rails) >= 1, "%d %d" % (len(arms), len(rails)))
g.tft = T()
g.state = ui.STATE_SETTINGS
g._settings_page = ui._SET_MAIN
g._settings_idx = 0
seen = [0]
for _ in range(20):
    g._settings_scroll_down()
    if g._settings_idx != seen[-1]:
        seen.append(g._settings_idx)
check("v1 Setup still walks every row, in SETUP_ORDER", seen == list(ui.SETUP_ORDER), repr(seen))
for _ in range(20):
    g._settings_scroll_up()
    if g._settings_idx != seen[-1]:
        seen.append(g._settings_idx)
check("...and back up", seen[len(ui.SETUP_ORDER) - 1:] == list(reversed(ui.SETUP_ORDER)), repr(seen))
g.tft = Rec()
g._cache = [''] * ui.CACHE_ROWS
g.draw()
check("v1 Setup still has its body frame", len([r for r in g.tft.rects if r[2] == 1 and r[3] == 12]) == 4)
g.tft = Rec2 = type("Rec2", (), {"__init__": lambda s: setattr(s, "texts", []),
                              "text": lambda s, f, x, a, b, *k: s.texts.append((x if isinstance(x, str) else bytes(x).decode("latin-1"), a, b)),
                              "__getattr__": lambda s, n: (lambda *a, **k: None)})()
g._settings_page = ui._SET_NODE_NAME
g.cmd_buf = bytearray(b"x")
g._cache = [''] * ui.CACHE_ROWS
g.draw()
check("v1 sub-page header is the old row at BODY_Y", any(s == "Node Name" and y == ui.BODY_Y for s, x, y in g.tft.texts), repr(g.tft.texts[:3]))


def v1_lines(page, **kw):
    g.tft = type(g.tft)()
    g._settings_page = page
    for k, v in kw.items():
        setattr(g, k, v)
    g._cache = [''] * ui.CACHE_ROWS
    g.draw()
    return [s for s, x, y in g.tft.texts]


ls = v1_lines(ui._SET_WIFI_SCAN, _wifi_networks=[("home", -40)], _settings_idx=3)
check("v1 WiFi scan rows are unchanged", "  {:<28}{}dBm".format("home", -40) in ls, repr(ls[:4]))
ls = v1_lines(ui._SET_LORA, _lora_applying="")
check("v1 LoRa cfg page carries the access-code group and the Apply row",
      "Access code (optional)" in ls and "   Net    none" in ls and "  [ Apply & Save ]" in ls, repr(ls))
g.get_radio_stats = lambda: [("RF", "868000kHz SF8 17dBm and a very long tail")]
ls = v1_lines(ui._SET_RADIO, _settings_scroll=0)
check("v1 Radio stats rows are unchanged (14-column labels, cut at the edge)",
      ("  " + ui._pad("RF", 14) + "868000kHz SF8 17dBm and a very long tail")[:ui.COLS].rstrip() in ls and g._radio_rows == 1, repr(ls))
import rrc_ui  # noqa: E402
check("v1 RRC panel keeps its seven thin rows", rrc_ui.PANEL_ROWS == 7 and rrc_ui.PANEL_H == 158)
g.tft = type(g.tft)()
g.state = ui.STATE_RRC_CHAT
g._rrc_room = "mesh"
g._rrc_members = 3
g._rrc_roster = [(b"\x01" * 16, "alex")]
g._rrc_panel = True
g._rrc_panel_kind = "members"
g._cache = [''] * ui.CACHE_ROWS
g.draw()
ls = [(s, y) for s, x, y in g.tft.texts]
check("v1 room header is the old row", ("#mesh", ui.BODY_Y) in ls, repr(ls[:6]))
check("v1 member row is one line with [hash8]", ("[01010101]", rrc_ui.PANEL_Y + 22) in ls and ("alex", rrc_ui.PANEL_Y + 22) in ls, repr(ls))
g.tft = type(g.tft)()
g.state = ui.STATE_BROWSER
g.browser_title = "CICADA"
g.browser_path = "/page/index.mu"
g.browser_lines = []
g._cache = [''] * ui.CACHE_ROWS
g.draw()
ls = [(s, y) for s, x, y in g.tft.texts]
check("v1 browser header is the old '> title' row", any(s.startswith("> CICADA") and y == ui.BODY_Y for s, y in ls), repr(ls[:5]))
g.tft = type(g.tft)()
g._bl = None
g._screen_on = True
g._panel_flush = lambda force_full=False, user=False: None
g.sleep_screen()
ls = [s for s, x, y in g.tft.texts]
check("v1-layout sleep text is unchanged", "sleeping" in ls and "press any key" in ls, repr(ls))
src = open(os.path.join(ROOT, "ui.py")).read()
i0 = src.index("sleep_tick(self)")
check("the screen-off loop reaches ui_pro only behind the _pro test",
      src[i0 - 80:i0].rstrip().endswith("if _pro is not None:\n                    _pro.") and src.count("sleep_tick(") == 1, src[i0 - 80:i0 + 20])
check("v1 Lookup keeps its thin rows", g._find_rows() == ui.BODY_ROWS - 3)
check("v1 keeps its Find titles", ui.UI._FIND_TITLES[0].startswith("Find peer"))
g.state = ui.STATE_SHELL
g._shell_menu = True
g._shell_menu_idx = 0
ls = v1_lines(ui._SET_MAIN)
g.tft = type(g.tft)()
g._cache = [''] * ui.CACHE_ROWS
g.draw()
ls = [s for s, x, y in g.tft.texts]
check("v1 shell menu is the old one, CLICK and all", "Control keys (trackball):" in ls and "CLICK" in ls and any(s.startswith("> Ctrl-C   interrupt") for s in ls), repr(ls[:6]))
g.state = ui.STATE_SETTINGS
g._has_trackball = True                  # the real v1 has one (this fixture is built without)
ls = v1_lines(ui._SET_WIFI_SCAN, _wifi_networks=[("home", -40)], _settings_idx=0)
check("v1 Setup footer still says CLICK", "CLICK" in ls and "ENT" not in ls, repr(ls))
print()
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("all passed")

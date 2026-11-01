# The ESP32 sleeps only when nothing needs it: screen off, UI idle, LoRa-only
# mode, on battery (or told to ignore USB), radio quiet, no recent failure.
# These tests pin the decision table, the trackball polarity helper and the
# wake classifier, because a wrong gate here either never sleeps (no saving)
# or sleeps at the wrong moment (a transfer stalls, the USB console vanishes).
#
# Run:  python3 tests/test_power.py

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))

import power  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


def _ok_state(**over):
    """A state that allows sleep; override one field to test one gate."""
    s = power.State(screen_on=False, sleep_blocked=False, wifi_on=False,
                    lora_online=True, usb=False, sleep_on_usb=False,
                    quiet_ms=power.QUIET_MS, backoff_ms=0)
    for k, v in over.items():
        setattr(s, k, v)
    return s


print("may_sleep")
check("all clear allows", power.may_sleep(_ok_state()) is None)
check("default State refuses", power.may_sleep(power.State()) is not None)
check("screen on blocks", power.may_sleep(_ok_state(screen_on=True)) == "screen on")
check("ui busy blocks", power.may_sleep(_ok_state(sleep_blocked=True)) == "ui busy")
check("wifi blocks", power.may_sleep(_ok_state(wifi_on=True)) == "wifi")
check("lora offline blocks", power.may_sleep(_ok_state(lora_online=False)) == "lora offline")
check("usb blocks", power.may_sleep(_ok_state(usb=True)) == "usb")
check("usb override allows", power.may_sleep(_ok_state(usb=True, sleep_on_usb=True)) is None)
check("traffic blocks", power.may_sleep(_ok_state(quiet_ms=power.QUIET_MS - 1)) == "traffic")
check("quiet exactly at threshold allows", power.may_sleep(_ok_state(quiet_ms=power.QUIET_MS)) is None)
check("backoff blocks", power.may_sleep(_ok_state(backoff_ms=1)) == "backoff")
check("first reason wins", power.may_sleep(_ok_state(screen_on=True, usb=True)) == "screen on")

print("board wake tables (source scan)")
import re


def _wake_table_src(fname):
    src = open(os.path.join(ROOT, fname)).read()
    m = re.search(r"def sleep_wake_pins\(\):(.*?)\n\n\ndef ", src, re.S)
    return m.group(1) if m else ""


pro = _wake_table_src("board_tdeck_pro.py")
v1 = _wake_table_src("board_tdeck_v1.py")
check("pro table present", pro != "")
check("pro: DIO1 restores IRQ_RISING", "(config.LORA_DIO1, 1, Pin.IRQ_RISING, \"lora\")" in pro, pro)
check("pro: KBD_INT wakes low, no restore", "(config.KBD_INT, 0, 0, \"key\")" in pro, pro)
check("v1 table present", v1 != "")
check("v1: DIO1 45 restores IRQ_RISING", "(45, 1, Pin.IRQ_RISING, \"lora\")" in v1, v1)
check("v1: click restores both edges", "Pin.IRQ_FALLING | Pin.IRQ_RISING, \"input\")" in v1, v1)
check("v1: no trackball movement lines (they self-toggle at rest)", "opposite_levels" not in v1 and "_TB_MOVE" not in v1, v1)
check("v1: keyboard INT 46 is not a wake line (glitches low ~23 ms into every sleep)", "config.KBD_INT" not in v1, v1)

print("classify")
PINS = ((5, 1, 1, "lora"), (15, 0, 0, "input"))


def _rd(levels):
    return lambda g: levels[g]


check("timer", power.classify(0, PINS, _rd({5: 0, 15: 1}), 0, 1) == "timer")
check("gpio + DIO1 high -> lora", power.classify(1, PINS, _rd({5: 1, 15: 1}), 0, 1) == "lora")
check("gpio + INT low -> input", power.classify(1, PINS, _rd({5: 0, 15: 0}), 0, 1) == "input")
check("input beats lora when both", power.classify(1, PINS, _rd({5: 1, 15: 0}), 0, 1) == "input")
check("gpio + nothing active -> other", power.classify(1, PINS, _rd({5: 0, 15: 1}), 0, 1) == "other")
check("unknown cause -> other", power.classify(2, PINS, _rd({5: 1, 15: 0}), 0, 1) == "other")

print("Power.step")


class FakeLowpower:
    WAKE_TIMER, WAKE_GPIO, WAKE_OTHER = 0, 1, 2

    def __init__(self):
        self.calls = []
        self.result = 0
        self.raise_once = None
        self.usb = False
        self.levels = {5: 0, 15: 1}

    def sleep(self, ms, pins):
        self.calls.append((ms, pins))
        if self.raise_once:
            e, self.raise_once = self.raise_once, None
            raise e
        return self.result

    def usb_connected(self):
        return self.usb


class FakeBoard:
    def __init__(self, lp):
        self.lp = lp
        self.before = 0
        self.after = 0
        self.pins = ((5, 1, 1, "lora"), (15, 0, 0, "input"))
        self.powered = False

    def usb_power(self):
        return self.powered

    def sleep_wake_pins(self):
        return self.pins

    def before_sleep(self):
        self.before += 1

    def after_wake(self):
        self.after += 1

    def read_pin(self, g):
        return self.lp.levels[g]


class FakeGui:
    def __init__(self):
        self._screen_on = False
        self.blocked = False
        self.woken = 0

    def _sleep_blocked(self):
        return self.blocked

    def wake_screen(self, why=None):
        self.woken += 1


class FakeIface:
    def __init__(self):
        self.online = True
        self.rxb = 0
        self.txb = 0


def _mk(**over):
    lp = FakeLowpower()
    bd = FakeBoard(lp)
    gui = FakeGui()
    iface = FakeIface()
    clock = [over.pop("t0", 10000)]
    logs = []
    p = power.Power(gui, bd, lp, lambda: iface, lambda: False, False,
                    lambda: clock[0], logs.append, read_pin=bd.read_pin)
    for k, v in over.items():
        setattr(p, k, v)
    return p, lp, bd, gui, iface, clock, logs


p, lp, bd, gui, iface, clock, logs = _mk()
check("first pass: not quiet yet", p.step() == "traffic" and lp.calls == [])
clock[0] += power.QUIET_MS
check("quiet after QUIET_MS: sleeps", p.step() == "timer" and len(lp.calls) == 1)
check("sleep capped", lp.calls[0][0] == power.SLEEP_CAP_MS)
check("pins passed through", lp.calls[0][1] == bd.pins)
check("hooks bracket the sleep", bd.before == 1 and bd.after == 1)
check("timer counted", p.counts["timer"] == 1)
check("screen stays off on timer", gui.woken == 0)

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
lp.result = 1
lp.levels = {5: 1, 15: 1}
check("DIO1 wake -> lora, no screen wake", p.step() == "lora" and gui.woken == 0)
check("lora counted", p.counts["lora"] == 1)

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
lp.result = 1
lp.levels = {5: 0, 15: 0}
check("INT wake -> input, screen wakes", p.step() == "input" and gui.woken == 1)

print("keyboard wakes are left to the keyboard loop")
p, lp, bd, gui, iface, clock, logs = _mk()
bd.pins = ((5, 1, 1, "lora"), (15, 0, 0, "key"))     # the Pro: the key sits in the TCA8418 FIFO
clock[0] += power.QUIET_MS
lp.result = 1
lp.levels = {5: 0, 15: 0}
r = p.step()
check("key wake counted under k", r == "key" and p.counts["input"] == 1, (r, p.counts))
check("key wake does NOT light the screen itself (kbd_loop must swallow the key)", gui.woken == 0)
pro_src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
check("pro: KBD_INT entry has kind key", '(config.KBD_INT, 0, 0, "key")' in pro_src)

p, lp, bd, gui, iface, clock, logs = _mk()
p.step()                         # first reading is the baseline
clock[0] += power.QUIET_MS
iface.rxb = 100
check("counter moved: traffic", p.step() == "traffic" and lp.calls == [])
clock[0] += power.QUIET_MS - 1
check("still inside the window", p.step() == "traffic")
clock[0] += 1
check("quiet again", p.step() == "timer")

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
gui._screen_on = True
check("screen on", p.step() == "screen on")
gui._screen_on = False
gui.blocked = True
check("ui busy", p.step() == "ui busy")
gui.blocked = False
iface.online = False
check("lora offline", p.step() == "lora offline")
iface.online = True
lp.usb = True
check("usb", p.step() == "usb")
p.sleep_on_usb = True
check("usb override", p.step() == "timer")

p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
lp.raise_once = OSError(-1)
check("raise -> backoff reported", p.step() == "backoff")
check("raise logged", any("lowpower" in m or "sleep" in m for m in logs), repr(logs))
check("hooks still bracketed", bd.before == 1 and bd.after == 1)
clock[0] += power.BACKOFF_MS - 1
check("backoff holds", p.step() == "backoff")
clock[0] += 1
check("backoff expires", p.step() == "timer")

p, lp, bd, gui, iface, clock, logs = _mk()
p.wifi_on = lambda: True
clock[0] += power.QUIET_MS
check("wifi on", p.step() == "wifi")

p, lp, bd, gui, iface, clock, logs = _mk()
p.lora_iface = lambda: None
clock[0] += power.QUIET_MS
check("no lora interface", p.step() == "lora offline")

p, lp, bd, gui, iface, clock, logs = _mk()
p.step()
check("summary before any sleep names the reason", p.summary() == "off traffic", p.summary())
p.step()
check("reason logged once", logs.count("power: no sleep: traffic") == 1, repr(logs))
clock[0] += power.QUIET_MS
p.step()
check("summary counts", p.summary() == "l0 k0 t1", p.summary())
check("summary fits a 14-char Radio value", len(p.summary()) <= 14, p.summary())

print("summary remembers the last reason seen while dark")
p, lp, bd, gui, iface, clock, logs = _mk()
p.wifi_on = lambda: True
clock[0] += power.QUIET_MS
p.step()                                   # dark, blocked by wifi
gui._screen_on = True
p.step()                                   # user opened a page to look
check("screen-on pass does not hide the dark reason", p.summary() == "off wifi", p.summary())
p, lp, bd, gui, iface, clock, logs = _mk()
gui._screen_on = True
p.step()
check("never dark yet", p.summary() == "off never idle", p.summary())
p.lora_iface = lambda: None
gui._screen_on = False
clock[0] += power.QUIET_MS
p.step()
check("long reasons are abbreviated to fit", p.summary() == "off no lora", p.summary())

print("wake input pads are configured as inputs (source scan)")
pro_src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
v1_src = open(os.path.join(ROOT, "board_tdeck_v1.py")).read()
check("pro: KBD_INT pad is a pulled-up input", "Pin(config.KBD_INT, Pin.IN, Pin.PULL_UP)" in pro_src)

print("before_sleep veto")
p, lp, bd, gui, iface, clock, logs = _mk()
bd.before_sleep = lambda: False
clock[0] += power.QUIET_MS
check("a False from before_sleep skips the sleep", p.step() == "key pending" and lp.calls == [])
check("after_wake not called on a veto", bd.after == 0)
bd.before_sleep = lambda: None
check("None from before_sleep sleeps as before", p.step() == "timer" and len(lp.calls) == 1)
pro_src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
check("pro: before_sleep vetoes on pending key events", "if keyboard.key_count():" in pro_src and "return False" in pro_src)

print("pro: unused GPS receiver is switched off at boot (source scan)")
pro_src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
check("pro: GPS_EN driven low in power_up", "Pin(config.GPS_EN, Pin.OUT).value(0)" in pro_src)

print("poll cap")
p, lp, bd, gui, iface, clock, logs = _mk()
bd.SLEEP_POLL_MS = 300
clock[0] += power.QUIET_MS
p.step()
check("board poll cap used", lp.calls[0][0] == 300, repr(lp.calls))
v1_src = open(os.path.join(ROOT, "board_tdeck_v1.py")).read()
check("v1 declares SLEEP_POLL_MS = 300", "SLEEP_POLL_MS = 300" in v1_src)

print("board usb_power gate")
p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
bd.powered = True
check("board reports USB power: no sleep even with a silent bus", p.step() == "usb" and lp.calls == [])
p.sleep_on_usb = True
check("override still applies", p.step() == "timer")
check("CONTRACT in test_board lists usb_power", "usb_power" in open(os.path.join(ROOT, "tests", "test_board.py")).read())
check("POLL_MS short enough for 300 ms sleep slices", power.POLL_MS <= 100, power.POLL_MS)

print("summary shows the current blocker, not only history")
p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
check("slept once: counts", p.step() == "timer" and p.summary() == "l0 k0 t1", p.summary())
bd.powered = True
p.step()                                   # dark, now blocked by the cable
check("blocked after sleeping: shows the blocker", p.summary() == "off usb", p.summary())
gui._screen_on = True
p.step()                                   # user looks at the page
check("looking does not hide it", p.summary() == "off usb", p.summary())
gui._screen_on = False
bd.powered = False
check("unblocked and asleep again: counts", p.step() == "timer" and p.summary() == "l0 k0 t2", p.summary())

print("ticks_ms wrap")
p, lp, bd, gui, iface, clock, logs = _mk(t0=(1 << 30) - 400)   # born 400 ms before the wrap
p.step()                                             # baseline reading
clock[0] = ((1 << 30) - 400 + power.QUIET_MS) & ((1 << 30) - 1)   # wrapped
check("quiet window survives the wrap", p.step() == "timer", p.last_reason)
p, lp, bd, gui, iface, clock, logs = _mk(t0=(1 << 30) - 100)
p.step()
clock[0] = ((1 << 30) - 100 + power.QUIET_MS) & ((1 << 30) - 1)
lp.raise_once = OSError(-1)
p.step()                                             # backoff set 100 ms before... no: now
clock[0] = (clock[0] + power.BACKOFF_MS - 1) & ((1 << 30) - 1)
check("backoff holds across the wrap", p.step() == "backoff")
clock[0] = (clock[0] + 1) & ((1 << 30) - 1)
check("backoff expires across the wrap", p.step() == "timer")

print("charger vs host")
p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
bd.powered = True                                     # VBUS, but no SOF: charger or a host still enumerating
check("VBUS without SOF: awake for the grace window", p.step() == "usb")
clock[0] += power.USB_GRACE_MS - 1
check("still inside the window", p.step() == "usb")
clock[0] += 1
check("after the window a silent VBUS is a charger: sleeps", p.step() == "timer")
lp.usb = True                                         # the host started talking
check("SOF seen: no sleep", p.step() == "usb")
lp.usb = False
bd.powered = False
p.step()
bd.powered = True
check("VBUS false->true restarts the grace window", p.step() == "usb")

print("usb probe only after the cheap gates pass")
p, lp, bd, gui, iface, clock, logs = _mk()
probes = [0]
lp.usb_connected = lambda: probes.__setitem__(0, probes[0] + 1) or False
bd.usb_power = lambda: probes.__setitem__(0, probes[0] + 1) or False
gui._screen_on = True
p.step()
check("screen on: no USB probe", probes[0] == 0, probes[0])
gui._screen_on = False
p.step()                                             # traffic (quiet window not yet elapsed)
check("dark but not quiet: USB probed anyway, so the row can say usb", probes[0] >= 1, probes[0])
gui.blocked = True
n = probes[0]
p.step()
check("ui busy: no USB probe", probes[0] == n, probes[0])

print("usb outranks traffic in the row")
p, lp, bd, gui, iface, clock, logs = _mk()
bd.powered = True
check("cable in, inside the quiet window: usb", p.step() == "usb" and p.summary() == "off usb", p.summary())

print("board hook failures back off too")
p, lp, bd, gui, iface, clock, logs = _mk()
clock[0] += power.QUIET_MS
def _boom(): raise OSError(19)
bd.before_sleep = _boom
check("before_sleep raising -> backoff", p.step() == "backoff" and lp.calls == [])
bd.before_sleep = lambda: None
clock[0] += power.BACKOFF_MS
check("backoff expired: sleeps", p.step() == "timer")

print("summary stays within 14 chars")
p, lp, bd, gui, iface, clock, logs = _mk()
p.counts.update({"lora": 15000, "input": 20, "timer": 2000, "other": 3})
check("big counts fit", len(p.summary()) <= 14, p.summary())
check("big counts readable", p.summary().startswith("l15k k20 t2k"), p.summary())
p.counts.update({"lora": 0, "input": 0, "timer": 0, "other": 0})
p.dark_reason = "key pending"; p._blocked = True
check("key pending fits", len(p.summary()) <= 14 and p.summary() == "off key", p.summary())

print("sleep pad policy")
# Bench 2026-10-11 (Pro, battery): stock light sleep 7 mA, ours 40 mA. 13 mA
# was the I2S LRCK pad left driven; ~20 mA the flash/PSRAM pads losing
# ESP-IDF's sleep pull-ups when we keep every pad's awake state. The board
# says which pads to let go and whether the flash/PSRAM pads get their
# standby configuration; Power hands that to the lowpower module once.


class CfgLowpower(FakeLowpower):
    def __init__(self):
        super().__init__()
        self.cfg = []

    def configure(self, pins, mspi):
        self.cfg.append((pins, mspi))


def _mk_cfg(lp, **board_attrs):
    bd = FakeBoard(lp)
    for k, v in board_attrs.items():
        setattr(bd, k, v)
    logs = []
    p = power.Power(FakeGui(), bd, lp, lambda: FakeIface(), lambda: False, False,
                    lambda: 10000, logs.append, read_pin=bd.read_pin)
    return p, logs


lp = CfgLowpower()
_mk_cfg(lp, sleep_isolate_pins=lambda: [9], SLEEP_MSPI_STANDBY=True)
check("the board's pads and flash-pin choice reach the module, once", lp.cfg == [((9,), True)], repr(lp.cfg))
lp = CfgLowpower()
_mk_cfg(lp)
check("a board that says nothing changes nothing", lp.cfg == [((), False)], repr(lp.cfg))
p, logs = _mk_cfg(FakeLowpower(), sleep_isolate_pins=lambda: (9,), SLEEP_MSPI_STANDBY=True)
check("an older firmware's lowpower (no configure) still works", p is not None)


class BadLowpower(FakeLowpower):
    def configure(self, pins, mspi):
        raise ValueError("bad gpio")


p, logs = _mk_cfg(BadLowpower(), sleep_isolate_pins=lambda: (99,), SLEEP_MSPI_STANDBY=True)
check("a refused configuration is logged, not fatal", p is not None and any("pad" in m for m in logs), repr(logs))

src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
check("the Pro asks for the flash/PSRAM standby pads", "SLEEP_MSPI_STANDBY = True" in src)
check("the Pro lets the I2S LRCK pad go in sleep", "def sleep_isolate_pins" in src)

print("idle CPU clock (80 MHz while dark and idle, 240 when lit or busy)")


def _mk_clk(**attrs):
    lp = FakeLowpower()
    bd = FakeBoard(lp)
    gui = FakeGui()
    calls = []
    logs = []
    for k, v in attrs.items():
        setattr(bd, k, v)
    wifi = [False]
    p = power.Power(gui, bd, lp, lambda: FakeIface(), lambda: wifi[0], False, lambda: 10000,
                    logs.append, read_pin=bd.read_pin, set_freq=calls.append)
    p._wifi_flag = wifi
    return p, gui, calls, logs


p, gui, calls, logs = _mk_clk(IDLE_CPU_MHZ=80, AWAKE_CPU_MHZ=240)
p.step()
check("dark + idle: 80 MHz once", calls == [80_000_000], repr(calls))
p.step()
check("no repeat while unchanged", calls == [80_000_000], repr(calls))
gui._screen_on = True
p.step()
check("lit: 240 MHz", calls[-1:] == [240_000_000], repr(calls))
gui._screen_on = False
gui.blocked = True
p.step()
check("dark but blocked: awake clock", calls[-1:] == [240_000_000] and len(calls) == 2, repr(calls))
gui.blocked = False
p.step()
check("unblocked dark: back to 80", calls[-1:] == [80_000_000], repr(calls))
p, gui, calls, logs = _mk_clk()
p.step()
check("board without IDLE_CPU_MHZ: never called", calls == [], repr(calls))
p, gui, calls, logs = _mk_clk(IDLE_CPU_MHZ=80, AWAKE_CPU_MHZ=240)


def _boom(mhz):
    raise OSError("no")


p.set_freq = _boom
p.step()
p.step()
check("set_freq failure logged once, not fatal", sum("clock" in m for m in logs) == 1, repr(logs))
src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
p, gui, calls, logs = _mk_clk(IDLE_CPU_MHZ=80, AWAKE_CPU_MHZ=240)
p._wifi_flag[0] = True
p.step()
check("dark but a station/TCP link up: full clock (backbone traffic is not idle)", calls == [240_000_000], repr(calls))
p._wifi_flag[0] = False
p.step()
check("station down again: 80 MHz", calls == [240_000_000, 80_000_000], repr(calls))
check("the Pro idles at 80 MHz", "IDLE_CPU_MHZ = 80" in src and "AWAKE_CPU_MHZ = 240" in src)
check("the v1 idles at 80 MHz too",
      "IDLE_CPU_MHZ = 80" in open(os.path.join(ROOT, "board_tdeck_v1.py")).read())

# The radio's own regulator: LDO RX is ~10 mA, DC-DC ~5 (SX1262 datasheet),
# and the Pro listens through every sleep. The urns preset carries it.
_presets = open(os.path.join(ROOT, "vendor", "uP-reticulum", "firmware", "lora_boards.py")).read()
_pro_preset = _presets.split('"tdeck_pro_sx1262": {', 1)[1].split("},", 1)[0]
check("the Pro's radio preset runs the SX1262 in DC-DC mode", '"use_dcdc": True' in _pro_preset)
# The v1 has no fuel gauge to measure with and was not on the bench: it keeps
# the sleep it shipped with until someone can check a change on the device.
src = open(os.path.join(ROOT, "board_tdeck_v1.py")).read()
check("the v1 changes nothing about its sleep pads", "SLEEP_MSPI_STANDBY" not in src and "sleep_isolate_pins" not in src)

# Bench 2026-10-11: GPIO 43 (the chip's default UART0 TX, wired to the GPS
# module's receive pin) idles high and feeds the switched-off GPS: 20 mA,
# asleep and awake. The board parks it as an input at power-up.
src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
pu = src[src.index("def power_up():"):src.index("power_up()\n\n")]
check("the Pro parks the GPS serial line at power-up", "Pin(config.GPS_TX, Pin.IN, Pin.PULL_DOWN)" in pu, pu[-400:])
check("...after switching the GPS off", pu.index("config.GPS_EN") < pu.index("config.GPS_TX"))


print("wifi_action (a station with no job must not hold the deck awake)")
# Two drains, same gate: 2026-10-13 the station hunted an absent AP all
# night; 2026-10-31 it sat connected at home with TCP off. A station has a
# job only while TCP is on or someone is looking at the screen.
_W = dict(wanted=True, tcp_wanted=False, connected=True, sta_on=True, screen_on=False,
          dark_ms=power.WIFI_GIVEUP_MS, unconnected_ms=0, since_try_ms=power.WIFI_RETRY_MS)


def _wa(**over):
    d = dict(_W)
    d.update(over)
    return power.wifi_action(**d)


check("not wanted: nothing", _wa(wanted=False) is None)
check("tcp off, connected, dark past give-up: off", _wa() == "off")
check("tcp off, connected, dark before give-up: wait", _wa(dark_ms=power.WIFI_GIVEUP_MS - 1) is None)
check("tcp off, hunting, dark past give-up: off", _wa(connected=False) == "off")
check("tcp off, connected, lit: stay", _wa(screen_on=True, dark_ms=10 ** 7) is None)
check("tcp on, connected, dark: stay", _wa(tcp_wanted=True, dark_ms=10 ** 7) is None)
check("tcp on, hunting, dark past give-up: off",
      _wa(tcp_wanted=True, connected=False, unconnected_ms=power.WIFI_GIVEUP_MS) == "off")
check("tcp on, hunting, dark before give-up: wait",
      _wa(tcp_wanted=True, connected=False, unconnected_ms=power.WIFI_GIVEUP_MS - 1) is None)
check("tcp on, hunting, lit: never off",
      _wa(tcp_wanted=True, connected=False, screen_on=True, unconnected_ms=10 ** 7) is None)
check("lit, radio off, retry due, tcp wished: on", _wa(screen_on=True, sta_on=False, tcp_wanted=True) == "on")
check("lit, radio off, retry due, tcp off: stays off (a glance must not re-associate)",
      _wa(screen_on=True, sta_on=False) is None)
check("lit, radio off, retry not due: wait",
      _wa(screen_on=True, sta_on=False, tcp_wanted=True, since_try_ms=power.WIFI_RETRY_MS - 1) is None)
check("dark, radio off: never on", _wa(sta_on=False, since_try_ms=10 ** 7) is None)
_node_src = open(os.path.join(ROOT, "tdeck_node.py")).read()
_loop_src = _node_src.split("def _wifi_policy_loop", 1)[1].split("async def _low_battery_loop", 1)[0]
_sta_branch = _loop_src.split("if _wifi_sta_on:\n", 1)[1]
check("station up: its job is the live TCP link, never the saved wish (a server that is down must not hold it)",
      "power.tcp_job(" in _sta_branch[:200] and "_load_settings" not in _sta_branch.split("else", 1)[0])
_task_src = _node_src.split("async def _wifi_connect_task", 1)[1].split("def _wifi_give_up", 1)[0]
check("auto connect does not rewrite unchanged settings", "changed" in _task_src.split("if wlan.isconnected():", 1)[1][:900])
_probe_src = open(os.path.join(ROOT, "tools", "tdeck_pro_dcdc_probe.py")).read()
check("dcdc probe leaves continuous RX through the driver before the raw regulator write",
      "modem.standby" in _probe_src.split("def regulator", 1)[1].split('_cmd("BB", 0x96', 1)[0])

print("tcp_job (a TCP link that lost its server is not a job for the station)")
check("no link: no job", power.tcp_job(False, False, 10 ** 7) is False)
check("link online: job", power.tcp_job(True, True, 0) is True)
check("link reconnecting briefly: still a job", power.tcp_job(True, False, power.WIFI_GIVEUP_MS - 1) is True)
check("link offline past give-up: no job", power.tcp_job(True, False, power.WIFI_GIVEUP_MS) is False)
check("loop tracks how long the TCP link has been offline", "_tcp_offline_since" in _loop_src)
check("loop feeds tcp_job", "power.tcp_job(" in _loop_src)
_off_branch = _loop_src.split('if act == "off"', 1)[1].split('elif act == "on"', 1)[0]
check("give-up waits for the policy loop's own connect and for the saved-TCP bring-up",
      "_wifi_auto_connecting" in _off_branch and "_tcp_starting" in _off_branch)
check("the policy loop does not borrow the Setup page's connecting flag",
      "gui._wifi_connecting = " not in _loop_src)
check("the connect task marks its own auto runs in flight", _task_src.count("_wifi_auto_connecting = ") >= 2)
check("auto connect leaves the Setup page's flag alone", "gui._wifi_connecting = " not in _task_src)
check("a read that says 'no TCP wish' is not repeated every 5 s", _loop_src.count("_wifi_last_try = now") >= 2)
check("the synchronous wifi_connect (no callers) is gone", "\ndef wifi_connect(" not in _node_src)
_giveup_src = _node_src.split("def _wifi_give_up", 1)[1].split("\nasync def ", 1)[0]
check("give-up never writes settings, with or without TCP", "_save_settings" not in _giveup_src and "_load_settings" not in _giveup_src)
check("TCP teardown is one function with a save switch", "def _tcp_down(save" in _node_src
      and "_tcp_down(save=False)" in _giveup_src)
check("node never disables WiFi power save (pm=0 kept the receiver on all night)",
      "pm=0" not in _node_src and "PM_NONE" not in _node_src)
_off_branch = _node_src.split("def _wifi_policy_loop", 1)[1].split('if act == "off"', 1)[1].split('elif act == "on"', 1)[0]
check("give-up is guarded against a connect in flight", "_wifi_connecting" in _off_branch)
check("give-up without TCP writes no settings",
      "_save_settings" not in _node_src.split("def _wifi_give_up", 1)[1].split("_wifi_radio_off()", 1)[1][:400])

print("backbone (2026-11-01): one wish, dial on association, one published state")
check("tcp_enabled is written and read nowhere", '"tcp_enabled"' not in _node_src)
check("the UI's TCP toggle, TCP connect and WiFi reconnect callbacks are gone",
      "on_tcp_toggle" not in _node_src and "on_tcp_connect" not in _node_src and "on_wifi_reconnect" not in _node_src)
check("the UI gets one state", "gui.set_backbone_state(" in _node_src and "power.backbone_state(" in _node_src)
check("wifi_shown is gone", "wifi_shown" not in _node_src and not hasattr(power, "wifi_shown"))
_sb_src = _node_src.split("async def _start_backbone", 1)[1].split("\nasync def ", 1)[0]
check("the backbone start has no wish gate and dials saved-else-default", "tcp_enabled" not in _sb_src and "_backbone_target()" in _sb_src)
check("the backbone start clears its flag in a finally", "finally:" in _sb_src and "_tcp_starting = False" in _sb_src.split("finally:", 1)[1])
_bt_src = _node_src.split("def _backbone_target", 1)[1].split("\ndef ", 1)[0]
check("saved else the built-in default", 'TCP_CONFIG["target_host"]' in _bt_src and '"tcp_host"' in _bt_src)
_body_src = _node_src.split("async def _wifi_connect_body", 1)[1].split("\ndef ", 1)[0]
check("association starts the backbone", "_start_backbone()" in _body_src and "_backbone_task = " in _body_src)
check("lit re-dial every WIFI_RETRY_MS while the station is associated with no link",
      "_backbone_last_dial" in _loop_src and "_start_backbone()" in _loop_src
      and "screen_on" in _loop_src.split("_backbone_last_dial", 1)[1][:400])
check("the loop publishes the state", "_publish_backbone()" in _loop_src)
_wd_src = _node_src.split("def wifi_disconnect", 1)[1].split("\ndef ", 1)[0]
check("disconnect cancels the connect in flight and the backbone start before tearing down",
      "_wifi_task" in _wd_src.split("_tcp_down(save=True)", 1)[0] and "_backbone_task" in _wd_src.split("_tcp_down(save=True)", 1)[0])
_ap_src = _node_src.split("async def backbone_apply", 1)[1].split("\ndef ", 1)[0]
check("apply writes what was typed before dialing", _ap_src.index("_save_settings") < _ap_src.index("_dial_backbone("))
check("apply with the station down only saves", "set_tcp_result(True" in _ap_src)
check("the down-station wish is wifi_enabled", 'settings.get("wifi_enabled")' in _loop_src)
check("boot restores the Backbone row's host whatever the WiFi wish",
      "_backbone_target()" in _node_src.split("async def _auto_start_tcp", 1)[1].split("\nasync def ", 1)[0])

print("review 2026-11-01: the station is supervised whenever it is up, one association at a time")
check("policy loop supervises a station that is up even with the wish off (a failed manual connect left it hunting, awake)",
      "if not _wifi_enabled and not _wifi_sta_on:" in _loop_src)
check("the repaint on a state change lives in the UI's setter, not the loop", "gui.dirty = True" not in _loop_src)
_scan_src = _node_src.split("def wifi_scan", 1)[1].split("\ndef ", 1)[0]
check("a scan on a deck whose station was off leaves the radio off afterwards",
      "wlan.active(False)" in _scan_src.split("finally:", 1)[1].split("for iface in paused", 1)[0])
_wca_src = _node_src.split("def wifi_connect_async", 1)[1].split("\nasync def ", 1)[0]
check("one association at a time: a new request cancels the loop's retry or is dropped",
      "_wifi_task" in _wca_src and ".cancel()" in _wca_src)
check("the connect task clears its in-flight flag in a finally",
      "finally:" in _task_src and "_wifi_auto_connecting = False" in _task_src.split("finally:", 1)[1])
_td_src = _node_src.split("def _tcp_down", 1)[1].split("\nasync def ", 1)[0]
check("_tcp_down leaves the shown WiFi state to _wifi_radio_off", "gui._wifi_connected" not in _td_src)
_tt_src = _node_src.split("def _dial_backbone", 1)[1].split("\ndef ", 1)[0]
check("a dial over a live link deregisters it first",
      "deregister_interface" in _tt_src.split("TCPClientInterface(TCP_CONFIG)", 1)[0])

print("backbone_state (one state for the Setup WiFi row)")
check("station off: off", power.backbone_state(False, False, False, False, False) == "off")
check("connect in flight: connecting", power.backbone_state(True, True, False, False, False) == "connecting")
check("associated, link up: up", power.backbone_state(True, False, True, True, True) == "up")
check("associated, no link: no_server", power.backbone_state(True, False, True, False, False) == "no_server")
check("associated, link offline: no_server", power.backbone_state(True, False, True, True, False) == "no_server")
check("station up, no AP, link registered: no_network (never off while the station is up)",
      power.backbone_state(True, False, False, True, False) == "no_network")
check("station up, no AP, no link: no_network", power.backbone_state(True, False, False, False, False) == "no_network")

print("README (the Setup rows after the backbone switch)")
_readme = open(os.path.join(ROOT, "README.md")).read()
_readme_setup = _readme.split("### Setup", 1)[1].split("### Power", 1)[0]
check("README names the Backbone row and no TCP row", "**Backbone**" in _readme_setup and "**TCP**" not in _readme_setup)
check("README no longer saves a TCP enabled state", "TCP enabled state" not in _readme and "`tcp_enabled`" not in _readme)
check("README says WiFi on is the backbone", "WiFi on" in _readme_setup or "backbone" in _readme_setup.split("**WiFi**", 1)[1][:400])

print("LowBattery (cut the pack before the chip browns out)")
check("threshold above the brownout, below the empty mark", 2.9 < power.LOW_BATTERY_V <= 3.3)
lb = power.LowBattery()
check("one low read is not enough", lb.sample(power.LOW_BATTERY_V - 0.1, False) is False)
check("second consecutive low read cuts", lb.sample(power.LOW_BATTERY_V - 0.1, False) is True)
lb = power.LowBattery()
lb.sample(3.0, False)
check("a healthy read in between resets the count",
      lb.sample(3.5, False) is False and lb.sample(3.0, False) is False)
lb = power.LowBattery()
check("never on external power", lb.sample(3.0, True) is False and lb.sample(3.0, True) is False)
lb = power.LowBattery()
check("no reading is not low", lb.sample(None, False) is False and lb.sample(0.0, False) is False
      and lb.sample(None, False) is False)
lb = power.LowBattery()
check("exactly at threshold counts as low",
      lb.sample(power.LOW_BATTERY_V, False) is False and lb.sample(power.LOW_BATTERY_V, False) is True)
lb = power.LowBattery()
check("just above threshold is fine", lb.sample(power.LOW_BATTERY_V + 0.01, False) is False
      and lb.sample(power.LOW_BATTERY_V + 0.01, False) is False)

print("board power-off (source scan)")
_pro_src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
_v1_src = open(os.path.join(ROOT, "board_tdeck_v1.py")).read()
_board_src = open(os.path.join(ROOT, "board.py")).read()
check("board.py re-exports power_off", "power_off = _impl.power_off" in _board_src)
check("pro: power_off is the charger's ship mode (REG09 BATFET_DIS)",
      "def power_off" in _pro_src and "0x09" in _pro_src.split("def power_off", 1)[1][:1500]
      and "0x20" in _pro_src.split("def power_off", 1)[1][:1500])
check("v1: power_off exists and says it cannot", "def power_off" in _v1_src
      and "return False" in _v1_src.split("def power_off", 1)[1][:600])

# The boards have published these since 2026-10-11, but the app hands power.py
# the facade, so until board.py forwards them the Pro slept without its LRCK
# isolation / flash-pad standby and the v1 without its 300 ms keyboard poll.
for _name in ("SLEEP_POLL_MS", "sleep_isolate_pins", "SLEEP_MSPI_STANDBY", "IDLE_CPU_MHZ"):
    check("board.py forwards %s" % _name, ('%s = getattr(_impl, "%s"' % (_name, _name)) in _board_src)

if _failures:
    print("\n%d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("\nall ok")

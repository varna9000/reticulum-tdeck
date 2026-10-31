# power.py -- ESP32 light sleep policy for the T-Deck (v1 and Pro).
#
# The screen timeout is the trigger: once ui.py has put the screen to sleep,
# this module puts the ESP32-S3 to sleep as well, and a received LoRa packet
# (SX1262 DIO1), a keypress, a trackball nudge or a housekeeping timer wakes
# it. The radio keeps receiving throughout; urns' poll loop reads whatever
# arrived on its next pass, so nothing here touches the radio.
#
# The hardware side lives in the `lowpower` built-in C module (tools/
# c_modules/lowpower). Everything here is policy, kept pure so the host tests
# can cover the decision table without a board.

QUIET_MS = 1000        # radio byte counters unchanged for this long
SLEEP_CAP_MS = 20000   # longest single sleep; due asyncio tasks run after
BACKOFF_MS = 60000     # after a failed sleep call
POLL_MS = 100          # how often the loop re-evaluates (awake gap between sleeps)
USB_GRACE_MS = 3000    # VBUS seen but no host frames yet: give enumeration this long

# WiFi with nothing to do. A manual light sleep drops a WiFi association,
# so a station that is up keeps the ESP32 awake (may_sleep: "wifi"). Two
# drains came from leaving one up with no job: carried out of range, the
# station hunted its AP forever (Pro, 2026-10-13: 1400 mAh gone in a day,
# cell at 2 V); at home with TCP off, it sat connected and idle with power
# save disabled (2026-10-31: full to the 3.2 V cut in ~14 h, ~100 mA). The
# station has a job only while TCP is on or someone is looking at the
# screen. With TCP off it is switched off once the deck has been dark this
# long; with TCP on it stays (the link is real-time) unless it has lost its
# AP for this long. Either way it is tried again while the screen is lit.
WIFI_GIVEUP_MS = 30000
WIFI_RETRY_MS = 60000

# Low battery. Nothing else stops the discharge: the chip runs until its
# brownout detector trips (about 2.5 V on the 3.3 V rail, so a cell near
# 2.8 V), reboots, transmits its announce, and browns out again, which pulls
# a lithium cell well under its own protection threshold. Cut the pack at a
# safe voltage instead, after two readings in a row (the battery loop reads
# every 10 s) so one sag under a transmission does not do it.
LOW_BATTERY_V = 3.2
LOW_BATTERY_READS = 2


def _default_ticks_diff(a, b):
    """time.ticks_diff on the device; the same 2^30 wrap arithmetic on the
    host so the tests exercise what the device runs."""
    try:
        import time
        return time.ticks_diff(a, b)
    except AttributeError:
        return ((a - b + (1 << 29)) & ((1 << 30) - 1)) - (1 << 29)

_FIELDS = ("screen_on", "sleep_blocked", "wifi_on", "lora_online",
           "usb", "sleep_on_usb", "quiet_ms", "backoff_ms")


class State:
    """One pass's inputs to may_sleep(). Defaults are the 'cannot sleep'
    side, so a field nobody filled in refuses rather than allows."""

    def __init__(self, **kw):
        self.screen_on = True
        self.sleep_blocked = True
        self.wifi_on = True
        self.lora_online = False
        self.usb = True
        self.sleep_on_usb = False
        self.quiet_ms = 0
        self.backoff_ms = 0
        for k, v in kw.items():
            if k not in _FIELDS:
                raise TypeError("State has no field " + k)
            setattr(self, k, v)


def may_sleep(s):
    """None when the ESP32 may sleep now, else a short reason (logged when
    it changes, shown on the Radio page)."""
    if s.screen_on:
        return "screen on"
    if s.sleep_blocked:
        return "ui busy"
    if s.wifi_on:
        return "wifi"
    if not s.lora_online:
        return "lora offline"
    if s.usb and not s.sleep_on_usb:
        return "usb"
    if s.quiet_ms < QUIET_MS:
        return "traffic"
    if s.backoff_ms > 0:
        return "backoff"
    return None


def wifi_action(wanted, tcp_wanted, connected, sta_on, screen_on, dark_ms,
                unconnected_ms, since_try_ms):
    """What to do about the WiFi station this pass: "off" (a dark deck's
    station has no job: no TCP link, or it has lost its AP), "on" (TCP is
    wished, the screen is lit and the last try is old enough) or None.
    tcp_wanted: the live TCP link while the station is up (a saved wish
    whose server is down must not hold the deck awake), the saved wish
    while it is down (nothing else uses the station, so a glance at the
    screen must not re-associate). dark_ms: how long the screen has been
    off; unconnected_ms: how long the station has been without its AP;
    both 0 when not applicable."""
    if not wanted:
        return None
    if sta_on:
        if screen_on:
            return None
        if not tcp_wanted:
            return "off" if dark_ms >= WIFI_GIVEUP_MS else None
        if not connected and unconnected_ms >= WIFI_GIVEUP_MS:
            return "off"
        return None
    if screen_on and tcp_wanted and since_try_ms >= WIFI_RETRY_MS:
        return "on"
    return None


class LowBattery:
    """Counts consecutive low readings on battery; sample() is True on the
    reading that should power the deck off."""

    def __init__(self):
        self.low = 0

    def sample(self, volts, usb):
        if usb or not volts or volts > LOW_BATTERY_V:
            self.low = 0
            return False
        self.low += 1
        return self.low >= LOW_BATTERY_READS


WAKE_KINDS = ("lora", "input", "timer", "other")


def classify(cause, pins, read, wake_timer, wake_gpio):
    """What woke us. Input lines first: a key and a packet together should
    still light the screen, and urns reads the packet regardless."""
    if cause == wake_timer:
        return "timer"
    if cause != wake_gpio:
        return "other"
    for gpio, level, _restore, kind in pins:
        if kind in ("input", "key") and read(gpio) == level:
            return kind
    for gpio, level, _restore, kind in pins:
        if kind == "lora" and read(gpio) == level:
            return "lora"
    return "other"


def _pin_level(gpio):
    from machine import Pin
    return Pin(gpio).value()


class Power:
    """One sleep policy instance. step() is one pass: decide, maybe sleep,
    classify the wake. Everything it touches arrives through the
    constructor so the host tests can drive it with fakes."""

    def __init__(self, gui, board, lowpower, lora_iface, wifi_on, sleep_on_usb,
                 ticks_ms, log, read_pin=_pin_level, ticks_diff=_default_ticks_diff,
                 set_freq=None):
        self.gui = gui
        self.board = board
        self.lp = lowpower
        self.lora_iface = lora_iface      # () -> interface or None
        self.wifi_on = wifi_on            # () -> bool
        self.sleep_on_usb = sleep_on_usb
        self.ticks_ms = ticks_ms
        self.ticks_diff = ticks_diff      # ticks_ms wraps at 2^30: never subtract
        self.log = log
        self.read_pin = read_pin
        self.counts = {k: 0 for k in WAKE_KINDS}
        self.last_reason = None
        self.dark_reason = None           # last reason seen with the screen off
        self._blocked = False             # the latest dark pass refused to sleep
        self._last_bytes = None
        self._last_change = ticks_ms()
        self._backoff_start = None
        self._vbus_since = None           # when usb_power() last turned true
        # CPU clock while the screen is dark and nothing is transferring: a
        # board that publishes IDLE_CPU_MHZ (the Pro: 80) idles there and
        # runs AWAKE_CPU_MHZ (240) otherwise; 6-11 mA by the ESP32-S3
        # datasheet. set_freq is machine.freq unless the tests inject one.
        self.set_freq = set_freq
        self._clock_idle = None           # None = never set this boot
        self._clock_dead = False          # set_freq raised: leave the clock alone
        # Which pads sleep. The lowpower module keeps every pad in its awake
        # state through light sleep (the board's rails and pull-ups depend on
        # it), and that has two costs measured on the Pro (2026-10-11): a pad
        # left driving a chip that would otherwise idle, and the flash/PSRAM
        # pads losing ESP-IDF's standby pull-ups. The board names the pads to
        # let go and opts in to the flash/PSRAM configuration.
        cfg = getattr(lowpower, "configure", None)
        if cfg is not None:                # absent on firmware before 2026-10-11
            iso = getattr(board, "sleep_isolate_pins", None)
            try:
                cfg(tuple(iso()) if iso else (), bool(getattr(board, "SLEEP_MSPI_STANDBY", False)))
            except Exception as e:
                log("power: sleep pad setup failed: " + str(e))

    def _quiet_ms(self, iface):
        """ms since the radio's byte counters last moved. The first reading
        is the baseline, not a change: quiet is measured from when we
        started watching."""
        now = self.ticks_ms()
        b = (iface.rxb + iface.txb) if iface is not None else None
        if self._last_bytes is None:
            self._last_bytes = b
        elif b != self._last_bytes:
            self._last_bytes = b
            self._last_change = now
        return self.ticks_diff(now, self._last_change)

    def _backoff_ms(self):
        if self._backoff_start is None:
            return 0
        left = BACKOFF_MS - self.ticks_diff(self.ticks_ms(), self._backoff_start)
        if left <= 0:
            self._backoff_start = None
            return 0
        return left

    def _usb(self):
        """External power that should keep us awake. Host frames (SOF) are
        definitive. VBUS alone is either a charger or a host that has not
        enumerated yet -- it needs a few hundred ms of us being awake, which
        the 100 ms gap never gives it -- so VBUS gets USB_GRACE_MS of grace
        from the moment it appears, then counts as a charger and we sleep."""
        now = self.ticks_ms()
        vbus = bool(self.board.usb_power())
        if not vbus:
            self._vbus_since = None
        elif self._vbus_since is None:
            self._vbus_since = now
        if self.lp.usb_connected():
            return True                   # frames flowing: a host, whatever VBUS says
        return vbus and self.ticks_diff(now, self._vbus_since) < USB_GRACE_MS

    def _fail(self, e):
        self._backoff_start = self.ticks_ms()
        self.log("power: sleep failed, backing off: " + str(e))
        return self._note("backoff")

    def _note(self, reason):
        if reason != "screen on":
            # What stopped a dark deck from sleeping. Looking at the Radio
            # page to find out turns the screen on, so "screen on" must not
            # overwrite it.
            self.dark_reason = reason
            self._blocked = True
        if reason != self.last_reason:
            self.last_reason = reason
            self.log("power: no sleep: " + reason)
        return reason

    def _clock(self, idle):
        """Move the CPU clock between the board's idle and awake rates when
        the idle state changes. Never raises: a board that cannot (or a
        firmware without machine.freq) just keeps its clock."""
        idle_mhz = getattr(self.board, "IDLE_CPU_MHZ", None)
        if not idle_mhz or self._clock_dead or idle == self._clock_idle:
            return
        mhz = idle_mhz if idle else getattr(self.board, "AWAKE_CPU_MHZ", 240)
        try:
            fn = self.set_freq
            if fn is None:
                from machine import freq as fn
            fn(int(mhz) * 1000000)
            self._clock_idle = idle
        except Exception as e:
            self._clock_dead = True
            self.log("power: CPU clock change failed, leaving it: " + str(e))

    def step(self):
        iface = self.lora_iface()
        quiet_ms = self._quiet_ms(iface)
        screen_on = bool(self.gui._screen_on)
        sleep_blocked = bool(self.gui._sleep_blocked())
        self._clock(not screen_on and not sleep_blocked)
        s = State(
            screen_on=screen_on,
            sleep_blocked=sleep_blocked,
            wifi_on=bool(self.wifi_on()),
            lora_online=bool(iface is not None and iface.online),
            usb=False,                    # probed below, once the UI-side gates pass
            sleep_on_usb=bool(self.sleep_on_usb),
            quiet_ms=QUIET_MS,            # the traffic gate is applied after the probe
            backoff_ms=self._backoff_ms(),
        )
        reason = may_sleep(s)
        if reason is not None:
            return self._note(reason)
        # The USB probe costs a 3 ms stall plus an I2C or ADC read, so it
        # waits for the screen/UI/WiFi/LoRa gates -- never while the user is
        # typing. It runs ahead of the sub-second traffic gate so that the
        # Radio row says "usb" on a cable rather than whichever packet
        # landed last.
        s.usb = self._usb()
        s.quiet_ms = quiet_ms
        reason = may_sleep(s)
        if reason is not None:
            return self._note(reason)

        try:
            pins = self.board.sleep_wake_pins()
            if self.board.before_sleep() is False:
                # The board saw input it has not handed to the UI yet (a key
                # pressed since the last keyboard poll): let the UI take it
                # and wake the screen rather than sleep through it.
                return self._note("key pending")
            # A board whose keys cannot wake the chip (the v1: its keyboard
            # MCU never drives INT) sets SLEEP_POLL_MS so the keyboard loop
            # gets to poll between short sleeps.
            cap = getattr(self.board, "SLEEP_POLL_MS", None) or SLEEP_CAP_MS
            try:
                cause = self.lp.sleep(cap, pins)
            finally:
                self.board.after_wake()
        except Exception as e:
            return self._fail(e)

        kind = classify(cause, pins, self.read_pin, self.lp.WAKE_TIMER, self.lp.WAKE_GPIO)
        # "key": the event is queued for the UI (the Pro's TCA8418 FIFO), and
        # kbd_loop both swallows that first key and wakes the screen -- only
        # if the screen is still off when it reads it, so this must not wake
        # it. "input": nothing else will (the v1's click, whose edge was lost
        # while its interrupt was disabled), so do it here.
        self.counts["input" if kind == "key" else kind] += 1
        self.last_reason = None
        self._blocked = False
        if kind == "input":
            self.gui.wake_screen(why="input")
        return kind

    def summary(self):
        """One Radio-page value, at most 14 characters (the Pro's 30-column
        page leaves that much after the label): 'l15k k20 t2k' or 'off wifi'."""
        c = self.counts
        total = c["lora"] + c["input"] + c["timer"] + c["other"]
        if total == 0 or self._blocked:
            # Nothing yet, or the latest dark pass refused: say why. Counts
            # alone would read as "still sleeping" to someone who just
            # plugged a cable in.
            r = self.dark_reason or "never idle"
            return "off " + _SHORT_REASON.get(r, r)
        s = "l%s k%s t%s" % (_abbr(c["lora"]), _abbr(c["input"]), _abbr(c["timer"]))
        if c["other"]:
            extra = " ?%s" % _abbr(c["other"])
            if len(s) + len(extra) <= 14:
                s += extra
        return s

# Reasons as shown on the Radio page, where the value has 14 characters.
_SHORT_REASON = {"screen on": "screen", "ui busy": "busy", "lora offline": "no lora",
                 "key pending": "key"}


def _abbr(n):
    """Counts reach tens of thousands a day on a busy mesh: 15000 -> '15k'.
    Three figures must share 14 characters, so anything past 999 is in k."""
    return "%dk" % (n // 1000) if n >= 1000 else str(n)

instance = None


async def loop(p):
    """Run p.step() forever. A failure inside a pass is logged, never fatal."""
    import uasyncio as asyncio
    global instance
    instance = p
    while True:
        await asyncio.sleep_ms(POLL_MS)
        try:
            p.step()
        except Exception as e:
            p.log("power: " + str(e))

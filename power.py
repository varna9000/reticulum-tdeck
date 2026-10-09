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


WAKE_KINDS = ("lora", "input", "timer", "other")


def classify(cause, pins, read, wake_timer, wake_gpio):
    """What woke us. Input lines first: a key and a packet together should
    still light the screen, and urns reads the packet regardless."""
    if cause == wake_timer:
        return "timer"
    if cause != wake_gpio:
        return "other"
    for gpio, level, _restore, kind in pins:
        if kind == "input" and read(gpio) == level:
            return "input"
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
                 ticks_ms, log, read_pin=_pin_level):
        self.gui = gui
        self.board = board
        self.lp = lowpower
        self.lora_iface = lora_iface      # () -> interface or None
        self.wifi_on = wifi_on            # () -> bool
        self.sleep_on_usb = sleep_on_usb
        self.ticks_ms = ticks_ms
        self.log = log
        self.read_pin = read_pin
        self.counts = {k: 0 for k in WAKE_KINDS}
        self.last_reason = None
        self.dark_reason = None           # last reason seen with the screen off
        self._last_bytes = None
        self._last_change = ticks_ms()
        self._backoff_until = None

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
        return now - self._last_change

    def _backoff_ms(self):
        if self._backoff_until is None:
            return 0
        left = self._backoff_until - self.ticks_ms()
        if left <= 0:
            self._backoff_until = None
            return 0
        return left

    def _note(self, reason):
        if reason != "screen on":
            # What stopped a dark deck from sleeping. Looking at the Radio
            # page to find out turns the screen on, so "screen on" must not
            # overwrite it.
            self.dark_reason = reason
        if reason != self.last_reason:
            self.last_reason = reason
            self.log("power: no sleep: " + reason)
        return reason

    def step(self):
        iface = self.lora_iface()
        s = State(
            screen_on=bool(self.gui._screen_on),
            sleep_blocked=bool(self.gui._sleep_blocked()),
            wifi_on=bool(self.wifi_on()),
            lora_online=bool(iface is not None and iface.online),
            # Two signals: the board's power sense (instant, no enumeration
            # needed -- a cable plugged into a sleeping deck must stop the
            # next sleep) and the OTG frame counter (a host actually talking).
            usb=bool(self.board.usb_power() or self.lp.usb_connected()),
            sleep_on_usb=bool(self.sleep_on_usb),
            quiet_ms=self._quiet_ms(iface),
            backoff_ms=self._backoff_ms(),
        )
        reason = may_sleep(s)
        if reason is not None:
            return self._note(reason)

        pins = self.board.sleep_wake_pins()
        if self.board.before_sleep() is False:
            # The board saw input it has not handed to the UI yet (a key
            # pressed since the last keyboard poll): let the UI take it and
            # wake the screen rather than sleep through it.
            return self._note("key pending")
        # A board whose keys cannot wake the chip (the v1: its keyboard MCU
        # never drives INT) sets SLEEP_POLL_MS so the keyboard loop gets to
        # poll between short sleeps.
        cap = getattr(self.board, "SLEEP_POLL_MS", None) or SLEEP_CAP_MS
        try:
            cause = self.lp.sleep(cap, pins)
        except Exception as e:
            self.board.after_wake()
            self._backoff_until = self.ticks_ms() + BACKOFF_MS
            self.log("power: sleep failed, backing off: " + str(e))
            return self._note("backoff")
        self.board.after_wake()

        kind = classify(cause, pins, self.read_pin, self.lp.WAKE_TIMER, self.lp.WAKE_GPIO)
        self.counts[kind] += 1
        self.last_reason = None
        if kind == "input":
            self.gui.wake_screen()
        return kind

    def summary(self):
        """One Radio-page value, at most 14 characters (the Pro's 30-column
        page leaves that much after the label): '12 l9 k2 t1' or 'off wifi'."""
        c = self.counts
        total = c["lora"] + c["input"] + c["timer"] + c["other"]
        if total == 0:
            r = self.dark_reason or "never idle"
            return "off " + _SHORT_REASON.get(r, r)
        s = "%d l%d k%d t%d" % (total, c["lora"], c["input"], c["timer"])
        if c["other"]:
            s += " ?%d" % c["other"]
        return s

# Reasons as shown on the Radio page, where the value has 14 characters.
_SHORT_REASON = {"screen on": "screen", "ui busy": "busy", "lora offline": "no lora"}

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

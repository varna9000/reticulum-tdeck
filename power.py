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
POLL_MS = 250          # how often the loop re-evaluates

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


def opposite_levels(gpios, read, restore):
    """Wake-table entries for lines that idle at either level (the v1's
    trackball quadrature pins): arm each for the opposite of what it reads
    now, so any movement wakes the chip."""
    return tuple((g, 0 if read(g) else 1, restore, "input") for g in gpios)

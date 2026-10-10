# DRV2605L haptic driver, as fitted to the LilyGO T-Deck Pro V1.1: one short
# click, used to confirm a touch at once on a panel that takes ~700 ms to
# show anything.
#
# Checked on the deck with tools/tdeck_pro_haptic_probe.py (2026-10-11):
# the chip is a DRV2605L (status id 7) and answers on I2C whatever GPIO 2
# does, but GPIO 2 is its enable: held low the motor is silent. The motor
# is an ERM; of the effects tried, 24 ("sharp tick") was picked by feel as
# the shortest, crispest confirmation of a tap.

from micropython import const
from machine import Pin

DRV2605_ADDR = const(0x5A)

LIBRARY = 1      # 1..5 = ERM libraries, 6 = LRA
EFFECT = 24      # sharp tick 1, 100%

_REG_MODE = const(0x01)
_REG_LIBRARY = const(0x03)
_REG_SEQ1 = const(0x04)
_REG_SEQ2 = const(0x05)
_REG_GO = const(0x0C)
_REG_FEEDBACK = const(0x1A)


class DRV2605:
    def __init__(self, i2c, en_gpio=None, addr=DRV2605_ADDR):
        self._i2c = i2c
        self._addr = addr
        self.ok = False
        self.enabled = True
        self._en = Pin(en_gpio, Pin.OUT, value=1) if en_gpio is not None else None
        try:
            self._w(_REG_MODE, 0x00)                 # leave standby, internal trigger
            self._w(_REG_LIBRARY, LIBRARY)
            fb = i2c.readfrom_mem(addr, _REG_FEEDBACK, 1)[0]
            self._w(_REG_FEEDBACK, (fb | 0x80) if LIBRARY == 6 else (fb & 0x7F))
            self._w(_REG_SEQ1, EFFECT)
            self._w(_REG_SEQ2, 0x00)
            self.ok = True
        except OSError:
            pass

    def _w(self, reg, val):
        self._i2c.writeto_mem(self._addr, reg, bytes([val]))

    def enable(self, on):
        """Gate the motor driver. An enabled driver draws idle current, so
        the board turns it off whenever the screen is dark. The registers
        survive (the chip stays on the bus with the pin low)."""
        self.enabled = bool(on)
        if self._en is not None:
            self._en.value(1 if on else 0)

    def click(self):
        """Fire the click and return; the chip plays it by itself. Never
        raises: feedback must not be able to break input."""
        if not self.ok or not self.enabled:
            return
        try:
            self._w(_REG_GO, 0x01)
        except OSError:
            pass

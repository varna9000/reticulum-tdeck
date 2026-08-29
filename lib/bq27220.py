# BQ27220 battery fuel gauge, read-only.
#
# The T-Deck v1 senses its battery through a resistor divider into an ADC, which
# is what peripherals/adc_reader.py handles. The Pro has no such divider -- it
# has this gauge on I2C instead -- so adc_reader finds nothing to read and the
# UI sits at 0.0 V forever. This is the replacement.
#
# Only the standard commands are used. They are plain 16-bit little-endian
# reads and need no unsealing, so there is nothing here that can brick a pack
# by writing to it: this module never writes.
#
# Verified on hardware 2026-08-28: 4229 mV, 100 %, 0 mA, 1400 mAh full charge
# capacity, which matches tdeck_pro_config.BQ27220_DESIGN_CAPACITY.

from micropython import const

BQ27220_ADDR = const(0x55)

_VOLTAGE = const(0x08)      # mV
_CURRENT = const(0x0C)      # mA, signed: negative is discharge
_REMAINING = const(0x10)    # mAh
_FULL_CAP = const(0x12)     # mAh
_SOC = const(0x2C)          # %


class BQ27220:
    def __init__(self, i2c, addr=BQ27220_ADDR):
        self._i2c = i2c
        self._addr = addr

    def _word(self, cmd):
        """One 16-bit standard command, or None if the gauge does not answer.

        Returning None rather than raising keeps a missing or asleep gauge from
        taking down a redraw: every caller here is on a display path.
        """
        try:
            self._i2c.writeto(self._addr, bytes([cmd]))
            b = self._i2c.readfrom(self._addr, 2)
        except OSError:
            return None
        return b[0] | (b[1] << 8)

    def voltage_mv(self):
        return self._word(_VOLTAGE)

    def voltage(self):
        """Pack voltage in volts, or None."""
        mv = self._word(_VOLTAGE)
        return None if mv is None else mv / 1000.0

    def current_ma(self):
        """Signed: positive is charge, negative is discharge."""
        v = self._word(_CURRENT)
        if v is None:
            return None
        return v - 65536 if v & 0x8000 else v

    def soc(self):
        """State of charge, 0..100, or None."""
        return self._word(_SOC)

    def remaining_mah(self):
        return self._word(_REMAINING)

    def full_capacity_mah(self):
        return self._word(_FULL_CAP)

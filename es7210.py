# ES7210 4-Channel Audio ADC — MicroPython I2C Driver
# Configured for standard I2S stereo output (non-TDM)
# ADC1 = left channel, ADC2 = right channel on SDOUT1

import time


class ES7210:
    ADDR = 0x40

    # Gain values (bit4 = PGA enable, bits[3:0] = gain level)
    GAIN_0DB    = 0x10
    GAIN_3DB    = 0x11
    GAIN_6DB    = 0x12
    GAIN_9DB    = 0x13
    GAIN_12DB   = 0x14
    GAIN_15DB   = 0x15
    GAIN_18DB   = 0x16
    GAIN_21DB   = 0x17
    GAIN_24DB   = 0x18
    GAIN_27DB   = 0x19
    GAIN_30DB   = 0x1A
    GAIN_33DB   = 0x1B
    GAIN_34_5DB = 0x1C
    GAIN_36DB   = 0x1D
    GAIN_37_5DB = 0x1E

    def __init__(self, i2c, addr=0x40):
        self.i2c = i2c
        self.addr = addr

    def _wr(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val]))
        time.sleep_ms(2)

    def _rd(self, reg):
        return self.i2c.readfrom_mem(self.addr, reg, 1)[0]

    def init(self, gain=0x1E):
        """Initialize ES7210 for 16kHz 16-bit standard I2S stereo (non-TDM).
        MCLK must be provided externally at 4.096MHz (16kHz * 256).
        gain: use GAIN_* constants (default GAIN_37_5DB = 0x1E)."""

        # Software reset
        self._wr(0x00, 0xFF)
        time.sleep_ms(50)
        self._wr(0x00, 0x32)
        time.sleep_ms(20)

        # Disable clocks during configuration
        self._wr(0x01, 0x1F)

        # Power-up timing
        self._wr(0x09, 0x30)
        self._wr(0x0A, 0x30)

        # High-pass filter (remove DC offset)
        self._wr(0x23, 0x2A)
        self._wr(0x22, 0x0A)
        self._wr(0x21, 0x2A)
        self._wr(0x20, 0x0A)

        # I2S format: 16-bit, standard I2S (Philips)
        self._wr(0x11, 0x60)

        # NON-TDM mode: ADC1/2 on SDOUT1, ADC3/4 on SDOUT2
        self._wr(0x12, 0x00)

        # Analog power + VMID
        self._wr(0x40, 0xC3)

        # Mic bias voltage 2.87V
        self._wr(0x41, 0x70)
        self._wr(0x42, 0x70)

        # Mic gain — T-Deck mic is on MIC1 (left channel, confirmed by test)
        self._wr(0x43, gain)  # MIC1 — active mic
        self._wr(0x44, gain)  # MIC2
        self._wr(0x45, gain)  # MIC3
        self._wr(0x46, gain)  # MIC4

        # Power on mic inputs
        self._wr(0x47, 0x00)
        self._wr(0x48, 0x00)
        self._wr(0x49, 0x00)
        self._wr(0x4A, 0x00)

        # Clock config: OSR
        self._wr(0x07, 0x20)

        # ADC clock: doubler + DLL
        self._wr(0x02, 0xC1)

        # LRCK divider = 256 (4.096MHz MCLK / 16kHz = 256)
        self._wr(0x04, 0x01)
        self._wr(0x05, 0x00)

        # DLL power down (0x04 works better with PWM MCLK)
        self._wr(0x06, 0x04)

        # Power up ADC + PGA for all channels
        self._wr(0x4B, 0x0F)
        self._wr(0x4C, 0x0F)

        # Slave mode (ESP32 provides BCLK/LRCK) — 0x20 = slave, 0x00 = master
        self._wr(0x08, 0x20)

        # MCLK from external pin
        self._wr(0x03, 0x04)

        # Enable device
        self._wr(0x00, 0x71)
        time.sleep_ms(10)
        self._wr(0x00, 0x41)
        time.sleep_ms(10)

        # Start clock
        self._wr(0x01, 0x00)
        time.sleep_ms(100)

    def start(self):
        """Enable ADC clock. ADC+PGA stay powered between recordings
        to avoid disrupting the analog path with power register writes."""
        self._wr(0x01, 0x00)  # enable clock

    def stop(self):
        """Disable ADC clock only. ADC+PGA stay powered so next start()
        doesn't need to re-power them (writing 0x4B/0x4C while running
        can glitch the output, and forgetting to restore them was a bug)."""
        self._wr(0x01, 0x7F)  # stop clock only

    def set_gain(self, gain):
        """Set gain on all mics. Use GAIN_* constants."""
        self._wr(0x43, gain)
        self._wr(0x44, gain)
        self._wr(0x45, gain)
        self._wr(0x46, gain)

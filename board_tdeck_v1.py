# T-Deck v1 hardware bring-up: power, shared SPI, ST7789 display, keyboard.
#
# This code lived inline at the top of tdeck_node.py, which is why the comments
# below read as bench notes rather than documentation -- they are kept verbatim.
# It moved here so that tdeck_node.py can select a board instead of hardwiring
# one. board_tdeck_pro.py provides the same names with the same contracts, and
# board.py picks between them.

import time
from machine import Pin, SPI, SoftI2C

import tdeck_config as config
from tdeck_config import (
    DISP_CS, DISP_DC, DISP_BL,
    LORA_MISO,
    KBD_SCL, KBD_SDA, KBD_PWR, KBD_ADDR,
)

# --- board traits ----------------------------------------------------------
# What tdeck_node.py has to branch on. Everything else it reaches for by name.
HAS_TRACKBALL   = True    # ui.py claims GPIO 0/1/2/3/15 for the trackball ISRs
HAS_AUDIO       = True    # MAX98357A I2S speaker
HAS_MIC         = True    # ES7210 I2S ADC, and therefore Codec2 voice
HAS_JPEG_SPLASH = True    # blit_buffer is a fast C path on a real TFT

# --- Peripheral power ON ---
pwr = Pin(KBD_PWR, Pin.OUT)
pwr.on()
time.sleep_ms(100)


def power_up():
    """Already done at import, as it was in tdeck_node.py. Kept for symmetry
    with board_tdeck_pro, where the gates matter enough to be callable."""
    pwr.on()


# --- Shared SPI bus (display + LoRa) ---
# Display: 40MHz. NOT 80: an 80MHz experiment (2026-08-08) rendered cleanly
#          and benched 13% faster, then HARD-FROZE the VM after ~30min of
#          use — stuck in a display-driver C wait with no Ctrl-C, WiFi/lwIP
#          still alive. GPIO-matrix pins at 80MHz are marginal; a glitched
#          transaction wedges the SPI peripheral wait forever. 40MHz is the
#          max reliable clock on these pins, as this comment always said.
# LoRa:    10MHz (SX1262 datasheet max=16MHz — the radio can NEVER share the
#          display clock, hence the acquire/release frequency switch; the
#          switch itself is ~1ms, noise next to a redraw)
_SPI_PINS = {"sck": Pin(40), "mosi": Pin(41), "miso": Pin(LORA_MISO)}
_SPI_DISP = 40_000_000
_SPI_LORA = 10_000_000
spi = SPI(1, baudrate=_SPI_LORA, **_SPI_PINS)

# Display CS — we manage this to keep it deasserted during LoRa ops.
# LoRa CS is managed internally by the lora-sx126x driver.
_disp_cs = Pin(DISP_CS, Pin.OUT, value=1)


def spi_acquire_display():
    """Acquire SPI bus for display: switch to 40MHz."""
    spi.init(baudrate=_SPI_DISP, **_SPI_PINS)


def spi_release_display():
    """Release SPI bus from display: deassert CS, restore LoRa speed."""
    _disp_cs.value(1)
    spi.init(baudrate=_SPI_LORA, **_SPI_PINS)


def spi_acquire_lora():
    """Acquire SPI bus for LoRa: deassert display CS, ensure 10MHz."""
    _disp_cs.value(1)
    spi.init(baudrate=_SPI_LORA, **_SPI_PINS)


def spi_release_lora():
    """Release SPI bus from LoRa."""
    pass  # LoRa driver manages its own CS


# --- Init display ---
try:
    import st7789                       # C driver (firmware-embedded)
    _st7789_c = True
except ImportError:
    import st7789py as st7789           # fallback: pure Python driver
    _st7789_c = False

dc = Pin(DISP_DC, Pin.OUT)
bl = Pin(DISP_BL, Pin.OUT)
bl.value(1)

spi_acquire_display()
tft = st7789.ST7789(spi, 240, 320, dc=dc, cs=_disp_cs, backlight=bl, rotation=1)
if _st7789_c:
    tft.init()                          # C driver requires explicit init
spi_release_display()


def flush():
    """No-op: a TFT is written directly, there is nothing to push.

    The e-ink board needs one of these per rendered screen; ui.py therefore
    calls it unconditionally and this is where it costs nothing.
    """


# --- Init keyboard ---
i2c = SoftI2C(scl=Pin(KBD_SCL), sda=Pin(KBD_SDA), freq=400000, timeout=50000)
time.sleep_ms(500)

# Drain startup garbage
for _ in range(20):
    try:
        i2c.readfrom(KBD_ADDR, 1)
    except OSError:
        pass
    time.sleep_ms(20)


def get_key():
    try:
        return i2c.readfrom(KBD_ADDR, 1)
    except OSError:
        return b'\x00'


def attach_ui(gui):
    """No-op: navigation arrives through the trackball ISRs ui.py installs.

    The Pro has no pointing device and routes its arrow keys into gui.nav_event
    instead, which is why this hook exists at all.
    """


def set_kbd_backlight(on):
    """Drive the keyboard MCU's backlight PWM over I2C
    (LILYGO_KB_BRIGHTNESS_CMD 0x01 + value; 0 = off). Keyboards running
    pre-2023 stock firmware ignore the write — Alt+B still works there.
    Returns True when the I2C write succeeded."""
    try:
        i2c.writeto(KBD_ADDR, bytes([0x01, 160 if on else 0]))
        return True
    except OSError as e:
        if config.DEBUG >= 1:
            print("[Kbd] backlight cmd failed:", e)
        return False

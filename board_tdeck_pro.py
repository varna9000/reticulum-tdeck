# T-Deck Pro hardware bring-up: power, shared SPI, e-ink, keyboard.
#
# tdeck_node.py does this inline for the T-Deck v1. The Pro differs enough that
# it is collected here instead: different power gates, an e-ink panel behind a
# compatibility shim, and a raw key matrix rather than a co-processor that
# hands back ASCII.
#
# SPI arbitration works out the same as the v1 in the end: display and radio
# share one pin set and only the clock rate changes between them. That was not
# obvious from the datasheet-level sources. Meshtastic's variant.h declares
# PIN_EINK_MOSI 47, but on hardware the panel ignores 47 entirely and answers
# on 33, the same MOSI the radio uses. See tdeck_pro_config.DISP_MOSI.

import time
from machine import Pin, SPI, I2C

import tdeck_pro_config as config
from tdeck_pro_config import (
    BOARD_1V8_EN, LORA_EN,
    DISP_CS, DISP_DC, DISP_BUSY,
    LORA_SCK, LORA_MOSI, LORA_MISO,
    I2C_SDA, I2C_SCL, KBD_ADDR, KBD_BL_PIN,
)

import eink_gdeq031t10
import eink_shim
import tca8418

# The panel tolerates a fast bus but gains nothing from one: a refresh is
# ~700 ms, so the transfer is never the bottleneck. Keep it conservative.
_SPI_DISP = 8_000_000
_SPI_LORA = 10_000_000

# --- board traits ----------------------------------------------------------
# What tdeck_node.py has to branch on. Everything else it reaches for by name.
#
# HAS_TRACKBALL is the load-bearing one. ui.py's trackball block claims GPIO
# 0, 1, 2, 3 and 15; on this board GPIO 3 is the SX1262 chip select. Construct
# the UI without trackball=False and the radio silently never works.
HAS_TRACKBALL   = False   # no pointing device; arrows live on the alt layer
HAS_AUDIO       = False   # PCM5102A DAC present, driver not ported
HAS_MIC         = False   # no ES7210, so no Codec2 voice either
HAS_JPEG_SPLASH = False   # blit_buffer here is a per-pixel Python loop

# One shared pin set. Confirmed on hardware: the panel and the radio are on the
# same SCK/MOSI, so only the clock rate differs between them.
_SPI_PINS = {"sck": Pin(LORA_SCK), "mosi": Pin(LORA_MOSI),
             "miso": Pin(LORA_MISO)}


def power_up():
    """Raise the two gates the radio and panel sit behind.

    Nothing on this board answers until both are high, so this runs before any
    peripheral is touched. A correct pin map with these left low looks exactly
    like a wiring fault.
    """
    Pin(BOARD_1V8_EN, Pin.OUT).value(1)
    Pin(LORA_EN, Pin.OUT).value(1)
    time.sleep_ms(100)


power_up()

spi = SPI(1, baudrate=_SPI_LORA, **_SPI_PINS)

_disp_cs = Pin(DISP_CS, Pin.OUT, value=1)
_disp_dc = Pin(DISP_DC, Pin.OUT, value=1)
_disp_busy = Pin(DISP_BUSY, Pin.IN)


def spi_acquire_display():
    spi.init(baudrate=_SPI_DISP, **_SPI_PINS)


def spi_release_display():
    _disp_cs.value(1)
    spi.init(baudrate=_SPI_LORA, **_SPI_PINS)


def spi_acquire_lora():
    _disp_cs.value(1)
    spi.init(baudrate=_SPI_LORA, **_SPI_PINS)


def spi_release_lora():
    pass  # the LoRa driver manages its own CS


# --- display ---------------------------------------------------------------

panel = eink_gdeq031t10.GDEQ031T10(
    spi, _disp_cs, _disp_dc, _disp_busy,
    acquire=spi_acquire_display, release=spi_release_display)

tft = eink_shim.EinkShim(panel)

# No backlight to drive. ui.set_backlight(board.bl) then becomes a no-op and
# the screen-timeout path just stops redrawing, which on e-ink leaves the last
# frame legible on the panel rather than blanking it.
bl = None


def flush():
    """Push the frame to the panel. The only slow call in a redraw.

    Drawing never touches the panel: every ui.py call lands in a framebuffer
    and records a dirty rectangle. Call this once per rendered screen. Once per
    draw call would be 86 refreshes at ~700 ms, which is ten minutes a screen.
    """
    tft.flush()

# --- keyboard --------------------------------------------------------------

# I2C pins confirmed by tools/tdeck_pro_bringup.py; variant.h never names them.
_kbd_bl = Pin(KBD_BL_PIN, Pin.OUT, value=0)
keyboard = None

i2c = I2C(0, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=100000)
if KBD_ADDR in i2c.scan():
    keyboard = tca8418.TCA8418(i2c, KBD_ADDR, bl_pin=_kbd_bl)
else:
    print("[board] TCA8418 did not answer at 0x%02x; keyboard disabled"
          % KBD_ADDR)


def set_kbd_backlight(on):
    """Returns True when the backlight was actually driven, matching the v1."""
    if keyboard is not None:
        keyboard.set_backlight(on)
    else:
        _kbd_bl.value(1 if on else 0)
    return True


# --- key routing -----------------------------------------------------------

# The Pro has no trackball, so the arrow keys on the alt layer drive the same
# navigation counters ui.py's trackball ISRs would have. Routing them here
# rather than teaching ui.py new key codes keeps every navigation path in the
# UI identical across both boards.
_NAV = {
    tca8418.KEY_UP: "up",
    tca8418.KEY_DOWN: "down",
    tca8418.KEY_LEFT: "left",
    tca8418.KEY_RIGHT: "right",
}

_gui = None


def attach_ui(gui):
    """Point key routing at the UI instance, after it has been constructed."""
    global _gui
    _gui = gui


def get_key():
    """One keystroke, or b'\\x00' if nothing is pending.

    Same contract as the v1's i2c.readfrom(KBD_ADDR, 1), so this drops straight
    into UI(get_key_func=...). Note the empty result is b'\\x00' and not the
    driver's b'': ui.py and tdeck_node.py both test against b'\\x00', and a
    board adapter is the right place to absorb that difference.

    Arrow keys are consumed here and turned into navigation events rather than
    being passed through as characters.
    """
    if keyboard is None:
        return b'\x00'
    k = keyboard.get_key()
    if not k:
        return b'\x00'
    nav = _NAV.get(k[0])
    if nav is not None:
        if _gui is not None:
            _gui.nav_event(nav)
        return b'\x00'
    return k

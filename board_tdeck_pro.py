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
    LORA_EN,
    DISP_CS, DISP_DC, DISP_BUSY,
    LORA_SCK, LORA_MOSI, LORA_MISO,
    I2C_SDA, I2C_SCL, KBD_ADDR, KBD_BL_PIN,
    BQ27220_ADDR,
)

import bq27220
import eink_gdeq031t10
import eink_shim
import tca8418
import tdeck_pro_rev

# --- revision --------------------------------------------------------------
# Resolved first, from an I2C scan with nothing powered (the DRV2605 that
# marks a V1.1 answers regardless), because what differs includes a power
# gate. config.BOARD_REV is "auto" or an override; see tdeck_pro_rev.py.
i2c = I2C(0, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=100000)
# Detection runs once: the result lives in tdeck_pro_rev.PROFILE_PATH and is
# read back on later boots. A config value other than "auto" overrides it.
_profile = tdeck_pro_rev.load_profile()
_rev_setting = getattr(config, "BOARD_REV", "auto")
if _profile is not None and str(_rev_setting).strip().lower() == "auto":
    BOARD_REV = _profile["rev"]
else:
    BOARD_REV = tdeck_pro_rev.resolve(_rev_setting, i2c.scan())
BOARD_1V8_EN, DISP_RES = tdeck_pro_rev.pins(BOARD_REV)
print("[board] T-Deck Pro V%s (BOARD_REV=%s%s)" % (
    BOARD_REV, _rev_setting, ", saved" if _profile is not None else ""))

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
# The Pro ships in two versions that share GPIO 7/8/9: one with the PCM5102A
# I2S DAC, one with the A7682E 4G modem (its RI/ITR/RST lines). Driving those
# pins as I2S on a modem unit would fight the modem, so the DAC is opt-in from
# the config file, and absent there (an older config) means off. The pins are
# LilyGO's README numbers -- Meshtastic's variant.h says 47/17/18, which puts
# the bit clock on LoRa MISO and is wrong, like its e-ink MOSI.
# HAS_AUDIO: True/False from the config, or "auto" -- then the saved profile,
# or (first boot only) a modem probe once the panel is up, see below.
_audio_setting = tdeck_pro_rev.audio_setting(getattr(config, "HAS_AUDIO", "auto"))
HAS_AUDIO       = None    # resolved just below, or after the display is up
if _audio_setting == "auto" and _profile is not None and _profile.get("audio") is not None:
    HAS_AUDIO = _profile["audio"]
elif _audio_setting == "auto":
    HAS_AUDIO = None           # decided after the display is up
else:
    HAS_AUDIO = _audio_setting
SPK_PINS        = None    # set once HAS_AUDIO is final (after the display is up)
# The DAC's small speaker is near-silent at the stock chirps (440-660 Hz,
# amplitude ~2400): an octave up and 8x the level is what it took to hear
# them (ladder test on hardware, 2026-10-09). Editable in the config file.
AUDIO_GAIN      = getattr(config, "AUDIO_GAIN", 8)
AUDIO_PITCH     = getattr(config, "AUDIO_PITCH", 2)
# A two-wire PDM microphone on MIC_CLOCK 18 / MIC_DATA 17 (LilyGO's V1.1
# pinmap; Meshtastic's variant.h mislabels these as DAC pins). Read through
# the pdm_mic built-in module on I2S port 0, which is why playback is on 1.
HAS_MIC         = bool(getattr(config, "HAS_MIC", False))
MIC_KIND        = "pdm" if HAS_MIC else None
MIC_PINS        = ((getattr(config, "MIC_CLK", 18), getattr(config, "MIC_DATA", 17))
                   if HAS_MIC else None)
SPK_PORT        = 1
HAS_JPEG_SPLASH = False   # blit_buffer here is a per-pixel Python loop
MONO_DISPLAY    = True    # 1-bit panel: "dimmed" cannot be shown as a shade

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
    if BOARD_1V8_EN is not None:        # V1.1/MAX have no 1.8 V gate (tdeck_pro_rev)
        Pin(BOARD_1V8_EN, Pin.OUT).value(1)
    Pin(LORA_EN, Pin.OUT).value(1)
    # The GPS receiver's LDO enable idles HIGH on a V1.1 (bench 2026-10-09:
    # GPIO 39 read 1 and the MIA-M10Q was streaming NMEA with nothing driving
    # it; the V1.0 sheet shows a pull-down). Nothing here reads the GPS, so
    # hold it off: it would otherwise run through every sleep.
    Pin(config.GPS_EN, Pin.OUT).value(0)
    if HAS_AUDIO:              # True here only when the config or the saved profile says so
        # The DAC sits in the module slot, behind its own power enable.
        Pin(getattr(config, "AUDIO_EN", 41), Pin.OUT).value(1)
    time.sleep_ms(100)


power_up()

spi = SPI(1, baudrate=_SPI_LORA, **_SPI_PINS)

_disp_cs = Pin(DISP_CS, Pin.OUT, value=1)
_disp_dc = Pin(DISP_DC, Pin.OUT, value=1)
_disp_busy = Pin(DISP_BUSY, Pin.IN)
# V1.1/MAX have a real panel reset line (tdeck_pro_rev.pins); the driver
# pulses it on init. V1.0 has none and resets the panel over a command.
_disp_rst = Pin(DISP_RES, Pin.OUT, value=1) if DISP_RES >= 0 else None


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
    spi, _disp_cs, _disp_dc, _disp_busy, rst=_disp_rst,
    acquire=spi_acquire_display, release=spi_release_display)

tft = eink_shim.EinkShim(panel)


def _first_boot_audio_probe():
    """First boot with HAS_AUDIO = "auto" and no saved answer: ask the
    module slot whether a modem lives there. Shown on the panel because it
    takes up to ~12 s, and done exactly once (the answer is saved)."""
    import spleen_8x16 as _font

    def _centered(lines, y0):
        for i, line in enumerate(lines):
            x = (tft.width - len(line) * _font.WIDTH) // 2
            tft.text(_font, line, x, y0 + i * (_font.HEIGHT + 4), 0xFFFF, 0x0000)

    tft.fill(0x0000)
    _centered(("T-Deck Pro V" + BOARD_REV, "", "First boot", "",
               "Detecting 4G / audio", "module (~10 s)..."), 100)
    tft.flush(force_full=True)
    from machine import UART
    slot_en = Pin(getattr(config, "AUDIO_EN", 41), Pin.OUT, value=1)   # powers either module
    pwrkey = Pin(getattr(config, "MODEM_PWRKEY", 40), Pin.OUT, value=1)
    uart = UART(1, baudrate=115200, tx=getattr(config, "MODEM_RXD", 10),
                rx=getattr(config, "MODEM_TXD", 11))
    try:
        modem = tdeck_pro_rev.probe_modem(uart, pwrkey, time.sleep_ms, time.ticks_ms,
                                          time.ticks_diff)
    finally:
        uart.deinit()
    if modem:
        slot_en.value(0)       # a modem nobody uses should not drain the pack
    print("[board] first boot: modem %s -> audio %s" % ("answered" if modem else "silent",
                                                       not modem))
    _centered(("4G modem found" if modem else "Audio module found",
               "Saved; not asked again"), 220)
    tft.flush()
    time.sleep_ms(1500)
    return not modem


if HAS_AUDIO is None:
    HAS_AUDIO = _first_boot_audio_probe()
if _profile is None or (_profile.get("audio") is None and _audio_setting == "auto"):
    try:
        tdeck_pro_rev.save_profile(BOARD_REV, HAS_AUDIO if _audio_setting == "auto" else None)
    except Exception as _e:
        print("[board] could not save the hardware profile:", _e)

SPK_PINS = ((getattr(config, "I2S_BCLK", 7), getattr(config, "I2S_LRCK", 9),
             getattr(config, "I2S_DOUT", 8)) if HAS_AUDIO else None)
if HAS_AUDIO:
    # Raised in power_up() when the config said so outright; a probed answer
    # arrives only now.
    Pin(getattr(config, "AUDIO_EN", 41), Pin.OUT).value(1)

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

if KBD_ADDR in i2c.scan():
    keyboard = tca8418.TCA8418(i2c, KBD_ADDR, bl_pin=_kbd_bl)
else:
    print("[board] TCA8418 did not answer at 0x%02x; keyboard disabled"
          % KBD_ADDR)


# --- touch -------------------------------------------------------------------
# The CST328 hands the gesture layer DOWN/MOVE/UP events; it is held in reset
# whenever the screen is dark (see _service_touch below), so light sleep
# finds it already off and the keyboard stays the only input that wakes the
# deck. Everything about the chip itself is in lib/cst328.py.
import cst328

HAS_TOUCH = bool(getattr(config, "HAS_TOUCH", True))
_TOUCH_RST = tdeck_pro_rev.touch_reset(BOARD_REV)
touch = None
if HAS_TOUCH and config.TOUCH_ADDR in i2c.scan():
    touch = cst328.CST328(i2c, config.TOUCH_INT, _TOUCH_RST, addr=config.TOUCH_ADDR)
    touch.sleep()            # woken by the first get_key() pass with the screen on
else:
    Pin(_TOUCH_RST, Pin.OUT, value=0)
    if HAS_TOUCH:
        print("[board] CST328 did not answer at 0x%02x; touch disabled" % config.TOUCH_ADDR)


# --- battery -----------------------------------------------------------

# No ADC divider on this board, so peripherals/adc_reader.py has nothing to
# read and the UI would sit at 0.0 V forever. The gauge is the sense path here.
gauge = None
if BQ27220_ADDR in i2c.scan():
    gauge = bq27220.BQ27220(i2c, BQ27220_ADDR)
else:
    print("[board] BQ27220 did not answer at 0x%02x; battery unavailable"
          % BQ27220_ADDR)


def battery_voltage():
    """Pack voltage in volts, or None when there is no reading."""
    if gauge is None:
        return None
    return gauge.voltage()


def battery_percent():
    """State of charge 0..100, or None. The gauge knows this properly, unlike
    the v1, which can only infer it from voltage."""
    if gauge is None:
        return None
    return gauge.soc()


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
    # Alt+W is this board's press of the ball. The v1 opens the RRC member
    # panel and the room picker by clicking the trackball; without a click
    # here the Pro could open neither, and since joining by name lives on
    # the picker's action row it could not join a room at all.
    tca8418.KEY_CLICK: "click",
}

_gui = None


def attach_ui(gui):
    """Point key routing at the UI instance, after it has been constructed."""
    global _gui
    _gui = gui


def _touch_handler(ev):
    """Where touch events go. The gesture layer (the next piece of the
    touch work) replaces this; until then a touch only keeps the screen
    awake and, at DEBUG >= 2, shows up on the console."""
    if _gui is not None and ev[0] == cst328.DOWN:
        _gui.note_user_input()
    if config.DEBUG >= 2:
        print("[touch]", ev)


def _service_touch():
    """One pass, from get_key(): keep the chip's reset in step with the
    screen, then drain its events. The screen-off case holds the chip in
    reset; the screen-on case pulses it once and retries wake() on later
    passes until it answers (~200 ms), without blocking the key loop."""
    if touch is None or _gui is None:
        return
    if not _gui._screen_on:
        if not touch.asleep:
            touch.sleep()
        return
    if not touch.ready:
        if not touch.wake():
            return
    for ev in touch.poll():
        _touch_handler(ev)


def _get_key():
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
    if k[0] == tca8418.KEY_BL_TOGGLE:
        # The driver surfaces alt+B rather than driving the pin itself, so the
        # toggle lands on the UI's persisted setting instead of going behind it.
        if _gui is not None:
            _gui.toggle_kbd_backlight()
        return b'\x00'
    return k


def get_key():
    """The keyboard, plus one touch pass per call: UI.kbd_loop calls this
    every 20 ms, which is the cadence the touch driver was designed for."""
    k = _get_key()
    _service_touch()
    return k


# --- light sleep -----------------------------------------------------------
# The keyboard INT line is polled by nobody (get_key() works over I2C), so
# until here nothing configured its pad. An ESP32-S3 pad left at reset has
# its input buffer disabled and reads 0 -- the "key pressed" level -- which
# woke the chip the instant it slept and lit the screen (bench 2026-10-09).
# The board carries a pull-up on KEY_INT; the internal one is belt and braces.
_kbd_int = Pin(config.KBD_INT, Pin.IN, Pin.PULL_UP)


def sleep_wake_pins():
    """DIO1 wakes high and gets the LoRa driver's rising-edge ISR back; the
    TCA8418 INT wakes low and had no interrupt of its own. Its kind is
    "key": the keystroke waits in the controller's FIFO and kbd_loop both
    swallows it and wakes the screen, provided nobody lit the screen first
    -- otherwise that key would be acted on (Enter would send a draft)."""
    return (
        (config.LORA_DIO1, 1, Pin.IRQ_RISING, "lora"),
        (config.KBD_INT, 0, 0, "key"),
    )


BQ25896_ADDR = 0x6B


def usb_power():
    """VBUS present, from the charger's REG11 VBUS_GD bit. Instant, and
    independent of whether the host has enumerated us yet."""
    try:
        return bool(i2c.readfrom_mem(BQ25896_ADDR, 0x11, 1)[0] & 0x80)
    except Exception:
        return False


def before_sleep():
    """None to sleep, False to veto: a key pressed since the last poll is
    still in the TCA8418 FIFO, and the chip re-asserts INT while the FIFO
    holds anything, so the sleep would end at once and the keystroke would
    be lost. Let the keyboard loop take it instead. With the FIFO empty,
    clear the stale key-event flag that get_key() leaves behind, or INT
    stays low and wakes the chip straight back up."""
    if keyboard is not None:
        if keyboard.key_count():
            return False
        keyboard.clear_int()
    return None


def after_wake():
    pass

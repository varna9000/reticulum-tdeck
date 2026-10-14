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
import cst328
import drv2605
import eink_gdeq031t10
import eink_shim
import tca8418
import tdeck_pro_rev
import supervise

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
    # ...and take our serial line off it. GPIO 43 is the chip's default UART0
    # TX and comes out of the boot ROM driven high, straight into the GPS
    # module's receive pin: with the module's supply off that fed it through
    # the pin instead (bench 2026-10-11, battery: light sleep 25 mA with the
    # pad live, 5 mA with it isolated; no other pad on the board mattered).
    # Nothing here uses UART0 -- the REPL is USB -- so park it as an input.
    Pin(config.GPS_TX, Pin.IN, Pin.PULL_DOWN)
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
#
# No I2C scan decides whether the chip is there. Its reset pad is still
# driven low after a soft reset (the last sleep() left it so), and on a V1.0
# it is a strapping pin with a pull-down, so at import the chip is often off
# the bus; a scan here read that as "no touch panel" until the next power
# cycle. The driver's own wake() retries decide instead, each time the
# screen comes on.
HAS_TOUCH = bool(getattr(config, "HAS_TOUCH", True))
_TOUCH_RST = tdeck_pro_rev.touch_reset(BOARD_REV)
touch = None
if HAS_TOUCH:
    touch = cst328.CST328(i2c, config.TOUCH_INT, _TOUCH_RST, addr=config.TOUCH_ADDR)
    touch.sleep()            # woken by the first get_key() pass with the screen on
else:
    Pin(_TOUCH_RST, Pin.OUT, value=0)
_touch_reported = False

# Feedback for an accepted touch: the motor where there is one (the chip's
# presence is what marks a V1.1), else a 50 ms blink of the keyboard light.
# PIN_VIBRATION is the driver's enable (probed 2026-10-11: held low, the
# motor is silent). It stays low while the screen is dark, since an enabled
# driver idles at a current the sleep work cannot afford; _service_touch
# raises it with the screen.
HAS_HAPTICS = bool(getattr(config, "HAS_HAPTICS", True))
haptic = None
if HAS_TOUCH and HAS_HAPTICS and drv2605.DRV2605_ADDR in i2c.scan():
    haptic = drv2605.DRV2605(i2c, en_gpio=config.PIN_VIBRATION)
    if haptic.ok:
        haptic.enable(False)
    else:
        haptic = None
_blink_until = None
_touch_ctl = None
# Driver events read while the panel was refreshing, oldest first. The key
# loop does not run for those ~650 ms; without this a drag is seen once per
# frame and whatever the finger did in between -- the rest of a swipe, the
# lift that makes it a flick -- is gone.
_touch_backlog = []
_TOUCH_BACKLOG_MAX = 160     # a full refresh at one event per ~9 ms, with room


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


def _touch_feedback():
    """An accepted touch: say so now, the panel will catch up in ~700 ms."""
    global _blink_until
    if _gui is None or not _gui._haptics:
        return                      # Setup > Touch feedback, off unless turned on
    if haptic is not None:
        haptic.click()
    elif HAS_HAPTICS and _gui is not None and _blink_until is None:
        # Flip the keyboard light for 50 ms, whatever it is showing, without
        # going through the UI's setting (that path persists the preference).
        set_kbd_backlight(not _gui._kbd_bl_lit)
        _blink_until = time.ticks_add(time.ticks_ms(), 50)


def _touch_handler(ev):
    """One driver event: stamp activity, then let the controller turn it
    into gestures and act on them.

    Activity is the inactivity clock, which only wake_screen() stamps (the
    key loop calls it for every key too). The screen is already on when a
    finger is down, so this lights nothing and it respects the lock. An UP
    is left out on purpose: the driver also sends one to close a stroke as
    the screen goes dark, and that must not turn it back on."""
    if _gui is not None and ev[0] != cst328.UP:
        _gui.wake_screen(why="touch")
    if _touch_ctl is not None:
        _touch_ctl.event(ev)
    if config.DEBUG >= 2:
        print("[touch]", ev)


def _touch_sample():
    """The panel driver's idle hook: called every few ms while a refresh
    holds the loop. It only reads the chip and queues what it says -- the
    events carry their own times, and _service_touch acts on them on the
    next pass. Acting here would change the UI in the middle of its flush."""
    if touch is None or _gui is None or not _gui._screen_on or not touch.ready:
        return
    for ev in touch.poll():
        # Full: only MOVEs are dropped. Every DOWN still gets its UP.
        if len(_touch_backlog) < _TOUCH_BACKLOG_MAX or ev[0] != cst328.MOVE:
            _touch_backlog.append(ev)


if touch is not None:
    panel.idle = _touch_sample


def _service_touch():
    """One pass, from get_key(). The driver keeps its own reset in step
    with the screen; the controller stamps layout changes and runs the
    long-press clock before this pass's events arrive."""
    global _touch_reported, _touch_ctl, _blink_until
    if touch is None or _gui is None:
        return
    if _touch_ctl is None:
        # Imported here, not at the top: touch_ui pulls in ui.py, and this
        # module is imported before the app has brought the radio and the
        # panel up. The first pass with a UI attached is early enough.
        import touch_ui
        _touch_ctl = touch_ui.Controller(_gui, feedback=_touch_feedback, report=supervise.report)
    now = time.ticks_ms()
    if _blink_until is not None and time.ticks_diff(now, _blink_until) >= 0:
        _blink_until = None
        set_kbd_backlight(_gui._kbd_bl_lit)
    if haptic is not None:
        want = _gui._screen_on and _gui._haptics
        if haptic.enabled != want:
            haptic.enable(want)
    _touch_ctl.service(now)
    if _touch_backlog:
        evs = _touch_backlog[:]
        del _touch_backlog[:]
        for ev in evs:
            _touch_handler(ev)
    for ev in touch.service(_gui._screen_on):
        _touch_handler(ev)
    if touch.gave_up and not _touch_reported:
        _touch_reported = True
        print("[board] CST328 did not answer at 0x%02x; touch is off until the screen next wakes"
              % config.TOUCH_ADDR)


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
    if _touch_ctl is not None:
        _touch_ctl.key(time.ticks_ms())     # thumbs are on the keys: no taps for 300 ms
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


# Sleep current, measured on battery 2026-10-11 (tools/tdeck_pro_*sleep*_probe.py):
# the chip's stock light sleep, every pad isolated, is 7 mA; ours, with every
# pad kept in its awake state, was 40 mA. Two pads' worth of difference:
#   * the I2S LRCK line left driven (high or low) keeps the PCM5102A side
#     awake: 13 mA. Isolated for the sleep it floats and the DAC idles
#     (5-6 mA with every other pad still held). Nothing else on the board
#     cares: panel, radio, I2C, the INT lines, touch reset, keyboard light,
#     vibration, GPS/modem/SD, USB, mic, BCLK and DOUT each add nothing.
#   * the GPS serial line, GPIO 43, driven high into the switched-off GPS:
#     20 mA. That one is parked in power_up(), awake or asleep.
# The flash/PSRAM pads get ESP-IDF's sleep pull-ups as well (0.5 mA here).
SLEEP_MSPI_STANDBY = True


def sleep_isolate_pins():
    """Pads the lowpower module switches to high impedance for each sleep
    and back on wake (ESP-IDF's per-pad sleep configuration)."""
    return (config.I2S_LRCK,)


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


HAS_POWER_OFF = True     # the BQ25896's ship mode, see power_off()


def power_off():
    """Cut the pack from the board: the BQ25896's ship mode (REG09 bit 5,
    BATFET_DIS) opens the FET between battery and system, and the whole
    board is off at the charger's shipping current. The charger leaves ship
    mode again when a cable is plugged in (VBUS), which is how a deck that
    ran its battery down is meant to come back. Only called on battery:
    with VBUS present the system would just keep running from USB. Bit 3
    (BATFET_DLY) is cleared so the FET opens now rather than 10 s later.
    True when the write went in; the deck is then gone within a moment."""
    try:
        r = i2c.readfrom_mem(BQ25896_ADDR, 0x09, 1)[0]
        i2c.writeto_mem(BQ25896_ADDR, 0x09, bytes([(r | 0x20) & ~0x08]))
        return True
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

# Board selection.
#
# tdeck_node.py runs on more than one machine now, and the two differ in ways
# that reach past a pin table: the T-Deck v1 has a TFT it can repaint freely, a
# trackball, a speaker and a microphone; the T-Deck Pro has an e-ink panel that
# has to be flushed, a key matrix, and none of the audio. Rather than branch on
# the board in 1600 lines of app code, each board publishes the same set of
# names and this module picks one.
#
# The contract is the list at the bottom of this file. A new board implements
# it and nothing above the board layer changes.
#
# Which board is chosen comes from board_id.py, a one-line file the deploy
# script writes onto the device. It is a marker rather than a probe on purpose:
# guessing wrong here would drive the wrong pins as outputs before anything has
# had a chance to notice, and on the Pro two of the v1's trackball pins are the
# radio's chip select and the side button.

try:
    from board_id import BOARD
except ImportError:
    # No marker: the T-Deck v1, which is what every existing install is.
    BOARD = "tdeck_v1"

if BOARD == "tdeck_pro":
    import board_tdeck_pro as _impl
elif BOARD == "tdeck_v1":
    import board_tdeck_v1 as _impl
else:
    raise ValueError("unknown BOARD in board_id.py: %r" % (BOARD,))

# --- the board contract ----------------------------------------------------
# Re-exported by name rather than with `import *` so that this list is the
# documentation: anything a board has to provide appears here, and a board
# missing a piece fails at import instead of at first use.

# Configuration module for this board (pins, radio parameters, node name).
config = _impl.config

# Traits the app branches on.
HAS_TRACKBALL = _impl.HAS_TRACKBALL
HAS_AUDIO = _impl.HAS_AUDIO
# (bck, ws, dout) for sound.Sound.init() where HAS_AUDIO; None otherwise.
SPK_PINS = _impl.SPK_PINS
# Tone level and pitch multipliers for this board's speaker (1 and 1 = the
# tones as written, which the v1's amplifier wants; the Pro needs more).
AUDIO_GAIN = _impl.AUDIO_GAIN
AUDIO_PITCH = _impl.AUDIO_PITCH
# I2S port for playback (the Pro's PDM mic needs port 0, so it plays on 1).
SPK_PORT = _impl.SPK_PORT
# Microphone: "es7210" (v1, over I2C + I2S) or "pdm" (Pro, MIC_PINS =
# (clk, data)); None where HAS_MIC is False.
MIC_KIND = _impl.MIC_KIND
MIC_PINS = _impl.MIC_PINS
HAS_MIC = _impl.HAS_MIC
HAS_JPEG_SPLASH = _impl.HAS_JPEG_SPLASH
# True where the panel is 1-bit. "Dimmed" is not a shade there, so anything
# drawn dim to mean "off" has to be drawn as background instead.
MONO_DISPLAY = _impl.MONO_DISPLAY

# Shared SPI bus, and the arbitration that lets the display and the radio take
# turns on it. Both boards share one pin set and switch only the clock rate.
spi = _impl.spi
spi_acquire_display = _impl.spi_acquire_display
spi_release_display = _impl.spi_release_display
spi_acquire_lora = _impl.spi_acquire_lora
spi_release_lora = _impl.spi_release_lora

# Display. `tft` answers to the ST7789 surface ui.py draws through; `flush`
# pushes a completed frame and is a no-op where drawing already reaches the
# panel; `bl` is the backlight pin, or None where there is no backlight.
tft = _impl.tft
flush = _impl.flush
bl = _impl.bl

# Keyboard. get_key() returns one byte, or b'\x00' when nothing is pending.
get_key = _impl.get_key
set_kbd_backlight = _impl.set_kbd_backlight

# Battery. Volts and percent, or None where the board cannot tell. The v1
# reads an ADC divider; the Pro has no divider and a BQ27220 gauge instead.
battery_voltage = _impl.battery_voltage
battery_percent = _impl.battery_percent

# Called once with the UI instance, after it is constructed. Boards without a
# pointing device use it to route their arrow keys into gui.nav_event().
attach_ui = _impl.attach_ui

# Light sleep (power.py). sleep_wake_pins() lists (gpio, wake_level,
# restore_trigger, kind) for every line that may wake the ESP32: kind "lora"
# for the radio's DIO1, "input" for keys and pointing devices. The restore
# value is the Pin.IRQ_* trigger the line had before (0 = none); ESP-IDF's
# GPIO wake rewrites it to level-sensitive and lowpower puts it back.
# before_sleep() / after_wake() bracket each sleep.
sleep_wake_pins = _impl.sleep_wake_pins
before_sleep = _impl.before_sleep
after_wake = _impl.after_wake

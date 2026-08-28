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
HAS_MIC = _impl.HAS_MIC
HAS_JPEG_SPLASH = _impl.HAS_JPEG_SPLASH

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

# Called once with the UI instance, after it is constructed. Boards without a
# pointing device use it to route their arrow keys into gui.nav_event().
attach_ui = _impl.attach_ui

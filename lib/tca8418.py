# TCA8418 I2C key-matrix controller, with the LilyGO T-Deck Pro keymap.
#
# The T-Deck v1 talks to an ESP32-C3 co-processor at 0x55 that hands back a
# ready-made ASCII byte, so tdeck_node.get_key() is a one-line I2C read. The Pro
# instead has a raw TCA8418 matrix scanner: it reports row/column events and the
# host does the mapping. This module does that mapping and presents the same
# one-byte interface, so it can be dropped straight into UI(get_key_func=...).
#
# Matrix and keymap are taken from Meshtastic's src/input/TDeckProKeyboard.cpp
# so the layout matches what the hardware is silkscreened for and what other
# firmware on this board does.
#
#   4 rows x 10 columns, 35 usable keys
#   raw event byte: bit 7 = press (clear = release), bits 0..6 = key code
#   key index = code - 1, then row = index // 10, col = index % 10
#
# There are no dedicated arrow keys and no trackball on this board. Navigation
# lives on the alt layer: E/X/S/F are up/down/left/right.

import time
from micropython import const

TCA8418_ADDR = const(0x34)

_REG_CFG            = const(0x01)
_REG_INT_STAT       = const(0x02)
_REG_KEY_LCK_EC     = const(0x03)
_REG_KEY_EVENT_A    = const(0x04)
_REG_GPIO_INT_STAT1 = const(0x11)
_REG_KP_GPIO_1      = const(0x1D)
_REG_KP_GPIO_2      = const(0x1E)
_REG_KP_GPIO_3      = const(0x1F)
_REG_GPI_EM_1       = const(0x20)
_REG_GPIO_DIR_1     = const(0x23)
_REG_GPIO_INT_LVL_1 = const(0x26)
_REG_DEBOUNCE_DIS_1 = const(0x29)
_REG_GPIO_PULL_1    = const(0x2C)

_ROWS = const(4)
_COLS = const(10)
_NUM_KEYS = const(35)

# Sticky-modifier timeout, matching Meshtastic's multi-tap threshold.
_MODIFIER_MS = const(1500)

# Bound on how many FIFO entries one get_key() call will work through.
_DRAIN_LIMIT = const(8)

# Navigation and control codes. These match the values ui.py already handles
# for Enter and backspace; the arrows reuse Meshtastic's Key:: values.
KEY_BSP   = const(0x08)
KEY_TAB   = const(0x09)
KEY_ENTER = const(0x0D)
KEY_ESC   = const(0x1B)
KEY_LEFT  = const(0xB4)
KEY_UP    = const(0xB5)
KEY_DOWN  = const(0xB6)
KEY_RIGHT = const(0xB7)

_BL_TOGGLE = const(0xAB)

# Layers per key index: (base, shift, sym, alt). None means the key emits
# nothing on that layer. Index order is row-major across the 4x10 matrix.
_KEYMAP = (
    (b'p', b'P', b'@', None),
    (b'o', b'O', b'+', None),
    (b'i', b'I', b'-', None),
    (b'u', b'U', b'_', None),
    (b'y', b'Y', b')', None),
    (b't', b'T', b'(', bytes([KEY_TAB])),
    (b'r', b'R', b'3', None),
    (b'e', b'E', b'2', bytes([KEY_UP])),
    (b'w', b'W', b'1', None),
    (b'q', b'Q', b'#', bytes([KEY_ESC])),
    (bytes([KEY_BSP]), None, None, None),
    (b'l', b'L', b'"', None),
    (b'k', b'K', b"'", None),
    (b'j', b'J', b';', None),
    (b'h', b'H', b':', None),
    (b'g', b'G', b'/', None),
    (b'f', b'F', b'6', bytes([KEY_RIGHT])),
    (b'd', b'D', b'5', None),
    (b's', b'S', b'4', bytes([KEY_LEFT])),
    (b'a', b'A', b'*', None),
    (bytes([KEY_ENTER]), None, None, None),
    (b'$', None, None, None),
    (b'm', b'M', b'.', None),
    (b'n', b'N', b',', None),
    (b'b', b'B', b'!', bytes([_BL_TOGGLE])),
    (b'v', b'V', b'?', None),
    (b'c', b'C', b'9', None),
    (b'x', b'X', b'8', bytes([KEY_DOWN])),
    (b'z', b'Z', b'7', None),
    (None, None, None, None),   # 29 alt
    (None, None, None, None),   # 30 right shift
    (None, None, None, None),   # 31 sym
    (b' ', None, None, None),   # 32 space
    (None, None, b'0', None),   # 33 mic
    (None, None, None, None),   # 34 left shift
)

_K_ALT = const(29)
_K_RSHIFT = const(30)
_K_SYM = const(31)
_K_LSHIFT = const(34)

_LAYER_BASE = const(0)
_LAYER_SHIFT = const(1)
_LAYER_SYM = const(2)
_LAYER_ALT = const(3)


class TCA8418:
    """Key-matrix scanner. Call get_key() to drain one keystroke."""

    def __init__(self, i2c, addr=TCA8418_ADDR, bl_pin=None):
        self._i2c = i2c
        self._addr = addr
        self._bl = bl_pin
        self._bl_on = False
        self._layer = _LAYER_BASE
        self._layer_at = 0
        self.reset()

    # --- register access ---------------------------------------------------

    def _w(self, reg, val):
        self._i2c.writeto(self._addr, bytes([reg, val]))

    def _r(self, reg):
        self._i2c.writeto(self._addr, bytes([reg]))
        return self._i2c.readfrom(self._addr, 1)[0]

    # --- setup -------------------------------------------------------------

    def reset(self):
        # All pins to input, falling edge.
        #
        # GPI_EM stays 0x00 on purpose. Meshtastic sets it to 0xFF ("all pins
        # to key events") and then discards whatever the matrix cannot explain.
        # That is measurably wasteful here: pressing a key in column 9 emits
        # the matrix event AND a GPIO event (codes 97+), so 'a' arrives as two
        # FIFO entries while 'b' and 'c' arrive as one. Verified on hardware
        # 2026-08-28. Only matrix keys matter, so never ask for the GPIO ones.
        for off in range(3):
            self._w(_REG_GPIO_DIR_1 + off, 0x00)
            self._w(_REG_GPI_EM_1 + off, 0x00)
            self._w(_REG_GPIO_INT_LVL_1 + off, 0x00)

        # Claim the 4x10 matrix: rows on KP_GPIO_1, columns split across
        # KP_GPIO_2 (0..7) and KP_GPIO_3 (8..9).
        self._w(_REG_KP_GPIO_1, (1 << _ROWS) - 1)
        self._w(_REG_KP_GPIO_2, 0xFF)
        self._w(_REG_KP_GPIO_3, (1 << (_COLS - 8)) - 1)

        # Debounce on (the register disables it, so clear the bits).
        for off in range(3):
            self._w(_REG_DEBOUNCE_DIS_1 + off, 0x00)

        self.flush()
        # Enable key-event interrupts and keep the FIFO running.
        self._w(_REG_CFG, 0x01)

    def flush(self):
        """Drain the event FIFO and clear pending interrupts."""
        while self.key_count():
            self._r(_REG_KEY_EVENT_A)
        self._w(_REG_INT_STAT, 0x03)

    def key_count(self):
        return self._r(_REG_KEY_LCK_EC) & 0x0F

    # --- backlight ---------------------------------------------------------

    def set_backlight(self, on):
        """The Pro drives the keyboard backlight from a GPIO, not over I2C."""
        if self._bl is not None:
            self._bl.value(1 if on else 0)
        self._bl_on = bool(on)

    def toggle_backlight(self):
        self.set_backlight(not self._bl_on)

    # --- key reading -------------------------------------------------------

    def _expire_layer(self):
        if self._layer != _LAYER_BASE and \
                time.ticks_diff(time.ticks_ms(), self._layer_at) > _MODIFIER_MS:
            self._layer = _LAYER_BASE

    def get_key(self):
        """Return a one-byte keystroke, or b'' if nothing is pending.

        Same shape as the T-Deck v1's i2c.readfrom(KBD_ADDR, 1), so this can be
        handed straight to UI(get_key_func=...).

        Modifiers are sticky rather than held: tapping shift or sym applies to
        the next key only, and lapses after 1.5 s. A physical keyboard this
        small is usually thumbed one key at a time, and sticky modifiers are
        also what the vendor firmware does.
        """
        self._expire_layer()

        # One call yields one real keystroke. Releases, modifier taps and any
        # stray non-matrix event are consumed here rather than handed back as
        # an empty result, so a caller polling once per loop does not need
        # extra iterations to work through them.
        out = None
        for _ in range(_DRAIN_LIMIT):
            if not self.key_count():
                return b''

            raw = self._r(_REG_KEY_EVENT_A)
            pressed = bool(raw & 0x80)
            code = raw & 0x7F
            if not pressed or code == 0:
                continue

            idx = code - 1
            if idx >= _NUM_KEYS:
                continue          # codes 97+ are GPIO events, not matrix keys

            # Modifiers latch a layer for the next keystroke.
            if idx in (_K_LSHIFT, _K_RSHIFT):
                self._layer = (_LAYER_BASE if self._layer == _LAYER_SHIFT
                               else _LAYER_SHIFT)
                self._layer_at = time.ticks_ms()
                continue
            if idx == _K_SYM:
                self._layer = (_LAYER_BASE if self._layer == _LAYER_SYM
                               else _LAYER_SYM)
                self._layer_at = time.ticks_ms()
                continue
            if idx == _K_ALT:
                self._layer = (_LAYER_BASE if self._layer == _LAYER_ALT
                               else _LAYER_ALT)
                self._layer_at = time.ticks_ms()
                continue

            out = _KEYMAP[idx][self._layer]
            if out is None:
                # Nothing on this layer; fall back to the base character so a
                # mistimed modifier does not silently swallow the keystroke.
                out = _KEYMAP[idx][_LAYER_BASE]
            self._layer = _LAYER_BASE
            break

        if out is None:
            return b''
        if out[0] == _BL_TOGGLE:
            self.toggle_backlight()
            return b''
        return out

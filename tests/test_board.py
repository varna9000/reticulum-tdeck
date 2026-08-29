# board.py is the seam that lets one tdeck_node.py run on both the T-Deck v1
# and the T-Deck Pro. What matters about it is not much code but it is easy to
# get subtly wrong, and wrong here means the wrong pins are driven as outputs
# before anything can notice. These tests pin three things:
#
#   - selection follows board_id.BOARD, and an absent marker means the v1
#     (which is what every existing install is)
#   - the contract is complete: every name tdeck_node.py reaches for exists
#   - the real board modules both declare it, without importing them, since
#     they touch `machine` at import time
#
# Run: python3 tests/test_board.py

import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


# Every name tdeck_node.py takes off the board. Keep this in step with the
# re-export list at the bottom of board.py.
CONTRACT = (
    "config",
    "HAS_TRACKBALL", "HAS_AUDIO", "HAS_MIC", "HAS_JPEG_SPLASH",
    "MONO_DISPLAY",
    "spi", "spi_acquire_display", "spi_release_display",
    "spi_acquire_lora", "spi_release_lora",
    "tft", "flush", "bl",
    "get_key", "set_kbd_backlight", "attach_ui",
    "battery_voltage", "battery_percent",
)


def _stub(name, **extra):
    """A board module that satisfies the contract, with a tag to identify it."""
    m = types.ModuleType(name)
    for attr in CONTRACT:
        setattr(m, attr, attr + "@" + name)
    m.which = name
    for k, v in extra.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def load_board(board_id):
    """Import board.py fresh with a given board_id.BOARD, or none at all."""
    for mod in ("board", "board_id", "board_tdeck_v1", "board_tdeck_pro"):
        sys.modules.pop(mod, None)
    _stub("board_tdeck_v1")
    _stub("board_tdeck_pro")
    if board_id is None:
        # Block the real file on disk so the fallback path is what runs.
        sys.modules["board_id"] = None
    else:
        m = types.ModuleType("board_id")
        m.BOARD = board_id
        sys.modules["board_id"] = m
    import board
    return board


print("selection")
b = load_board("tdeck_v1")
check("board_id tdeck_v1 selects the v1", b.tft == "tft@board_tdeck_v1")
b = load_board("tdeck_pro")
check("board_id tdeck_pro selects the Pro", b.tft == "tft@board_tdeck_pro")
b = load_board(None)
check("a missing board_id falls back to the v1",
      b.BOARD == "tdeck_v1" and b.tft == "tft@board_tdeck_v1")

try:
    load_board("tdeck_ultra")
    check("an unknown board is rejected", False, "no exception raised")
except ValueError as e:
    check("an unknown board is rejected", "tdeck_ultra" in str(e))
except ImportError as e:
    check("an unknown board is rejected", False,
          "raised ImportError, not ValueError: %s" % e)

print("contract")
b = load_board("tdeck_pro")
missing = [n for n in CONTRACT if not hasattr(b, n)]
check("board re-exports every name in the contract", not missing, str(missing))

# A board that is missing a piece must fail at import, not at first use --
# a keyboard that turns out not to exist an hour into a session is worse than
# one that never comes up.
for mod in ("board", "board_id", "board_tdeck_v1", "board_tdeck_pro"):
    sys.modules.pop(mod, None)
_stub("board_tdeck_v1")
_incomplete = _stub("board_tdeck_pro")
del _incomplete.attach_ui
_m = types.ModuleType("board_id")
_m.BOARD = "tdeck_pro"
sys.modules["board_id"] = _m
try:
    import board  # noqa: F401
    check("an incomplete board fails at import", False, "import succeeded")
except AttributeError:
    check("an incomplete board fails at import", True)

print("board modules")
# The real modules cannot be imported here (they touch machine, SPI and I2C at
# import time), so read them as text and confirm each declares the contract.
for fn in ("board_tdeck_v1.py", "board_tdeck_pro.py"):
    src = open(os.path.join(ROOT, fn)).read()
    absent = []
    for n in CONTRACT:
        if n == "config":
            ok = ("as config" in src) or ("\nconfig = " in src)
        else:
            # Traits are column-aligned, so allow padding before the '='.
            ok = (re.search(r"^%s\s*=" % n, src, re.M) is not None
                  or ("\ndef %s(" % n in src))
        if not ok:
            absent.append(n)
    check("%s declares the contract" % fn, not absent, str(absent))

# The one that silently costs a radio: ui.py's trackball block claims GPIO
# 0/1/2/3/15, and GPIO 3 is the Pro's SX1262 chip select.
src = open(os.path.join(ROOT, "board_tdeck_pro.py")).read()
check("the Pro declares HAS_TRACKBALL False",
      re.search(r"^HAS_TRACKBALL\s*=\s*False", src, re.M) is not None)
src = open(os.path.join(ROOT, "board_tdeck_v1.py")).read()
check("the v1 declares HAS_TRACKBALL True",
      re.search(r"^HAS_TRACKBALL\s*=\s*True", src, re.M) is not None)

print()
if _failures:
    print("FAILED: %s" % ", ".join(_failures))
    sys.exit(1)
print("all board tests passed")

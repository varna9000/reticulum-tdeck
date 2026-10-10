# T-Deck Pro vibration motor probe (DRV2605 at I2C 0x5A). One phase per run:
#
#   mpremote connect /dev/cu.usbmodem1101 exec "PHASE=1" run tools/tdeck_pro_haptic_probe.py
#
#   PHASE 0  no motor: does the chip answer, what are its ID / status bits,
#            what level is GPIO 2 and does driving it change anything
#   PHASE 1  ERM library (1): effects 1, 10, 14, 17, 24, each twice
#   PHASE 2  LRA library (6): the same effects
#   PHASE 3  the effect given as EFFECT=n LIB=m, five times, for comparing
#   add EN=0 to any playing phase to hold GPIO 2 low (does the motor still run?)
#
# Before each effect the keyboard light blinks its number (1..5), then the
# effect plays three times. Hold the deck and say which number felt like a
# short, crisp click.

import time
from machine import Pin, I2C

PHASE = globals().get("PHASE", 0)
EFFECT = globals().get("EFFECT", 1)
LIB = globals().get("LIB", 1)
EN = globals().get("EN", 1)          # level to drive GPIO 2 at while playing

i2c = I2C(0, sda=Pin(13), scl=Pin(14), freq=100000)
A = 0x5A
bl = Pin(42, Pin.OUT, value=0)


def w(reg, val):
    i2c.writeto_mem(A, reg, bytes([val]))


def r(reg):
    return i2c.readfrom_mem(A, reg, 1)[0]


def setup(lib):
    w(0x01, 0x00)                    # MODE: out of standby, internal trigger
    w(0x03, lib)                     # LIBRARY
    fb = r(0x1A)
    w(0x1A, (fb | 0x80) if lib == 6 else (fb & 0x7F))   # N_ERM_LRA
    w(0x05, 0x00)                    # sequence ends after slot 1


def play(effect):
    w(0x04, effect)
    w(0x0C, 0x01)                    # GO


print("\n[phase %d] 0x5A on the bus: %s" % (PHASE, A in i2c.scan()))
if PHASE == 0:
    en = Pin(2, Pin.IN)
    print("  GPIO 2 as input reads:", en.value())
    st = r(0x00)
    print("  STATUS 0x%02x  device id %d (3 = DRV2605, 7 = DRV2605L)" % (st, st >> 5))
    print("  MODE 0x%02x  LIBRARY 0x%02x  FEEDBACK 0x%02x  CONTROL3 0x%02x" % (r(0x01), r(0x03), r(0x1A), r(0x1D)))
    for level in (0, 1):
        Pin(2, Pin.OUT, value=level)
        time.sleep_ms(50)
        try:
            print("  GPIO 2 driven %d: chip answers, STATUS 0x%02x" % (level, r(0x00)))
        except OSError as e:
            print("  GPIO 2 driven %d: chip does NOT answer (%s)" % (level, e))
    Pin(2, Pin.IN)
elif PHASE in (1, 2):
    Pin(2, Pin.OUT, value=EN)
    lib = 1 if PHASE == 1 else 6
    setup(lib)
    for idx, eff in enumerate((1, 10, 14, 17, 24)):
        print("  #%d: library %d effect %d" % (idx + 1, lib, eff))
        for _ in range(idx + 1):         # the light blinks the effect's number
            bl.value(1)
            time.sleep_ms(180)
            bl.value(0)
            time.sleep_ms(220)
        time.sleep_ms(500)
        for _ in range(3):
            play(eff)
            time.sleep_ms(800)
        time.sleep_ms(900)
elif PHASE == 3:
    Pin(2, Pin.OUT, value=EN)
    setup(LIB)
    bl.value(1)
    print("  library %d effect %d x5" % (LIB, EFFECT))
    for _ in range(5):
        play(EFFECT)
        time.sleep_ms(600)
    bl.value(0)
w(0x01, 0x40)                        # back to standby
print("  done")

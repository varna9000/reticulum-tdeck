# Which pads does the remaining ~16 mA of the app's light sleep depend on?
#
# 2026-10-11: with LRCK isolated and the flash/PSRAM pads on their sleep
# pull-ups, lowpower.sleep reads 23 mA; stock light sleep with every pad
# HELD in the same state reads 6 mA. Same pad levels, 17 mA apart -- so it is
# something a live pad does that a frozen one does not. lowpower.configure()
# can isolate up to 8 pads for the sleep and restore them on wake, so this
# isolates one group at a time (always with LRCK) and looks for the drop.
#
# Keyboard light ON = unplug USB now; OFF = measuring (~5.5 min, hands off);
# ON again = done, plug back in. Results in /measure.txt.

import time
import board_tdeck_pro as board
import tdeck_pro_config as config
import lowpower
import spleen_8x16 as font

gauge = board.gauge
tft = board.tft
OUT = "/measure.txt"
with open(OUT, "w") as f:
    f.write("sleep groups probe\n")

tft.fill(0x0000)
tft.text(font, "sleep groups probe", 8, 120, 0xFFFF, 0x0000)
tft.text(font, "unplug USB, wait for", 8, 150, 0xFFFF, 0x0000)
tft.text(font, "the keyboard light", 8, 168, 0xFFFF, 0x0000)
tft.flush(force_full=True)
board.panel.power_off()

LRCK = config.I2S_LRCK
# Run 2 (2026-10-11): run 1 found the whole remainder in one group --
# gps/modem/sd 44 43 1 40 10 11 48 isolated: 25 mA -> 5 mA. Pin by pin.
GROUPS = (
    ("gps rx 44 (uart rx)", (44,)),
    ("gps tx 43 (uart tx)", (43,)),
    ("gps pps 1", (1,)),
    ("modem pwrkey 40", (40,)),
    ("modem rxd 10", (10,)),
    ("modem txd 11", (11,)),
    ("sd cs 48", (48,)),
    ("gps uart 44 43 together", (44, 43)),
)

print("gauge:", gauge.current_ma(), "mA", gauge.voltage(), "V")
print("keyboard light on: unplug the cable now")
board.set_kbd_backlight(True)
while board.usb_power():
    time.sleep_ms(200)
time.sleep(5)
board.set_kbd_backlight(False)
time.sleep(2)


def block(tag, pins, n=6):
    lowpower.configure((LRCK,) + tuple(pins), True)
    s = []
    for k in range(n):
        lowpower.sleep(5000, ())
        s.append(gauge.current_ma())
    with open(OUT, "a") as f:
        f.write("%-40s %s usb=%d\n" % (tag, s, 1 if board.usb_power() else 0))


block("baseline (LRCK only)", ())
for name, pins in GROUPS:
    block(name, pins)
block("baseline again", ())
lowpower.configure((LRCK,), True)

with open(OUT, "a") as f:
    f.write("done\n")
board.set_kbd_backlight(True)
while True:
    time.sleep(1)

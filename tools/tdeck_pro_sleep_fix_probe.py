# Does lowpower.configure() remove what the 2026-10-11 probes found?
# Needs firmware with lowpower.configure (commit "Sleep current: ...").
# Fresh boot, battery, the app's own sleep call throughout:
#
#   A  lowpower.sleep, nothing configured          (as shipped before: ~40 mA)
#   B  + flash/PSRAM standby pads                  (expected ~20 mA)
#   C  + I2S LRCK isolated                         (expected ~6-7 mA)
#
# The steps only add, so they run in this order once, 12 x 5 s each.
# Keyboard light ON = unplug USB now; OFF = measuring (~3.5 min, hands off);
# ON again = done, plug back in. Results in /measure.txt.

import time
from machine import Pin
import board_tdeck_pro as board
import tdeck_pro_config as config
import lowpower
import spleen_8x16 as font

gauge = board.gauge
tft = board.tft
OUT = "/measure.txt"
with open(OUT, "w") as f:
    f.write("sleep fix probe; configure=%s\n" % hasattr(lowpower, "configure"))

tft.fill(0x0000)
tft.text(font, "sleep fix probe", 8, 120, 0xFFFF, 0x0000)
tft.text(font, "unplug USB, wait for", 8, 150, 0xFFFF, 0x0000)
tft.text(font, "the keyboard light", 8, 168, 0xFFFF, 0x0000)
tft.flush(force_full=True)
board.panel.power_off()

print("configure available:", hasattr(lowpower, "configure"))
print("gauge:", gauge.current_ma(), "mA", gauge.voltage(), "V")
print("keyboard light on: unplug the cable now")
board.set_kbd_backlight(True)
while board.usb_power():
    time.sleep_ms(200)
time.sleep(5)
board.set_kbd_backlight(False)
time.sleep(2)


def block(tag, n=12):
    s = []
    took = []
    for k in range(n):
        t0 = time.ticks_ms()
        lowpower.sleep(5000, ())
        took.append(time.ticks_diff(time.ticks_ms(), t0))
        s.append(gauge.current_ma())
    with open(OUT, "a") as f:
        f.write("%-44s %s ms=%d..%d usb=%d\n" % (tag, s, min(took), max(took), 1 if board.usb_power() else 0))


def awake(tag, n=10):
    s = []
    for k in range(n):
        time.sleep_ms(1100)
        s.append(gauge.current_ma())
    with open(OUT, "a") as f:
        f.write("%-44s %s\n" % (tag, s))


# Run 2 (2026-10-11): the board now parks GPIO 43 (GPS serial line) at
# power-up. D is the app's configuration; E puts the old state back to show
# the difference in the same run, asleep and awake.
gps_tx = Pin(config.GPS_TX)
with open(OUT, "a") as f:
    f.write("GPS_TX level as the board left it: %d\n" % gps_tx.value())
lowpower.configure(board.sleep_isolate_pins(), board.SLEEP_MSPI_STANDBY)
block("D app config, GPS line parked (new)")
awake("  awake, GPS line parked")
Pin(config.GPS_TX, Pin.OUT, value=1)
time.sleep(2)
block("E app config, GPS line driven high (old)")
awake("  awake, GPS line driven high")
Pin(config.GPS_TX, Pin.IN, Pin.PULL_DOWN)
time.sleep(2)
block("D again")

with open(OUT, "a") as f:
    f.write("done\n")
board.set_kbd_backlight(True)
while True:
    time.sleep(1)

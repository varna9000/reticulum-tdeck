# How much of the Pro's ~25 mA light-sleep floor is the ESP32-S3 holding the
# board up, and how much is the chip itself?
#
# The app sleeps through tools/c_modules/lowpower, which deliberately keeps
# (a) the RTC peripheral domain powered and (b) every pad in its awake state
# (esp_sleep_enable_gpio_switch(false)). Stock machine.lightsleep() does
# neither: ESP-IDF isolates every pad (no drive, no pulls, no input) and
# powers the RTC peripherals down. Comparing the two on battery says what
# those two choices cost here. Both settings are one-way for the life of a
# boot, so this MUST run on a fresh boot that has not slept yet, stock first:
#
#   block S1  machine.lightsleep()   stock: pads isolated, RTC periph off
#   block L   lowpower.sleep()       the app's way (sets both, for good)
#   block S2  machine.lightsleep()   now inherits both: should read like L
#
# The radio LDO, GPS and audio enables are held at their levels through the
# isolated sleep (Pin hold), so S1 does not differ merely by a rail dropping.
#
#   mpremote connect /dev/cu.usbmodemXXXX run tools/tdeck_pro_esp_sleep_probe.py
#
# Keyboard light ON = unplug USB now; OFF = measuring (~3.5 min, hands off);
# ON again = done, plug back in. Results in /measure.txt.

import time
import machine
from machine import Pin
import board_tdeck_pro as board
import tdeck_pro_config as config
import lowpower
import spleen_8x16 as font

gauge = board.gauge
tft = board.tft
OUT = "/measure.txt"
with open(OUT, "w") as f:
    f.write("esp sleep probe\n")

tft.fill(0x0000)
tft.text(font, "esp sleep probe", 8, 120, 0xFFFF, 0x0000)
tft.text(font, "unplug USB, wait for", 8, 150, 0xFFFF, 0x0000)
tft.text(font, "the keyboard light", 8, 168, 0xFFFF, 0x0000)
tft.flush(force_full=True)
board.panel.power_off()             # booster off: one variable fewer

# Hold the rails' enables where the board put them.
held = []
for name, level in (("LORA_EN", 1), ("GPS_EN", 0), ("AUDIO_EN", None)):
    gpio = getattr(config, name, None)
    if gpio is None:
        continue
    if level is None:
        level = Pin(gpio).value()
    Pin(gpio, Pin.OUT, value=level, hold=True)
    held.append("%s=%d@%d" % (name, level, gpio))
print("held:", held)
print("gauge:", gauge.current_ma(), "mA", gauge.voltage(), "V")
print("keyboard light on: unplug the cable now")
board.set_kbd_backlight(True)

while board.usb_power():
    time.sleep_ms(200)
time.sleep(5)
board.set_kbd_backlight(False)
time.sleep(2)


def block(tag, fn, n=12):
    awake = gauge.current_ma()
    s = []
    took = []
    for k in range(n):
        t0 = time.ticks_ms()
        fn()
        took.append(time.ticks_diff(time.ticks_ms(), t0))
        s.append(gauge.current_ma())
    with open(OUT, "a") as f:
        f.write("%s awake=%s sleep=%s ms=%d..%d usb=%d v=%s\n" % (
            tag, awake, s, min(took), max(took), 1 if board.usb_power() else 0, gauge.voltage()))


block("S1 stock lightsleep (pads isolated, RTC periph off)", lambda: machine.lightsleep(5000))
block("L  lowpower.sleep   (pads kept, RTC periph on)   ", lambda: lowpower.sleep(5000, ()))
block("S2 stock lightsleep after lowpower (inherits both)", lambda: machine.lightsleep(5000))

with open(OUT, "a") as f:
    f.write("held %s\ndone\n" % held)
board.set_kbd_backlight(True)
while True:
    time.sleep(1)

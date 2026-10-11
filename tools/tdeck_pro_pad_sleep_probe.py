# Which pins cost the Pro ~33 mA in light sleep?
#
# tools/tdeck_pro_esp_sleep_probe.py (2026-10-11): stock machine.lightsleep()
# (every pad isolated, RTC peripheral domain off) reads ~7 mA on battery; the
# app's lowpower.sleep() (pads kept in their awake state, RTC peripherals on)
# reads ~40 mA. This probe stays on the stock sleep -- so the RTC domain is
# OFF throughout -- and uses Pin hold to keep chosen groups of pads in their
# awake state through it, one group at a time:
#
#   none held            -> the ~7 mA baseline
#   every pad held       -> ~40 mA if the pads are the whole story;
#                           ~7 mA if it is the RTC domain instead
#   one group at a time  -> which wiring it is
#
# The radio LDO, GPS and audio enables are held throughout, as before.
# MUST run on a fresh boot that has not slept through lowpower yet.
#
#   mpremote connect /dev/cu.usbmodemXXXX run tools/tdeck_pro_pad_sleep_probe.py
#
# Keyboard light ON = unplug USB now; OFF = measuring (~7 min, hands off);
# ON again = done, plug back in. Results in /measure.txt.

import time
import machine
from machine import Pin
import board_tdeck_pro as board
import tdeck_pro_config as config
import spleen_8x16 as font

gauge = board.gauge
tft = board.tft
OUT = "/measure.txt"
with open(OUT, "w") as f:
    f.write("pad sleep probe\n")

tft.fill(0x0000)
tft.text(font, "pad sleep probe", 8, 120, 0xFFFF, 0x0000)
tft.text(font, "unplug USB, wait for", 8, 150, 0xFFFF, 0x0000)
tft.text(font, "the keyboard light", 8, 168, 0xFFFF, 0x0000)
tft.flush(force_full=True)
board.panel.power_off()

ALWAYS = (config.LORA_EN, config.GPS_EN, config.AUDIO_EN)       # 46, 39, 41
Pin(config.LORA_EN, Pin.OUT, value=1, hold=True)
Pin(config.GPS_EN, Pin.OUT, value=0, hold=True)
Pin(config.AUDIO_EN, Pin.OUT, value=Pin(config.AUDIO_EN).value(), hold=True)

# Run 2 (2026-10-11): run 1 found the audio/mic group alone costs +13 mA and
# could not read the I2C group; split both, pin by pin.
GROUPS = (
    ("i2c bus 13 14", (13, 14)),
    ("kbd int 15", (15,)),
    ("touch int 12", (12,)),
    ("touch rst 38", (38,)),
    ("i2s bclk 7", (7,)),
    ("i2s dout 8", (8,)),
    ("i2s lrck 9", (9,)),
    ("mic clk 18", (18,)),
    ("mic data 17", (17,)),
)
OTHERS = (36, 33, 47, 34, 35, 37, 16, 3, 4, 6, 5, 42, 2, 44, 43, 1, 40, 10, 11, 48, 19, 20, 0, 21, 45)
EVERY = list(OTHERS)
for _name, _pins in GROUPS:
    EVERY.extend(_pins)

# What each pad is doing right now (level; the mode is not readable here).
with open(OUT, "a") as f:
    f.write("levels " + " ".join("%d=%d" % (p, Pin(p).value()) for p in sorted(EVERY)) + "\n")

print("gauge:", gauge.current_ma(), "mA", gauge.voltage(), "V")
print("keyboard light on: unplug the cable now")
board.set_kbd_backlight(True)
while board.usb_power():
    time.sleep_ms(200)
time.sleep(5)
board.set_kbd_backlight(False)
time.sleep(2)


def hold(pins, on):
    for p in pins:
        Pin(p, hold=on)


def block(tag, pins, n=6):
    s = []
    for k in range(n):
        hold(pins, True)
        machine.lightsleep(5000)
        # Release before reading: a held SDA/SCL cannot talk to the gauge
        # (run 1 read None for every group that included the I2C pads). The
        # gauge's value is its average over the second that just ended.
        hold(pins, False)
        s.append(gauge.current_ma())
    with open(OUT, "a") as f:
        f.write("%-34s %s usb=%d\n" % (tag, s, 1 if board.usb_power() else 0))


block("none held", ())
block("EVERY pad held", EVERY)
block("all audio+mic 7 8 9 18 17", (7, 8, 9, 18, 17))
for name, pins in GROUPS:
    block(name, pins)
block("none held (again)", ())

with open(OUT, "a") as f:
    f.write("done\n")
board.set_kbd_backlight(True)
while True:
    time.sleep(1)

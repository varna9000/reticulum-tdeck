# Splitting the Pro's light-sleep current, part 3.
#
# So far (2026-10-11, battery, stock machine.lightsleep unless noted):
#   every pad isolated, RTC periph off          7 mA
#   + I2S LRCK (GPIO 9) kept low               20 mA   <- the DAC side
#   + every other pad kept                     19 mA   (nothing else adds)
#   the app's lowpower.sleep                   40 mA
# What is the other ~20 mA?  Two candidates differ between "pads held" and the
# app's sleep: (a) the RTC peripheral domain, which the app keeps ON, and
# (b) the flash/PSRAM pads, which only get their sleep pull-ups (ESP-IDF's
# leakage workarounds) when the sleep pad switch is enabled -- the app
# disables it. This probe keeps the stock sleep (so (b) stays applied) and
# turns the RTC domain ON by arming an ext0 wake on the keyboard INT line:
#   ext0 armed ~ 7 mA  -> the RTC domain is innocent: it is (b)
#   ext0 armed ~27 mA  -> it is the RTC domain
# It also tries the I2S LRCK pin three ways.
# MUST run on a fresh boot that has not slept through lowpower yet.
#
# Keyboard light ON = unplug USB now; OFF = measuring (~4 min, hands off, no
# key presses); ON again = done, plug back in. Results in /measure.txt.

import time
import machine
import esp32
from machine import Pin
import board_tdeck_pro as board
import tdeck_pro_config as config
import spleen_8x16 as font

gauge = board.gauge
tft = board.tft
OUT = "/measure.txt"
with open(OUT, "w") as f:
    f.write("sleep split probe\n")

tft.fill(0x0000)
tft.text(font, "sleep split probe", 8, 120, 0xFFFF, 0x0000)
tft.text(font, "unplug USB, wait for", 8, 150, 0xFFFF, 0x0000)
tft.text(font, "the keyboard light", 8, 168, 0xFFFF, 0x0000)
tft.flush(force_full=True)
board.panel.power_off()

Pin(config.LORA_EN, Pin.OUT, value=1, hold=True)
Pin(config.GPS_EN, Pin.OUT, value=0, hold=True)
audio_en = Pin(config.AUDIO_EN).value()
Pin(config.AUDIO_EN, Pin.OUT, value=audio_en, hold=True)

EVERY = (36, 33, 47, 34, 35, 37, 16, 3, 4, 6, 5, 42, 2, 44, 43, 1, 40, 10, 11, 48,
         19, 20, 0, 21, 45, 13, 14, 15, 12, 38, 7, 8, 9, 18, 17)
LRCK = config.I2S_LRCK

print("gauge:", gauge.current_ma(), "mA", gauge.voltage(), "V")
print("keyboard light on: unplug the cable now")
board.set_kbd_backlight(True)
while board.usb_power():
    time.sleep_ms(200)
time.sleep(5)
board.set_kbd_backlight(False)
time.sleep(2)


def block(tag, pins=(), n=6):
    s = []
    took = []
    for k in range(n):
        for p in pins:
            Pin(p, hold=True)
        t0 = time.ticks_ms()
        machine.lightsleep(5000)
        took.append(time.ticks_diff(time.ticks_ms(), t0))
        for p in pins:
            Pin(p, hold=False)
        s.append(gauge.current_ma())
    with open(OUT, "a") as f:
        f.write("%-44s %s ms=%d..%d usb=%d\n" % (tag, s, min(took), max(took), 1 if board.usb_power() else 0))


block("none held")
esp32.wake_on_ext0(pin=Pin(config.KBD_INT, Pin.IN, Pin.PULL_UP), level=esp32.WAKEUP_ALL_LOW)
block("ext0 armed (RTC periph ON), none held")
block("ext0 armed (RTC periph ON), EVERY pad held", EVERY)
esp32.wake_on_ext0(pin=None, level=esp32.WAKEUP_ALL_LOW)
block("ext0 off again, none held")

Pin(LRCK, Pin.IN)
block("LRCK as INPUT, EVERY pad held", EVERY)
Pin(LRCK, Pin.OUT, value=1)
block("LRCK driven HIGH, held", (LRCK,))
Pin(LRCK, Pin.OUT, value=0)
Pin(config.AUDIO_EN, hold=False)
Pin(config.AUDIO_EN, Pin.OUT, value=0, hold=True)
time.sleep_ms(300)
block("LRCK low held, AUDIO_EN low", (LRCK,))
Pin(config.AUDIO_EN, hold=False)
Pin(config.AUDIO_EN, Pin.OUT, value=audio_en, hold=True)
block("none held (again)")

with open(OUT, "a") as f:
    f.write("audio_en was %d\ndone\n" % audio_en)
board.set_kbd_backlight(True)
while True:
    time.sleep(1)

# Does the e-ink panel's booster, left on after a refresh, cost current while
# the deck sleeps?  (lib/eink_gdeq031t10.py never sends POWER_OFF after an
# update, although the sleep spec says the panel "stays in its power-off
# state"; the 2026-10-09 bench row "+ panel POF" may have compared off with
# off, since power_off() is a no-op unless an update ran first.)
#
#   mpremote connect /dev/cu.usbmodemXXXX run tools/tdeck_pro_panel_power_probe.py
#
# Keyboard light ON  = waiting: unplug the USB cable now.
# Keyboard light OFF = measuring on battery, about 4.5 minutes. Hands off.
# Keyboard light ON again = done: plug the cable back in.
#
# It alternates booster ON / OFF three times; in each state it light-sleeps
# 8 x 5 s and reads the BQ27220's averaged current after each sleep. Results
# go to /measure.txt (nothing is printed once the cable is out).

import time
import board_tdeck_pro as board
import lowpower
import spleen_8x16 as font

panel = board.panel
tft = board.tft
gauge = board.gauge

OUT = "/measure.txt"
with open(OUT, "w") as f:
    f.write("panel booster probe\n")

tft.fill(0x0000)
tft.text(font, "panel power probe", 8, 120, 0xFFFF, 0x0000)
tft.text(font, "unplug USB, wait for", 8, 150, 0xFFFF, 0x0000)
tft.text(font, "the keyboard light", 8, 168, 0xFFFF, 0x0000)
tft.flush(force_full=True)          # a real update: the booster is on after it
print("panel._power_on after the refresh:", panel._power_on)
print("gauge:", gauge.current_ma() if gauge else None, "mA", gauge.voltage() if gauge else None, "V")
print("keyboard light on: unplug the cable now")
board.set_kbd_backlight(True)

while board.usb_power():
    time.sleep_ms(200)
time.sleep(5)                        # let the gauge settle on battery
board.set_kbd_backlight(False)


def booster(on):
    if panel._acquire:
        panel._acquire()
    try:
        if on:
            panel._power_on = False
            panel._do_power_on()     # 0x04 POWER_ON
        else:
            panel._power_on = True
            panel._power_off()       # 0x02 POWER_OFF
    finally:
        if panel._release:
            panel._release()


for rnd in range(3):
    for on in (True, False):
        booster(on)
        time.sleep_ms(1500)
        awake = gauge.current_ma()
        s = []
        for k in range(8):
            lowpower.sleep(5000, ())
            s.append(gauge.current_ma())
        with open(OUT, "a") as f:
            f.write("%s awake=%s sleep=%s usb=%d v=%s\n" % (
                "ON " if on else "OFF", awake, s, 1 if board.usb_power() else 0, gauge.voltage()))

booster(False)
with open(OUT, "a") as f:
    f.write("done\n")
board.set_kbd_backlight(True)
while True:
    time.sleep(1)

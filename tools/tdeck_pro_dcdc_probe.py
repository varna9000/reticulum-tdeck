# SX1262 regulator A/B with the radio LISTENING through the app's own sleep:
# the one sleep-current figure the 2026-10-11 bench never took. Battery only.
#
#   L  LDO (the driver's default, what shipped before 2026-10-31)
#   D  DC-DC (SetRegulatorMode 0x96 = 1, the Pro preset since 2026-10-31)
#   L  LDO again, to rule out drift
#
# 12 x 5 s light sleeps per block, exactly as tdeck_pro_sleep_fix_probe.py,
# with the app's pad configuration applied (LRCK let go, flash pads standby)
# and the radio in continuous RX the whole time. An awake row follows each
# block. Expect D about 4 mA under L (datasheet: 8.8 -> 4.6 mA RX at the
# reset gain, 125 kHz; the 1.5 mA TCXO stays). Keyboard light ON = unplug USB now; OFF = measuring (~4
# min, hands off); ON again = done. Results in /measure.txt.
#
# The radio is built the way urns does it (LoRaInterface on the board's
# shared SPI, the tdeck_pro_sx1262 preset + LORA_CONFIG) so the SPI
# arbitration and TCXO setup are the app's; the regulator is then switched
# live on the modem with the same sequence lora.py uses at init.

import time
import board_tdeck_pro as board
import tdeck_pro_config as config
import lowpower
import spleen_8x16 as font
from lora_boards import LORA_BOARDS
from urns.interfaces.lora import LoRaInterface

gauge = board.gauge
tft = board.tft
OUT = "/measure.txt"


def log(line):
    print(line)
    with open(OUT, "a") as f:
        f.write(line + "\n")


with open(OUT, "w") as f:
    f.write("dcdc probe\n")

cfg = dict(LORA_BOARDS["tdeck_pro_sx1262"])
cfg.update(config.LORA_CONFIG)
cfg["spi"] = board.spi
cfg["spi_acquire"] = board.spi_acquire_lora
cfg["spi_release"] = board.spi_release_lora
cfg["use_dcdc"] = False               # start in LDO; switched by hand below
iface = LoRaInterface(cfg)
if not iface.online:
    raise SystemExit("radio did not come up")
modem = iface._modem
lora_cfg = {
    "freq_khz": iface._freq_khz, "sf": iface._sf, "bw": iface._bw,
    "coding_rate": iface._coding_rate, "output_power": iface._tx_power,
    "preamble_len": iface._preamble_len, "crc_en": iface._crc_en,
    "syncword": iface._syncword,
}


def regulator(dcdc):
    """lora.py's init sequence, applied live. standby() first: it clears
    the driver's receiving state (configure() refuses while a continuous
    RX is up), then the raw STDBY_RC so 0x96 lands in the mode the
    datasheet asks for; recalibrate, reconfigure, listen. The RX gain
    register is left at its reset value both times, as the app leaves it
    (neither passes rx_boost), so the datasheet figures to expect are the
    un-boosted ones: 8.8 mA LDO against 4.6 mA DC-DC, plus the TCXO."""
    board.spi_acquire_lora()
    try:
        modem.standby()
        modem._cmd("BB", 0x80, 0)                    # STDBY_RC
        modem._cmd("BB", 0x96, 0x01 if dcdc else 0x00)
        time.sleep_ms(5)
        modem.calibrate()
        modem.calibrate_image()
        time.sleep_ms(10)
        modem.configure(lora_cfg)
        modem.start_recv(continuous=True)
    finally:
        board.spi_release_lora()


tft.fill(0x0000)
tft.text(font, "dcdc probe", 8, 120, 0xFFFF, 0x0000)
tft.text(font, "unplug USB, wait for", 8, 150, 0xFFFF, 0x0000)
tft.text(font, "the keyboard light", 8, 168, 0xFFFF, 0x0000)
tft.flush(force_full=True)
board.panel.power_off()

log("gauge: %d mA %.2f V" % (gauge.current_ma(), gauge.voltage()))
print("keyboard light on: unplug the cable now")
board.set_kbd_backlight(True)
while board.usb_power():
    time.sleep_ms(200)
time.sleep(5)
board.set_kbd_backlight(False)
time.sleep(2)

lowpower.configure(board.sleep_isolate_pins(), board.SLEEP_MSPI_STANDBY)


def block(tag, n=12):
    s = []
    took = []
    for k in range(n):
        t0 = time.ticks_ms()
        lowpower.sleep(5000, ())
        took.append(time.ticks_diff(time.ticks_ms(), t0))
        s.append(gauge.current_ma())
    log("%-40s %s ms=%d..%d usb=%d" % (tag, s, min(took), max(took), 1 if board.usb_power() else 0))


def awake(tag, n=8):
    s = []
    for k in range(n):
        time.sleep_ms(1100)
        s.append(gauge.current_ma())
    log("%-40s %s" % (tag, s))


regulator(False)
time.sleep(2)
block("L  LDO, radio listening, asleep")
awake("   LDO, awake")
regulator(True)
time.sleep(2)
block("D  DC-DC, radio listening, asleep")
awake("   DC-DC, awake")
regulator(False)
time.sleep(2)
block("L  LDO again")
log("done")
board.set_kbd_backlight(True)
while True:
    time.sleep(1)

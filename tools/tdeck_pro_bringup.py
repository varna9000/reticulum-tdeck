# T-Deck Pro hardware bring-up. Run this on the device REPL, on stock
# MicroPython, before trusting any of the drivers:
#
#   mpremote connect /dev/cu.usbmodem101 run tools/tdeck_pro_bringup.py
#
# It answers, in order, the questions that decide whether the rest of the port
# is even pointed at the right hardware:
#
#   1. Do the two power gates actually bring the board up?
#   2. Which pins is the I2C bus on, and is the TCA8418 keyboard there?
#   3. Does the SX1262 answer over SPI on the pins from Meshtastic's variant.h?
#   4. Is the e-ink panel wired with its data line on GPIO 33 or GPIO 47?
#
# Question 4 is the open one. Meshtastic's variant.h gives PIN_EINK_MOSI 47
# while putting the shared bus on SPI_MOSI 33 / SPI_MISO 47, which cannot both
# be taken at face value. This script drives the panel each way and asks you
# which one changed the screen.

import time
from machine import Pin, SPI, I2C

# --- pins (see tdeck_pro_config.py) ---
BOARD_1V8_EN = 38
LORA_EN = 46

LORA_SCK, LORA_MOSI, LORA_MISO = 36, 33, 47
LORA_CS, LORA_RST, LORA_BUSY, LORA_DIO1 = 3, 4, 6, 5

EINK_CS, EINK_DC, EINK_BUSY, EINK_SCK = 34, 35, 37, 36

# Candidate I2C pairs. The Pro's variant.h defers to the Arduino core's default
# SDA/SCL rather than naming them, so the bus has to be found by probing.
# (13, 14) is the confirmed pair on this board; the rest stay as fallbacks.
I2C_CANDIDATES = ((13, 14), (8, 9), (18, 8), (43, 44), (17, 18))

_results = []


def report(name, ok, detail=""):
    _results.append((name, ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  -- " + detail) if detail else ""))


# --- 1. power gates --------------------------------------------------------

def power_up():
    print("\n[1] Power gates")
    try:
        p18 = Pin(BOARD_1V8_EN, Pin.OUT)
        p18.value(1)
        report("BOARD_1V8_EN (GPIO38) driven high", True)
    except Exception as e:
        report("BOARD_1V8_EN (GPIO38) driven high", False, str(e))
    try:
        pl = Pin(LORA_EN, Pin.OUT)
        pl.value(1)
        report("LORA_EN (GPIO46) driven high", True)
    except Exception as e:
        report("LORA_EN (GPIO46) driven high", False, str(e))
    time.sleep_ms(100)


# --- 2. I2C ----------------------------------------------------------------

def scan_i2c():
    print("\n[2] I2C bus")
    found_any = False
    for sda, scl in I2C_CANDIDATES:
        try:
            bus = I2C(0, sda=Pin(sda), scl=Pin(scl), freq=100000)
            devs = bus.scan()
        except Exception:
            continue
        if devs:
            found_any = True
            print("      SDA=%d SCL=%d -> %s" %
                  (sda, scl, [hex(d) for d in devs]))
            if 0x34 in devs:
                report("TCA8418 keyboard found at 0x34 (SDA=%d SCL=%d)"
                       % (sda, scl), True)
            for addr, what in ((0x1A, "CST328 touch panel"),
                               (0x55, "BQ27220 battery fuel gauge"),
                               (0x5A, "DRV2605 haptic driver"),
                               (0x6B, "BQ25896 PMU")):
                if addr in devs:
                    print("      note: %s present, likely the %s"
                          % (hex(addr), what))
    if not found_any:
        report("any I2C device on the candidate pin pairs", False,
               "widen I2C_CANDIDATES")
    elif not any(n.startswith("TCA8418") for n, _ in _results):
        report("TCA8418 keyboard at 0x34", False,
               "bus works but the keyboard did not answer")


# --- 3. SX1262 -------------------------------------------------------------

def _busy_wait(busy, timeout_ms=100):
    t = time.ticks_add(time.ticks_ms(), timeout_ms)
    while busy.value():
        if time.ticks_diff(t, time.ticks_ms()) < 0:
            return False
        time.sleep_ms(1)
    return True


def check_lora():
    print("\n[3] SX1262 radio")
    cs = Pin(LORA_CS, Pin.OUT, value=1)
    rst = Pin(LORA_RST, Pin.OUT, value=1)
    busy = Pin(LORA_BUSY, Pin.IN)
    Pin(LORA_DIO1, Pin.IN)

    spi = SPI(1, baudrate=2_000_000, polarity=0, phase=0,
              sck=Pin(LORA_SCK), mosi=Pin(LORA_MOSI), miso=Pin(LORA_MISO))

    # Hardware reset, then wait for the chip to release BUSY.
    rst.value(0)
    time.sleep_ms(5)
    rst.value(1)
    time.sleep_ms(20)
    report("BUSY released after reset", _busy_wait(busy, 200),
           "BUSY stuck high: check LORA_EN and the 1V8 rail")

    # WriteRegister/ReadRegister round trip on the sync-word registers. If the
    # value comes back, SPI wiring and chip select are both correct.
    def write_reg(addr, data):
        _busy_wait(busy)
        cs.value(0)
        spi.write(bytes([0x0D, addr >> 8, addr & 0xFF]) + bytes(data))
        cs.value(1)

    def read_reg(addr, n):
        _busy_wait(busy)
        cs.value(0)
        spi.write(bytes([0x1D, addr >> 8, addr & 0xFF, 0x00]))
        out = spi.read(n)
        cs.value(1)
        return out

    try:
        write_reg(0x0740, b'\x14\x24')
        back = read_reg(0x0740, 2)
        report("SX1262 register round trip", back == b'\x14\x24',
               "wrote 1424 read %s" % (
                   "".join("%02x" % b for b in back) if back else "nothing"))
    except Exception as e:
        report("SX1262 register round trip", False, str(e))

    # GetStatus should return something other than all-zeros / all-ones.
    try:
        _busy_wait(busy)
        cs.value(0)
        spi.write(bytes([0xC0]))
        st = spi.read(1)
        cs.value(1)
        report("SX1262 GetStatus answers", st not in (b'\x00', b'\xff'),
               "status byte %s" % ("%02x" % st[0] if st else "none"))
    except Exception as e:
        report("SX1262 GetStatus answers", False, str(e))

    spi.deinit()


# --- 4. e-ink data line ----------------------------------------------------

def _panel_poke(mosi_pin):
    """Drive a full-white refresh with the panel data line on `mosi_pin`.

    Returns (responded, busy_ms). Commands travel over MOSI too, so a panel on
    the wrong data pin receives nothing at all and never asserts BUSY. A real
    refresh holds BUSY low for roughly a second.
    """
    cs = Pin(EINK_CS, Pin.OUT, value=1)
    dc = Pin(EINK_DC, Pin.OUT, value=1)
    busy = Pin(EINK_BUSY, Pin.IN)

    spi = SPI(1, baudrate=2_000_000, polarity=0, phase=0,
              sck=Pin(EINK_SCK), mosi=Pin(mosi_pin), miso=Pin(LORA_MISO))

    def cmd(c, data=None):
        dc.value(0)
        cs.value(0)
        spi.write(bytes([c]))
        if data:
            dc.value(1)
            spi.write(bytes(data))
        cs.value(1)

    try:
        cmd(0x00, [0x1F, 0x0D])          # panel setting
        # Fill both panel buffers with white.
        for c in (0x10, 0x13):
            cmd(c)
            dc.value(1)
            cs.value(0)
            row = bytes([0xFF] * 30)
            for _ in range(320):
                spi.write(row)
            cs.value(1)
        cmd(0x50, [0x97])
        cmd(0x04)                        # power on
        cmd(0x12)                        # refresh

        # BUSY is active low on this panel. A panel that actually received the
        # refresh command pulls BUSY low for the ~1.1 s the update takes, then
        # releases it. Idle-high proves nothing, so the test is specifically
        # "did BUSY ever go low", not "is BUSY high".
        went_low = False
        t = time.ticks_add(time.ticks_ms(), 1000)
        while time.ticks_diff(t, time.ticks_ms()) > 0:
            if not busy.value():
                went_low = True
                break
            time.sleep_ms(5)
        if not went_low:
            return False, 0

        started = time.ticks_ms()
        t = time.ticks_add(started, 4000)
        while not busy.value():
            if time.ticks_diff(t, time.ticks_ms()) < 0:
                break
            time.sleep_ms(10)
        return True, time.ticks_diff(time.ticks_ms(), started)
    finally:
        spi.deinit()


def check_eink():
    print("\n[4] E-ink data line (the open question)")
    print("      Watch the screen. Each attempt tries a full white refresh.")
    for pin in (47, 33):
        print("      -- trying panel MOSI on GPIO %d ..." % pin)
        try:
            responded, busy_ms = _panel_poke(pin)
        except Exception as e:
            print("         error: %s" % e)
            continue
        if responded:
            print("         BUSY went low for %d ms -- panel ACCEPTED this pin"
                  % busy_ms)
        else:
            print("         BUSY never went low -- nothing listening here")
        time.sleep_ms(1000)
    print("      If the screen went white on exactly one of those, that pin is")
    print("      the panel's data line. Set DISP_MOSI in tdeck_pro_config.py")
    print("      to whichever one worked, and note it in lora_boards.py.")


def main():
    print("T-Deck Pro bring-up")
    print("===================")
    power_up()
    scan_i2c()
    check_lora()
    check_eink()

    print("\nSummary")
    failed = [n for n, ok in _results if not ok]
    for name, ok in _results:
        print("  %s %s" % ("PASS" if ok else "FAIL", name))
    if failed:
        print("\n%d check(s) failed. Do not flash the messenger until the "
              "radio checks pass." % len(failed))
    else:
        print("\nAll automated checks passed.")


main()

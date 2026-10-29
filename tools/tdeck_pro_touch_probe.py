# T-Deck Pro touch panel probe (CST328 or CST3530 at I2C 0x1A). A bring-up
# aid, not a driver: it answers the questions the driver design depends on,
# on hardware, one phase per run so the person holding the deck knows what
# to do:
#
#   mpremote connect /dev/cu.usbmodem1101 exec "PHASE=1" run tools/tdeck_pro_touch_probe.py
#
#   PHASE 0  revision, reset pin, chip answers, debug-info registers (no touching)
#   PHASE 1  hold one finger still near the centre            -> report rate, INT model
#   PHASE 2  drag left to right along the top edge             -> X axis direction/range
#   PHASE 3  drag top to bottom along the left edge            -> Y axis direction/range
#   PHASE 4  two fingers at once                               -> multi-touch layout
#   PHASE 5  hands off                                         -> phantom reports
#   PHASE 6  chip put to sleep by command, then touch          -> does INT still fire
#   PHASE 7  reset held low, then touch                        -> does INT still fire
#   PHASE 8  (CST3530) hands off for SECS, then one read        -> auto-sleep: does it
#            still answer, and does the wake preamble bring it back
#   PHASE 9  (CST3530) as 8, after "disable cycle sleep" (D0 00 07 AB)
#
# Which chip is fitted is decided at the top, the way lib/cst328.py does it:
# the CST3530 info probe first (LilyGO's own order), else CST328. Issue #19:
# later V1.1 boards carry the CST3530, which nobody here has yet. Someone
# with one, please run phases 0, 1, 2, 3, 5, 8 (with SECS=5, 20, 60) and 9,
# and paste the output into the issue.
#
# The keyboard backlight is the cue: it blinks the phase number, then stays
# ON while the phase records. Touch while it is lit, stop when it goes out.
#
# Register map from the Waveshare ESP32-S3-Touch-LCD-2.8 driver and LilyGO's
# SensorLib TouchDrvCST328, corrected by PHASE 0 on this board (2026-10-10):
#   0xD000  first point: [id<<4|status, x_h, y_h, x_l<<4|y_l, pressure]
#           further points at +7, +12, +17, +22 (5 bytes each)
#   0xD005  low nibble = number of touches
#   0xD000 <- 0xAB  clears the report after reading
#   0xD101  enter debug-info mode     0xD109  back to normal mode
#   0xD1F4  TX channels  0xD1F6 RX channels  0xD1F8 X res (le16)  0xD1FA Y res
#   0xD1FE  chip type 0xCACA (the references say 0xD204; this board says 0xD1FE)
#   0xD105  deep sleep: the chip stops answering I2C until the reset pin is pulsed
#
# CST3530 words (LilyGO touch_hyn_core / Hynitron hyn_driver / SensorLib):
#   D0 03 00 00 + read 50   info block: [0..3] chip type LE (0xCACA220E), [27] keys,
#                           [28..29] res x, [30..31] res y, [32..35] fw, [48] tx, [49] rx
#   D0 00 04 00 (twice, 1 ms apart)  wake preamble; then D0 00 00 00, D0 00 0C 00,
#                           D0 00 01 00 = normal mode, coordinate reports
#   D0 07 00 00 + read 9    report: [0..1] checksum LE (0x55 + record bytes), [2] 0xFF,
#                           [3] keys<<4 | fingers, then 5-byte records
#                           x[7:0] y[7:0] z (y[11:8]<<4|x[11:8]) (event<<4|id); event 0 = lifted
#   D0 00 02 AB             end of report, after EVERY read
#   D0 00 22 AB             sleep; only the reset pin wakes it
#   D0 00 07 AB             disable "cycle sleep" (datasheet; no driver uses it)

import time
from machine import Pin, I2C

PHASE = globals().get("PHASE", 0)
SECS = globals().get("SECS", 10)

I2C_SDA, I2C_SCL = 13, 14
TOUCH_ADDR = 0x1A
TOUCH_INT = 12
LORA_EN = 46
BOARD_1V8_EN_V10 = 38      # V1.0 only: the 1.8 V gate. On V1.1 GPIO 38 is the touch reset.
TOUCH_RST_V10 = 45
TOUCH_RST_V11 = 38
DRV2605_ADDR = 0x5A        # present on V1.1
XL9555_ADDR = 0x20         # present on MAX
KBD_BL = 42

i2c = I2C(0, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=100000)
_bl = Pin(KBD_BL, Pin.OUT, value=0)


def hx(b):
    return " ".join("%02x" % x for x in b)


def wr(reg, data=b""):
    i2c.writeto(TOUCH_ADDR, bytes([reg >> 8, reg & 0xFF]) + bytes(data))


def rd(reg, n):
    return i2c.readfrom_mem(TOUCH_ADDR, reg, n, addrsize=16)


# CST3530: command words and plain reads
PROBE = b"\xD0\x03\x00\x00"
PREAMBLE = b"\xD0\x00\x04\x00"
MODE_WORDS = (b"\xD0\x00\x00\x00", b"\xD0\x00\x0C\x00", b"\xD0\x00\x01\x00")
REPORT = b"\xD0\x07\x00\x00"
END = b"\xD0\x00\x02\xAB"
SLEEP_3530 = b"\xD0\x00\x22\xAB"
NO_CYCLE_SLEEP = b"\xD0\x00\x07\xAB"
CHIP = "cst328"
INFO = None
_bad_frames = 0
_wakes = 0


def cmd(word):
    i2c.writeto(TOUCH_ADDR, word)


def rdraw(n):
    return i2c.readfrom(TOUCH_ADDR, n)


def normal_mode():
    """The vendor's wake-and-configure sequence; the first word may be refused."""
    try:
        cmd(PREAMBLE)
    except OSError:
        pass
    time.sleep_ms(1)
    cmd(PREAMBLE)
    for w in MODE_WORDS:
        cmd(w)


# --- bring-up: every phase starts from a known chip state -------------------
addrs = i2c.scan()
rev = "max" if XL9555_ADDR in addrs else ("1.1" if DRV2605_ADDR in addrs else "1.0")
if rev == "1.0":
    Pin(BOARD_1V8_EN_V10, Pin.OUT).value(1)
    rst_gpio = TOUCH_RST_V10
else:
    rst_gpio = TOUCH_RST_V11
Pin(LORA_EN, Pin.OUT).value(1)
time.sleep_ms(50)
rst = Pin(rst_gpio, Pin.OUT, value=1)
rst.value(0)
time.sleep_ms(20)
rst.value(1)
time.sleep_ms(200)

# Which chip: the CST3530 info probe first, as LilyGO's driver does on every
# board (a CST3530 ACKs CST328 register reads, so the other order lies).
for _ in range(5):
    try:
        cmd(PROBE)
        _info = rdraw(50)
    except OSError:
        time.sleep_ms(50)
        continue
    if _info[2] == 0xCA and _info[3] == 0xCA:
        CHIP = "cst3530"
        INFO = _info
        normal_mode()
    break

_int_edges = 0


def _on_int(pin):
    global _int_edges
    _int_edges += 1


int_pin = Pin(TOUCH_INT, Pin.IN, Pin.PULL_UP)
int_pin.irq(trigger=Pin.IRQ_FALLING, handler=_on_int)


def cue(n):
    """Blink the keyboard light n times, then leave it on."""
    _bl.value(0)
    time.sleep_ms(500)
    for _ in range(n):
        _bl.value(1)
        time.sleep_ms(150)
        _bl.value(0)
        time.sleep_ms(250)
    time.sleep_ms(400)
    _bl.value(1)


def read_points_3530():
    """(fingers, [(id, event, x, y, pressure), ...]) and acknowledge the report."""
    global _bad_frames
    cmd(REPORT)
    buf = rdraw(9)
    keys = buf[3] >> 4
    n = buf[3] & 0x0F
    if keys + n > 1:
        buf += rdraw((keys + n - 1) * 5)
    cmd(END)
    cs = 0x55
    for i in range(4, 4 + (keys + n) * 5):
        cs += buf[i]
    if buf[2] != 0xFF or (cs & 0xFFFF) != (buf[0] | (buf[1] << 8)):
        _bad_frames += 1
    pts = []
    for i in range(min(n, 5)):
        o = 4 + (keys + i) * 5
        x = buf[o] | ((buf[o + 3] & 0x0F) << 8)
        y = buf[o + 1] | ((buf[o + 3] & 0xF0) << 4)
        pts.append((buf[o + 4] & 0x0F, buf[o + 4] >> 4, x, y, buf[o + 2]))
    return n, pts


def read_points():
    """(count, [(id, status, x, y, pressure), ...]) and clear the report.
    On the CST3530 'status' is the event nibble: 1 press, 2 hold, 0 lifted."""
    if CHIP == "cst3530":
        return read_points_3530()
    n = rd(0xD005, 1)[0] & 0x0F
    pts = []
    if n:
        buf = rd(0xD000, 27)
        for i in range(min(n, 5)):
            o = i * 5 + (2 if i else 0)
            st = buf[o]
            x = (buf[o + 1] << 4) | (buf[o + 3] >> 4)
            y = (buf[o + 2] << 4) | (buf[o + 3] & 0x0F)
            pts.append((st >> 4, st & 0x0F, x, y, buf[o + 4]))
    wr(0xD000, b"\xAB")
    return n, pts


def stream(secs, expect_reports=True):
    """Poll for secs seconds with the light on; print every change; summarise."""
    global _int_edges
    _int_edges = 0
    t0 = time.ticks_ms()
    last = None
    samples = touched = low = errs = 0
    minx = miny = 9999
    maxx = maxy = -1
    statuses = {}
    maxn = 0
    lines = 0
    first_x = first_y = None
    last_x = last_y = None
    while time.ticks_diff(time.ticks_ms(), t0) < secs * 1000:
        try:
            n, pts = read_points()
        except Exception as e:
            errs += 1
            if errs == 1:
                print("  read error:", e)
            if CHIP == "cst3530":
                # A dozing chip refuses the first write; the preamble wakes it.
                try:
                    normal_mode()
                    _wakes += 1
                except Exception:
                    pass
            time.sleep_ms(20)
            continue
        samples += 1
        if int_pin.value() == 0:
            low += 1
        if n:
            touched += 1
            maxn = max(maxn, n)
            for (_id, st, x, y, p) in pts:
                statuses[st] = statuses.get(st, 0) + 1
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
            if first_x is None:
                first_x, first_y = pts[0][2], pts[0][3]
            last_x, last_y = pts[0][2], pts[0][3]
            key = (n, pts[0][2] // 10, pts[0][3] // 10)
            if key != last and lines < 60:
                last = key
                lines += 1
                print("  n=%d  %s  int=%d" % (
                    n, "  ".join("id%d st%x (%d,%d) p%d" % q for q in pts), _int_edges))
        time.sleep_ms(10)
    _bl.value(0)
    el = max(1, time.ticks_diff(time.ticks_ms(), t0))
    print("  -- %d polls, %d saw a touch, max fingers %d, i2c errors %d"
          % (samples, touched, maxn, errs))
    if touched:
        print("     x %d..%d   y %d..%d   first (%d,%d) last (%d,%d)   status nibbles %s"
              % (minx, maxx, miny, maxy, first_x, first_y, last_x, last_y, statuses))
    print("     INT: %d falling edges (%.1f/s), level low on %d of %d polls (%d%%)"
          % (_int_edges, _int_edges * 1000 / el, low, samples, low * 100 // max(1, samples)))
    if CHIP == "cst3530":
        print("     CST3530: %d frames failed their checksum, %d preamble wakes after errors"
              % (_bad_frames, _wakes))


def doze_test(secs, label):
    """Hands off for secs with nothing on the bus, then one read: does the
    CST3530 still answer, and if not, does the wake preamble bring it back
    without a reset?"""
    print("  %s: waiting %d s with the bus idle (hands off)..." % (label, secs))
    time.sleep_ms(secs * 1000)
    try:
        n, pts = read_points()
        print("  after %d s idle the chip STILL answers (count=%d)" % (secs, n))
        return
    except Exception as e:
        print("  after %d s idle the first read FAILED (%s): it dozed" % (secs, e))
    try:
        normal_mode()
        n, pts = read_points()
        print("  the wake preamble brought it back without a reset (count=%d)" % n)
    except Exception as e:
        print("  the preamble did NOT bring it back (%s); pulsing reset" % e)
        rst.value(0)
        time.sleep_ms(20)
        rst.value(1)
        time.sleep_ms(200)
        try:
            normal_mode()
            n, pts = read_points()
            print("  reset pulse: chip answers again, count=%d" % n)
        except Exception as e:
            print("  reset pulse: still no answer (%s)" % e)


print("\n[phase %d] revision %s, touch reset GPIO %d, 0x1a on the bus: %s, chip %s, INT idle level %d"
      % (PHASE, rev, rst_gpio, "YES" if TOUCH_ADDR in i2c.scan() else "NO", CHIP, int_pin.value()))

if PHASE == 0 and CHIP == "cst3530":
    print("  info block:", hx(INFO))
    print("  chip type 0x%08x (expect 0xcaca220e)   firmware 0x%08x   project 0x%08x" % (
        INFO[0] | (INFO[1] << 8) | (INFO[2] << 16) | (INFO[3] << 24),
        INFO[32] | (INFO[33] << 8) | (INFO[34] << 16) | (INFO[35] << 24),
        INFO[36] | (INFO[37] << 8) | (INFO[38] << 16) | (INFO[39] << 24)))
    print("  resolution %d x %d   keys %d   tx %d rx %d" % (
        INFO[28] | (INFO[29] << 8), INFO[30] | (INFO[31] << 8), INFO[27], INFO[48], INFO[49]))
    n, pts = read_points()
    print("  normal mode read: count=%d (should be 0 with no finger), bad frames %d" % (n, _bad_frames))

elif PHASE == 0:
    wr(0xD101)
    time.sleep_ms(20)
    d1f4 = rd(0xD1F4, 12)
    d200 = rd(0xD200, 8)
    print("  0xD1F4..0xD1FF:", hx(d1f4))
    print("  0xD200..0xD207:", hx(d200))
    print("  tx %d rx %d   resolution %d x %d   chip type @D1FE 0x%04x (expect 0xcaca)" % (
        d1f4[0], d1f4[2], d1f4[4] | (d1f4[5] << 8), d1f4[6] | (d1f4[7] << 8),
        (d1f4[10] << 8) | d1f4[11]))
    wr(0xD109)
    time.sleep_ms(20)
    n, pts = read_points()
    print("  normal mode read: count=%d (should be 0 with no finger)" % n)

elif PHASE in (1, 2, 3, 4, 5):
    cue(PHASE)
    stream(SECS)

elif PHASE == 6:
    if CHIP == "cst3530":
        normal_mode()
        cmd(SLEEP_3530)
        which = "D0 00 22 AB"
    else:
        wr(0xD105)
        which = "0xD105"
    time.sleep_ms(50)
    try:
        read_points()
        print("  after %s the chip STILL answers (sleep command did not take)" % which)
    except Exception as e:
        print("  after %s the chip does not answer (%s): asleep" % (which, e))
    cue(6)
    stream(SECS, expect_reports=False)
    rst.value(0)
    time.sleep_ms(20)
    rst.value(1)
    time.sleep_ms(200)
    if CHIP == "cst3530":
        normal_mode()
    n, pts = read_points()
    print("  reset pulse: chip answers again, count=%d" % n)

elif PHASE == 8:
    if CHIP != "cst3530":
        print("  phase 8 is for the CST3530; this is a %s" % CHIP)
    else:
        doze_test(SECS, "auto-sleep")

elif PHASE == 9:
    if CHIP != "cst3530":
        print("  phase 9 is for the CST3530; this is a %s" % CHIP)
    else:
        normal_mode()
        cmd(NO_CYCLE_SLEEP)
        doze_test(SECS, "auto-sleep with cycle sleep disabled")

elif PHASE == 7:
    rst.value(0)
    time.sleep_ms(20)
    cue(7)
    stream(SECS, expect_reports=False)
    rst.value(1)
    time.sleep_ms(200)
    n, pts = read_points()
    print("  reset released: chip answers again, count=%d" % n)

_bl.value(0)
print("  done")
